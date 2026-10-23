"""The owner-only admin API: list, add, deactivate and reactivate admins.

With passwordless login the admins table is the credential store -- whoever is
in it can sign in with an emailed code -- so this is the most sensitive API the
app has. The Admins page is only shown to owners, but hiding a link protects
nothing, so these tests call the API directly as each kind of caller: nobody, a
signed-in admin who is not an owner, and an owner. Admins are never deleted; an
owner can't deactivate themselves, which is what keeps an active owner around;
and writes need a JSON body, which a cross-site form can't send.

The database is shared with every other test module in the run, so each test
registers the admins it needs, owner included, under unique emails.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, auth, database, sign_in  # noqa: E402
from test_admin_auth import (  # noqa: E402
    address_of_length, assert_refused_body, gated_client, make_admin, parse_utc,
    pin_login_policy, request_code, sign_in_by_email, unique_email)

ADMIN_FIELDS = {"id", "email", "display_name", "role", "is_active", "created_at",
                "last_login_at"}


def admins_snapshot():
    """Every admins row, to show that a refused request changed nothing."""
    return [tuple(row) for row in database.query_all(
        "SELECT id, email, display_name, role, is_active, created_at, last_login_at "
        "FROM admins ORDER BY id")]


class AnonymousTests(unittest.TestCase):
    """Without a signed-in, active admin, every admin route is 401."""

    def setUp(self):
        pin_login_policy(self)
        self.target = make_admin("anon-target")

    def assertAllRefused(self, new_client):
        """List, add and deactivate, each from a fresh client made by `new_client`."""
        before = admins_snapshot()
        responses = {
            "GET /api/admins": new_client().get("/api/admins"),
            "POST /api/admins": new_client().post("/api/admins", json={
                "email": unique_email("intruder"), "display_name": "Intruder"}),
            "PATCH /api/admins/<id>": new_client().patch(
                "/api/admins/%s" % self.target["id"], json={"is_active": False}),
        }
        for route, response in responses.items():
            with self.subTest(route):
                self.assertEqual((response.status_code, response.json),
                                 (401, {"error": "auth required"}))
        self.assertEqual(admins_snapshot(), before)

    def test_no_session(self):
        self.assertAllRefused(app.test_client)

    def test_half_way_through_signing_in(self):
        def pending():
            client = gated_client()
            request_code(client, unique_email("pending"))
            return client
        self.assertAllRefused(pending)

    def test_a_deactivated_owner(self):
        """Signed out, not merely forbidden: they are not an admin any more."""
        owner = make_admin("anon-owner", role=auth.ROLE_OWNER)
        auth.set_admin_active(owner["id"], False)

        def deactivated_owner():
            client = app.test_client()
            sign_in(client, owner["id"])
            return client
        self.assertAllRefused(deactivated_owner)


class NonOwnerTests(unittest.TestCase):
    """A signed-in admin who isn't an owner gets 403 from every admin route."""

    def setUp(self):
        pin_login_policy(self)
        self.admin = make_admin("plain")
        self.client = app.test_client()
        self.assertEqual(sign_in_by_email(self.client, self.admin["email"]).json, {"ok": True})

    def test_every_admin_route_is_403_and_changes_nothing(self):
        owner = make_admin("plain-boss", role=auth.ROLE_OWNER)
        colleague = make_admin("plain-colleague")
        before = admins_snapshot()
        attempts = {
            "list the admins": ("GET", "/api/admins", None),
            "add an admin": ("POST", "/api/admins", {
                "email": unique_email("sneaky"), "display_name": "Sneaky"}),
            "add an owner": ("POST", "/api/admins", {
                "email": unique_email("sneaky"), "display_name": "Sneaky", "role": "owner"}),
            "deactivate an owner": ("PATCH", "/api/admins/%s" % owner["id"],
                                    {"is_active": False}),
            "deactivate a colleague": ("PATCH", "/api/admins/%s" % colleague["id"],
                                       {"is_active": False}),
            "deactivate themselves": ("PATCH", "/api/admins/%s" % self.admin["id"],
                                      {"is_active": False}),
            # Refused before any lookup, so ids can't be probed either.
            "an id that doesn't exist": ("PATCH", "/api/admins/%s" % uuid.uuid4(),
                                         {"is_active": False}),
        }
        for label, (method, path, body) in attempts.items():
            with self.subTest(label):
                response = self.client.open(path, method=method, json=body)
                self.assertEqual((response.status_code, response.json),
                                 (403, {"error": "owner only"}))
        self.assertEqual(admins_snapshot(), before)
        # Refused, but still signed in.
        self.assertEqual(self.client.get("/api/history").status_code, 200)


