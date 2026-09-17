"""Authentication + OTP for Farmers Forward.

Contains bcrypt password hashing, one-time-passcode generation/verification,
email delivery via the Resend HTTPS API (best-effort, gated on credentials),
the `login_required` decorator, and default-admin seeding.

OTP delivery is email-only. An SMS leg existed here previously; it was removed
because the Twilio credentials were never configured and the dependency bought
nothing. Do not reintroduce it without also removing this note.

Delivery goes over HTTPS, not SMTP: Render blocks outbound SMTP on every port
(confirmed "Network is unreachable" on both 587 and 465), so smtplib can never
deliver from there. The SMTP_* settings survive in config.py for a possible
revert on a host that permits SMTP, but nothing in this module reads them.

Security notes:
  * The OTP is 6 digits from `secrets`, expires after config.OTP_TTL_SECONDS,
    and locks after config.OTP_MAX_ATTEMPTS wrong tries.
  * Only one active (unconsumed) OTP exists per admin at a time.
  * The code is NEVER returned in any HTTP response. In dev it is printed to
    stdout so the flow can be exercised without a real mail server.
"""

from __future__ import annotations

import functools
import json
import secrets
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import bcrypt
from flask import jsonify, session

from backend import config
from backend import database

# Resend's transactional-send endpoint. Plain HTTPS on 443, which is why we use
# it: Render blocks outbound SMTP entirely (confirmed "Network is unreachable"
# on both 587/STARTTLS and 465/implicit-TLS), so smtplib can never work there.
RESEND_ENDPOINT = "https://api.resend.com/emails"

# Hard cap on a single delivery attempt. Without an explicit timeout the socket
# inherits the platform default (effectively "never" on Linux), which would park
# the whole /api/auth/login request until gunicorn kills the worker and the
# browser sees a 500. Ten seconds is generous for an HTTPS API call and short
# enough that an unreachable endpoint fails fast and visibly.
EMAIL_TIMEOUT_SECONDS = 10

# Sent on the Resend call. Anything that names the app works; what matters is
# that it is NOT urllib's default "Python-urllib/3.x".
USER_AGENT = "FarmersForward/1.0"


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(pw: str) -> str:
    """Hash a plaintext password with bcrypt; return the utf-8 decoded hash."""
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(pw: str, hashed: str) -> bool:
    """Constant-time verify of a plaintext password against a stored hash."""
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# OTP generation / delivery
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
              % (email, RESEND_ENDPOINT, type(exc).__name__, exc), flush=True)
        return
    print("[auth] Email OTP to %s FAILED - %s talking to %s (%s: %s). Login "
          "still succeeded, but the user will NOT receive a code."
          % (email, kind, RESEND_ENDPOINT, type(exc).__name__, exc), flush=True)


def _send_email(email: str, code: str) -> bool:
    """Send the OTP by email through the Resend HTTPS API.

    Uses urllib from the stdlib rather than the `resend` SDK: one less package
    to install on the host, no import-time failure mode, and the timeout stays
    under our control. The request is a plain POST to RESEND_ENDPOINT over 443.

    Returns True only when Resend accepted the message. Every failure is caught
    and logged rather than raised: OTP delivery is best-effort and must never
    turn a valid login into a 500.
    """
    if not _email_configured():
        print("[auth] Email OTP to %s SKIPPED - RESEND_API_KEY is not set, so "
              "no delivery was attempted. Set it to enable login emails."
              % email, flush=True)
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
              % (email, message_id), flush=True)
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
    except Exception as exc:  # noqa: BLE001 - backstop: login must never 500
        _log_email_failure("unexpected error", email, exc, delivered)
    return delivered


