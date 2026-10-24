"""The field map page on the combined PDF report.

The map is rasterised in the browser (the Render free tier cannot run a
headless browser) and POSTed to /api/report/<id>/all, so these tests cover the
POST path, the GET path that still works without a map, and the rule that a
browser-supplied image can never break the download: anything unreadable costs
the report its map page and nothing else.
"""
import base64
import io
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, database, sign_in
from backend import pdf_report

from PIL import Image


def png_data_url(width=320, height=140, color="#2E9E5B"):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def jpeg_data_url(width=320, height=140, color="#E8302A"):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


ANALYSIS = {
    "farm_name": "Map Test Field",
    "date": "2026-09-22",
    "field_summary": {
        "mean_health_score": 71, "health_category": "Good",
        "min_health_score": 52, "max_health_score": 90, "n_sections": 2,
        "dominant_problem_label": "Acidic soil (low pH)",
    },
    "results": [
        {"section_label": "A1", "health_score": 52, "health_category": "Fair",
         "latitude": 29.53, "longitude": -95.77, "crop_type": "Corn",
         "inputs": {"ph": 5.2, "ec": 1.4, "nitrogen": 20,
                    "phosphorus": 15, "potassium": 120}},
        {"section_label": "A2", "health_score": 90, "health_category": "Excellent",
         "latitude": 29.54, "longitude": -95.76, "crop_type": "Corn",
         "inputs": {"ph": 6.6, "ec": 0.8, "nitrogen": 44,
                    "phosphorus": 33, "potassium": 240}},
    ],
}


def page_count(pdf_bytes):
    # /Type /Page, but not the /Type /Pages node that lists them.
    return len(re.findall(rb"/Type\s*/Page(?![s])", pdf_bytes))


class BuildCombinedPdfMapTests(unittest.TestCase):
    """pdf_report.build_combined_pdf's map_image handling."""

    def test_map_image_adds_exactly_one_page(self):
        without = pdf_report.build_combined_pdf(ANALYSIS)
        with_map = pdf_report.build_combined_pdf(ANALYSIS, map_image=png_data_url())
        self.assertEqual(page_count(with_map), page_count(without) + 1)
        self.assertGreater(len(with_map), len(without))

    def test_png_and_jpeg_are_both_embedded(self):
        for label, url in (("png", png_data_url()), ("jpeg", jpeg_data_url())):
            with self.subTest(encoding=label):
                pdf = pdf_report.build_combined_pdf(ANALYSIS, map_image=url)
                self.assertIn(b"/Subtype /Image", pdf)

    def test_unusable_images_fall_back_to_a_mapless_report(self):
        baseline = pdf_report.build_combined_pdf(ANALYSIS)
        unusable = [
            None,                             # not sent at all
            "",                               # empty string
            "not-a-data-url",                 # wrong shape
            "data:image/png;base64,",         # no payload
            "data:image/png;base64,%%%",      # not valid base64
            "data:image/png;base64," + base64.b64encode(b"nope").decode(),  # not an image
            123,                              # not even a string
            {"src": "x"},
        ]
        for value in unusable:
            with self.subTest(map_image=value):
                pdf = pdf_report.build_combined_pdf(ANALYSIS, map_image=value)
                self.assertEqual(page_count(pdf), page_count(baseline))

    def test_a_tall_map_is_capped_rather_than_overflowing(self):
        # A very tall capture must still fit on its one page.
        tall = pdf_report.build_combined_pdf(
            ANALYSIS, map_image=png_data_url(width=300, height=3000))
        self.assertEqual(page_count(tall), page_count(
            pdf_report.build_combined_pdf(ANALYSIS, map_image=png_data_url())))


class ReportAllRouteTests(unittest.TestCase):
    """The /api/report/<id>/all route, GET and POST."""

    def setUp(self):
        self.client = app.test_client()
        sign_in(self.client)
        self.farm_id = database.execute(
            "INSERT INTO farms (farm_name, date, n_sections, mean_health_score,"
            " result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Map Test Field", "2026-09-22", len(ANALYSIS["results"]), 71,
             json.dumps(ANALYSIS), "2026-09-22T00:00:00"))

    def test_post_with_a_map_returns_a_bigger_pdf_than_get(self):
        plain = self.client.get("/api/report/%d/all" % self.farm_id)
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(plain.mimetype, "application/pdf")

        withmap = self.client.post("/api/report/%d/all" % self.farm_id,
                                   json={"map_image": png_data_url()})
        self.assertEqual(withmap.status_code, 200)
        self.assertEqual(withmap.mimetype, "application/pdf")
        self.assertEqual(page_count(withmap.data), page_count(plain.data) + 1)

    def test_post_without_a_map_matches_get(self):
        withmap = self.client.post("/api/report/%d/all" % self.farm_id, json={})
        plain = self.client.get("/api/report/%d/all" % self.farm_id)
        self.assertEqual(withmap.status_code, 200)
        self.assertEqual(page_count(withmap.data), page_count(plain.data))

    def test_post_with_a_junk_map_still_downloads(self):
        response = self.client.post("/api/report/%d/all" % self.farm_id,
                                    json={"map_image": "javascript:alert(1)"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")

    def test_unknown_farm_is_404_on_both_methods(self):
        self.assertEqual(self.client.get("/api/report/999999/all").status_code, 404)
        self.assertEqual(
            self.client.post("/api/report/999999/all", json={}).status_code, 404)

    def test_report_requires_login(self):
        client = app.test_client()
        self.assertEqual(
            client.post("/api/report/%d/all" % self.farm_id, json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
