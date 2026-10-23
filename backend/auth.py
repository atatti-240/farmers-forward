"""Authentication for Farmers Forward: passwordless admin login by emailed code.

An admin is an email address in the `admins` table (uuid id, display name,
role, active flag). Signing in is the hidden passcode gate, then an email
address, then the 6-digit code emailed to it -- there are no passwords. This
module holds that login-code flow (issue, verify, throttle), email delivery via
the Resend HTTPS API (best-effort, gated on credentials), `current_admin()`
with the `login_required` / `owner_required` decorators, admin management
(create, activate, deactivate -- never delete), and seeding of the first owner.

OTP delivery is email-only. An SMS leg existed here previously; it was removed
because the Twilio credentials were never configured and the dependency bought
nothing. Do not reintroduce it without also removing this note.

Delivery goes over HTTPS, not SMTP: Render blocks outbound SMTP on every port
(confirmed "Network is unreachable" on both 587 and 465), so smtplib can never
deliver from there. The SMTP_* settings survive in config.py for a possible
revert on a host that permits SMTP, but nothing in this module reads them.

Security notes:
  * The code is 6 digits from `secrets`, expires after config.OTP_TTL_SECONDS,
    and locks after config.OTP_MAX_ATTEMPTS wrong tries.
  * Only one active (unconsumed) code exists per email at a time.
  * Nothing a client can observe depends on whether an email belongs to an
    active admin: every request writes the same challenge row (admin_id NULL
    and nothing sent when there is no such admin), the reply is fixed, and the
    send happens off the request thread. See request_login_code().
  * With no password, the per-email throttles (config.OTP_RESEND_COOLDOWN_SECONDS,
    OTP_FAILURE_LIMIT per OTP_FAILURE_WINDOW_SECONDS) are what keep a 6-digit
    code from being brute-forced.
  * The session cookie is signed, not encrypted, so it holds only the pending
    email and, once signed in, the admin id. current_admin() re-reads the
    admin on every request, which is how deactivation ends a session at once.
  * The code is NEVER returned in any HTTP response. In dev it is printed to
    stdout so the flow can be exercised without a real mail server.
"""

from __future__ import annotations

import functools
import json
import secrets
import socket
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from flask import after_this_request, g, has_request_context, jsonify, session

from backend import config
from backend import database

# Resend's transactional-send endpoint. Plain HTTPS on 443, which is why we use
# it: Render blocks outbound SMTP entirely (confirmed "Network is unreachable"
# on both 587/STARTTLS and 465/implicit-TLS), so smtplib can never work there.
RESEND_ENDPOINT = "https://api.resend.com/emails"

# Hard cap on a single delivery attempt. Without an explicit timeout the socket
# inherits the platform default (effectively "never" on Linux), which would
# park the sending thread until gunicorn recycles the worker. Ten seconds is
# generous for an HTTPS API call and short enough that an unreachable endpoint
# fails fast and visibly.
EMAIL_TIMEOUT_SECONDS = 10

# Sent on the Resend call. Anything that names the app works; what matters is
# that it is NOT urllib's default "Python-urllib/3.x".
USER_AGENT = "FarmersForward/1.0"

# The one reply /api/auth/login gives for every well-formed email.
LOGIN_SENT_MESSAGE = "If this email is registered, a code was sent."

# `role` is free text so later roles need no migration; these two exist today.
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"

MAX_DISPLAY_NAME_LENGTH = 100

# Shared with init_db()'s migration, which can't import this module.
EMAIL_RE = database.EMAIL_RE
MAX_EMAIL_LENGTH = database.MAX_EMAIL_LENGTH
normalize_email = database.normalize_email
is_valid_email = database.is_valid_email
mask_email = database.mask_email

_ADMIN_COLUMNS = "id, email, display_name, role, is_active, created_at, last_login_at"

# First key of the two-key Postgres advisory lock that serializes login-code
# requests per email (the second key is a hash of the email). Two-key locks
# never collide with init_db()'s one-key lock: the key spaces don't overlap.
_LOGIN_LOCK_SPACE = 0x4646
# Serializes activation changes, so two owners deactivating each other at the
# same moment can't both succeed and leave nobody able to manage admins.
_ADMINS_LOCK_SPACE = 0x4647