class OwnerTestCase(unittest.TestCase):
    """Signed in as an owner of the test's own, so the seeded owner is never at stake."""

    def setUp(self):
        pin_login_policy(self)
        self.owner = make_admin("owner", role=auth.ROLE_OWNER)
        self.client = app.test_client()
        sign_in(self.client, self.owner["id"])


class ListAndCreateTests(OwnerTestCase):
    def test_the_list_has_every_admin_oldest_first(self):
        earlier = make_admin("listed")
        later = make_admin("listed-gone", active=False)
        response = self.client.get("/api/admins")
        self.assertEqual(response.status_code, 200, response.json)
        admins = response.json["admins"]

        for admin in admins:
            with self.subTest(admin.get("email")):
                self.assertEqual(set(admin), ADMIN_FIELDS)
                self.assertEqual(str(uuid.UUID(admin["id"])), admin["id"])
                for field in ("email", "display_name", "role"):
                    self.assertIsInstance(admin[field], str)
                self.assertIsInstance(admin["is_active"], bool)
                parse_utc(admin["created_at"])
                if admin["last_login_at"] is not None:
                    parse_utc(admin["last_login_at"])
        created = [parse_utc(admin["created_at"]) for admin in admins]
        self.assertEqual(created, sorted(created), "not oldest first")

        ids = [admin["id"] for admin in admins]
        self.assertLess(ids.index(earlier["id"]), ids.index(later["id"]))
        listed = dict(zip(ids, admins))
        self.assertEqual(listed[earlier["id"]], earlier)
        # Deactivated admins are listed too: that is where they get reactivated.
        self.assertEqual(listed[later["id"]], later)
        self.assertIs(listed[later["id"]]["is_active"], False)
        self.assertIn(self.owner["id"], listed)

    def test_adding_an_admin_tidies_what_was_typed(self):
        tag = uuid.uuid4().hex[:10]
        before = datetime.now(timezone.utc)
        response = self.client.post("/api/admins", json={
            "email": "  New.Person.%s@Example.COM " % tag.upper(),
            "display_name": "  New Person  "})
        after = datetime.now(timezone.utc)
        self.assertEqual(response.status_code, 201, response.json)
        admin = response.json["admin"]
        self.assertEqual(set(admin), ADMIN_FIELDS)
        self.assertEqual(admin["email"], "new.person.%s@example.com" % tag)
        self.assertEqual(admin["display_name"], "New Person")
        self.assertEqual(admin["role"], "admin")
        self.assertIs(admin["is_active"], True)
        self.assertIsNone(admin["last_login_at"])
        self.assertEqual(str(uuid.UUID(admin["id"])), admin["id"])
        self.assertTrue(before <= parse_utc(admin["created_at"]) <= after, admin["created_at"])
        # Stored as returned, and on the list.
        self.assertEqual(auth.get_admin(admin["id"]), admin)
        self.assertIn(admin, self.client.get("/api/admins").json["admins"])

    def test_the_api_cannot_add_an_owner(self):
        """Owners come from the first-run seed, the migration or reset_admin.
        Anything else in the request is ignored."""
        response = self.client.post("/api/admins", json={
            "email": unique_email("would-be-owner"), "display_name": "Would Be",
            "role": "owner", "is_active": False})
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["admin"]["role"], "admin")
        self.assertIs(response.json["admin"]["is_active"], True)

    def test_invalid_details_are_400_and_add_nobody(self):
        email = unique_email("valid")
        bad_names = {
            "missing": {"email": email},
            "blank": {"email": email, "display_name": "   "},
            "over 100 characters": {"email": email, "display_name": "x" * 101},
            "a control character": {"email": email, "display_name": "New\x07Person"},
            "a line break inside": {"email": email, "display_name": "New\nPerson"},
            "a C1 control character": {"email": email, "display_name": "New\x85Person"},
            # Right-to-left override: would render the header's "Logged in as"
            # name as a different string.
            "a bidi override": {"email": email, "display_name": "\u202enimdA"},
            "a bidi isolate": {"email": email, "display_name": "New\u2066Person"},
            "a number": {"email": email, "display_name": 42},
            "a list": {"email": email, "display_name": ["New Person"]},
            "null": {"email": email, "display_name": None},
        }
        bad_emails = {
            "missing": {},
            "blank": {"email": "   "},
            "no @": {"email": "new.person.example.com"},
            "a space inside": {"email": "new person@example.com"},
            "a control character": {"email": "new\x07person@example.com"},
            "over 254 characters": {"email": address_of_length(255)},
            "a number": {"email": 12345},
            "a list": {"email": [email]},
        }
        before = admins_snapshot()
        for label, body in bad_names.items():
            with self.subTest("display name " + label):
                response = self.client.post("/api/admins", json=body)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn("display name", response.json["error"].lower())
        for label, body in bad_emails.items():
            with self.subTest("email " + label):
                response = self.client.post("/api/admins",
                                            json=dict(body, display_name="New Person"))
                self.assertEqual((response.status_code, response.json),
                                 (400, {"error": "Please enter a valid email address"}))
        self.assertEqual(admins_snapshot(), before)
        # The limits themselves are fine, and so are names in any script.
        response = self.client.post("/api/admins", json={
            "email": address_of_length(254), "display_name": "x" * 100})
        self.assertEqual(response.status_code, 201, response.json)
        response = self.client.post("/api/admins", json={
            "email": unique_email("intl"), "display_name": "Zoë Ångström 李雷 👩‍🌾"})
        self.assertEqual(response.status_code, 201, response.json)

    def test_an_email_already_registered_is_409_whatever_its_case_or_state(self):
        existing = make_admin("dup")
        retired = make_admin("dup-retired", active=False)
        before = admins_snapshot()
        for label, email in {
            "the same address": existing["email"],
            "other capitals and spaces": "  %s  " % existing["email"].upper(),
            # A deactivated admin is reactivated, never added a second time.
            "a deactivated admin's": retired["email"].title(),
            "an owner's": self.owner["email"].upper(),
        }.items():
            with self.subTest(label):
                response = self.client.post("/api/admins", json={
                    "email": email, "display_name": "Someone Else"})
                self.assertEqual((response.status_code, response.json),
                                 (409, {"error": "An admin with that email already exists"}))
        # Nothing renamed or revived: the deactivated admin stays deactivated.
        self.assertEqual(admins_snapshot(), before)

    def test_a_new_admin_can_sign_in(self):
        email = unique_email("newcomer")
        response = self.client.post("/api/admins", json={
            "email": email.upper(), "display_name": "Newcomer"})
        self.assertEqual(response.status_code, 201, response.json)
        added = response.json["admin"]

        newcomer = app.test_client()
        self.assertEqual(sign_in_by_email(newcomer, email).json, {"ok": True})
        self.assertEqual(newcomer.get("/api/auth/me").json, {"authed": True, "admin": {
            "id": added["id"], "email": email, "display_name": "Newcomer", "role": "admin"}})
        listed = {admin["id"]: admin for admin in self.client.get("/api/admins").json["admins"]}
        parse_utc(listed[added["id"]]["last_login_at"])


