"""End-to-end tests of the passwordless admin login, through the Flask test client.

There is no password any more: the 6-digit code emailed to an admin is the only
secret. That puts three duties on this flow, and these tests hold it to each:

  * Nothing a client can see -- status, body, headers, or the session cookie,
    which is signed but readable -- may depend on whether an email belongs to
    an active admin. Anyone who knows the public passcode can reach the email
    form, so any difference would let them list who the admins are.
  * The per-email throttles must hold, because a 6-digit code falls to anyone
    allowed to keep asking for fresh ones.
  * Deactivating an admin must end their session on its very next request.

No response ever contains a code, so the tests read it from the otps table, the
way an admin reads their inbox, and they let time pass by rewriting created_at
and expires_at there instead of sleeping. The database is shared with every
other test module in the run: each test registers its own admins under unique
emails and depends on no other test.
"""

import contextlib
import io
import json
import os
import re
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app, auth, config, database, sign_in  # noqa: E402

# The OTP policy these tests assume -- the defaults, whatever a developer's
# .env sets -- so the countdowns asserted here (2, then 1, then locked) hold.
# The cap across all emails is set out of the way: every test module in a run
# shares one database. The test about that cap sets its own.
LOGIN_POLICY = dict(OTP_TTL_SECONDS=300, OTP_MAX_ATTEMPTS=3, OTP_RESEND_COOLDOWN_SECONDS=60,
                    OTP_FAILURE_LIMIT=10, OTP_FAILURE_WINDOW_SECONDS=3600,
                    OTP_FAILURE_LIMIT_PER_DAY=30,
                    OTP_ISSUE_LIMIT_PER_HOUR=5, OTP_GLOBAL_LIMIT_PER_HOUR=10 ** 6)

# The one reply /api/auth/login gives every well-formed email.
LOGIN_REPLY = {"ok": True, "message": auth.LOGIN_SENT_MESSAGE}

# Three wrong tries on one code, under LOGIN_POLICY.
LOCKOUT = [
    (401, {"error": "invalid", "attempts_remaining": 2}),
    (401, {"error": "invalid", "attempts_remaining": 1}),
    (423, {"error": "locked"}),
]

# created_at / last_login_at as the contract shows them.
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?\+00:00$")

# Three 63-character labels and a real suffix: 203 characters with every part
# inside the RFC limits, so only the overall length can be what is refused.
LONG_DOMAIN = ".".join(["x" * 63] * 3) + ".example.com"


def pin_login_policy(test):
    """Run `test` under LOGIN_POLICY, with no Resend key so nothing is emailed."""
    patcher = mock.patch.multiple(config, RESEND_API_KEY=None, **LOGIN_POLICY)
    patcher.start()
    test.addCleanup(patcher.stop)


def unique_email(label="admin"):
    """An address no other test, in this module or any other, will use."""
    return "%s-%s@example.com" % (label, uuid.uuid4().hex[:12])


def address_of_length(length):
    """A unique, otherwise well-formed address exactly `length` characters long."""
    local = ("len-" + uuid.uuid4().hex).ljust(length - len(LONG_DOMAIN) - 1, "a")
    return "%s@%s" % (local, LONG_DOMAIN)


def make_admin(label="admin", role=auth.ROLE_ADMIN, active=True):
    """Register an admin under a unique email; returns its record."""
    admin = auth.create_admin(unique_email(label), "Test %s" % label, role=role)
    return admin if active else auth.set_admin_active(admin["id"], False)


def parse_utc(value):
    """The moment an API timestamp names; fails unless it is ISO-8601 UTC."""
    if not isinstance(value, str) or not ISO_UTC_RE.match(value):
        raise AssertionError("not an ISO-8601 UTC timestamp: %r" % (value,))
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# The login steps, as the frontend makes them
# ---------------------------------------------------------------------------
def pass_gate(client):
    """Type the hidden passcode, as the public page does after the triple-click."""
    response = client.post("/api/auth/verify-code", json={"code": config.ADMIN_SECRET_CODE})
    if response.json != {"ok": True}:
        raise AssertionError("the gate refused ADMIN_SECRET_CODE: %r" % (response.json,))


def gated_client():
    """A new browser that has typed the passcode."""
    client = app.test_client()
    pass_gate(client)
    return client