class AdminExists(Exception):
    """An admin with that email is already registered."""


class LastOwnerError(Exception):
    """The change would leave no active owner."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def clean_display_name(value) -> Tuple[str, str]:
    """(name, error) for a display name: trimmed, 1-100 characters, no control
    characters. On success error is ''."""
    if not isinstance(value, str) or not value.strip():
        return "", "Display name is required"
    name = value.strip()
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        return "", "Display name must be %d characters or fewer" % MAX_DISPLAY_NAME_LENGTH
    if database.has_control_chars(name):
        return "", "Display name can't contain control characters"
    return name, ""


def parse_uuid(value) -> Optional[str]:
    """The canonical form of a uuid string, or None if `value` isn't one.

    Every admin id that reaches SQL goes through this first: Postgres rejects a
    malformed uuid literal with an error (a 500), and a session cookie from
    before the multi-admin migration still carries an integer id.
    """
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Admin records
# ---------------------------------------------------------------------------
def _record(row) -> Optional[Dict]:
    """An admins row as the JSON-ready dict the API and scripts use."""
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def admin_count() -> int:
    """Number of rows currently in the admins table."""
    row = database.query_one("SELECT COUNT(*) AS c FROM admins")
    return int(row["c"]) if row is not None else 0


def get_admin(admin_id) -> Optional[Dict]:
    """The admin with this id, active or not, or None."""
    admin_id = parse_uuid(admin_id)
    if admin_id is None:
        return None
    return _record(database.query_one(
        "SELECT %s FROM admins WHERE id = ?" % _ADMIN_COLUMNS, (admin_id,)))


def find_admin_by_email(email) -> Optional[Dict]:
    """The admin registered under this email, active or not, or None."""
    email = normalize_email(email)
    if not email:
        return None
    return _record(database.query_one(
        "SELECT %s FROM admins WHERE email = ?" % _ADMIN_COLUMNS, (email,)))


def list_admins() -> List[Dict]:
    """Every admin, active or not, oldest first."""
    rows = database.query_all(
        "SELECT %s FROM admins ORDER BY created_at, email" % _ADMIN_COLUMNS)
    return [_record(row) for row in rows]


def default_display_name(email: str) -> str:
    """The name a new owner gets when none is given: DEFAULT_ADMIN_NAME, else the
    part of the email before the @."""
    return (config.DEFAULT_ADMIN_NAME or "").strip() or email.partition("@")[0]


def create_admin(email, display_name, role: str = ROLE_ADMIN) -> Dict:
    """Register a new, active admin and return it.

    Raises ValueError (with a message fit for the API) for invalid input and
    AdminExists when the email is already registered -- in any state, since a
    deactivated admin is reactivated rather than re-added.
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("Please enter a valid email address")
    name, error = clean_display_name(display_name)
    if error:
        raise ValueError(error)
    if find_admin_by_email(email) is not None:
        raise AdminExists(email)

    admin_id = database.new_id()
    try:
        database.execute(
            "INSERT INTO admins (id, email, display_name, role, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (admin_id, email, name, role, True, database.now_iso()))
    except database.integrity_error():
        # Either a concurrent add of the same email won the race, or the
        # database's lower() disagrees with Python's for an unusual character
        # and the CHECK constraint refused it.
        if find_admin_by_email(email) is not None:
            raise AdminExists(email)
        raise ValueError("That email address can't be stored")
    return get_admin(admin_id)


def set_admin_active(admin_id, active: bool) -> Optional[Dict]:
    """Activate or deactivate an admin; returns it, or None if there is none.

    Admins are never deleted: a deactivated row keeps every reference to it
    valid. Deactivating also voids any login code still outstanding, and the
    admin's sessions die on their next request because current_admin() re-reads
    the row every time. It also stamps sessions_valid_after, so those sessions
    stay dead after a reactivation: if an account was deactivated because it
    was compromised, reactivating it must not hand a stolen cookie back.

    Raises LastOwnerError, changing nothing, if the deactivation would leave no
    active owner. The check runs after the UPDATE in the same transaction, and
    changes take turns (an advisory lock on Postgres; SQLite's write lock), so
    two owners deactivating each other at once can't both succeed.
    """
    admin_id = parse_uuid(admin_id)
    if admin_id is None:
        return None
    conn = database.get_db()
    try:
        with conn:
            if database.using_postgres():
                conn.execute("SELECT pg_advisory_xact_lock(?, 0)", (_ADMINS_LOCK_SPACE,))
            cur = conn.execute("UPDATE admins SET is_active = ? WHERE id = ?",
                               (bool(active), admin_id))
            if cur.rowcount == 0:
                return None
            if not active:
                owners = conn.execute(
                    "SELECT COUNT(*) AS n FROM admins WHERE role = ? AND is_active = ?",
                    (ROLE_OWNER, True)).fetchone()["n"]
                if owners == 0:
                    raise LastOwnerError()
                conn.execute("UPDATE admins SET sessions_valid_after = ? WHERE id = ?",
                             (database.now_iso(), admin_id))
                conn.execute("UPDATE otps SET consumed = 1 "
                             "WHERE admin_id = ? AND consumed = 0", (admin_id,))
    finally:
        conn.close()
    return get_admin(admin_id)


def ensure_owner(email, display_name: Optional[str] = None) -> Dict[str, object]:
    """Make `email` an active owner, creating the admin if there is none.

    The break-glass path behind backend/reset_admin.py. It only ever adds:
    nobody is deleted, deactivated or demoted. An existing admin with this
    email is promoted to owner and reactivated as needed (and renamed when
    `display_name` is given).

    Returns {'admin': record, 'created': bool, 'changes': [str]}; `changes`
    is empty when the email was already an active owner under that name.
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("Please enter a valid email address")
    name = None
    if display_name is not None:
        name, error = clean_display_name(display_name)
        if error:
            raise ValueError(error)

    existing = find_admin_by_email(email)
    if existing is None:
        record = create_admin(email, name or default_display_name(email), role=ROLE_OWNER)
        return {"admin": record, "created": True, "changes": []}

    sets, params, changes = [], [], []
    if existing["role"] != ROLE_OWNER:
        sets.append("role = ?")
        params.append(ROLE_OWNER)
        changes.append("role %s -> %s" % (existing["role"], ROLE_OWNER))
    if not existing["is_active"]:
        sets.append("is_active = ?")
        params.append(True)
        changes.append("reactivated")
    if name is not None and name != existing["display_name"]:
        sets.append("display_name = ?")
        params.append(name)
        changes.append("renamed %r -> %r" % (existing["display_name"], name))
    if sets:
        # The column list is built from the constants above, never from input.
        database.execute("UPDATE admins SET %s WHERE id = ?" % ", ".join(sets),
                         tuple(params) + (existing["id"],))
    return {"admin": get_admin(existing["id"]), "created": False, "changes": changes}


# ---------------------------------------------------------------------------
# The signed-in admin
# ---------------------------------------------------------------------------
def current_admin() -> Optional[Dict]:
    """The signed-in admin as {id, email, display_name, role}, or None.

    The admin is re-read on every request (and cached on flask.g for the rest
    of it), so a deactivated admin's session stops working on their very next
    request even though the cookie itself can't be revoked. A session that no
    longer maps to an active admin -- deactivated, signed in before a
    deactivation (sessions_valid_after), or an old cookie carrying the integer
    id from before the migration -- is signed out on the spot.

    Use this, never an id from the request, to say who did something
    (created_by / updated_by and the like).
    """
    if "ff_admin" in g:
        return g.ff_admin
    admin = None
    if session.get("authed") is True:
        admin_id = parse_uuid(session.get("admin_id"))
        if admin_id is not None:
            row = database.query_one(
                "SELECT id, email, display_name, role, sessions_valid_after FROM admins "
                "WHERE id = ? AND is_active = ?", (admin_id, True))
            if row is not None and _session_predates(row["sessions_valid_after"]):
                row = None
            if row is not None:
                admin = {"id": str(row["id"]), "email": row["email"],
                         "display_name": row["display_name"], "role": row["role"]}
        if admin is None:
            session.pop("authed", None)
            session.pop("admin_id", None)
    g.ff_admin = admin
    return admin


def start_session(admin_id: str) -> None:
    """Make the current session a signed-in session for `admin_id`.

    Starts from a clean session, so nothing from the login steps (or from a
    cookie that predates multi-admin) carries over. login_at is compared with
    the admin's sessions_valid_after on every request, so a deactivation ends
    this session for good, even across a reactivation.
    """
    session.clear()
    session["authed"] = True
    session["admin_id"] = admin_id
    session["login_at"] = database.now_iso()
    g.pop("ff_admin", None)


def _session_predates(cutoff) -> bool:
    """True if this session signed in before `cutoff` (or can't say when)."""
    if not cutoff:
        return False
    try:
        return _parse_iso(session.get("login_at")) < _parse_iso(cutoff)
    except (TypeError, ValueError):
        return True


def login_required(view):
    """Reject requests without a signed-in, still-active admin (401)."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if current_admin() is None:
            return jsonify({"error": "auth required"}), 401
        return view(*args, **kwargs)

    return wrapper


def owner_required(view):
    """Like login_required, and the admin must be an owner (else 403)."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        admin = current_admin()
        if admin is None:
            return jsonify({"error": "auth required"}), 401
        if admin["role"] != ROLE_OWNER:
            return jsonify({"error": "owner only"}), 403
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Login codes: generation, delivery, verification
# ---------------------------------------------------------------------------
def generate_otp() -> str:
    """Return a cryptographically-random 6-digit, zero-padded code."""
    return "%06d" % secrets.randbelow(1_000_000)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _email_configured() -> bool:
    return bool(config.RESEND_API_KEY)


def request_login_code(email: str) -> None:
    """Start a sign-in for `email`: issue a code, emailed only to an active admin.

    The caller answers every well-formed email with the same fixed message, and
    nothing here lets a client tell the cases apart: a challenge row is written
    whether or not `email` belongs to an active admin (admin_id NULL, and no
    email, when it doesn't), the throttles apply to every email alike, and the
    send runs on a background thread so it can't lengthen the response.

    Per-email throttles -- with no password, these are what keep a 6-digit code
    from being brute-forced by asking for fresh ones, and an admin's inbox (and
    the Resend quota every admin shares) from being flooded:
      * at most one new code per config.OTP_RESEND_COOLDOWN_SECONDS while the
        last one can still be used; a faster request is ignored and that code
        stays valid (a code already used, locked or expired doesn't block the
        next sign-in);
      * at most config.OTP_ISSUE_LIMIT_PER_HOUR codes per hour;
      * once the wrong guesses within config.OTP_FAILURE_WINDOW_SECONDS reach
        config.OTP_FAILURE_LIMIT, or those within a day reach
        config.OTP_FAILURE_LIMIT_PER_DAY, no new code until the window moves on.
    And one across all emails: at most config.OTP_GLOBAL_LIMIT_PER_HOUR codes
    an hour, because a flood of random addresses would otherwise write rows
    until the free-tier database fills. While it holds, nobody gets a new code
    -- which keeps registered and unknown emails indistinguishable even then.
    """
    now = _now_utc()
    code = generate_otp()
    conn = database.get_db()
    try:
        with conn:
            if database.using_postgres():
                # Requests for one email take turns, so the cooldown check
                # below can't be raced: a double-clicked "Send code" sends one
                # email, not two codes of which only the second works. SQLite
                # gets the same from the write lock its DELETE takes next.
                conn.execute("SELECT pg_advisory_xact_lock(?, hashtext(?))",
                             (_LOGIN_LOCK_SPACE, email))
            # Unknown emails write rows too, so old challenges are pruned here.
            conn.execute("DELETE FROM otps WHERE created_at < ?",
                         ((now - timedelta(days=1)).isoformat(),))
            latest = conn.execute(
                "SELECT created_at, expires_at, attempts, consumed FROM otps "
                "WHERE email = ? ORDER BY created_at DESC LIMIT 1", (email,)).fetchone()
            cooldown = timedelta(seconds=config.OTP_RESEND_COOLDOWN_SECONDS)
            usable = (latest is not None and not latest["consumed"]
                      and latest["attempts"] < config.OTP_MAX_ATTEMPTS
                      and now <= _parse_iso(latest["expires_at"]))
            if usable and now - _parse_iso(latest["created_at"]) < cooldown:
                print("[auth] login code for %s not reissued: one was sent less "
                      "than %ds ago." % (mask_email(email),
                                         config.OTP_RESEND_COOLDOWN_SECONDS), flush=True)
                return
            hour_ago = (now - timedelta(hours=1)).isoformat()
            issued = conn.execute(
                "SELECT COUNT(*) AS n FROM otps WHERE email = ? AND created_at >= ?",
                (email, hour_ago)).fetchone()["n"]
            if issued >= config.OTP_ISSUE_LIMIT_PER_HOUR:
                print("[auth] login code for %s not issued: %d codes in the last "
                      "hour." % (mask_email(email), issued), flush=True)
                return
            window_start = now - timedelta(seconds=config.OTP_FAILURE_WINDOW_SECONDS)
            failures = conn.execute(
                "SELECT COALESCE(SUM(attempts), 0) AS n FROM otps "
                "WHERE email = ? AND created_at >= ?",
                (email, window_start.isoformat())).fetchone()["n"]
            if failures >= config.OTP_FAILURE_LIMIT:
                print("[auth] login code for %s not issued: %d wrong guesses in "
                      "the last %ds." % (mask_email(email), failures,
                                         config.OTP_FAILURE_WINDOW_SECONDS), flush=True)
                return
            # A slow trickle under the hourly limit is capped too. Rows live a day
            # (the prune above), so this is a rolling 24 hours.
            day_failures = conn.execute(
                "SELECT COALESCE(SUM(attempts), 0) AS n FROM otps WHERE email = ?",
                (email,)).fetchone()["n"]
            if day_failures >= config.OTP_FAILURE_LIMIT_PER_DAY:
                print("[auth] login code for %s not issued: %d wrong guesses in "
                      "the last day." % (mask_email(email), day_failures), flush=True)
                return
            everyone = conn.execute("SELECT COUNT(*) AS n FROM otps WHERE created_at >= ?",
                                    (hour_ago,)).fetchone()["n"]
            if everyone >= config.OTP_GLOBAL_LIMIT_PER_HOUR:
                print("[auth] WARNING: login code for %s not issued: %d codes went "
                      "out in the last hour across all emails (limit %d). Someone "
                      "may be flooding /api/auth/login." % (
                          mask_email(email), everyone, config.OTP_GLOBAL_LIMIT_PER_HOUR),
                      flush=True)
                return

            admin = conn.execute("SELECT id FROM admins WHERE email = ? AND is_active = ?",
                                 (email, True)).fetchone()
            admin_id = str(admin["id"]) if admin is not None else None
            # Only one live code per email.
            conn.execute("UPDATE otps SET consumed = 1 WHERE email = ? AND consumed = 0",
                         (email,))
            conn.execute(
                "INSERT INTO otps (id, email, admin_id, code, expires_at, attempts, "
                "consumed, created_at) VALUES (?, ?, ?, ?, ?, 0, 0, ?)",
                (database.new_id(), email, admin_id, code,
                 (now + timedelta(seconds=config.OTP_TTL_SECONDS)).isoformat(),
                 now.isoformat()))
    finally:
        conn.close()

    if admin_id is None:
        print("[auth] login code requested for %s, which is not an active admin; "
              "nothing was sent." % mask_email(email), flush=True)
        return
    _deliver(email, code)


def _deliver(email: str, code: str) -> None:
    """Send the code without holding up the HTTP response.

    A send takes hundreds of milliseconds and only ever happens for a real
    admin, so doing it inline would let response time reveal which emails are
    registered. Even a background thread started during the request competes
    for the GIL (TLS setup, JSON) and shows up as a few milliseconds of extra
    latency, so the thread starts only once the response has been written: the
    WSGI server closes the response after sending it, which runs call_on_close.
    A daemon thread is enough: _send_email never raises, and a code lost to a
    worker restart only means asking for another one.
    """
    if not _email_configured():
        # Dev fallback: print the code ONLY when email is unconfigured, so the
        # flow is testable locally but the code never reaches production logs.
        print("[DEV] OTP for %s: %s" % (email, code), flush=True)
        return

    def start():
        threading.Thread(target=_send_email, args=(email, code),
                         name="otp-email", daemon=True).start()

    if not has_request_context():
        start()
        return

    @after_this_request
    def send_after_response(response):
        response.call_on_close(start)
        return response


def verify_login_code(email: str, code: str) -> Tuple[str, Optional[str]]:
    """Check a submitted code against the live challenge for `email`.

    Returns (result, admin_id): result is one of 'ok', 'invalid', 'expired',
    'locked', and admin_id is set only for 'ok' (when the admin's
    last_login_at is also stamped). A challenge for an email with no active
    admin behaves exactly like a real one -- it counts attempts, locks and
    expires -- but can never succeed, even with the right digits.
    """
    now = _now_utc()
    submitted = (code or "").strip()
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT id, admin_id, code, expires_at, attempts FROM otps "
            "WHERE email = ? AND consumed = 0 ORDER BY created_at DESC LIMIT 1",
            (email,)).fetchone()
        if row is None:
            return "expired", None

        # Expiry takes precedence over the attempt cap (BUILD_CONTRACT §2.4).
        if now > _parse_iso(row["expires_at"]):
            return "expired", None

        # Claim an attempt BEFORE comparing, in one conditional UPDATE: guesses
        # racing on the same code then can't all be compared against a code
        # that should already have locked. No slot left -> locked.
        claim = conn.execute(
            "UPDATE otps SET attempts = attempts + 1 "
            "WHERE id = ? AND consumed = 0 AND attempts < ?",
            (row["id"], config.OTP_MAX_ATTEMPTS))
        conn.commit()
        if claim.rowcount != 1:
            return "locked", None

        # On bytes: compare_digest raises for a str with non-ASCII characters.
        matched = secrets.compare_digest(submitted.encode("utf-8"),
                                         row["code"].encode("utf-8"))
        admin_id = str(row["admin_id"]) if row["admin_id"] is not None else None
        if matched and admin_id is not None:
            active = conn.execute("SELECT id FROM admins WHERE id = ? AND is_active = ?",
                                  (admin_id, True)).fetchone()
            if active is not None:
                # Consume only if still live, so two racing correct submissions
                # can't both sign in; the claimed attempt is handed back, so a
                # sign-in never counts as a wrong guess.
                cur = conn.execute("UPDATE otps SET consumed = 1, attempts = attempts - 1 "
                                   "WHERE id = ? AND consumed = 0", (row["id"],))
                if cur.rowcount == 1:
                    conn.execute("UPDATE admins SET last_login_at = ? WHERE id = ?",
                                 (now.isoformat(), admin_id))
                    conn.commit()
                    return "ok", admin_id
                conn.commit()
                return "expired", None

        # Wrong code -- or a right one with no active admin behind it, which has
        # to look exactly the same. The attempt was already counted above.
        attempts = conn.execute("SELECT attempts FROM otps WHERE id = ?",
                                (row["id"],)).fetchone()["attempts"]
        if attempts >= config.OTP_MAX_ATTEMPTS:
            return "locked", None
        return "invalid", None
    finally:
        conn.close()


def attempts_remaining(email: str) -> int:
    """How many attempts are left on the live code for `email` (>= 0)."""
    row = database.query_one(
        "SELECT attempts FROM otps WHERE email = ? AND consumed = 0 "
        "ORDER BY created_at DESC LIMIT 1", (email,))
    if row is None:
        return 0
    return max(0, config.OTP_MAX_ATTEMPTS - row["attempts"])


# ---------------------------------------------------------------------------
# Email delivery (Resend)
# ---------------------------------------------------------------------------
def _response_detail(exc: urllib.error.HTTPError) -> str:
    """Best-effort one-line error body from a failed Resend call, for logging."""
    try:
        return exc.read().decode("utf-8", "replace").strip().replace("\n", " ")[:300]
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return "<body unavailable>"


def _log_email_failure(kind: str, email: str, exc: BaseException,
                       delivered: bool) -> None:
    """Log a delivery failure loudly enough to diagnose it from the Render logs.

    Never logs the OTP itself, and never logs RESEND_API_KEY. `delivered`
    distinguishes "Resend never took the message" from "Resend took it and the
    response could not be read afterwards", which is NOT a delivery failure and
    must not be reported as one.
    """
    if delivered:
        print("[auth] Email OTP to %s was ACCEPTED by %s, but the response "
              "could not be read (%s: %s). Treating as delivered."
              % (mask_email(email), RESEND_ENDPOINT, type(exc).__name__, exc), flush=True)
        return
    print("[auth] Email OTP to %s FAILED - %s talking to %s (%s: %s). The login "
          "request got its usual answer, but the admin will NOT receive a code."
          % (mask_email(email), kind, RESEND_ENDPOINT, type(exc).__name__, exc), flush=True)


def _send_email(email: str, code: str) -> bool:
    """Send the OTP by email through the Resend HTTPS API.

    Uses urllib from the stdlib rather than the `resend` SDK: one less package
    to install on the host, no import-time failure mode, and the timeout stays
    under our control. The request is a plain POST to RESEND_ENDPOINT over 443.

    Returns True only when Resend accepted the message. Every failure is caught
    and logged rather than raised: it runs on a background thread (see
    _deliver()), where an exception would only vanish into stderr.
    """
    if not _email_configured():
        print("[auth] Email OTP to %s SKIPPED - RESEND_API_KEY is not set, so "
              "no delivery was attempted. Set it to enable login emails."
              % mask_email(email), flush=True)
        return False
    if not email:
        return False

    # Set once Resend has accepted the message, so a failure while reading the
    # response afterwards is not miscounted as a failed send.
    delivered = False
    try:
        body = ("Your Farmers Forward verification code is %s.\n\n"
                "It expires in %d minutes. If you did not request this, you can "
                "ignore this message." % (code, max(1, config.OTP_TTL_SECONDS // 60)))
        payload = json.dumps({
            "from": config.RESEND_FROM,
            "to": [email],
            "subject": "Farmers Forward verification code",
            "text": body,
        }).encode("utf-8")

        request = urllib.request.Request(
            RESEND_ENDPOINT,
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer %s" % config.RESEND_API_KEY,
                "Content-Type": "application/json",
                # Identify the app explicitly. urllib otherwise sends
                # "Python-urllib/3.x", a known bot signature that Cloudflare
                # rejects in front of the Resend API with error 1010 (HTTP 403).
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(request,
                                    timeout=EMAIL_TIMEOUT_SECONDS) as response:
            # urlopen raises HTTPError on any 4xx/5xx, so reaching this point
            # means Resend already took the message. Record that BEFORE reading
            # the body: a read failure afterwards cannot un-send the email.
            delivered = True
            raw = response.read()

        message_id = json.loads(raw.decode("utf-8")).get("id") or "unknown"
        print("[auth] Email OTP to %s accepted by Resend (id=%s)."
              % (mask_email(email), message_id), flush=True)
        return True
    # socket.timeout, HTTPError and URLError are all OSError subclasses, so they
    # must be caught before the broader OSError arm below.
    except socket.timeout as exc:
        _log_email_failure("TIMED OUT after %ds" % EMAIL_TIMEOUT_SECONDS,
                           email, exc, delivered)
    except urllib.error.HTTPError as exc:
        # Resend puts the real reason in the body ("domain not verified",
        # "invalid api key", the test-sender recipient restriction, ...).
        _log_email_failure("HTTP %s from the Resend API: %s"
                           % (exc.code, _response_detail(exc)),
                           email, exc, delivered)
    except urllib.error.URLError as exc:
        # DNS failure, TLS failure, network unreachable, connection refused.
        _log_email_failure("network error (%s)" % exc.reason, email, exc, delivered)
    except OSError as exc:
        _log_email_failure("network error", email, exc, delivered)
    except Exception as exc:  # noqa: BLE001 - backstop: never raise from the sender
        _log_email_failure("unexpected error", email, exc, delivered)
    return delivered


def describe_delivery() -> str:
    """One line saying how login codes reach admins. Never includes the key."""
    if not _email_configured():
        return "NOT configured (codes are printed to this log)"
    return "Resend from %s" % config.RESEND_FROM


def delivery_warnings() -> List[str]:
    """Configuration that will stop codes reaching (some) admins."""
    warnings = []
    if _email_configured() and config.RESEND_FROM == config.RESEND_TEST_SENDER:
        warnings.append(
            "RESEND_FROM is Resend's shared test sender (%s), which only "
            "delivers to the Resend account owner's own inbox. Verify a domain "
            "in Resend and set RESEND_FROM to an address on it, or other admins "
            "will never receive a code." % config.RESEND_TEST_SENDER)
    return warnings


# ---------------------------------------------------------------------------
# Seeding + startup report
# ---------------------------------------------------------------------------
def seed_owner() -> Dict[str, object]:
    """Create the first owner from DEFAULT_ADMIN_EMAIL -- only into an EMPTY table.

    A fresh database (local dev, a new Supabase project) gets its owner this
    way; everyone after that is added from the Admins page. This is a *seed*,
    not an upsert: once any admin exists it returns immediately. With
    DEFAULT_ADMIN_EMAIL unset nothing is created, and the startup report says
    how to recover (backend/reset_admin.py).

    Returns {'created': bool, 'email': str|None}.
    """
    if admin_count() > 0:
        return {"created": False, "email": None}
    email = normalize_email(config.DEFAULT_ADMIN_EMAIL)
    if not email:
        return {"created": False, "email": None}
    if not is_valid_email(email):
        print("[admin-auth] WARNING: DEFAULT_ADMIN_EMAIL is not a valid email "
              "address, so no owner was seeded.", flush=True)
        return {"created": False, "email": None}
    # A name that can't be stored (too long, a pasted tab) must not crash-loop a
    # fresh deploy: fall back to the email's local part, then to "Owner".
    name, error = clean_display_name(default_display_name(email))
    if error:
        name = clean_display_name(email.partition("@")[0][:MAX_DISPLAY_NAME_LENGTH])[0] or "Owner"
        print("[admin-auth] WARNING: the owner's display name from DEFAULT_ADMIN_NAME "
              "can't be used (%s); using %r instead." % (error, name), flush=True)
    try:
        create_admin(email, name, role=ROLE_OWNER)
    except AdminExists:
        # Lost the check-then-insert race: gunicorn's workers all import app.py
        # at once, so on an empty table several can pass the admin_count() test
        # before any of them inserts. The account exists, which is the point.
        return {"created": False, "email": None}

    banner = "=" * 70
    print(banner, flush=True)
    print("  Farmers Forward - first owner created", flush=True)
    print("  email: %s (from DEFAULT_ADMIN_EMAIL)" % mask_email(email), flush=True)
    print("  Sign in with that address; the one-time code arrives by email.", flush=True)
    print(banner, flush=True)
    return {"created": True, "email": email}


def log_admin_status(db_existed_at_boot: Optional[bool] = None) -> None:
    """Print, on every boot, who can sign in and whether codes can reach them.

    Called after seed_owner(), so it describes the state the app will actually
    serve. Never prints a full email address or any key.
    """
    admins = list_admins()
    active = [admin for admin in admins if admin["is_active"]]
    owners = [admin for admin in active if admin["role"] == ROLE_OWNER]
    bar = "-" * 70

    def _line(text: str) -> None:
        print("[admin-auth] %s" % text, flush=True)

    print(bar, flush=True)
    _line("startup admin report")
    _line("  database: %s" % database.describe_backend())
    if db_existed_at_boot is not None:
        _line("  database existed before this boot: %s"
              % ("yes" if db_existed_at_boot
                 else "NO - fresh/empty disk, everything is being re-created"))
    _line("  admins: %d row(s), %d active; owner(s): %s"
          % (len(admins), len(active),
             ", ".join(mask_email(owner["email"]) for owner in owners) or "none"))
    _line("  DEFAULT_ADMIN_EMAIL: %s"
          % ("set (from %s)" % config.env_source("DEFAULT_ADMIN_EMAIL")
             if config.DEFAULT_ADMIN_EMAIL else "unset"))
    _line("  OTP email: %s" % describe_delivery())
    for warning in delivery_warnings():
        _line("  WARNING: %s" % warning)
    if not owners:
        _line("  WARNING: no active owner, so nobody can manage admins. Fix with:")
        _line("    python3 -m backend.reset_admin --email you@example.com")
        if not admins:
            _line("    (or set DEFAULT_ADMIN_EMAIL and restart: an empty admins")
            _line("    table is seeded from it)")
    print(bar, flush=True)
