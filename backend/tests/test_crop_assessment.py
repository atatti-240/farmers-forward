"""Crop selection must survive validation, analysis, storage and report output."""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, database
from backend import model_runner, pdf_report

model_runner._ensure_pipeline_path()
from crop_data.crop_lookup import fert_rec
from pipeline import agronomy, crop_profile, model, recommender
from test_database import FakeConnection


# EC as the 1:2 sensor reads it (ECe 3.0); the pipeline-level tests below pass ECe.
MEASUREMENTS = dict(ph=6.5, ec=1.0, nitrogen=10, phosphorus=25, potassium=180)
CORN, COTTON, TOMATO = ("row", "CORN (150 BU/A)"), ("fiber", "COTTON (2.0 BALES/A)"), ("veg", "TOMATOES")


def section(label, crop):
    return dict(section_label=label, crop_type=crop, latitude=29.53,
                longitude=-95.77, **MEASUREMENTS)


def predictions(_bundle, frame):
    return [dict(ml_health_score=75, ml_category_from_score="Good",
                 ml_crop_suitable=["Corn"]) for _ in range(len(frame))]


def pdf_text(flowables):
    """Plain text of PDF flowables, including paragraphs nested in tables."""
    out = []
    for item in flowables:
        if isinstance(item, (list, tuple)):
            out.append(pdf_text(item))
        elif hasattr(item, "getPlainText"):
            out.append(item.getPlainText())
        elif hasattr(item, "_cellvalues"):
            out.append(pdf_text([cell for row in item._cellvalues for cell in row]))
    return "\n".join(out)


def rec(result_or_recs, fix_code):
    recs = result_or_recs["recommendations"] if isinstance(result_or_recs, dict) else result_or_recs
    return next((r for r in recs if r["fix_code"] == fix_code), None)


class CropSubmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ff-crop-tests-")
        self.addCleanup(temp.cleanup)
        patch = mock.patch.multiple(database.config, DATABASE_URL=None,
                                    DATABASE_PATH=os.path.join(temp.name, "test.db"))
        patch.start()
        self.addCleanup(patch.stop)
        database.init_db()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True
            session["admin_id"] = 1
        for patch in (mock.patch.object(model_runner, "_get_bundle", return_value=object()),
                      mock.patch.object(model, "predict_frame", side_effect=predictions)):
            patch.start()
            self.addCleanup(patch.stop)

    def submit(self, sections):
        return self.client.post("/api/submit", json={
            "farm_name": "Crop round trip", "date": "2026-09-16", "sections": sections})

    def test_each_sample_keeps_its_own_crop_and_tailored_recommendations(self):
        response = self.submit([section("A1", " corn "), section("A2", "Soybeans"),
                                section("A3", "Local heritage millet")])
        self.assertEqual(response.status_code, 200, response.json)
        analysis = response.json
        crops = [r["crop_type"] for r in analysis["results"]]
        self.assertEqual(crops, ["corn", "Soybeans", "Local heritage millet"])
        rows = database.query_all("SELECT section_label, crop_type FROM samples ORDER BY id")
        self.assertEqual([(r["section_label"], r["crop_type"]) for r in rows],
                         list(zip(["A1", "A2", "A3"], crops)))
        stored = self.client.get("/api/results/%s" % analysis["farm_id"])
        self.assertEqual(stored.json, analysis)
        corn, soy, millet = analysis["results"]
        # Corn: A&M rate at a 150 bu/A yield goal. Soybeans fix their own N, and
        # A&M recommends none at 10 ppm. The custom crop gets the generic rate.
        self.assertTrue(rec(corn, "nitrogen_deficient")["rate"].startswith(
            "%.0f lb N/acre" % fert_rec(*CORN, "N", 10)))
        self.assertIn("150 bu/A", rec(corn, "nitrogen_deficient")["notes"])
        self.assertIsNone(rec(soy, "nitrogen_deficient"))
        self.assertIn("generic", rec(millet, "nitrogen_deficient")["notes"])
        self.assertEqual(corn["selected_crop"]["basis"],
                         "Based on a corn yield goal of 150 bu/A (Texas A&M soil-test tables).")
        self.assertEqual(soy["selected_crop"]["basis"],
                         "Based on the Texas A&M soil-test table for soybeans.")
        self.assertIsNone(millet["selected_crop"]["basis"])
        self.assertIsNone(millet["selected_crop"]["within_profile"])
        self.assertEqual(corn["crop_assessment"]["crop"], CORN[1])
        self.assertIsNone(millet["crop_assessment"])

    def test_crop_score_is_the_headline_and_ml_is_general_soil_health(self):
        corn, millet = self.submit([section("A1", "Corn"),
                                    section("A2", "Local heritage millet")]).json["results"]
        self.assertEqual(corn["score_basis"], "crop")
        self.assertEqual(corn["health_score"], corn["rule_health_score"])
        self.assertNotEqual(corn["health_score"], 75)
        self.assertEqual((corn["general_health_score"], corn["general_health_category"]),
                         (75, "Good"))                  # the (mocked) ML score
        # Custom crop: unchanged, the ML score is the headline.
        self.assertEqual(millet["score_basis"], "general")
        self.assertEqual((millet["health_score"], millet["health_category"]), (75, "Good"))
        styles = pdf_report._styles()
        self.assertIn("Score is for corn. General soil health: 75/100 (Good).", pdf_text(
            pdf_report._section_flowables(corn, "Field", "2026-09-16", styles)))
        self.assertNotIn("General soil health", pdf_text(
            pdf_report._section_flowables(millet, "Field", "2026-09-16", styles)))

    def test_sensor_ec_is_shown_as_entered_and_analyzed_as_ece(self):
        frames = []
        def capture(bundle, frame):
            frames.append(frame)
            return predictions(bundle, frame)
        with mock.patch.object(model, "predict_frame", side_effect=capture):
            result = self.submit([section("A1", "Corn")]).json["results"][0]
            live = self.client.post("/api/score", json={"sections": [
                dict(section_label="A1", **MEASUREMENTS)]})
        self.assertEqual(result["inputs"]["ec"], 1.0)
        self.assertEqual(result["inputs"]["ece"], 3.0)
        self.assertEqual(result["inputs"]["ec_method"], "1:2")
        self.assertEqual(result["crop_assessment"]["salinity"]["ece_dS_m"], 3.0)
        self.assertEqual(database.query_one("SELECT ec FROM samples")["ec"], 1.0)
        # Both the submission and the live map score hand the ML model ECe.
        self.assertEqual(live.status_code, 200)
        self.assertEqual([list(frame["ec"]) for frame in frames], [[3.0], [3.0]])

    def test_pdf_shows_the_yield_goal_and_ece(self):
        response = self.submit([section("A1", "Corn")])
        result = response.json["results"][0]
        flow = pdf_report._section_flowables(result, "Field", "2026-09-16", pdf_report._styles())
        text = "\n".join(item.getPlainText() for item in flow if hasattr(item, "getPlainText"))
        self.assertIn("Based on a corn yield goal of 150 bu/A", text)
        table = pdf_report._inputs_table(result["inputs"], pdf_report._styles())
        self.assertIn(["EC (1:2 soil:water)", "1.00", "dS/m"], table._cellvalues)
        self.assertIn(["ECe (est. saturated paste)", "3.00", "dS/m"], table._cellvalues)

    def test_missing_multiple_and_invalid_crops_are_rejected_before_writes(self):
        for crop in (None, "", "   ", ["Corn", "Cotton"], {"name": "Corn"},
                     123, "x" * 101, "Corn\nCotton"):
            with self.subTest(crop=crop):
                response = self.submit([section("A1", crop)])
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn("crop_type", response.json["error"])
        self.assertEqual(database.query_all("SELECT * FROM farms"), [])
        missing = section("A1", "Corn")
        del missing["crop_type"]
        self.assertEqual(self.submit([missing]).status_code, 400)

    def test_one_missing_crop_blocks_the_entire_field(self):
        self.assertEqual(self.submit([section("A1", "Corn"), section("A2", " ")]).status_code, 400)
        self.assertEqual(database.query_all("SELECT * FROM samples"), [])

    def test_custom_crop_is_literal_text_in_pdf_and_json(self):
        crop = 'Heritage <millet> & "trial"'
        response = self.submit([section("A1", crop)])
        self.assertEqual(response.status_code, 200)
        result = response.json["results"][0]
        flow = pdf_report._section_flowables(result, "Field", "2026-09-16", pdf_report._styles())
        text = "\n".join(item.getPlainText() for item in flow if hasattr(item, "getPlainText"))
        self.assertIn(crop, text)
        self.assertIn("general soil guidance", text)
        report = self.client.get("/api/report/%s/section/A1" % response.json["farm_id"])
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.data.startswith(b"%PDF"))

    def test_old_saved_results_still_render_without_a_crop(self):
        analysis = model_runner.run_analysis("Old field", "2026-09-01", [section("A1", "Corn")])
        result = analysis["results"][0]
        result.pop("crop_type")
        result.pop("selected_crop")
        self.assertTrue(pdf_report.build_section_pdf(result, "Old field", "2026-09-01").startswith(b"%PDF"))


