"""Guard the HTTP surface that can touch admin credentials.

A `/create-temp-admin` route once shipped to production: an unauthenticated GET
that reset the admin password to a hardcoded value. It was added as a temporary
recovery hatch, its own comment said to remove it, and it survived several
deploys. These tests make that class of route fail loudly instead of quietly.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, auth, database  # noqa: E402

# The complete intended HTTP surface. A new route has to be added here
# deliberately, which is the point: it forces a look at whether it needs a
# login_required decorator.
EXPECTED_RULES = {
    "/api/auth/verify-code",
    "/api/auth/login",
    "/api/auth/otp",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/submit",
    "/api/score",
    "/api/results/<int:farm_id>",
    "/api/history",
    "/api/contact",
    "/api/report/<int:farm_id>/section/<path:section_label>",
    "/api/report/<int:farm_id>/all",
    "/",                 # SPA entry
    "/<path:path>",      # SPA client-side routing fallback
}


class AdminRouteSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_url_map_matches_the_intended_surface(self):
        actual = {str(rule) for rule in app.url_map.iter_rules()
                  if rule.endpoint != "static"}
        unexpected = actual - EXPECTED_RULES
        self.assertEqual(unexpected, set(),
                         "unreviewed route(s) exposed: %s" % sorted(unexpected))
        self.assertEqual(EXPECTED_RULES - actual, set(), "route(s) disappeared")

    def test_no_recovery_route_resets_admin_credentials(self):
        """The exact hatch that shipped, plus the obvious renamings of it."""
        row = database.query_one("SELECT username, password_hash FROM admins")
        self.assertIsNotNone(row, "seeding should have produced an admin to protect")
        before = row["password_hash"]

        for path in ("/create-temp-admin", "/create_temp_admin", "/temp-admin",
                     "/reset-admin", "/admin/reset"):
            for method in ("GET", "POST"):
                self.client.open(path, method=method)

        after = database.query_one(
            "SELECT password_hash FROM admins WHERE username = ?", (row["username"],))
        self.assertEqual(after["password_hash"], before,
                         "an unauthenticated request changed the admin password")
        self.assertFalse(auth.check_password("TempPass123!", after["password_hash"]),
                         "the hardcoded recovery password is live again")

    def test_admin_count_is_not_changed_by_anonymous_requests(self):
        before = auth.admin_count()
        for path in ("/create-temp-admin", "/api/auth/me", "/anything-else"):
            self.client.get(path)
        self.assertEqual(auth.admin_count(), before)


if __name__ == "__main__":
    unittest.main()
