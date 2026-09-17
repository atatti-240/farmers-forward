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
from pipeline import model, recommender
from test_database import FakeConnection


MEASUREMENTS = dict(ph=6.5, ec=1.0, nitrogen=10, phosphorus=25, potassium=180)


def section(label, crop):
    return dict(section_label=label, crop_type=crop, latitude=29.53,
                longitude=-95.77, **MEASUREMENTS)


def predictions(_bundle, frame):
    return [dict(ml_health_score=75, ml_category_from_score="Good",
                 ml_crop_suitable=["Corn"]) for _ in range(len(frame))]


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
        n_recs = [next(r for r in result["recommendations"]
                       if r["fix_code"] == "nitrogen_deficient") for result in analysis["results"]]
        self.assertNotEqual(n_recs[0]["rate"], n_recs[1]["rate"])
        self.assertIn("Corn", n_recs[0]["notes"])
        self.assertIn("Soybeans", n_recs[1]["notes"])
        self.assertIn("generic", n_recs[2]["notes"])
        self.assertIsNone(analysis["results"][2]["selected_crop"]["within_profile"])

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
                assessment = recommender.assess_target_crop(MEASUREMENTS, name)
                self.assertEqual(assessment["profile_name"], "Corn")
                self.assertFalse(assessment["within_profile"])
                self.assertTrue(any(f.startswith("N ") for f in assessment["limiting_factors"]))
        self.assertEqual(recommender.normalize_crop_name("soybean"), "Soybeans")

    def test_unknown_crop_does_not_get_a_fabricated_profile(self):
        for crop in ("Custom millet", "Rice", "Hay", None):
            assessment = recommender.assess_target_crop(MEASUREMENTS, crop)
            self.assertFalse(assessment["profile_available"])
            self.assertIsNone(assessment["within_profile"])

    def test_crop_profiles_can_differ_for_identical_readings(self):
        readings = dict(MEASUREMENTS, ph=7.5, nitrogen=50)
        self.assertFalse(recommender.assess_target_crop(readings, "Corn")["within_profile"])
        self.assertTrue(recommender.assess_target_crop(readings, "Cotton")["within_profile"])


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
