"""The passive fertilizer calculator: A&M rates in, products, totals and bags out."""
import json
import math
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, database
from backend import fert_calc, fert_pdf, model_runner, pdf_report
from test_crop_assessment import pdf_text, predictions

model_runner._ensure_pipeline_path()
from pipeline import crop_profile, model, recommender, thresholds as T

# The digitized A&M tables, read raw so the calculator is checked against the
# JSON itself and not against the lookup it uses.
with open(os.path.join(model_runner.config.MODEL_DIR, "crop_data", "crop_npk_tables.json")) as f:
    RAW = json.load(f)["crops"]

# (form crop, A&M category, A&M table, [(N, P, K soil-test ppm), ...]); every
# ppm is a column of that table, so the expected rate is a raw JSON entry.
LEVELS = [
    ("Cotton", "fiber", "COTTON (2.0 BALES/A)", [(4, 10, 20), (30, 30, 100)]),
    ("Corn", "row", "CORN (150 BU/A)", [(4, 10, 20), (40, 30, 100)]),
    ("Tomatoes", "veg", "TOMATOES", [(4, 10, 20), (20, 30, 100)]),
]
OK_SOIL = dict(ph=6.5, ece=0.9)  # in range for all three crops: no warnings


def plan(crop, n, p, k, acres=None, **kw):
    readings = dict(OK_SOIL, **kw)
    return fert_calc.plan_for_region(crop, nitrogen=n, phosphorus=p, potassium=k,
                                     acres=acres, **readings)


def products(result):
    return [i for i in result["items"] if i["kind"] == "product"]


def lines(result):
    return [i["label"] + ": " + i["amounts"] if i["kind"] == "product" else i["text"]
            for i in result["items"]]


def table_text(flowables):
    """pdf_text() plus the plain-string cells of tables."""
    cells = [cell for item in flowables if hasattr(item, "_cellvalues")
             for row in item._cellvalues for cell in row if isinstance(cell, str)]
    return pdf_text(flowables) + "\n" + "\n".join(cells)


class TableParityTests(unittest.TestCase):
    def test_rates_are_the_raw_am_table_entries(self):
        for crop, category, table, levels in LEVELS:
            entry = RAW[category][table]
            for ppm in levels:
                with self.subTest(crop=crop, ppm=ppm):
                    expected = {}
                    for nut, value in zip("NPK", ppm):
                        self.assertIn(str(value), entry[nut])  # a real column
                        expected[nut] = entry[nut][str(value)]
                    result = plan(crop, *ppm)
                    self.assertEqual(result["nutrients"], expected)
                    # ...and the recommender's own lookup gives the same numbers.
                    resolved = crop_profile.resolve(crop)
                    for nut, value in zip("NPK", ppm):
                        self.assertEqual(recommender._am_rate(resolved, nut, value),
                                         expected[nut])
                    self.assertTrue(result["source"].startswith(
                        "Source: Texas A&M AgriLife %s table" % table))

    def test_source_names_the_table_and_default_yield_goal(self):
        self.assertEqual(plan("Corn", 4, 10, 20)["source"],
                         "Source: Texas A&M AgriLife CORN (150 BU/A) table, "
                         "yield goal 150 bu/A (default)")
        self.assertEqual(plan("Cotton", 4, 10, 20)["source"],
                         "Source: Texas A&M AgriLife COTTON (2.0 BALES/A) table, "
                         "yield goal 2.0 bales/A (default)")
        self.assertEqual(plan("Tomatoes", 4, 10, 20)["source"],
                         "Source: Texas A&M AgriLife TOMATOES table "
                         "(the only A&M table for tomatoes)")
        self.assertEqual(plan("Peanuts", 4, 10, 20)["source"],
                         "Source: Texas A&M AgriLife PEANUTS, DRYLAND table "
                         "(default table for peanuts)")


