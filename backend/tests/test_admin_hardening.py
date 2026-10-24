"""Regression tests for the review of the multi-admin change.

Each class pins one problem a reviewer demonstrated, so it can't come back
quietly: the email send showing up in response time, racing guesses beating the
attempt cap, a used or locked code blocking the next sign-in, the rollback
target accepting this version's cookies, the slow-trickle brute force, settings
that silently lock everyone out, two owners deactivating each other, and a few
untested promises (random codes, the signing key, seed-only-when-empty, the
startup report, sessions without login_at).
"""

import contextlib
import io
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import OWNER_EMAIL, TEST_SECRET_KEY, app, auth, config, database, sign_in  # noqa: E402
from test_admin_auth import (  # noqa: E402
    ago, gated_client, live_otp, make_admin, otp_rows, pin_login_policy, request_code,
    submit_code, unique_email, wrong_code)


class DeliveryTimingTests(unittest.TestCase):
    """The send must not run while the request is in flight: even on a thread
    it competes for the GIL and makes a registered email's reply slower."""

    def setUp(self):
        pin_login_policy(self)
        self.sent = threading.Event()
        patches = (mock.patch.object(config, "RESEND_API_KEY", "re_test_key"),
                   mock.patch.object(auth, "_send_email",
                                     side_effect=lambda email, code: self.sent.set()))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_email_goes_out_only_after_the_response_is_finished(self):
        admin = make_admin("timing")
        client = gated_client()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            response = client.post("/api/auth/login", json={"email": admin["email"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.sent.wait(0.3), "the send started during the request")
        response.close()          # what the WSGI server does once it has sent the reply
        self.assertTrue(self.sent.wait(5), "the send never started")
        # With Resend configured the code never reaches the log.
        self.assertNotIn(live_otp(admin["email"])["code"], out.getvalue())

    def test_an_unknown_email_never_sends(self):
        client = gated_client()
        response = request_code(client, unique_email("nobody"))
        response.close()
        self.assertFalse(self.sent.wait(0.3))


class AttemptRaceTests(unittest.TestCase):
    """Guesses racing on one code are each claimed before they are compared."""

    def setUp(self):
        pin_login_policy(self)

    def test_simultaneous_guesses_cannot_exceed_the_attempt_cap(self):
        admin = make_admin("racer")
        request_code(gated_client(), admin["email"])
        code = live_otp(admin["email"])["code"]
        results, barrier = [], threading.Barrier(8)

        def guess():
            barrier.wait()
            results.append(auth.verify_login_code(admin["email"], wrong_code(code))[0])

        threads = [threading.Thread(target=guess) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        compared = [r for r in results if r in ("invalid", "locked")]
        self.assertEqual(len(compared), 8)
        # Only three guesses were ever compared; the rest found the code locked.
        self.assertEqual(live_otp(admin["email"])["attempts"], config.OTP_MAX_ATTEMPTS)
        self.assertEqual(auth.verify_login_code(admin["email"], code), ("locked", None))

    def test_a_successful_sign_in_is_not_counted_as_a_wrong_guess(self):
        admin = make_admin("counted")
        client = gated_client()
        request_code(client, admin["email"])
        row = live_otp(admin["email"])
        self.assertEqual(submit_code(client, row["code"]).json, {"ok": True})
        self.assertEqual([r["attempts"] for r in otp_rows(admin["email"])], [0])


class CooldownTests(unittest.TestCase):
    """The cooldown only protects a code that can still be used."""

    def setUp(self):
        pin_login_policy(self)

    def test_signing_in_again_right_after_a_sign_in_gets_a_new_code(self):
        admin = make_admin("again")
        client = gated_client()
        request_code(client, admin["email"])
        self.assertEqual(submit_code(client, live_otp(admin["email"])["code"]).json, {"ok": True})
        client.post("/api/auth/logout")
        second = gated_client()
        request_code(second, admin["email"])
        self.assertEqual(len(otp_rows(admin["email"])), 2)
        self.assertEqual(submit_code(second, live_otp(admin["email"])["code"]).json, {"ok": True})

    def test_a_locked_code_does_not_block_a_new_one(self):
        admin = make_admin("locked-out")
        client = gated_client()
        request_code(client, admin["email"])
        code = live_otp(admin["email"])["code"]
        for _ in range(config.OTP_MAX_ATTEMPTS):
            submit_code(client, wrong_code(code))
        request_code(client, admin["email"])
        self.assertEqual(len(otp_rows(admin["email"])), 2)
        self.assertEqual(submit_code(client, live_otp(admin["email"])["code"]).json, {"ok": True})

    def test_a_live_code_still_blocks_a_quick_resend(self):
        email = unique_email("live")
        client = gated_client()
        request_code(client, email)
        request_code(client, email)
        self.assertEqual(len(otp_rows(email)), 1)


class DailyFailureCapTests(unittest.TestCase):
    """A trickle that stays under the hourly failure limit is capped per day."""

    def setUp(self):
        pin_login_policy(self)

    def test_wrong_guesses_across_the_day_stop_new_codes(self):
        for label in ("known", "unknown"):
            with self.subTest(label):
                email = make_admin("daily")["email"] if label == "known" else unique_email("daily")
                # Yesterday-ish rows, all outside the hourly window, summing to the cap.
                for hours in range(2, 2 + config.OTP_FAILURE_LIMIT_PER_DAY // 3):
                    database.execute(
                        "INSERT INTO otps (id, email, admin_id, code, expires_at, attempts, "
                        "consumed, created_at) VALUES (?, ?, NULL, '000000', ?, 3, 1, ?)",
                        (database.new_id(), email, ago(hours * 3600), ago(hours * 3600)))
                before = len(otp_rows(email))
                request_code(gated_client(), email)
                self.assertEqual(len(otp_rows(email)), before, "a code was issued over the cap")


class CookieNameTests(unittest.TestCase):
    def test_the_session_cookie_is_not_flasks_default(self):
        """The previous version reads 'session'. After a rollback it would accept
        this version's cookies, a deactivated admin's included."""
        self.assertEqual(app.config["SESSION_COOKIE_NAME"], "ff_admin_session")
        response = app.test_client().post("/api/auth/verify-code",
                                          json={"code": config.ADMIN_SECRET_CODE})
        cookies = response.headers.getlist("Set-Cookie")
        self.assertTrue(any(c.startswith("ff_admin_session=") for c in cookies), cookies)
        self.assertFalse(any(c.startswith("session=") for c in cookies), cookies)


class SigningKeyTests(unittest.TestCase):
    def test_cookies_are_signed_with_the_configured_key(self):
        """A per-boot random key would also 'work' in every other test."""
        self.assertEqual(app.secret_key, TEST_SECRET_KEY)
        from itsdangerous import URLSafeTimedSerializer
        forged = app.test_client()
        serializer = app.session_interface.get_signing_serializer(app)
        other = URLSafeTimedSerializer("some-other-key-of-plenty-length-0123456789",
                                       salt=serializer.salt,
                                       serializer=serializer.serializer,
                                       signer_kwargs=serializer.signer_kwargs)
        forged.set_cookie(app.config["SESSION_COOKIE_NAME"], other.dumps(
            {"authed": True, "admin_id": auth.find_admin_by_email(OWNER_EMAIL)["id"],
             "login_at": database.now_iso()}))
        self.assertEqual(forged.get("/api/history").status_code, 401)


class SettingsTests(unittest.TestCase):
    def test_out_of_range_or_malformed_settings_fall_back_loudly(self):
        cases = {"0": 5, "-1": 5, "60s": 5, "5.0": 5, "10000000": 5, " 7 ": 7}
        for raw, expected in cases.items():
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"FF_TEST_KNOB": raw}), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(config._get_bounded_int("FF_TEST_KNOB", 5, 1, 1000), expected)
                self.assertEqual("WARNING" in out.getvalue(), expected != 7)

    def test_the_login_policy_defaults(self):
        """The documented numbers; an 8-hour code or no cooldown must fail here."""
        env = {name: "" for name in os.environ if name.startswith("OTP_")}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(
                [config._get_bounded_int(*args) for args in (
                    ("OTP_TTL_SECONDS", 300, 60, 3600), ("OTP_MAX_ATTEMPTS", 3, 1, 10))],
                [300, 3])
        source = open(config.__file__).read()
        for line in ('"OTP_TTL_SECONDS", 300,', '"OTP_MAX_ATTEMPTS", 3,',
                     '"OTP_RESEND_COOLDOWN_SECONDS", 60,', '"OTP_FAILURE_LIMIT", 10,',
                     '"OTP_FAILURE_WINDOW_SECONDS", 3600,', '"OTP_FAILURE_LIMIT_PER_DAY", 30,',
                     '"OTP_ISSUE_LIMIT_PER_HOUR", 5,', '"OTP_GLOBAL_LIMIT_PER_HOUR", 500,'):
            self.assertIn(line, source)

    def test_codes_are_random_six_digit_strings(self):
        codes = [auth.generate_otp() for _ in range(500)]
        self.assertTrue(all(len(c) == 6 and c.isdigit() for c in codes))
        self.assertGreater(len(set(codes)), 490)


class LastOwnerTests(unittest.TestCase):
    """Nothing may leave the app without an active owner."""

    def setUp(self):
        pin_login_policy(self)

    def test_the_last_active_owner_cannot_be_deactivated(self):
        a = make_admin("owner-a", role=auth.ROLE_OWNER)
        b = make_admin("owner-b", role=auth.ROLE_OWNER)
        others = [x for x in auth.list_admins()
                  if x["role"] == auth.ROLE_OWNER and x["is_active"] and x["id"] not in (a["id"], b["id"])]
        for other in others:   # leave exactly a and b as active owners
            database.execute("UPDATE admins SET is_active = ? WHERE id = ?", (False, other["id"]))
        self.addCleanup(lambda: [database.execute("UPDATE admins SET is_active = ? WHERE id = ?",
                                                  (True, other["id"])) for other in others])
        client = app.test_client()
        sign_in(client, a["id"])
        self.assertEqual(client.patch("/api/admins/%s" % b["id"], json={"is_active": False}).status_code, 200)
        with self.assertRaises(auth.LastOwnerError):
            auth.set_admin_active(a["id"], False)
        self.assertTrue(auth.get_admin(a["id"])["is_active"])

    def test_two_owners_deactivating_each_other_at_once_leave_one(self):
        a = make_admin("cross-a", role=auth.ROLE_OWNER)
        b = make_admin("cross-b", role=auth.ROLE_OWNER)
        others = [x for x in auth.list_admins()
                  if x["role"] == auth.ROLE_OWNER and x["is_active"] and x["id"] not in (a["id"], b["id"])]
        for other in others:
            database.execute("UPDATE admins SET is_active = ? WHERE id = ?", (False, other["id"]))
        self.addCleanup(lambda: [database.execute("UPDATE admins SET is_active = ? WHERE id = ?",
                                                  (True, other["id"])) for other in others])
        barrier, outcomes = threading.Barrier(2), []

        def deactivate(target):
            barrier.wait()
            try:
                auth.set_admin_active(target, False)
                outcomes.append("done")
            except auth.LastOwnerError:
                outcomes.append("refused")

        threads = [threading.Thread(target=deactivate, args=(t,)) for t in (a["id"], b["id"])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["done", "refused"])
        active = [x for x in (auth.get_admin(a["id"]), auth.get_admin(b["id"])) if x["is_active"]]
        self.assertEqual(len(active), 1)


class SeedingTests(unittest.TestCase):
    """seed_owner() only ever fills an EMPTY table, and never crash-loops."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ff-seed-tests-")
        self.addCleanup(temp.cleanup)
        patch = mock.patch.multiple(database.config, DATABASE_URL=None,
                                    DATABASE_PATH=os.path.join(temp.name, "seed.db"))
        patch.start()
        self.addCleanup(patch.stop)
        database.init_db()

    def test_an_existing_admin_means_no_seed_even_if_the_env_owner_is_missing(self):
        auth.create_admin("someone@example.com", "Someone")
        with mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", "boss@example.com"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(auth.seed_owner(), {"created": False, "email": None})
        self.assertIsNone(auth.find_admin_by_email("boss@example.com"))

    def test_an_unusable_display_name_falls_back_instead_of_crashing(self):
        with mock.patch.multiple(config, DEFAULT_ADMIN_EMAIL="boss@example.com",
                                 DEFAULT_ADMIN_NAME="Bad\tName"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(auth.seed_owner()["created"])
        self.assertIn("WARNING", out.getvalue())
        self.assertEqual(auth.find_admin_by_email("boss@example.com")["display_name"], "boss")


class StartupReportTests(unittest.TestCase):
    def test_the_report_masks_emails_and_warns_without_an_owner(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            auth.log_admin_status()
        self.assertNotIn(OWNER_EMAIL, out.getvalue())
        self.assertIn(auth.mask_email(OWNER_EMAIL), out.getvalue())
        with mock.patch.object(auth, "list_admins", return_value=[]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            auth.log_admin_status()
        self.assertIn("WARNING: no active owner", out.getvalue())


class SessionCutoffTests(unittest.TestCase):
    def test_a_session_without_login_at_is_refused_once_there_is_a_cutoff(self):
        admin = make_admin("no-login-at")
        database.execute("UPDATE admins SET sessions_valid_after = ? WHERE id = ?",
                         (ago(3600), admin["id"]))
        client = app.test_client()
        with client.session_transaction() as session:
            session["authed"] = True
            session["admin_id"] = admin["id"]
        self.assertEqual(client.get("/api/history").status_code, 401)
        sign_in(client, admin["id"])            # a real sign-in stamps login_at
        self.assertEqual(client.get("/api/history").status_code, 200)


class DeliveryWarningTests(unittest.TestCase):
    def test_the_admins_list_tells_the_owner_codes_cannot_reach_others(self):
        client = app.test_client()
        sign_in(client)
        with mock.patch.multiple(config, RESEND_API_KEY="re_test_key",
                                 RESEND_FROM=config.RESEND_TEST_SENDER):
            warnings = client.get("/api/admins").json["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("test sender", warnings[0])
        with mock.patch.multiple(config, RESEND_API_KEY="re_test_key",
                                 RESEND_FROM="login@farmersforward.org"):
            self.assertEqual(client.get("/api/admins").json["warnings"], [])


if __name__ == "__main__":
    unittest.main()
