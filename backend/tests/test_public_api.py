"""The public, no-login API (backend.public_api) and PUBLIC_ONLY mode.

Public submissions must reach only public_submissions, nothing stored may be
read back except a PDF through a live token, and a PUBLIC_ONLY process must not
contain a single admin route, table or seeded admin. The ML stack is stubbed
(model_runner._predict), as in test_score.py.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import REPO_ROOT, app as admin_app, config, database  # noqa: E402
from backend import model_runner, public_api, public_app  # noqa: E402

READING = dict(ph=6.5, ec=0.8, nitrogen=12, phosphorus=25, potassium=180)


def predictions(sections, _prefix):
    return [dict(ml_health_score=55.0 + i, ml_category_from_score="Fair",
                 ml_crop_suitable=[], ml_health_category="Fair",
                 ml_crop_confidence={}) for i in range(len(sections))]


def section(label="A1", **overrides):
    row = dict(section_label=label, latitude=29.53, longitude=-95.77,
               crop_type="Corn", **READING)
    row.update(overrides)
    return row


def assessment(**overrides):
    body = {
        "farm_name": "Test field",
        "date": "2026-10-23",
        "sections": [section("A1"), section("A2", ph=5.4)],
        "field": {"polygon": [[29.5, -95.8], [29.5, -95.7], [29.6, -95.7]],
                  "grid": {"rows": 1, "cols": 2},
                  "points": [{"label": "A1", "lat": 29.53, "lng": -95.77},
                             {"label": "A2", "lat": 29.54, "lng": -95.76}]},
    }
    body.update(overrides)
    return body


def counts(*tables):
    return {t: database.query_one("SELECT COUNT(*) AS n FROM %s" % t)["n"] for t in tables}


class PublicApiCase(unittest.TestCase):
    """A public app on the SAME database as the admin app, as on Render when
    the two share a Supabase project."""

    @classmethod
    def setUpClass(cls):
        cls.app = public_app.create_app()

    def setUp(self):
        public_api.limiter.reset()
        self.client = self.app.test_client()
        patch = mock.patch.object(model_runner, "_predict", side_effect=predictions)
        patch.start()
        self.addCleanup(patch.stop)

    def submit(self, body=None):
        return self.client.post("/api/public/assessments", json=body or assessment())


class AnonymousFlowTests(PublicApiCase):
    def test_score_works_without_login(self):
        response = self.client.post("/api/public/score", json={
            "sections": [dict(section_label="A1", **READING)]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["scores"][0]["section_label"], "A1")

    def test_assessment_returns_full_results_and_a_pdf_token(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.json)
        body = response.json
        analysis = body["analysis"]
        self.assertEqual([r["section_label"] for r in analysis["results"]], ["A1", "A2"])
        self.assertIn("field_summary", analysis)
        self.assertNotIn("farm_id", analysis)
        self.assertEqual(analysis["source"], "public")
        self.assertGreaterEqual(len(body["pdf_token"]), 40)
        expires = datetime.fromisoformat(body["pdf_token_expires_at"])
        self.assertAlmostEqual((expires - datetime.now(timezone.utc)).total_seconds(),
                               3600, delta=60)

    def test_pdf_downloads_with_the_token(self):
        token = self.submit().json["pdf_token"]
        response = self.client.post("/api/public/assessments/report",
                                    json={"token": token})
        self.assertEqual(response.status_code, 200, response.data[:200])
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF"))
        self.assertIn("test-field", response.headers["Content-Disposition"])

    def test_a_bad_map_image_drops_the_map_not_the_download(self):
        token = self.submit().json["pdf_token"]
        response = self.client.post("/api/public/assessments/report",
                                    json={"token": token, "map_image": "data:image/png;base64,!!"})
        self.assertEqual(response.status_code, 200)

    def test_health_starts_the_warmup_once(self):
        with mock.patch.object(public_api, "_warm_model") as warm, \
                mock.patch.dict(public_api._warm_state, {"status": "cold"}):
            first = self.client.get("/api/public/health")
            second = self.client.get("/api/public/health")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json, {"ok": True, "model": "warming"})
        self.assertEqual(second.json["model"], "warming")
        self.assertLessEqual(warm.call_count, 1)

    def test_soil_info_is_unavailable_until_the_feature_lands(self):
        with mock.patch.object(public_api, "_soil_info", return_value=None):
            response = self.client.get("/api/public/soil-info?lat=29.5&lon=-95.7")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["status"], "unavailable")

    def test_cors_allows_the_public_site_without_credentials(self):
        response = self.client.post("/api/public/score", headers={"Origin": "http://localhost:5173"},
                                    json={"sections": [dict(section_label="A1", **READING)]})
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"),
                         "http://localhost:5173")
        self.assertIsNone(response.headers.get("Access-Control-Allow-Credentials"))
        other = self.client.post("/api/public/score", headers={"Origin": "https://evil.test"},
                                 json={"sections": [dict(section_label="A1", **READING)]})
        self.assertIsNone(other.headers.get("Access-Control-Allow-Origin"))


class StorageIsolationTests(PublicApiCase):
    def test_writes_only_public_submissions(self):
        before = counts("farms", "samples", "results", "public_submissions")
        self.assertEqual(self.submit().status_code, 200)
        after = counts("farms", "samples", "results", "public_submissions")
        for table in ("farms", "samples", "results"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(after["public_submissions"], before["public_submissions"] + 1)

    def test_stores_inputs_and_results_but_only_a_token_hash(self):
        body = self.submit().json
        row = database.query_one(
            "SELECT * FROM public_submissions ORDER BY created_at DESC LIMIT 1")
        self.assertEqual(row["crop_type"], "Corn")
        inputs = json.loads(row["inputs"])
        self.assertEqual(len(inputs["sections"]), 2)
        self.assertEqual(inputs["field"]["grid"], {"rows": 1, "cols": 2})
        self.assertEqual(json.loads(row["results"])["results"][1]["section_label"], "A2")
        self.assertNotIn(body["pdf_token"], json.dumps(dict(row)))
        self.assertEqual(row["pdf_token_hash"], public_api._token_hash(body["pdf_token"]))
        for column in dict(row):
            self.assertNotIn("ip", column.lower().split("_"))

    def test_storage_setup_is_idempotent(self):
        public_api.ensure_storage()
        public_api.ensure_storage()

    def test_no_route_reads_a_submission_back(self):
        token = self.submit().json["pdf_token"]
        rules = {str(r) for r in self.app.url_map.iter_rules()}
        self.assertEqual(rules, {
            "/api/public/health", "/api/public/score", "/api/public/assessments",
            "/api/public/assessments/report", "/api/public/soil-info"})
        for path in ("/api/public/assessments", "/api/public/assessments/" + token,
                     "/api/public/assessments/report?token=" + token):
            self.assertIn(self.client.get(path).status_code, (404, 405), path)


class PdfTokenTests(PublicApiCase):
    def report(self, token):
        return self.client.post("/api/public/assessments/report", json={"token": token})

    def test_wrong_token_is_rejected(self):
        self.submit()
        response = self.report("x" * 43)
        self.assertEqual(response.status_code, 404)
        self.assertIn("expired", response.json["error"])

    def test_malformed_token_is_rejected(self):
        for token in (None, 12, "", "short", "y" * 101):
            self.assertEqual(self.report(token).status_code, 400, token)

    def test_expired_token_is_rejected(self):
        token = self.submit().json["pdf_token"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        database.execute("UPDATE public_submissions SET pdf_token_expires_at = ? "
                         "WHERE pdf_token_hash = ?", (past, public_api._token_hash(token)))
        self.assertEqual(self.report(token).status_code, 404)

    def test_token_still_works_shortly_before_expiry(self):
        token = self.submit().json["pdf_token"]
        soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        database.execute("UPDATE public_submissions SET pdf_token_expires_at = ? "
                         "WHERE pdf_token_hash = ?", (soon, public_api._token_hash(token)))
        self.assertEqual(self.report(token).status_code, 200)


class ValidationTests(PublicApiCase):
    POSTS = ("/api/public/score", "/api/public/assessments", "/api/public/assessments/report")

    def test_non_json_bodies_are_a_friendly_400(self):
        for path in self.POSTS:
            for kwargs in ({"data": "not json", "content_type": "application/json"},
                           {"data": "a=1", "content_type": "application/x-www-form-urlencoded"},
                           {"json": [1, 2]}, {"json": "text"}, {"json": None}, {}):
                public_api.limiter.reset()
                response = self.client.post(path, **kwargs)
                self.assertEqual(response.status_code, 400, (path, kwargs))
                self.assertIn("JSON object", response.json["error"])

    def test_out_of_range_readings_are_rejected(self):
        for key, bad in (("ph", 14.5), ("ph", -1), ("ec", 31), ("nitrogen", 2001),
                         ("phosphorus", -0.1), ("potassium", 10001), ("ph", "nan"),
                         ("ph", "inf"), ("ph", True), ("ph", None), ("ph", "six")):
            public_api.limiter.reset()
            response = self.submit(assessment(sections=[section(**{key: bad})]))
            self.assertEqual(response.status_code, 400, (key, bad))
            self.assertIn("error", response.json)
            score = self.client.post("/api/public/score", json={
                "sections": [dict(dict(section_label="A1", **READING), **{key: bad})]})
            self.assertEqual(score.status_code, 400, (key, bad))

    def submit(self, body=None):
        public_api.limiter.reset()  # many rejected requests in one test
        return super().submit(body)

    def test_limits_on_sections_and_text(self):
        many = [section("P%d" % i) for i in range(151)]
        self.assertEqual(self.submit(assessment(sections=many)).status_code, 400)
        self.assertEqual(self.submit(assessment(sections=[])).status_code, 400)
        self.assertEqual(self.submit(assessment(farm_name="x" * 101)).status_code, 400)
        self.assertEqual(self.submit(assessment(
            sections=[section(crop_type="c" * 101)])).status_code, 400)
        self.assertEqual(self.submit(assessment(
            sections=[section(crop_type="Corn\x00")])).status_code, 400)
        self.assertEqual(self.submit(assessment(
            sections=[section("A1"), section("A1")])).status_code, 400)
        self.assertEqual(self.submit(assessment(
            sections=[section(crop_type="")])).status_code, 400)

    def test_bad_coordinates_dates_and_fields_are_rejected(self):
        for body in (assessment(sections=[section(latitude=91)]),
                     assessment(sections=[section(longitude="east")]),
                     assessment(date="23/10/2026"),
                     assessment(date="2026-02-30"),
                     assessment(field={"polygon": [[0, 0, 0]]}),
                     assessment(field={"grid": {"rows": 0, "cols": 2}}),
                     assessment(field="square"),
                     assessment(soil_point={"lat": 100, "lon": 0})):
            self.assertEqual(self.submit(body).status_code, 400, body)

    def test_optional_fields_default_sensibly(self):
        body = assessment()
        del body["farm_name"], body["date"], body["field"]
        response = self.submit(body)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["analysis"]["farm_name"], "My field")


class RateLimitTests(PublicApiCase):
    def test_score_is_limited_per_client(self):
        body = {"sections": [dict(section_label="A1", **READING)]}
        codes = [self.client.post("/api/public/score", json=body).status_code
                 for _ in range(61)]
        self.assertEqual(codes[:60], [200] * 60)
        self.assertEqual(codes[60], 429)

    def test_assessments_are_limited_and_say_when_to_retry(self):
        codes = [self.submit().status_code for _ in range(11)]
        self.assertEqual(codes[:10], [200] * 10)
        last = self.submit()
        self.assertEqual(last.status_code, 429)
        self.assertGreater(int(last.headers["Retry-After"]), 0)
        self.assertIn("retry_after", last.json)

    def test_limits_are_per_client(self):
        for _ in range(10):
            self.submit()
        self.assertEqual(self.submit().status_code, 429)
        other = self.client.post("/api/public/assessments", json=assessment(),
                                 headers={"CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(other.status_code, 200)

    def test_pdf_is_limited(self):
        token = self.submit().json["pdf_token"]
        codes = [self.client.post("/api/public/assessments/report",
                                  json={"token": token}).status_code for _ in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])

    def test_health_and_soil_info_are_limited(self):
        with mock.patch.object(public_api, "start_warmup", return_value="ready"):
            codes = [self.client.get("/api/public/health").status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)
        with mock.patch.object(public_api, "_soil_info", return_value=None):
            codes = [self.client.get("/api/public/soil-info").status_code for _ in range(21)]
        self.assertEqual(codes[-1], 429)


class PublicOnlyModeTests(unittest.TestCase):
    """A PUBLIC_ONLY process, on its own empty database."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="ff-public-only-"), "public.db")
        patch = mock.patch.object(config, "DATABASE_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)
        self.app = public_app.create_app()
        self.client = self.app.test_client()

    def test_only_public_routes_exist(self):
        for rule in self.app.url_map.iter_rules():
            self.assertTrue(str(rule).startswith("/api/public/"), str(rule))
        admin_rules = {str(r) for r in admin_app.url_map.iter_rules()}
        self.assertTrue(admin_rules, "the admin app should have routes to compare")
        for method, path in (("POST", "/api/auth/verify-code"), ("POST", "/api/auth/login"),
                             ("POST", "/api/auth/otp"), ("POST", "/api/auth/logout"),
                             ("GET", "/api/auth/me"), ("POST", "/api/submit"),
                             ("POST", "/api/score"), ("GET", "/api/results/1"),
                             ("GET", "/api/history"), ("POST", "/api/contact"),
                             ("GET", "/api/report/1/all"), ("POST", "/api/report/1/all"),
                             ("GET", "/api/report/1/section/A1"), ("GET", "/api/soil-info"),
                             ("GET", "/api/admins"), ("GET", "/"), ("GET", "/index.html")):
            response = self.client.open(path, method=method, json={})
            self.assertEqual(response.status_code, 404, (method, path))

    def test_only_the_public_table_is_created(self):
        conn = sqlite3.connect(self.path)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
        self.assertEqual(tables, {"public_submissions"})

    def run_python(self, code, **env):
        environment = dict(os.environ, PYTHONPATH=REPO_ROOT, **env)
        environment.pop("DATABASE_URL", None)
        return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=environment,
                              capture_output=True, text=True, timeout=120)

    def test_wsgi_serves_the_public_app_and_never_imports_admin_code(self):
        code = (
            "import sys, sqlite3\n"
            "from backend import config\n"
            "config.DATABASE_PATH = %r\n"
            "from backend.wsgi import app\n"
            "rules = sorted(str(r) for r in app.url_map.iter_rules())\n"
            "assert all(r.startswith('/api/public/') for r in rules), rules\n"
            "assert 'backend.app' not in sys.modules\n"
            "assert 'backend.auth' not in sys.modules\n"
            "tables = {r[0] for r in sqlite3.connect(%r).execute(\n"
            "    \"SELECT name FROM sqlite_master WHERE type='table'\")}\n"
            "assert tables == {'public_submissions'}, tables\n"
            "print('ok')\n" % (self.path + ".wsgi", self.path + ".wsgi"))
        result = self.run_python(code, PUBLIC_ONLY="1")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("ok", result.stdout)

    def test_admin_app_refuses_to_boot_in_public_only_mode(self):
        code = ("from backend import config\n"
                "config.DATABASE_PATH = %r\n"
                "import backend.app\n" % (self.path + ".admin"))
        result = self.run_python(code, PUBLIC_ONLY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PUBLIC_ONLY is set", result.stderr)
        self.assertFalse(os.path.exists(self.path + ".admin"),
                         "no admin database may be created")

    def test_flag_off_wsgi_is_the_admin_app(self):
        code = ("from backend import config\n"
                "config.DATABASE_PATH = %r\n"
                "from backend.wsgi import app\n"
                "from backend.app import app as admin\n"
                "assert app is admin\n"
                "assert not any(str(r).startswith('/api/public') for r in app.url_map.iter_rules())\n"
                "print('ok')\n" % (self.path + ".off"))
        result = self.run_python(code, PUBLIC_ONLY="")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
