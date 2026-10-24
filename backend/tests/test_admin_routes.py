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
from _appfixture import app, auth, database, owner_id, sign_in  # noqa: E402


def admins_snapshot():
    return [tuple(row) for row in database.query_all(
        "SELECT id, email, display_name, role, is_active, last_login_at "
        "FROM admins ORDER BY id")]


# The complete intended HTTP surface. A new route has to be added here
# deliberately, which is the point: it forces a look at whether it needs a
# login_required decorator.
EXPECTED_RULES = {
    "/api/auth/verify-code",
    "/api/auth/login",
    "/api/auth/otp",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/admins",               # owner only
    "/api/admins/<admin_id>",    # owner only
    "/api/submit",
    "/api/score",
    "/api/results/<int:farm_id>",
    "/api/history",
    "/api/contact",
    "/api/soil-info",
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

    def test_no_recovery_route_changes_the_admins_table(self):
        """The exact hatch that shipped, plus the obvious renamings of it.

        Login is passwordless now, so the admins rows themselves are the
        credentials: no anonymous request may add, alter or reactivate one.
        """
        before = admins_snapshot()
        self.assertTrue(before, "seeding should have produced an admin to protect")

        for path in ("/create-temp-admin", "/create_temp_admin", "/temp-admin",
                     "/reset-admin", "/admin/reset", "/api/admins",
                     "/api/admins/%s" % before[0][0]):
            for method in ("GET", "POST", "PATCH", "PUT", "DELETE"):
                self.client.open(path, method=method, json={
                    "email": "intruder@example.com", "display_name": "Intruder",
                    "is_active": False, "role": "owner"})

        self.assertEqual(admins_snapshot(), before,
                         "an unauthenticated request changed the admins table")

    def test_a_body_that_is_not_a_json_object_is_never_a_500(self):
        """The auth and admin routes read their JSON body through
        _json_object(); a list, a string or a number used to raise
        AttributeError on .get (a 500). (submit, score, contact and the report
        route still have that pre-existing bug; they belong to other work.)"""
        signed_in = app.test_client()
        sign_in(signed_in)
        posts = ("/api/auth/verify-code", "/api/auth/login", "/api/auth/otp",
                 "/api/admins")
        for body in (["x"], "x", 7, None):
            for path in posts:
                for client in (self.client, signed_in):
                    with self.subTest(path=path, body=body):
                        self.assertLess(client.post(path, json=body).status_code, 500)
            with self.subTest(path="/api/admins/<id>", body=body):
                response = signed_in.patch("/api/admins/%s" % owner_id(), json=body)
                self.assertEqual(response.status_code, 400)

    def test_admin_count_is_not_changed_by_anonymous_requests(self):
        before = auth.admin_count()
        for path in ("/create-temp-admin", "/api/auth/me", "/anything-else"):
            self.client.get(path)
        self.assertEqual(auth.admin_count(), before)


if __name__ == "__main__":
    unittest.main()