def request_code(client, email):
    """POST /api/auth/login, quietly: without Resend the app prints the code."""
    with contextlib.redirect_stdout(io.StringIO()):
        return client.post("/api/auth/login", json={"email": email})


def submit_code(client, code):
    return client.post("/api/auth/otp", json={"code": code})


def sign_in_by_email(client, email):
    """The whole real flow -- passcode, email, then the code it was sent.

    Returns the /api/auth/otp response. Works once per cooldown for an email,
    unless the test moves that email's challenges back (move_back).
    """
    pass_gate(client)
    request_code(client, email)
    row = live_otp(email)
    return submit_code(client, row["code"] if row is not None else "")


# ---------------------------------------------------------------------------
# The otps table: reading codes, moving time
# ---------------------------------------------------------------------------
_OTP_COLUMNS = "id, admin_id, code, expires_at, attempts, consumed, created_at"


def otp_rows(email):
    """Every challenge for `email`, oldest first."""
    return database.query_all(
        "SELECT %s FROM otps WHERE email = ? ORDER BY created_at" % _OTP_COLUMNS,
        (auth.normalize_email(email),))


def live_otp(email):
    """The newest unconsumed challenge for `email`, or None. Its code is what
    the admin would read in their inbox."""
    return database.query_one(
        "SELECT %s FROM otps WHERE email = ? AND consumed = 0 "
        "ORDER BY created_at DESC LIMIT 1" % _OTP_COLUMNS, (auth.normalize_email(email),))


def otp_count():
    return database.query_one("SELECT COUNT(*) AS n FROM otps")["n"]


def codes_issued(email):
    """How many challenges exist for `email` under any spelling of it, so a
    code filed under the address as typed would still be counted."""
    return database.query_one("SELECT COUNT(*) AS n FROM otps WHERE lower(trim(email)) = ?",
                              (auth.normalize_email(email),))["n"]


def wrong_code(code):
    """A six-digit code that is certainly not `code`."""
    return "%06d" % ((int(code) + 1) % 1000000)