class ActivationTests(OwnerTestCase):
    def setUp(self):
        super().setUp()
        self.target = make_admin("target")

    def patch(self, admin_id, body):
        return self.client.patch("/api/admins/%s" % admin_id, json=body)

    def test_deactivate_then_reactivate_another_admin(self):
        off = self.patch(self.target["id"], {"is_active": False})
        self.assertEqual((off.status_code, off.json),
                         (200, {"admin": dict(self.target, is_active=False)}))
        self.assertIs(off.json["admin"]["is_active"], False)
        self.assertIs(auth.get_admin(self.target["id"])["is_active"], False)

        on = self.patch(self.target["id"], {"is_active": True})
        self.assertEqual((on.status_code, on.json), (200, {"admin": self.target}))
        self.assertIs(on.json["admin"]["is_active"], True)
        self.assertEqual(auth.get_admin(self.target["id"]), self.target)

    def test_an_owner_cannot_deactivate_themselves(self):
        before = admins_snapshot()
        # Ids are compared as uuids, so another spelling is no way round it.
        for spelling in (self.owner["id"], self.owner["id"].upper()):
            with self.subTest(spelling):
                response = self.patch(spelling, {"is_active": False})
                self.assertEqual((response.status_code, response.json),
                                 (400, {"error": "You can't deactivate yourself"}))
        self.assertEqual(admins_snapshot(), before)
        self.assertEqual(self.client.get("/api/admins").status_code, 200)

        # Asking to stay active is a harmless no-op.
        response = self.patch(self.owner["id"], {"is_active": True})
        self.assertEqual((response.status_code, response.json), (200, {"admin": self.owner}))
        self.assertEqual(admins_snapshot(), before)

    def test_unknown_or_malformed_ids_are_404(self):
        before = admins_snapshot()
        for admin_id in ("not-a-uuid", "1", str(uuid.uuid4()),
                         self.target["id"][:-1], self.target["id"] + "0"):
            with self.subTest(admin_id):
                response = self.patch(admin_id, {"is_active": False})
                self.assertEqual((response.status_code, response.json),
                                 (404, {"error": "not found"}))
        self.assertEqual(admins_snapshot(), before)

    def test_is_active_must_be_a_json_boolean(self):
        before = admins_snapshot()
        for body in ({}, {"is_active": None}, {"is_active": 1}, {"is_active": 0},
                     {"is_active": "false"}, {"is_active": "true"}, {"active": False}):
            with self.subTest(body=body):
                response = self.patch(self.target["id"], body)
                self.assertEqual((response.status_code, response.json),
                                 (400, {"error": "is_active must be true or false"}))
        with self.subTest(body=[False]):
            assert_refused_body(self, self.patch(self.target["id"], [False]))
        self.assertEqual(admins_snapshot(), before)

    def test_a_body_that_isnt_json_changes_nothing(self):
        """An HTML form on another site can send these without a CORS
        preflight, but it can't send JSON. No JSON body, no change."""
        fields = {"is_active": "false", "email": unique_email("csrf"), "display_name": "Csrf"}
        forms = {
            "form-encoded": ("application/x-www-form-urlencoded", urlencode(fields)),
            "multipart": ("multipart/form-data", fields),
            "JSON sent as text/plain": ("text/plain", '{"is_active": false, "email": "%s", '
                                        '"display_name": "Csrf"}' % fields["email"]),
        }
        routes = (("PATCH", "/api/admins/%s" % self.target["id"]),
                  ("POST", "/api/admins"))
        before = admins_snapshot()
        for label, (content_type, data) in forms.items():
            for method, path in routes:
                with self.subTest(label, route=method):
                    response = self.client.open(
                        path, method=method, content_type=content_type,
                        data=data if isinstance(data, str) else dict(data))
                    assert_refused_body(self, response)
        self.assertEqual(admins_snapshot(), before)

    def test_admins_are_never_deleted(self):
        for path in ("/api/admins/%s" % self.target["id"], "/api/admins"):
            with self.subTest(path):
                self.assertEqual(self.client.delete(path).status_code, 405)
        self.assertEqual(auth.get_admin(self.target["id"]), self.target)


if __name__ == "__main__":
    unittest.main()
