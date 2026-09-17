"""CORS regression checks.

A rejected cross-origin request is invisible from the server side: Flask still
answers 200, the browser just drops the response because
Access-Control-Allow-Origin is missing. That looks like a frontend bug, so the
allow-list is asserted here instead of being rediscovered each time a Render
service is renamed.
"""

import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _appfixture  # noqa: E402
from _appfixture import app, config  # noqa: E402

RENDER_FRONTEND = "https://farmers-forward-1.onrender.com"
PRODUCTION = "https://farmersforward.org"
PRODUCTION_WWW = "https://www.farmersforward.org"
DEV = "http://localhost:3000"

# Every browser-reachable API route. A new route that bypasses the app-wide
# CORS() call would otherwise fail silently for the deployed frontend.
API_ROUTES = [
    ("POST", "/api/auth/verify-code"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/otp"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/submit"),
    ("GET", "/api/results/1"),
    ("GET", "/api/history"),
    ("POST", "/api/contact"),
    ("GET", "/api/report/1/section/Summary"),
    ("GET", "/api/report/1/all"),
]


def reload_config(**environment):
    """Re-read config.py under a controlled environment.

    load_dotenv is neutralized: config.py reads a repository-root .env, and a
    developer's local copy (which does set FRONTEND_ORIGIN) would otherwise
    decide the result of these assertions.
    """
    with mock.patch.dict(os.environ, environment, clear=False), \
            mock.patch("dotenv.load_dotenv", return_value=False):
        for name in ("ALLOWED_ORIGINS", "FRONTEND_ORIGIN"):
            if name not in environment:
                os.environ.pop(name, None)
        return importlib.reload(config)


class AllowListTests(unittest.TestCase):
    def tearDown(self):
        # Leave the module as the rest of the suite found it. Reloading config
        # resets DATABASE_PATH to the real one, so re-point it at the fixture.
        reload_config()
        _appfixture.restore_database_path()

    def test_defaults_cover_production_and_dev(self):
        """An unset variable must still serve production, not dev only."""
        fresh = reload_config()
        for origin in (RENDER_FRONTEND, PRODUCTION, PRODUCTION_WWW, DEV):
            self.assertIn(origin, fresh.ALLOWED_ORIGINS, origin)

    def test_env_var_overrides_without_a_redeploy(self):
        """Renaming a Render service is a dashboard edit, not a code change."""
        fresh = reload_config(ALLOWED_ORIGINS="https://farmers-forward-2.onrender.com, https://x.test")
        self.assertEqual(fresh.ALLOWED_ORIGINS,
                         ["https://farmers-forward-2.onrender.com", "https://x.test"])
        # Whitespace and empty entries from a hand-edited dashboard value.
        self.assertEqual(reload_config(ALLOWED_ORIGINS=" a.test , ,b.test,").ALLOWED_ORIGINS,
                         ["a.test", "b.test"])

    def test_legacy_frontend_origin_still_honoured(self):
        """A deployment still setting the old variable must keep working."""
        fresh = reload_config(FRONTEND_ORIGIN="https://farmers-forward.onrender.com")
        self.assertIn("https://farmers-forward.onrender.com", fresh.ALLOWED_ORIGINS)
        self.assertIn(RENDER_FRONTEND, fresh.ALLOWED_ORIGINS)

    def test_frontend_origin_default_does_not_widen_an_explicit_list(self):
        """Its localhost default must not leak into a deliberately narrow list."""
        fresh = reload_config(ALLOWED_ORIGINS="https://only.test")
        self.assertEqual(fresh.ALLOWED_ORIGINS, ["https://only.test"])


class ResponseHeaderTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def allow_origin(self, method, path, origin):
        """(preflight, actual) Access-Control-Allow-Origin for one request."""
        preflight = self.client.options(path, headers={
            "Origin": origin, "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type"})
        actual = self.client.open(path, method=method,
                                  json={} if method == "POST" else None,
                                  headers={"Origin": origin})
        return (preflight.headers.get("Access-Control-Allow-Origin"),
                actual.headers.get("Access-Control-Allow-Origin"))

    def test_verify_code_preflight_and_post_allow_the_deployed_frontend(self):
        """The endpoint that was reported broken, on the origin that was failing."""
        for origin in (RENDER_FRONTEND, PRODUCTION, PRODUCTION_WWW, DEV):
            preflight, actual = self.allow_origin("POST", "/api/auth/verify-code", origin)
            self.assertEqual(preflight, origin, "preflight %s" % origin)
            self.assertEqual(actual, origin, "POST %s" % origin)

    def test_credentials_are_allowed_so_the_session_cookie_survives(self):
        response = self.client.post("/api/auth/verify-code", json={"code": "x"},
                                    headers={"Origin": RENDER_FRONTEND})
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")
        # Credentialed CORS forbids a wildcard, so the echoed origin must be exact.
        self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_every_api_route_allows_the_deployed_frontend(self):
        for method, path in API_ROUTES:
            preflight, actual = self.allow_origin(method, path, RENDER_FRONTEND)
            self.assertEqual(preflight, RENDER_FRONTEND, "preflight %s %s" % (method, path))
            self.assertEqual(actual, RENDER_FRONTEND, "%s %s" % (method, path))

    def test_unlisted_origin_is_refused(self):
        preflight, actual = self.allow_origin("POST", "/api/auth/verify-code",
                                              "https://evil.example.com")
        self.assertIsNone(preflight)
        self.assertIsNone(actual)


if __name__ == "__main__":
    unittest.main()