class CropProfileTests(unittest.TestCase):
    def test_spelling_case_and_aliases_use_the_existing_profile(self):
        for name in ("Corn", " corn ", "CORN", "maize"):
            with self.subTest(name=name):
                assessment = recommender.assess_target_crop(dict(MEASUREMENTS, ph=7.5), name)
                self.assertEqual(assessment["profile_name"], "Corn")
                self.assertEqual(assessment["yield_goal"], "150 bu/A")
                self.assertFalse(assessment["within_profile"])
                self.assertTrue(assessment["limiting_factors"][0].startswith(
                    "pH 7.5 is above the 5.8-7.0 optimum for corn"))
        self.assertEqual(recommender.normalize_crop_name("soybean"), "Soybeans")

    def test_unknown_crop_does_not_get_a_fabricated_profile(self):
        for crop in ("Custom millet", "Dragon fruit", None):
            assessment = recommender.assess_target_crop(MEASUREMENTS, crop)
            self.assertFalse(assessment["profile_available"])
            self.assertIsNone(assessment["within_profile"])
            self.assertIsNone(crop_profile.assess(MEASUREMENTS, crop))

    def test_rice_has_a_profile_but_no_am_nitrogen_table(self):
        self.assertTrue(recommender.assess_target_crop(MEASUREMENTS, "Rice")["profile_available"])
        n_rec = rec(recommender.build_recommendations(MEASUREMENTS, target_crop="Rice"),
                    "nitrogen_deficient")
        self.assertIn("Texas Rice Production Guidelines", n_rec["notes"])
        self.assertIn("generic", n_rec["notes"])

    def test_crop_profiles_can_differ_for_identical_readings(self):
        readings = dict(MEASUREMENTS, ec=4.0)  # ECe: past corn's 1.7, under cotton's 7.7
        self.assertFalse(recommender.assess_target_crop(readings, "Corn")["within_profile"])
        self.assertTrue(recommender.assess_target_crop(readings, "Cotton")["within_profile"])

    def test_assumed_tables_are_named_on_the_report(self):
        for crop, table in (("Peppers", "PEPPERS,BELL"), ("Peanuts", "PEANUTS, DRYLAND"),
                            ("Alfalfa", "ALFALFA (NON-IRRIGATED, ANNUALLY)"),
                            ("Hay", "IMPROVED AND HYBRID BERMUDA GRASS (2 HAY CUTTINGS-2 TONS/A AVG.)")):
            with self.subTest(crop=crop):
                assessment = recommender.assess_target_crop(MEASUREMENTS, crop)
                self.assertTrue(assessment["profile_available"])
                self.assertEqual(assessment["basis"], 'Assumes the Texas A&M table "%s".' % table)
        self.assertEqual(recommender.assess_target_crop(MEASUREMENTS, "Hay")["profile_name"],
                         "Bermudagrass")
        self.assertEqual(recommender.assess_target_crop(MEASUREMENTS, "Barley")["yield_goal"],
                         "30-40 bu/A")

    def test_viable_now_agrees_with_the_crop_assessment(self):
        for readings in (MEASUREMENTS, dict(MEASUREMENTS, ph=7.6, ec=3.0),
                         dict(MEASUREMENTS, ph=5.2, ec=0.5), dict(MEASUREMENTS, ph=8.4, ec=9.0)):
            viability = agronomy.crop_viability(readings)
            limits = {item["crop"]: item["limiting_factors"] for item in viability["not_viable"]}
            self.assertEqual(viability["viable"], [c for c in crop_profile.T.CROP_DEFAULT_YIELD
                                                   if c in viability["ideal"] + viability["with_adjustments"]])
            for crop in crop_profile.T.CROP_DEFAULT_YIELD:
                with self.subTest(readings=readings, crop=crop):
                    assessment = recommender.assess_target_crop(readings, crop)
                    bucket = ("ideal" if crop in viability["ideal"] else
                              "viable_with_adjustments" if crop in viability["with_adjustments"]
                              else "not_viable")
                    self.assertEqual(assessment["fit"], bucket)
                    self.assertEqual(assessment["within_profile"], bucket == "ideal")
                    self.assertEqual(assessment["limiting_factors"][:len(limits.get(crop, []))],
                                     limits.get(crop, []))

    def test_viable_now_uses_the_tolerable_band_and_80_percent_yield(self):
        def bucket(readings, crop):
            return agronomy.crop_fit(readings, crop)["status"]
        # Corn: optimal pH 5.8-7.0, tolerable 5.5-8.0; salt threshold 1.7, 12%/dS/m.
        self.assertEqual(bucket(dict(MEASUREMENTS, ph=6.5, ec=1.0), "Corn"), "ideal")
        self.assertEqual(bucket(dict(MEASUREMENTS, ph=7.8, ec=1.0), "Corn"), "viable_with_adjustments")
        self.assertEqual(bucket(dict(MEASUREMENTS, ph=8.1, ec=1.0), "Corn"), "not_viable")
        self.assertEqual(bucket(dict(MEASUREMENTS, ph=6.5, ec=3.3), "Corn"), "viable_with_adjustments")  # 80.8%
        self.assertEqual(bucket(dict(MEASUREMENTS, ph=6.5, ec=3.4), "Corn"), "not_viable")               # 79.6%
        # Oats publish no salt-yield slope: above the threshold, 80% cannot be shown.
        oats = agronomy.crop_fit(dict(MEASUREMENTS, ph=6.5, ec=6.5), "Oats")
        self.assertEqual(oats["status"], "not_viable")
        self.assertIn("yield loss unknown", oats["limiting_factors"][0])

    def test_ph_outside_the_tolerable_band_caps_the_crop_score_at_fair(self):
        clean = dict(ph=7.6, ec=1.0, nitrogen=60, phosphorus=60, potassium=200)  # ECe 1.0
        tomato = agronomy.compute_health_score(clean, "Tomatoes")   # tolerable to 7.5
        self.assertEqual(tomato, crop_profile.T.PH_OUT_OF_TOLERANCE_SCORE_CAP)
        self.assertEqual(crop_profile.T.categorize(tomato), "Fair")
        cotton = agronomy.compute_health_score(clean, "Cotton")     # tolerable to 8.2
        self.assertGreater(cotton, 60)
        self.assertGreater(agronomy.compute_health_score(clean), 60)  # no crop: no cap