class ProductMathTests(unittest.TestCase):
    def test_the_requested_example_line(self):
        # Cotton at 30 ppm nitrate: A&M says 40 lb N/acre. 40 / 0.46 = 86.96,
        # so 87 lb urea/acre; on 2 acres 174 lb, which is 3.48 -> 4 bags of 50 lb.
        cotton = plan("Cotton", 30, 30, 100, acres=2)
        self.assertEqual(lines(cotton)[0], "Urea (46-0-0): 87 lbs/acre · 174 lbs total · 4 bags")
        self.assertEqual(products(cotton)[0]["detail"], "Supplies 40 lbs nitrogen (N) per acre")
        self.assertEqual(cotton["area_text"], "2.00 acres · 50-lb bags")

    def test_each_default_product_meets_its_nutrient_exactly(self):
        corn = plan("Corn", 4, 10, 20, acres=2.5)  # A&M: 170 N, 80 P2O5, 105 K2O
        self.assertEqual(lines(corn), [
            "Urea (46-0-0): 370 lbs/acre · 925 lbs total · 19 bags",            # 170/.46
            "Triple superphosphate (0-46-0): 174 lbs/acre · 435 lbs total · 9 bags",  # 80/.46
            "Potash (0-0-60): 175 lbs/acre · 438 lbs total · 9 bags",           # 105/.60
        ])
        self.assertEqual([i["detail"] for i in products(corn)], [
            "Supplies 170 lbs nitrogen (N) per acre",
            "Supplies 80 lbs phosphate (P2O5) per acre",
            "Supplies 105 lbs potash (K2O) per acre",
        ])
        # Whole pounds, so the field total is the sum of what is shown: 437.5 -> 438.
        self.assertEqual([i["total_lb"] for i in products(corn)], [925, 435, 438])

    def test_half_pound_rates_do_not_drift(self):
        # Cotton at 17 ppm N: A&M says 62.5 lb N. (62.5 / 0.46) * 0.46 lands just
        # off 62.5 in floating point; the plan must still read 62, not 63.
        cotton = plan("Cotton", 17, 30, 100)
        self.assertEqual(cotton["nutrients"]["N"], 62.5)
        self.assertEqual(products(cotton)[0]["detail"], "Supplies 62 lbs nitrogen (N) per acre")
        self.assertEqual(products(cotton)[0]["lb_per_acre"], round(62.5 / 0.46))

    def test_tons_show_alongside_bags_from_one_ton(self):
        corn = plan("Corn", 6, 12, 70, acres=27.14)  # A&M: 165 lb N -> 359 lb urea/acre
        self.assertEqual(lines(corn)[0],
                         "Urea (46-0-0): 359 lbs/acre · 9,743 lbs total · 195 bags (~4.9\u00a0tons)")
        self.assertEqual(lines(plan("Corn", 6, 12, 70, acres=5.6))[0],  # 2,010 lbs
                         "Urea (46-0-0): 359 lbs/acre · 2,010 lbs total · 41 bags (~1.0\u00a0ton)")
        self.assertNotIn("ton", lines(plan("Corn", 6, 12, 70, acres=5))[0])  # 1,795 lbs

    def test_a_rate_that_rounds_to_zero_needs_nothing(self):
        corn = plan("Corn", 4, 49.95, 20, acres=2)  # A&M: 0.1 lb P2O5
        self.assertEqual(corn["nutrients"]["P"], 0.1)
        self.assertIn("No phosphate (P2O5) needed", lines(corn))
        self.assertNotIn("Triple superphosphate", json.dumps(corn))

    def test_one_pound_reads_singular(self):
        corn = plan("Corn", 4, 10, 128.8, acres=1)  # A&M: 0.6 lb K2O -> 1 lb potash
        self.assertEqual(lines(corn)[2], "Potash (0-0-60): 1 lb/acre · 1 lb total · 1 bag")
        self.assertEqual(products(corn)[2]["detail"], "Supplies 1 lb potash (K2O) per acre")

    def test_field_total_is_the_sum_of_the_region_totals_shown(self):
        regions = [("A%d" % i, plan("Cotton", 30, 30, 100, acres=1.5)) for i in (1, 2, 3)]
        # 87 lbs/acre x 1.5 acres = 130.5, shown as 131.
        self.assertEqual(lines(regions[0][1])[0],
                         "Urea (46-0-0): 87 lbs/acre · 131 lbs total · 3 bags")
        self.assertEqual(fert_calc.field_total(regions)["products"][0]["amounts"],
                         "393 lbs · 8 bags")

    def test_zero_ppm_takes_the_tables_first_column(self):
        corn = plan("Corn", 0, 0, 0)
        entry = RAW["row"]["CORN (150 BU/A)"]
        self.assertEqual(corn["nutrients"], {n: entry[n]["0"] for n in "NPK"})  # 180/105/130
        self.assertEqual([i["lb_per_acre"] for i in products(corn)],
                         [round(180 / 0.46), round(105 / 0.46), round(130 / 0.60)])

    def test_very_high_ppm_needs_nothing(self):
        for crop, *_ in LEVELS:
            with self.subTest(crop=crop):
                result = plan(crop, 10000, 10000, 10000, acres=3)
                self.assertEqual(lines(result), ["No nitrogen (N) needed",
                                                 "No phosphate (P2O5) needed",
                                                 "No potash (K2O) needed"])

    def test_a_region_with_no_area_gets_per_acre_amounts_only(self):
        for acres in (None, -2, "lots", float("nan"), float("inf"), 2e6):
            with self.subTest(acres=acres):
                corn = plan("Corn", 4, 10, 20, acres=acres)
                self.assertIsNone(corn["area_acres"])
                self.assertEqual(lines(corn)[0], "Urea (46-0-0): 370 lbs/acre")
                self.assertIsNone(products(corn)[0]["total_lb"])
                self.assertIsNone(products(corn)[0]["bags"])
                self.assertIn("Field area unknown", corn["area_text"])
        # A sample outside the drawn field has a known area of zero.
        outside = plan("Corn", 4, 10, 20, acres=0)
        self.assertEqual(outside["area_acres"], 0.0)
        self.assertEqual(lines(outside)[0], "Urea (46-0-0): 370 lbs/acre")
        self.assertIn("covers none of the drawn field", outside["area_text"])
        self.assertEqual(fert_calc.field_total([("A9", outside)])["excluded"],
                         ["A9: covers none of the drawn field, so it adds nothing."])

    def test_cost_shows_only_with_a_configured_price(self):
        corn = plan("Corn", 4, 10, 20, acres=2.5)
        self.assertNotIn("$", json.dumps(corn))
        priced = json.loads(json.dumps(fert_calc.PRODUCTS))
        priced["products"][0]["price_per_ton"] = 600.0
        corn = plan("Corn", 4, 10, 20, acres=2.5, config=priced)
        # As AgFertCalc prices it: rounded rate x $/ton / 2000.
        self.assertEqual(lines(corn)[0], "Urea (46-0-0): 370 lbs/acre · 925 lbs total · "
                                         "19 bags · $111.00/acre · $277.50 total")
        self.assertNotIn("$", lines(corn)[1])