def create_and_send_otp(admin_row) -> Dict[str, bool]:
    """Create a fresh OTP for an admin, invalidate prior ones, and deliver it.

    Delivery is email-only. Returns {'email': bool} indicating whether the send
    succeeded. The code is NEVER returned to the caller/HTTP client. It is
    printed to stdout ONLY when SMTP is unconfigured, so a genuine dev setup
    stays testable while production never leaks the OTP to logs.
    """
    admin_id = admin_row["id"]
    username = admin_row["username"]

    code = generate_otp()
    created = _now_utc()
    expires = created + timedelta(seconds=config.OTP_TTL_SECONDS)

    conn = database.get_db()
    try:
        # Invalidate any prior unconsumed OTPs so only one is ever active.
        conn.execute("UPDATE otps SET consumed = 1 WHERE admin_id = ? AND consumed = 0",
                     (admin_id,))
        conn.execute(
            "INSERT INTO otps (admin_id, code, expires_at, attempts, consumed, "
            "created_at) VALUES (?, ?, ?, 0, 0, ?)",
            (admin_id, code, expires.isoformat(), created.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    email_ok = _send_email(admin_row["email"], code)

    # Dev fallback: print the code ONLY when SMTP is unconfigured, so the flow is
    # testable locally but the OTP never reaches production logs.
    if not _email_configured():
        print("[DEV] OTP for %s: %s" % (username, code), flush=True)

    return {"email": email_ok}


def verify_otp(admin_id: int, code: str) -> str:
    """Verify a submitted OTP for an admin.

    Returns one of: 'ok', 'invalid', 'expired', 'locked'.
    """
    conn = database.get_db()
    try:
        row = conn.execute(
            "SELECT * FROM otps WHERE admin_id = ? AND consumed = 0 "
            "ORDER BY id DESC LIMIT 1",
            (admin_id,),
        ).fetchone()

        if row is None:
            return "expired"

        # Expiry takes precedence over the attempt cap (BUILD_CONTRACT §2.4).
        if _now_utc() > _parse_iso(row["expires_at"]):
            return "expired"

        # Already at/over the attempt cap -> locked.
        if row["attempts"] >= config.OTP_MAX_ATTEMPTS:
            return "locked"

        # Correct code -> consume it.
        submitted = (code or "").strip()
        if secrets.compare_digest(submitted, row["code"]):
            conn.execute("UPDATE otps SET consumed = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return "ok"

        # Wrong code -> increment attempts, possibly lock.
        new_attempts = row["attempts"] + 1
        conn.execute("UPDATE otps SET attempts = ? WHERE id = ?",
                     (new_attempts, row["id"]))
        conn.commit()
        if new_attempts >= config.OTP_MAX_ATTEMPTS:
            return "locked"
        return "invalid"
    finally:
        conn.close()


def attempts_remaining(admin_id: int) -> int:
    """How many OTP attempts are left for the active code (>= 0)."""
    row = database.query_one(
        "SELECT attempts FROM otps WHERE admin_id = ? AND consumed = 0 "
        "ORDER BY id DESC LIMIT 1",
        (admin_id,),
    )
    if row is None:
        return 0
    return max(0, config.OTP_MAX_ATTEMPTS - row["attempts"])


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def admin_count() -> int:
    """Number of rows currently in the admins table."""
    row = database.query_one("SELECT COUNT(*) AS c FROM admins")
    return int(row["c"]) if row is not None else 0


def describe_admin_password_source() -> str:
    """One line saying where the seeded password will come from.

    Reports only the origin and length -- never the password itself.
    """
    if config.DEFAULT_ADMIN_PASSWORD:
        return ("FOUND in %s (%d chars) - it is used verbatim, nothing random "
                "is generated" % (config.env_source("DEFAULT_ADMIN_PASSWORD"),
                                  len(config.DEFAULT_ADMIN_PASSWORD)))
    return ("%s - a RANDOM 12-char password will be generated and printed ONCE"
            % config.env_source("DEFAULT_ADMIN_PASSWORD"))


def log_admin_credential_status(db_existed_at_boot: Optional[bool] = None) -> None:
    """Print, on every boot, where the admin credentials come from.

    Deliberately unconditional and called BEFORE seed_default_admin(): seeding
    is a no-op once the admins table has rows, so without this the single most
    common failure mode ("I set DEFAULT_ADMIN_PASSWORD but login still fails")
    produced no log output at all. Never prints the password.
    """
    n_admins = admin_count()
    bar = "-" * 70
    def _line(text: str) -> None:
        print("[admin-auth] %s" % text, flush=True)

    print(bar, flush=True)
    _line("startup credential report")
    _line("  database: %s" % database.describe_backend())
    if db_existed_at_boot is not None:
        _line("  database existed before this boot: %s"
              % ("yes" if db_existed_at_boot
                 else "NO - fresh/empty disk, everything is being re-created"))
    elif database.using_postgres():
        _line("  persistence: Postgres, so rows survive a deploy")
    _line("  .env file: %s" % (config.DOTENV_PATH if config.DOTENV_LOADED
                               else "not found (process environment only)"))
    _line("  DEFAULT_ADMIN_USERNAME: %r (from %s)"
          % (config.DEFAULT_ADMIN_USERNAME,
             config.env_source("DEFAULT_ADMIN_USERNAME")))
    _line("  DEFAULT_ADMIN_PASSWORD: %s" % describe_admin_password_source())
    _line("  admins table: %d row(s)" % n_admins)

    if n_admins > 0:
        _line("  -> Admins already exist, so seeding is a NO-OP on this boot.")
        _line("     The stored password is whatever was in effect when that row")
        _line("     was first created. Setting DEFAULT_ADMIN_PASSWORD now does")
        _line("     NOT change it. To rotate the credentials, run:")
        _line("       python3 -m backend.reset_admin --yes")
    elif config.DEFAULT_ADMIN_PASSWORD:
        _line("  -> No admins yet: one will be seeded with the env password.")
    else:
        _line("  -> No admins yet: one will be seeded with a RANDOM password,")
        _line("     printed once in the banner below. Set DEFAULT_ADMIN_PASSWORD")
        _line("     and run `python3 -m backend.reset_admin --yes` to choose it.")
    print(bar, flush=True)


def seed_default_admin(username: Optional[str] = None,
                       password: Optional[str] = None) -> Dict[str, object]:
    """Create the default admin if -- and only if -- the admins table is empty.

    `username`/`password` override config.DEFAULT_ADMIN_USERNAME/_PASSWORD and
    exist for backend/reset_admin.py; both fall back to the configured values.
    If no password is available a random 12-char one is generated and printed
    once in the banner below.

    This is a *seed*, not an upsert: when the table already has rows it returns
    immediately without touching them. Use backend/reset_admin.py to rotate an
    existing account.

    Returns {'created': bool, 'username': str|None, 'generated': bool}.
    """
    if admin_count() > 0:
        return {"created": False, "username": None, "generated": False}

    # Remember which of the three sources won, so the banner below tells the
    # truth rather than always crediting the environment.
    supplied = bool(password)
    username = username or config.DEFAULT_ADMIN_USERNAME
    password = password or config.DEFAULT_ADMIN_PASSWORD
    generated = False
    if not password:
        password = secrets.token_urlsafe(9)[:12]
        generated = True

    try:
        database.execute(
            "INSERT INTO admins (username, password_hash, phone, email, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), config.DEFAULT_ADMIN_PHONE,
             config.DEFAULT_ADMIN_EMAIL, database.now_iso()),
        )
    except database.integrity_error():
        # Lost the check-then-insert race: gunicorn's workers all import app.py
        # at once, so on a fresh disk several can pass the admin_count() == 0
        # test before any of them inserts. admins.username is UNIQUE, so the
        # losers land here. That is a successful outcome (the account exists) --
        # do not take down the worker's boot over it.
        print("[admin-auth] admin %r was seeded concurrently by another worker; "
              "skipping." % username, flush=True)
        return {"created": False, "username": None, "generated": False}

    banner = "=" * 70
    print(banner, flush=True)
    print("  Farmers Forward - admin account created", flush=True)
    print("  username: %s" % username, flush=True)
    if generated:
        print("  password: %s   (RANDOM - save it now, shown once)" % password,
              flush=True)
        print("  Set DEFAULT_ADMIN_PASSWORD in the environment and run", flush=True)
        print("  `python3 -m backend.reset_admin --yes` to replace it with one",
              flush=True)
        print("  you choose (this survives redeploys; a random one does not).",
              flush=True)
    elif supplied:
        print("  password: (the value passed to this command)", flush=True)
    else:
        print("  password: (the DEFAULT_ADMIN_PASSWORD value from %s)"
              % config.env_source("DEFAULT_ADMIN_PASSWORD"), flush=True)
    print(banner, flush=True)

    return {"created": True, "username": username, "generated": generated}


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------
def login_required(view):
    """Reject requests that are not fully authenticated (authed + admin_id)."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if session.get("authed") is True and session.get("admin_id"):
            return view(*args, **kwargs)
        return jsonify({"error": "auth required"}), 401

    return wrapper