class CropThresholdTests(unittest.TestCase):
    # One sample (EC as ECe) run through three crops.
    SAMPLE = dict(ph=7.6, ec=3.0, nitrogen=12, phosphorus=18, potassium=110)

    def test_crop_without_a_table_uses_the_generic_thresholds(self):
        generic = agronomy.detect_problems(self.SAMPLE)
        self.assertEqual(agronomy.detect_problems(self.SAMPLE, "Local heritage millet"), generic)
        self.assertEqual(agronomy.detect_problems(self.SAMPLE, None), generic)

    def test_ph_and_salinity_thresholds_follow_the_crop(self):
        codes = {crop: {p["code"]: p for p in agronomy.detect_problems(self.SAMPLE, crop)}
                 for crop in ("Corn", "Cotton", "Tomatoes")}
        # ECe 3.0: past corn (1.7) and tomato (2.5) thresholds, under cotton (7.7).
        self.assertIn("salinity", codes["Corn"])
        self.assertIn("salinity", codes["Tomatoes"])
        self.assertNotIn("salinity", codes["Cotton"])
        self.assertGreater(codes["Corn"]["salinity"]["penalty"],
                           codes["Tomatoes"]["salinity"]["penalty"])
        # pH 7.6: inside cotton's tolerable range (to 8.2), past tomato's (7.5).
        self.assertLess(codes["Cotton"]["ph_alkaline"]["penalty"],
                        codes["Tomatoes"]["ph_alkaline"]["penalty"])

    def test_fertilizer_amounts_come_from_fert_rec(self):
        for crop, table in (("Corn", CORN), ("Cotton", COTTON), ("Tomatoes", TOMATO)):
            recs = recommender.build_recommendations(self.SAMPLE, target_crop=crop)
            for fix_codes, nutrient, metric, unit in (
                    (("nitrogen_deficient", "nitrogen_routine"), "N", "nitrogen", "N"),
                    (("phosphorus_deficient",), "P", "phosphorus", "P2O5"),
                    (("potassium_deficient",), "K", "potassium", "K2O")):
                with self.subTest(crop=crop, nutrient=nutrient):
                    found = [rec(recs, code) for code in fix_codes if rec(recs, code)]
                    self.assertEqual(len(found), 1)
                    expected = fert_rec(*table, nutrient, self.SAMPLE[metric])
                    self.assertTrue(found[0]["rate"].startswith("%.0f lb %s/acre" % (expected, unit)))

    def test_n_is_a_problem_only_when_soil_n_is_genuinely_low(self):
        # Corn at 12 ppm: 155 of a 180 lb 0-ppm rate (86%) -> low soil N.
        corn = {p["code"]: p for p in agronomy.detect_problems(self.SAMPLE, "Corn")}
        self.assertIn("nitrogen_deficient", corn)
        # Tomatoes at 12 ppm: 35 of 60 lb (58%) -> routine feeding, no penalty.
        tomato = {p["code"] for p in agronomy.detect_problems(self.SAMPLE, "Tomatoes")}
        self.assertNotIn("nitrogen_deficient", tomato)
        recs = recommender.build_recommendations(self.SAMPLE, target_crop="Tomatoes")
        routine = rec(recs, "nitrogen_routine")
        self.assertEqual((routine["title"], routine["severity"], routine["priority_weight"]),
                         ("Apply nitrogen for tomatoes", "routine", 0.0))
        self.assertTrue(routine["rate"].startswith("35 lb N/acre"))
        self.assertIs(recs[-1], routine)
        # Corn at 30 ppm: 120 of 180 lb (67%) -> routine; the score ignores N.
        well_fed = dict(self.SAMPLE, nitrogen=30)
        self.assertNotIn("nitrogen_deficient",
                         {p["code"] for p in agronomy.detect_problems(well_fed, "Corn")})
        self.assertEqual(agronomy.compute_health_score(well_fed, "Corn"),
                         agronomy.compute_health_score(dict(well_fed, nitrogen=80), "Corn"))
        self.assertIsNotNone(rec(recommender.build_recommendations(well_fed, target_crop="Corn"),
                                 "nitrogen_routine"))

    def test_nutrient_at_am_sufficiency_is_not_a_problem(self):
        rich = dict(self.SAMPLE, potassium=140)  # corn K sufficient at 130 ppm
        self.assertIsNone(rec(recommender.build_recommendations(rich, target_crop="Corn"),
                              "potassium_deficient"))

    def test_yield_goal_overrides_the_default_table(self):
        crop = crop_profile.resolve("Corn", yield_goal="CORN (180 BU/A)")
        self.assertEqual(crop.yield_goal, "180 bu/A")
        n_rec = rec(recommender.build_recommendations(self.SAMPLE, target_crop=crop),
                    "nitrogen_deficient")
        self.assertTrue(n_rec["rate"].startswith(
            "%.0f lb N/acre" % fert_rec("row", "CORN (180 BU/A)", "N", 12)))
        self.assertIn("180 bu/A", recommender.assess_target_crop(self.SAMPLE, crop)["basis"])
        with self.assertRaises(ValueError):
            crop_profile.resolve("Corn", yield_goal="COTTON (2.0 BALES/A)")

    def test_sensor_ec_converts_to_ece(self):
        self.assertEqual(crop_profile.to_ece(1.0), 3.0)
        self.assertEqual(crop_profile.to_ece(1.0, "1:1"), 2.0)


