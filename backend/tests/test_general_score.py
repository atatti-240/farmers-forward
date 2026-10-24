"""Each section's general soil health comes from its own readings (real model).

The other backend tests stub the ML model with one score for every section. A
sample report built that way showed "General soil health: 62/100" on every
section, so these run the trained model itself.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app  # noqa: F401  (imports the backend once, test DB)
from backend import model_runner

READINGS = {  # three very different soils
    "A1": dict(ph=5.2, ec=0.4, nitrogen=6, phosphorus=12, potassium=70),
    "A2": dict(ph=6.6, ec=0.5, nitrogen=22, phosphorus=65, potassium=190),
    "A3": dict(ph=7.6, ec=1.4, nitrogen=10, phosphorus=20, potassium=95),
}


def sections(crop, labels=READINGS):
    return [dict(section_label=label, crop_type=crop, latitude=29.53, longitude=-95.77,
                 **READINGS[label]) for label in labels]


@unittest.skipUnless(os.path.exists(model_runner.MODEL_PATH), "trained model bundle missing")
class GeneralScoreTests(unittest.TestCase):
    def analyze(self, crop, labels=READINGS):
        return model_runner.run_analysis("Field", "2026-10-23", sections(crop, labels),
                                         farm_id=1)["results"]

    def test_different_readings_give_different_general_scores(self):
        results = self.analyze("Corn")
        scores = [r["general_health_score"] for r in results]
        self.assertEqual(len(set(scores)), len(scores), scores)
        # Each section scores as it would alone: nothing carries across sections.
        for r in results:
            alone = self.analyze("Corn", [r["section_label"]])[0]
            self.assertEqual(r["general_health_score"], alone["general_health_score"])

    def test_headline_scores_and_map_colours_are_per_section(self):
        # Without an A&M table the headline (and the map colour) is the ML score.
        results = self.analyze("Hemp")
        self.assertEqual(len({r["health_score"] for r in results}), len(results))
        for r in results:
            self.assertEqual(r["health_score"], r["general_health_score"])
            self.assertEqual(r["color"], model_runner.score_color(r["health_score"]))


if __name__ == "__main__":
    unittest.main()