class AgFertCalcParityTests(unittest.TestCase):
    """The product rule against numbers read off the live AgFertCalc page."""

    def config(self, *grades):
        return [dict(name=g, grade=g, price_per_ton=None,
                     fractions=dict(zip("NPK", (int(x) / 100 for x in g.split("-")))))
                for g in grades]

    def test_default_products(self):
        applied, left = fert_calc._apply_products(
            {"N": 120.0, "P": 60.0, "K": 90.0}, self.config("46-0-0", "0-46-0", "0-0-60"))
        self.assertEqual([round(rate, 1) for _, rate, _, _ in applied], [260.9, 130.4, 150.0])
        self.assertEqual(left, {"N": 0.0, "P": 0.0, "K": 0.0})

    def test_a_blend_never_over_applies_and_later_products_fill_the_rest(self):
        applied, left = fert_calc._apply_products(
            {"N": 120.0, "P": 60.0, "K": 90.0}, self.config("18-46-0", "46-0-0", "0-0-60"))
        self.assertEqual([round(rate, 1) for _, rate, _, _ in applied], [130.4, 209.8, 150.0])
        self.assertEqual(round(applied[0][2]["N"], 1), 23.5)  # DAP's N, taken off urea's
        self.assertEqual(left, {"N": 0.0, "P": 0.0, "K": 0.0})

    def test_a_blend_alone_leaves_the_shortfall(self):
        applied, left = fert_calc._apply_products(
            {"N": 120.0, "P": 60.0, "K": 90.0}, self.config("10-10-10"))
        self.assertEqual(round(applied[0][1], 1), 600.0)
        self.assertEqual({n: round(v, 1) for n, v in left.items()},
                         {"N": 60.0, "P": 0.0, "K": 30.0})