def ago(seconds):
    """An ISO UTC timestamp `seconds` in the past, in the app's own format."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def move_back(email, seconds):
    """Pretend every challenge for `email` was issued `seconds` ago.

    The throttles compare otps.created_at with the clock, so this is how a
    cooldown or a failure window passes without sleeping.
    """
    database.execute("UPDATE otps SET created_at = ? WHERE email = ?",
                     (ago(seconds), auth.normalize_email(email)))


def set_otp(row_id, **columns):
    """Overwrite columns (expires_at, created_at, code) of one challenge."""
    database.execute(
        "UPDATE otps SET %s WHERE id = ?" % ", ".join("%s = ?" % name for name in columns),
        tuple(columns.values()) + (str(row_id),))


# ---------------------------------------------------------------------------
# The session cookie
# ---------------------------------------------------------------------------
def decode_session(value):
    """A session cookie's contents. It is signed, not encrypted: anyone who
    holds the cookie can read it exactly like this."""
    return dict(app.session_interface.get_signing_serializer(app).loads(value))


def session_of(client):
    """The session `client` holds now ({} once the server has dropped it)."""
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    return decode_session(cookie.value) if cookie is not None else {}


def cookie_set_by(response):
    """The value of the session cookie `response` sets, or None."""
    for header in response.headers.getlist("Set-Cookie"):
        name, _, rest = header.partition("=")
        if name == app.config["SESSION_COOKIE_NAME"]:
            return rest.partition(";")[0]
    return None


def visible_parts(response):
    """Status, headers and body, with the session cookie's value blanked out
    (it carries the email as typed, so it necessarily differs)."""
    value = cookie_set_by(response)
    headers = sorted((name, text.replace(value, "<value>") if value else text)
                     for name, text in response.headers)
    return response.status_code, headers, response.data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class PasscodeGateTests(unittest.TestCase):
    """The hidden passcode in front of the email form, and what the form takes."""

    def setUp(self):
        pin_login_policy(self)
        self.client = app.test_client()

    def assertInvalidEmail(self, response):
        self.assertEqual((response.status_code, response.json),
                         (400, {"error": "Please enter a valid email address"}))

    def test_no_email_form_without_the_passcode(self):
        email = make_admin("ungated")["email"]
        # A wrong passcode is a plain "no", and opens nothing.
        wrong = self.client.post("/api/auth/verify-code",
                                 json={"code": config.ADMIN_SECRET_CODE + "0"})
        self.assertEqual((wrong.status_code, wrong.json), (200, {"ok": False}))
        response = request_code(self.client, email)
        self.assertEqual((response.status_code, response.json), (403, {"error": "forbidden"}))
        self.assertEqual(otp_rows(email), [])
        self.assertEqual(session_of(self.client), {})

    def test_malformed_emails_are_400_and_issue_nothing(self):
        pass_gate(self.client)
        bodies = {
            "missing": {},
            "empty": {"email": ""},
            "only spaces": {"email": "   "},
            "no @": {"email": "owner.example.com"},
            "a space inside": {"email": "first last@example.com"},
            "a tab inside": {"email": "first\tlast@example.com"},
            "a control character": {"email": "own\x07er@example.com"},
            # C-string truncation would read this as owner@example.com.
            "a NUL": {"email": "owner@example.com\x00.attacker.test"},
            "over 254 characters": {"email": address_of_length(255)},
            "a JSON number": {"email": 12345},
            "a JSON list": {"email": ["owner@example.com"]},
            "a JSON null": {"email": None},
            "a list for a body": ["owner@example.com"],
        }
        not_json = {
            "form-encoded": ("application/x-www-form-urlencoded", "email=owner%40example.com"),
            "JSON sent as text/plain": ("text/plain", '{"email": "owner@example.com"}'),
            "broken JSON": ("application/json", '{"email": '),
        }
        before = otp_count()
        for label, body in bodies.items():
            with self.subTest(label):
                self.assertInvalidEmail(self.client.post("/api/auth/login", json=body))
        for label, (content_type, data) in not_json.items():
            with self.subTest(label):
                self.assertInvalidEmail(self.client.post(
                    "/api/auth/login", data=data, content_type=content_type))
        self.assertEqual(otp_count(), before)
        self.assertNotIn("pending_email", session_of(self.client))
        # The limit is on the address as stored: 254 characters is still fine.
        self.assertEqual(request_code(self.client, address_of_length(254)).json, LOGIN_REPLY)

    def test_odd_passcode_input_is_a_plain_no_not_a_500(self):
        """verify-code is public, so whatever arrives, the answer is {ok: false}."""
        bodies = {
            "a JSON list": [config.ADMIN_SECRET_CODE],
            "a JSON string": config.ADMIN_SECRET_CODE,
            "a JSON number": 123456,
            "a non-ASCII code": {"code": "\u00e9" + config.ADMIN_SECRET_CODE},
        }
        for label, body in bodies.items():
            with self.subTest(label):
                client = app.test_client()
                response = client.post("/api/auth/verify-code", json=body)
                self.assertEqual((response.status_code, response.json), (200, {"ok": False}))
                self.assertEqual(request_code(client, unique_email("odd")).status_code, 403)


class EnumerationTests(unittest.TestCase):
    """An active admin, an unknown email and a deactivated admin look alike.

    Any difference -- in status, body, headers or the readable session cookie
    -- would tell whoever knows the public passcode which addresses are admins,
    and which of them were deactivated.
    """

    def setUp(self):
        pin_login_policy(self)
        self.admin = make_admin("enum")
        self.emails = {
            "active admin": self.admin["email"],
            "unknown email": unique_email("enum-nobody"),
            "deactivated admin": make_admin("enum-gone", active=False)["email"],
        }
        self.clients = {case: gated_client() for case in self.emails}

    def assertSameForAll(self, seen, what):
        """`seen` maps each case to what it observed; all must be equal."""
        expected = next(iter(seen.values()))
        for case, value in seen.items():
            self.assertEqual(value, expected, "%s differs for the %s" % (what, case))

    def test_the_login_reply_is_the_same_for_all_three(self):
        with mock.patch.object(auth, "_deliver") as deliver:
            replies = {case: request_code(self.clients[case], email)
                       for case, email in self.emails.items()}

        self.assertEqual((replies["active admin"].status_code, replies["active admin"].json),
                         (200, LOGIN_REPLY))
        self.assertSameForAll({case: (reply.status_code, reply.data)
                               for case, reply in replies.items()}, "the status or body")
        self.assertSameForAll({case: visible_parts(reply)
                               for case, reply in replies.items()}, "the reply")

        # The cookie: the same keys, and nothing but the email typed differs.
        sessions = {}
        for case, reply in replies.items():
            value = cookie_set_by(reply)
            self.assertIsNotNone(value, "no session cookie for the %s" % case)
            sessions[case] = decode_session(value)
            self.assertEqual(sessions[case].pop("pending_email", None), self.emails[case], case)
        self.assertSameForAll(sessions, "the rest of the session")

        # One challenge each, but only the active admin's names an admin and is sent.
        rows = {case: otp_rows(email) for case, email in self.emails.items()}
        for case, written in rows.items():
            self.assertEqual(len(written), 1, case)
        self.assertEqual(str(rows["active admin"][0]["admin_id"]), self.admin["id"])
        self.assertIsNone(rows["unknown email"][0]["admin_id"])
        self.assertIsNone(rows["deactivated admin"][0]["admin_id"])
        deliver.assert_called_once_with(self.admin["email"], rows["active admin"][0]["code"])

    def test_wrong_codes_count_down_and_lock_the_same_way(self):
        replies = {}
        for case, email in self.emails.items():
            request_code(self.clients[case], email)
            guess = wrong_code(live_otp(email)["code"])
            replies[case] = [submit_code(self.clients[case], guess) for _ in range(3)]

        for case, responses in replies.items():
            self.assertEqual([(r.status_code, r.json) for r in responses], LOCKOUT, case)
        self.assertSameForAll({case: [visible_parts(r) for r in responses]
                               for case, responses in replies.items()}, "a reply")
        # Locked: the pending login is dropped for all three, the passcode kept.
        self.assertSameForAll({case: session_of(client)
                               for case, client in self.clients.items()}, "the session")

    def test_the_right_digits_sign_in_only_an_active_admin(self):
        """For anyone else they are one more wrong guess, and lock the same way."""
        replies = {}
        for case, email in self.emails.items():
            client = self.clients[case]
            request_code(client, email)
            code = live_otp(email)["code"]
            replies[case] = [submit_code(client, code)]
            while replies[case][-1].status_code == 401 and len(replies[case]) < 3:
                replies[case].append(submit_code(client, code))

        self.assertEqual([(r.status_code, r.json) for r in replies.pop("active admin")],
                         [(200, {"ok": True})])
        me = self.clients["active admin"].get("/api/auth/me").json
        self.assertEqual(me["admin"]["id"], self.admin["id"])
        for case, responses in replies.items():
            self.assertEqual([(r.status_code, r.json) for r in responses], LOCKOUT, case)
            self.assertEqual(self.clients[case].get("/api/auth/me").json, {"authed": False}, case)
        self.assertSameForAll({case: [visible_parts(r) for r in responses]
                               for case, responses in replies.items()}, "a reply")
        self.assertIsNone(
            auth.find_admin_by_email(self.emails["deactivated admin"])["last_login_at"])


class SignInTests(unittest.TestCase):
    """The flow end to end, and what the session holds after each step."""

    def setUp(self):
        pin_login_policy(self)
        self.admin = make_admin("signin")
        self.email = self.admin["email"]
        self.client = app.test_client()

    def test_passcode_email_and_code_sign_an_admin_in(self):
        pass_gate(self.client)
        # Typed the way people type it.
        response = request_code(self.client, "  %s " % self.email.upper())
        self.assertEqual((response.status_code, response.json), (200, LOGIN_REPLY))
        row = live_otp(self.email)
        self.assertEqual(str(row["admin_id"]), self.admin["id"])

        before = datetime.now(timezone.utc)
        response = submit_code(self.client, row["code"])
        after = datetime.now(timezone.utc)
        self.assertEqual((response.status_code, response.json), (200, {"ok": True}))

        self.assertEqual(self.client.get("/api/auth/me").json, {"authed": True, "admin": {
            "id": self.admin["id"], "email": self.email,
            "display_name": self.admin["display_name"], "role": "admin"}})
        # Who is signed in, and nothing left over from the login steps.
        session = session_of(self.client)
        self.assertIs(session.get("authed"), True)
        self.assertEqual(session.get("admin_id"), self.admin["id"])
        self.assertEqual(str(uuid.UUID(session["admin_id"])), session["admin_id"])
        self.assertNotIn("secret_ok", session)
        self.assertNotIn("pending_email", session)

        last_login = parse_utc(auth.get_admin(self.admin["id"])["last_login_at"])
        self.assertTrue(before <= last_login <= after, last_login)
        self.assertEqual(self.client.get("/api/history").status_code, 200)

    def test_a_code_signs_in_once(self):
        """Two tabs holding the same code -- or a tab and an eavesdropper."""
        pass_gate(self.client)
        other = gated_client()
        for client in (self.client, other):
            request_code(client, self.email)   # the second is inside the cooldown
        self.assertEqual(len(otp_rows(self.email)), 1)
        code = live_otp(self.email)["code"]

        self.assertEqual(submit_code(self.client, code).json, {"ok": True})
        self.assertEqual(submit_code(other, code).status_code, 401)
        self.assertEqual(other.get("/api/auth/me").json, {"authed": False})
        # Nor can the session that used it spend it again: nothing is pending.
        self.assertEqual(submit_code(self.client, code).json, {"error": "no pending login"})

    def test_an_expired_code_fails_even_with_the_right_digits(self):
        pass_gate(self.client)
        request_code(self.client, self.email)
        row = live_otp(self.email)
        set_otp(row["id"], expires_at=ago(1))
        response = submit_code(self.client, row["code"])
        self.assertEqual((response.status_code, response.json), (401, {"error": "expired"}))
        self.assertEqual(self.client.get("/api/auth/me").json, {"authed": False})
        self.assertIsNone(auth.get_admin(self.admin["id"])["last_login_at"])

    def test_a_code_with_no_login_pending_is_401(self):
        for label, client in (("no session", app.test_client()),
                              ("passcode only", gated_client())):
            with self.subTest(label):
                response = submit_code(client, "123456")
                self.assertEqual((response.status_code, response.json),
                                 (401, {"error": "no pending login"}))

    def test_a_locked_code_keeps_the_passcode_for_the_next_request(self):
        pass_gate(self.client)
        request_code(self.client, self.email)
        locked = live_otp(self.email)
        replies = [submit_code(self.client, wrong_code(locked["code"])) for _ in range(3)]
        self.assertEqual(replies[-1].status_code, 423)
        session = session_of(self.client)
        self.assertIs(session.get("secret_ok"), True)
        self.assertNotIn("pending_email", session)

        # Once the cooldown has passed: a new code, without retyping the passcode.
        move_back(self.email, config.OTP_RESEND_COOLDOWN_SECONDS + 1)
        self.assertEqual(request_code(self.client, self.email).json, LOGIN_REPLY)
        fresh = live_otp(self.email)
        self.assertNotEqual(fresh["id"], locked["id"])
        self.assertEqual(submit_code(self.client, fresh["code"]).json, {"ok": True})

    def test_a_non_ascii_code_is_a_wrong_code_not_a_500(self):
        """A phone keyboard can type full-width digits: a typo, not a crash."""
        pass_gate(self.client)
        request_code(self.client, self.email)
        guess = wrong_code(live_otp(self.email)["code"])
        full_width = "".join(chr(0xFF10 + int(digit)) for digit in guess)
        response = submit_code(self.client, full_width)
        self.assertEqual((response.status_code, (response.json or {}).get("error")),
                         (401, "invalid"))

    def test_logout_ends_the_session_and_the_passcode(self):
        self.assertEqual(sign_in_by_email(self.client, self.email).json, {"ok": True})
        pass_gate(self.client)   # as if about to switch accounts
        self.assertEqual(self.client.post("/api/auth/logout").json, {"ok": True})
        self.assertEqual(session_of(self.client), {})
        self.assertEqual(self.client.get("/api/auth/me").json, {"authed": False})
        self.assertEqual(self.client.get("/api/history").status_code, 401)
        self.assertEqual(request_code(self.client, self.email).status_code, 403)

    def test_me_reports_the_role(self):
        for role in (auth.ROLE_ADMIN, auth.ROLE_OWNER):
            with self.subTest(role):
                admin = make_admin("me-" + role, role=role)
                client = app.test_client()
                self.assertEqual(sign_in_by_email(client, admin["email"]).json, {"ok": True})
                self.assertEqual(client.get("/api/auth/me").json["admin"]["role"], role)

    def test_challenges_older_than_a_day_are_pruned(self):
        """Unknown emails write rows too, so they must not pile up."""
        stale = unique_email("stale")
        pass_gate(self.client)
        request_code(self.client, stale)
        move_back(stale, 24 * 60 * 60 + 60)
        request_code(self.client, unique_email("pruner"))   # any login request prunes
        self.assertEqual(otp_rows(stale), [])


class ThrottleTests(unittest.TestCase):
    """Per-email limits on new codes: what keeps six digits from being guessed.

    Per email, not per browser -- a new cookie is free -- and the same for an
    email that matches no admin, or the limits themselves would tell them apart.
    """

    def setUp(self):
        pin_login_policy(self)
        self.client = gated_client()

    def cooldown_transcript(self, email):
        """A wrong guess, a second request from another browser inside the
        cooldown, another wrong guess. Returns (replies, first browser, code)."""
        first, second = gated_client(), gated_client()
        replies = [request_code(first, email)]
        code = live_otp(email)["code"]
        replies.append(submit_code(first, wrong_code(code)))
        issued = codes_issued(email)
        # Typed differently, it is still the same address and the same throttle.
        replies.append(request_code(second, "  %s" % email.upper()))
        self.assertEqual(codes_issued(email), issued, "a new code inside the cooldown")
        replies.append(submit_code(second, wrong_code(code)))
        return [(reply.status_code, reply.data) for reply in replies], first, code

    def test_a_request_inside_the_cooldown_issues_nothing(self):
        known, first, code = self.cooldown_transcript(make_admin("cooldown")["email"])
        unknown, _, _ = self.cooldown_transcript(unique_email("cooldown-nobody"))
        # A fresh code would have reset attempts_remaining to 2 for the second guess.
        self.assertEqual([(status, json.loads(body)) for status, body in known], [
            (200, LOGIN_REPLY), (401, {"error": "invalid", "attempts_remaining": 2}),
            (200, LOGIN_REPLY), (401, {"error": "invalid", "attempts_remaining": 1})])
        self.assertEqual(unknown, known)
        # The code sent first is still the one that works.
        self.assertEqual(submit_code(first, code).json, {"ok": True})

    def test_after_the_cooldown_a_new_code_replaces_the_old(self):
        email = make_admin("resend")["email"]
        request_code(self.client, email)
        old = live_otp(email)
        set_otp(old["id"], created_at=ago(config.OTP_RESEND_COOLDOWN_SECONDS + 1))
        request_code(self.client, email)
        new = live_otp(email)
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(len(otp_rows(email)), 2)
        if new["code"] == old["code"]:
            # Two random codes match one time in a million; keep the test exact.
            set_otp(new["id"], code=wrong_code(old["code"]))
            new = live_otp(email)

        stale = submit_code(self.client, old["code"])
        self.assertEqual((stale.status_code, stale.json),
                         (401, {"error": "invalid", "attempts_remaining": 2}))
        self.assertEqual(submit_code(self.client, new["code"]).json, {"ok": True})

    def cap_transcript(self, email):
        """Wrong guesses up to the failure cap, one round per code, each round
        from a new browser once the cooldown has passed; then one request more.
        Returns everything the browsers were shown."""
        replies, guesses = [], 0
        while guesses < config.OTP_FAILURE_LIMIT:
            move_back(email, config.OTP_RESEND_COOLDOWN_SECONDS + 1)
            client, issued = gated_client(), codes_issued(email)
            replies.append(request_code(client, email))
            self.assertEqual(codes_issued(email), issued + 1,
                             "no code after only %d wrong guesses" % guesses)
            guess = wrong_code(live_otp(email)["code"])
            for _ in range(min(config.OTP_MAX_ATTEMPTS, config.OTP_FAILURE_LIMIT - guesses)):
                replies.append(submit_code(client, guess))
                guesses += 1
        # At the cap and past the cooldown: the usual reply, but no new code.
        move_back(email, config.OTP_RESEND_COOLDOWN_SECONDS + 1)
        issued = codes_issued(email)
        replies.append(request_code(gated_client(), email))
        self.assertEqual(codes_issued(email), issued, "a new code past the failure cap")
        return [visible_parts(reply) for reply in replies]

    # Out of the way, so that only the failure cap can be what refuses.
    @mock.patch.object(config, "OTP_ISSUE_LIMIT_PER_HOUR", 1000)
    def test_enough_wrong_guesses_stop_new_codes_until_the_window_passes(self):
        admin_email, nobody = make_admin("capped")["email"], unique_email("capped-nobody")
        known = self.cap_transcript(admin_email)
        self.assertEqual(known[-1], known[0], "the capped reply should be the usual one")
        self.assertEqual(self.cap_transcript(nobody), known)

        # Once those guesses are older than the window, codes are issued again.
        browsers = {}
        for email in (admin_email, nobody):
            move_back(email, config.OTP_FAILURE_WINDOW_SECONDS + 60)
            issued, browsers[email] = codes_issued(email), gated_client()
            request_code(browsers[email], email)
            self.assertEqual(codes_issued(email), issued + 1, email)
        self.assertEqual(submit_code(browsers[admin_email], live_otp(admin_email)["code"]).json,
                         {"ok": True})

    def test_only_a_few_codes_an_hour_per_email(self):
        """However patiently the cooldown is waited out."""
        admin_email, nobody = make_admin("hourly")["email"], unique_email("hourly-nobody")
        transcripts = {}
        for email in (admin_email, nobody):
            replies = []
            for _ in range(config.OTP_ISSUE_LIMIT_PER_HOUR + 1):
                move_back(email, config.OTP_RESEND_COOLDOWN_SECONDS + 1)
                replies.append(request_code(gated_client(), email))
            self.assertEqual(codes_issued(email), config.OTP_ISSUE_LIMIT_PER_HOUR, email)
            transcripts[email] = [visible_parts(reply) for reply in replies]
        self.assertEqual(transcripts[nobody], transcripts[admin_email])
        # An hour on, codes are issued again.
        move_back(admin_email, 60 * 60 + 60)
        self.assertEqual(sign_in_by_email(gated_client(), admin_email).json, {"ok": True})

    def test_a_flood_of_addresses_pauses_codes_for_everyone_alike(self):
        """The cap across all emails keeps made-up addresses from filling the
        database. While it holds nobody gets a code, so it tells no email apart."""
        admin_email = make_admin("flooded")["email"]
        recent = database.query_one("SELECT COUNT(*) AS n FROM otps WHERE created_at >= ?",
                                    (ago(60 * 60),))["n"]
        with mock.patch.object(config, "OTP_GLOBAL_LIMIT_PER_HOUR", recent + 1):
            last = request_code(gated_client(), unique_email("flood"))   # the hour's last code
            for email in (admin_email, unique_email("flood-nobody")):
                reply = request_code(gated_client(), email)
                self.assertEqual(codes_issued(email), 0, email)
                self.assertEqual(visible_parts(reply), visible_parts(last), email)
        # It was only the cap: with room again, the admin gets a code at once.
        self.assertEqual(sign_in_by_email(gated_client(), admin_email).json, {"ok": True})


class DeactivationTests(unittest.TestCase):
    """Deactivation is immediate: the admin's very next request is refused."""

    def setUp(self):
        pin_login_policy(self)
        self.admin = make_admin("leaver")
        # The owner's browser, and the admin's own (the incognito window).
        self.owner = app.test_client()
        sign_in(self.owner, make_admin("boss", role=auth.ROLE_OWNER)["id"])
        self.incognito = app.test_client()

    def set_active(self, active):
        """Deactivate or reactivate the admin the way an owner does: on the API."""
        response = self.owner.patch("/api/admins/%s" % self.admin["id"],
                                    json={"is_active": active})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIs(response.json["admin"]["is_active"], active)

    def test_a_signed_in_admin_is_signed_out_by_their_next_request(self):
        self.assertEqual(sign_in_by_email(self.incognito, self.admin["email"]).json,
                         {"ok": True})
        self.assertEqual(self.incognito.get("/api/history").status_code, 200)

        self.set_active(False)

        history = self.incognito.get("/api/history")
        self.assertEqual((history.status_code, history.json), (401, {"error": "auth required"}))
        self.assertEqual(self.incognito.get("/api/auth/me").json, {"authed": False})
        session = session_of(self.incognito)
        self.assertNotIn("authed", session)
        self.assertNotIn("admin_id", session)

    def test_a_code_sent_before_deactivation_never_works(self):
        pass_gate(self.incognito)
        request_code(self.incognito, self.admin["email"])
        code = live_otp(self.admin["email"])["code"]
        self.set_active(False)
        self.assertEqual(submit_code(self.incognito, code).status_code, 401)
        # Not even after reactivation, while it would still be in date:
        # deactivating voided it.
        self.set_active(True)
        self.assertEqual(submit_code(self.incognito, code).status_code, 401)
        self.assertEqual(self.incognito.get("/api/auth/me").json, {"authed": False})

    def test_reactivation_does_not_revive_a_session_from_before(self):
        """Cookies can't be revoked, so the server keeps a cut-off instead. If an
        admin was deactivated because their account was compromised, a cookie
        copied before then must stay dead after they are reactivated."""
        self.assertEqual(sign_in_by_email(self.incognito, self.admin["email"]).json,
                         {"ok": True})
        stolen = self.incognito.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
        thief = app.test_client()
        thief.set_cookie(app.config["SESSION_COOKIE_NAME"], stolen)
        self.assertEqual(thief.get("/api/history").status_code, 200)

        self.set_active(False)
        self.set_active(True)

        replay = app.test_client()
        replay.set_cookie(app.config["SESSION_COOKIE_NAME"], stolen)
        self.assertEqual(replay.get("/api/history").status_code, 401)
        self.assertEqual(replay.get("/api/auth/me").json, {"authed": False})
        # Signing in again after the reactivation works as normal.
        move_back(self.admin["email"], config.OTP_RESEND_COOLDOWN_SECONDS + 1)
        fresh = app.test_client()
        self.assertEqual(sign_in_by_email(fresh, self.admin["email"]).json, {"ok": True})
        self.assertEqual(fresh.get("/api/history").status_code, 200)

    def test_a_reactivated_admin_can_sign_in_again(self):
        self.assertEqual(sign_in_by_email(self.incognito, self.admin["email"]).json,
                         {"ok": True})
        self.set_active(False)
        self.assertEqual(self.incognito.get("/api/history").status_code, 401)
        self.set_active(True)
        move_back(self.admin["email"], config.OTP_RESEND_COOLDOWN_SECONDS + 1)
        self.assertEqual(sign_in_by_email(self.incognito, self.admin["email"]).json,
                         {"ok": True})
        self.assertEqual(self.incognito.get("/api/history").status_code, 200)


