"""Live scoring for the data-entry map: /api/score validation and output.

The map colours a point while data is entered and must not change colour when
the field is submitted, so these tests also pin `score_sections` and
`run_analysis` to the same prediction. They stub `model_runner._predict`, which
keeps the ML stack (pandas/xgboost) out of the test run.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app
from backend import model_runner


MEASUREMENTS = dict(ph=6.5, ec=1.0, nitrogen=10, phosphorus=25, potassium=180)


def predictions(sections, _prefix):
    # Score varies per row so the tests can check results keep their order.
    return [dict(ml_health_score=40.04 + 10 * i, ml_category_from_score="Fair",
                 ml_crop_suitable=[], ml_health_category="Fair",
                 ml_crop_confidence={}) for i in range(len(sections))]


class ScoreEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True
            session["admin_id"] = 1
        patch = mock.patch.object(model_runner, "_predict", side_effect=predictions)
        patch.start()
        self.addCleanup(patch.stop)

    def score(self, sections):
        return self.client.post("/api/score", json={"sections": sections})

    def test_returns_one_rounded_score_per_section_in_order(self):
        response = self.score([dict(section_label="A1", **MEASUREMENTS),
                               dict(section_label="A2", **MEASUREMENTS)])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["scores"], [
            {"section_label": "A1", "health_score": 40.0, "health_category": "Fair"},
            {"section_label": "A2", "health_score": 50.0, "health_category": "Fair"},
        ])

    def test_crop_and_coordinates_are_not_required(self):
        response = self.score([dict(section_label="A1", ph="6.5", ec="1", nitrogen="10",
                                    phosphorus="25", potassium="180")])
        self.assertEqual(response.status_code, 200, response.json)

    def test_rejects_missing_or_non_finite_measurements(self):
        missing = dict(MEASUREMENTS)
        del missing["ec"]
        self.assertEqual(self.score([dict(section_label="A1", **missing)]).status_code, 400)
        bad = dict(MEASUREMENTS, ph="nan")
        self.assertEqual(self.score([dict(section_label="A1", **bad)]).status_code, 400)
        self.assertEqual(self.score([]).status_code, 400)

    def test_rejects_duplicate_labels_and_oversized_requests(self):
        duplicate = [dict(section_label="A1", **MEASUREMENTS)] * 2
        self.assertEqual(self.score(duplicate).status_code, 400)
        many = [dict(section_label="S%d" % i, **MEASUREMENTS) for i in range(501)]
        self.assertEqual(self.score(many).status_code, 400)

    def test_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.post("/api/score", json={
            "sections": [dict(section_label="A1", **MEASUREMENTS)]})
        self.assertEqual(response.status_code, 401)

    def test_live_score_equals_the_score_the_submitted_analysis_reports(self):
        """A point must keep its map colour from data entry through to results."""
        sections = [dict(section_label="A1", **MEASUREMENTS),
                    dict(section_label="A2", ph=5.0, ec=4.0, nitrogen=5,
                         phosphorus=5, potassium=40)]
        # A mapped crop (crop-specific headline) and a custom one (ML headline).
        for crop in ("Corn", "Local heritage millet"):
            with self.subTest(crop=crop):
                live = model_runner.score_sections([dict(s, crop_type=crop) for s in sections])
                analysis = model_runner.run_analysis(
                    "Field", "2026-09-17",
                    [dict(s, latitude=29.53, longitude=-95.77, crop_type=crop) for s in sections])
                self.assertEqual([s["health_score"] for s in live],
                                 [r["health_score"] for r in analysis["results"]])
                self.assertEqual([s["health_category"] for s in live],
                                 [r["health_category"] for r in analysis["results"]])

    def test_a_mapped_crop_is_scored_on_its_own_thresholds(self):
        plain = self.score([dict(section_label="A1", **MEASUREMENTS)]).json["scores"][0]
        blank = self.score([dict(section_label="A1", crop_type="  ", **MEASUREMENTS)]).json["scores"][0]
        corn = self.score([dict(section_label="A1", crop_type="Corn", **MEASUREMENTS)]).json["scores"][0]
        self.assertEqual(plain["health_score"], 40.0)   # no crop: the ML score, as before
        self.assertEqual(blank, plain)
        self.assertNotEqual(corn["health_score"], 40.0)
        bad = self.score([dict(section_label="A1", crop_type=123, **MEASUREMENTS)])
        self.assertEqual(bad.status_code, 400)
        self.assertIn("crop_type", bad.json["error"])


if __name__ == "__main__":
    unittest.main()