class MissingDataTests(unittest.TestCase):
    def test_crop_without_an_am_table_gets_a_message_not_numbers(self):
        for crop in ("Hemp", "Local heritage millet"):
            with self.subTest(crop=crop):
                result = plan(crop, 4, 10, 20, acres=2)
                self.assertFalse(result["available"])
                self.assertEqual(result["items"], [])
                self.assertIn("No Texas A&M fertilizer table for %s" % crop, result["message"])

    def test_rice_has_no_am_nitrogen_table(self):
        rice = plan("Rice", 4, 10, 20, acres=2)
        self.assertEqual(rice["items"][0]["kind"], "no_rate")
        self.assertEqual(rice["items"][0]["text"], "No Texas A&M nitrogen (N) rate for rice; "
                                                   "see Texas A&M AgriLife's rice production guide.")
        self.assertEqual([i["product"] for i in products(rice)], ["Triple superphosphate", "Potash"])
        # The field total says the urea line leaves rice out, rather than going quiet.
        total = fert_calc.field_total([("A5", rice)])
        self.assertEqual(total["excluded"],
                         ["A5: no Texas A&M nitrogen (N) rate for this crop, so none is counted."])
        self.assertEqual([p["label"] for p in total["products"]],
                         ["Triple superphosphate (0-46-0)", "Potash (0-0-60)"])

    def test_am_application_notes_ride_along(self):
        wheat = plan("Wheat", 4, 10, 20)
        self.assertIn("Texas A&M note on nitrogen (N): Apply 1/3 of recommended nitrogen",
                      wheat["notes"][0])


class WarningTests(unittest.TestCase):
    def test_ph_out_of_range_gets_a_referral_not_a_rate(self):
        cases = [
            (5.2, "Soil pH 5.2 is too low (acidic) for corn (target 5.8–7.0). Get a lime "
                  "rate from the Texas A&M Soil Testing Lab or your county AgriLife agent."),
            (5.7, "Soil pH 5.7 is a little low (acidic) for corn (target 5.8–7.0)."),
            (7.4, "Soil pH 7.4 is a little high (alkaline) for corn (target 5.8–7.0). Get a "
                  "sulfur rate"),
            (8.1, "Soil pH 8.1 is too high (alkaline) for corn"),
        ]
        for ph, start in cases:
            with self.subTest(ph=ph):
                warnings = plan("Corn", 4, 10, 20, ph=ph)["warnings"]
                self.assertEqual(len(warnings), 1)
                self.assertTrue(warnings[0].startswith(start), warnings[0])
        self.assertEqual(plan("Corn", 4, 10, 20, ph=6.5)["warnings"], [])

    def test_salinity_warning(self):
        warnings = plan("Corn", 4, 10, 20, ece=4.2)["warnings"]
        self.assertEqual(warnings, [
            "Salt level is too high for corn: about 4.2 dS/m (estimated saturated-paste "
            "EC), and yield loss starts above 1.7 dS/m. Expect about 70% of a "
            "normal yield. Get a salinity treatment plan from the Texas A&M Soil Testing "
            "Lab or your county AgriLife agent."])

    def test_warnings_never_carry_an_amount(self):
        warnings = plan("Corn", 4, 10, 20, ph=4.8, ece=9.0)["warnings"]
        self.assertEqual(len(warnings), 2)
        for text in warnings:
            for unit in ("tons", "lb", "lbs"):
                self.assertNotIn(" %s" % unit, text)