class StaleSessionTests(unittest.TestCase):
    """A session that maps to no active admin is anonymous -- never a 500.

    Cookies are signed, so nobody can write these from outside, but they turn
    up anyway: a cookie from before the multi-admin migration carries an
    integer admin_id, and Postgres answers a malformed uuid with an error.
    """

    PROTECTED = (("GET", "/api/history"), ("POST", "/api/submit"), ("POST", "/api/score"),
                 ("GET", "/api/results/1"), ("GET", "/api/report/1/all"),
                 ("GET", "/api/admins"))

    def setUp(self):
        pin_login_policy(self)

    def client_holding(self, values):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(values)
        return client

    def test_stale_sessions_are_refused_cleanly(self):
        active_id = make_admin("stale-session")["id"]
        cases = {
            "a pre-migration integer admin_id": {"authed": True, "admin_id": 1},
            "a malformed uuid": {"authed": True, "admin_id": "not-a-uuid"},
            "the uuid of no admin": {"authed": True, "admin_id": str(uuid.uuid4())},
            "authed missing": {"admin_id": active_id},
            "authed truthy but not true": {"authed": 1, "admin_id": active_id},
            "admin_id missing": {"authed": True},
        }
        for label, values in cases.items():
            for method, path in self.PROTECTED:
                with self.subTest(label, route="%s %s" % (method, path)):
                    response = self.client_holding(values).open(
                        path, method=method, json={} if method == "POST" else None)
                    self.assertEqual((response.status_code, response.json),
                                     (401, {"error": "auth required"}))
            with self.subTest(label, route="GET /api/auth/me"):
                client = self.client_holding(values)
                self.assertEqual(client.get("/api/auth/me").json, {"authed": False})
                self.assertIsNot(session_of(client).get("authed"), True)


if __name__ == "__main__":
    unittest.main()