class CropMigrationTests(unittest.TestCase):
    def test_sqlite_upgrade_is_repeatable_and_preserves_old_readings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "old.db")
            with sqlite3.connect(path) as conn:
                conn.executescript(database.SCHEMA_SQLITE.replace("    crop_type     TEXT,\n", ""))
                conn.execute("INSERT INTO samples (farm_id, sample_id, section_label, latitude, longitude, ph) "
                             "VALUES (1, 'old-A1', 'A1', 29.5, -95.7, 6.5)")
            with mock.patch.multiple(database.config, DATABASE_URL=None, DATABASE_PATH=path):
                database.init_db()
                database.init_db()
                row = database.query_one("SELECT ph, crop_type FROM samples WHERE sample_id = ?", ("old-A1",))
                self.assertEqual(row["ph"], 6.5)
                self.assertIsNone(row["crop_type"])

    def test_postgres_upgrade_uses_idempotent_add_column(self):
        fake = FakeConnection()
        conn = database._PostgresConnection(fake)
        with mock.patch.object(database, "get_db", return_value=conn), \
             mock.patch.object(database, "using_postgres", return_value=True):
            database.init_db()
            database.init_db()
        alterations = [sql for sql, _ in fake.log if sql.startswith("ALTER TABLE")]
        self.assertEqual(alterations, ["ALTER TABLE samples ADD COLUMN IF NOT EXISTS crop_type TEXT"] * 2)
        self.assertIn("crop_type     TEXT", database.SCHEMA_POSTGRES)


if __name__ == "__main__":
    unittest.main()