class ActionItemTests(unittest.TestCase):
    """The report never contradicts itself: the Fertilizer Plan is the only place
    N/P/K amounts appear, and nothing gives a cost or an invented lime/sulfur rate."""

    # A fertilizer amount, not a yield goal such as "5,000 lb/A".
    AMOUNT = re.compile(r"\d[\d,.]*\s*(?:lbs?|tons?)\s*(?:/\s*acre|/A of|N\b|P2O5|K2O|urea|"
                        r"elemental)", re.I)
    SOILS = [dict(ph=5.0, ec=9.0, nitrogen=4, phosphorus=5, potassium=20),
             dict(ph=8.4, ec=0.5, nitrogen=12, phosphorus=200, potassium=60),
             dict(ph=6.5, ec=0.5, nitrogen=30, phosphorus=30, potassium=110)]

    def recs(self, crop, soil):
        return recommender.build_recommendations(soil, target_crop=crop)

    def test_no_item_carries_an_amount_or_a_cost(self):
        for crop in list(T.CROP_DEFAULT_YIELD) + ["Hemp", "Local heritage millet", None]:
            for soil in self.SOILS:
                for r in self.recs(crop, soil):
                    text = " ".join(str(r.get(k) or "")
                                    for k in ("title", "action", "rate", "notes", "timing"))
                    with self.subTest(crop=crop, fix=r["fix_code"]):
                        self.assertIsNone(self.AMOUNT.search(text), text)
                        self.assertNotIn("$", text)
                        self.assertNotIn("cost_estimate", r)

    def test_items_without_an_am_rate_say_what_the_plan_says(self):
        soil = self.SOILS[0]
        hemp = plan("Hemp", soil["nitrogen"], soil["phosphorus"], soil["potassium"])
        for code in ("nitrogen_deficient", "phosphorus_deficient", "potassium_deficient"):
            self.assertEqual(next(r for r in self.recs("Hemp", soil) if r["fix_code"] == code)
                             ["rate"], hemp["message"])
        rice = plan("Rice", soil["nitrogen"], soil["phosphorus"], soil["potassium"])
        n_item = next(r for r in self.recs("Rice", soil) if r["fix_code"] == "nitrogen_deficient")
        self.assertEqual(n_item["rate"], rice["items"][0]["text"])

    def test_lime_and_sulfur_get_the_plans_referral(self):
        for soil, fix, what in ((self.SOILS[0], "ph_acidic", "lime"),
                                (self.SOILS[1], "ph_alkaline", "sulfur")):
            item = next(r for r in self.recs("Corn", soil) if r["fix_code"] == fix)
            referral = ("Get a %s rate from the Texas A&M Soil Testing Lab or your county "
                        "AgriLife agent." % what)
            self.assertEqual(item["rate"], referral)
            warnings = plan("Corn", 4, 10, 20, ph=soil["ph"])["warnings"]
            self.assertTrue(warnings[0].endswith(referral), warnings[0])

    def test_nitrogen_timing_fits_the_crop(self):
        soil = self.SOILS[0]
        n_item = lambda crop: next(r for r in self.recs(crop, soil)  # noqa: E731
                                   if r["fix_code"] in ("nitrogen_deficient", "nitrogen_routine"))
        self.assertIn("split-apply for corn", n_item("Corn")["timing"])
        for crop in ("Cotton", "Tomatoes", "Rice", "Hemp"):
            with self.subTest(crop=crop):
                self.assertNotIn("corn", n_item(crop)["timing"] + n_item(crop)["action"])
        self.assertEqual(n_item("Wheat")["timing"], "Apply 1/3 of recommended nitrogen at "
                                                    "preplant and remainder prior to jointing.")
        self.assertNotIn("lbs", n_item("Bermudagrass")["timing"])  # its A&M note is an amount

    def test_crop_names_agree_with_their_verb(self):
        bad = dict(ph=4.5, ec=9.0, nitrogen=4, phosphorus=5, potassium=20)
        for crop, start in (("Tomatoes", "Tomatoes are not viable"),
                            ("Corn", "Corn is not viable"),
                            ("Bermudagrass", "Bermudagrass is not viable")):
            with self.subTest(crop=crop):
                self.assertTrue(recommender.assess_target_crop(bad, crop)["note"]
                                .startswith(start))

    def test_a_rate_that_rounds_to_zero_has_no_item(self):
        soil = dict(ph=6.5, ec=0.5, nitrogen=30, phosphorus=49.95, potassium=150)  # 0.1 lb P2O5
        self.assertFalse(any(r["fix_code"] == "phosphorus_deficient"
                             for r in self.recs("Corn", soil)))
        self.assertIn("No phosphate (P2O5) needed", lines(plan("Corn", 30, 49.95, 150)))


class FieldTotalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ff-fert-tests-")
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
            "farm_name": "Fertilizer field", "date": "2026-10-23", "sections": sections})

    @staticmethod
    def section(label, crop, n, p, k, area=None, ph=6.5, ec=0.3):
        sec = dict(section_label=label, crop_type=crop, latitude=29.53, longitude=-95.77,
                   ph=ph, ec=ec, nitrogen=n, phosphorus=p, potassium=k)
        if area is not None:
            sec["area_acres"] = area
        return sec

    def test_every_region_gets_a_plan_and_the_field_gets_totals(self):
        response = self.submit([
            self.section("A1", "Corn", 4, 10, 20, area=2.5),
            self.section("A2", "Cotton", 30, 30, 100, area=2),
            self.section("A3", "Tomatoes", 20, 30, 100, area=1.25),
            self.section("A4", "Hemp", 4, 10, 20, area=3),
            self.section("A5", "Corn", 4, 10, 20),  # no area
        ])
        self.assertEqual(response.status_code, 200, response.json)
        analysis = response.json
        self.assertEqual(self.client.get("/api/results/%s" % analysis["farm_id"]).json, analysis)
        plans = {r["section_label"]: r["fertilizer_plan"] for r in analysis["results"]}

        self.assertEqual(lines(plans["A1"])[0], "Urea (46-0-0): 370 lbs/acre · 925 lbs total · 19 bags")
        self.assertEqual(lines(plans["A2"])[0], "Urea (46-0-0): 87 lbs/acre · 174 lbs total · 4 bags")
        self.assertEqual(lines(plans["A3"]), [
            "Urea (46-0-0): 43 lbs/acre · 54 lbs total · 2 bags",                     # 20 N
            "Triple superphosphate (0-46-0): 109 lbs/acre · 136 lbs total · 3 bags",  # 50 P2O5
            "Potash (0-0-60): 50 lbs/acre · 63 lbs total · 2 bags",                   # 30 K2O
        ])
        self.assertFalse(plans["A4"]["available"])
        self.assertEqual(lines(plans["A5"])[0], "Urea (46-0-0): 370 lbs/acre")
        # Each region's plan agrees with its own A&M readout.
        for r in analysis["results"]:
            if r["crop_assessment"]:
                self.assertEqual(r["fertilizer_plan"]["nutrients"],
                                 {n: r["crop_assessment"][n]["add"] for n in "NPK"})

        total = analysis["fertilizer_total"]
        # Every sample counts toward the field: A4's 3 acres included, A5 has none.
        self.assertEqual((total["n_samples"], total["n_counted"], total["field_acres"],
                          total["acres"]), (5, 3, 8.75, 5.75))
        self.assertEqual(total["summary"],
                         "5 samples, 2 not counted. Field: 8.75 acres; these totals cover "
                         "5.75 acres. Each sample covers the part of the field closest to "
                         "it; bags are 50 lb.")
        # The region totals shown: urea 925 + 174 + 54; TSP 435 + 174 + 136;
        # potash 438 + 66 + 63.
        self.assertEqual([(p["label"], p["total_lb"], p["bags"]) for p in total["products"]], [
            ("Urea (46-0-0)", 1153, 24),
            ("Triple superphosphate (0-46-0)", 745, 15),
            ("Potash (0-0-60)", 567, 12),
        ])
        for p in total["products"]:
            self.assertEqual(p["total_lb"], sum(i["total_lb"] for plan_ in plans.values()
                                                for i in plan_["items"]
                                                if i.get("label") == p["label"] and i["total_lb"]))
        self.assertEqual(total["products"][0]["amounts"], "1,153 lbs · 24 bags")
        self.assertEqual(total["excluded"], [
            "A4: no Texas A&M table for this crop, so no amounts.",
            "A5: no field area, so it is shown per acre only.",
        ])
        # Bags come from the field total, not the sum of per-region bags.
        for p in total["products"]:
            self.assertEqual(p["bags"], math.ceil(p["total_lb"] / 50))

        # The PDF says the same: per-section plans plus a Whole Field Total page.
        styles = pdf_report._styles()
        a1 = pdf_text(pdf_report._section_flowables(analysis["results"][0], "F", "D", styles))
        self.assertIn("Fertilizer Plan", a1)
        self.assertIn("Urea (46-0-0): 370 lbs/acre · 925 lbs total · 19 bags", a1)
        self.assertIn("Supplies 170 lbs nitrogen (N) per acre", a1)
        self.assertIn("Source: Texas A&M AgriLife CORN (150 BU/A) table", a1)
        # The plan is the only place with amounts; no costs anywhere.
        self.assertIn("Amount: see Fertilizer Plan below.", a1)
        for gone in ("$", "Cost", "ballpark", "lb N/acre", "lb urea"):
            self.assertNotIn(gone, a1)
        page = table_text(fert_pdf.field_total_flowables(analysis, styles))
        for text in ("Whole Field Total", "Urea (46-0-0)", "1,153", "24",
                     "A4: no Texas A&M table for this crop"):
            self.assertIn(text, page)
        pdf = pdf_report.build_combined_pdf(analysis)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_area_must_be_a_non_negative_number(self):
        for bad in (-1, "lots", [2], 1e306, float("inf")):
            with self.subTest(area=bad):
                response = self.submit([self.section("A1", "Corn", 4, 10, 20, area=bad)])
                self.assertEqual(response.status_code, 400)
                self.assertIn("area_acres", response.json["error"])

    def test_readings_must_be_finite(self):
        # NaN would otherwise reach the plan's text ("about nan dS/m").
        response = self.submit([self.section("A1", "Corn", 4, 10, 20, ec=float("nan"))])
        self.assertEqual(response.status_code, 400)
        self.assertIn("non-finite 'ec'", response.json["error"])

    def test_old_results_without_a_plan_still_render(self):
        analysis = self.submit([self.section("A1", "Corn", 4, 10, 20, area=2)]).json
        old = dict(analysis)
        old.pop("fertilizer_total")
        old["results"] = [{k: v for k, v in r.items() if k != "fertilizer_plan"}
                          for r in analysis["results"]]
        styles = pdf_report._styles()
        old_text = pdf_text(pdf_report._section_flowables(old["results"][0], "F", "D", styles))
        self.assertNotIn("50-lb bags", old_text)  # no plan block
        self.assertNotIn("Supplies", old_text)
        self.assertEqual(fert_pdf.field_total_flowables(old, styles), [])
        self.assertTrue(pdf_report.build_combined_pdf(old).startswith(b"%PDF"))
        self.assertTrue(pdf_report.build_section_pdf(old["results"][0], "F", "D")
                        .startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
