"""Central configuration for the Farmers Forward backend.

Loads a `.env` from the repository root (if present) via python-dotenv and
exposes every setting the rest of the backend reads. All paths are computed as
absolutes so the app works regardless of the current working directory.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# `here` is the backend/ directory; the repo root is one level up.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, ".."))

# Load environment variables from the repo-root .env (no error if it is absent).
# The process environment is snapshotted FIRST so `env_source()` can tell a value
# that came from the real environment (e.g. Render's dashboard) apart from one
# that came out of the .env file -- the startup credential banner reports this.
DOTENV_PATH = os.path.join(REPO_ROOT, ".env")
_PROCESS_ENV_KEYS = frozenset(os.environ)
DOTENV_LOADED = bool(load_dotenv(DOTENV_PATH))


def _get(name: str, default=None):
    """Return an env var, treating empty strings as unset."""
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


def env_source(name: str) -> str:
    """Describe where `name` was read from, for logging. Never returns a value.

    One of: 'the process environment', 'the .env file', 'set but EMPTY (treated
    as unset)', or 'unset'. Note that python-dotenv does not override variables
    already present in the process environment, so the real environment wins.
    """
    raw = os.environ.get(name)
    if raw is None:
        return "unset"
    if raw == "":
        return "set but EMPTY (treated as unset)"
    return "the process environment" if name in _PROCESS_ENV_KEYS else "the .env file"


def _get_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_list(name: str, default) -> list:
    """Parse a comma-separated env var into a list, ignoring blank entries."""
    raw = _get(name, None)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _get_bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """An int setting that must lie in [minimum, maximum].

    Anything else -- '60s', '5.0', 0 read as "unlimited", a huge value that
    would overflow a timedelta -- is reported and replaced by the default. A
    loud warning, not a crash: a typo in the dashboard must not take the site
    down, but it must not silently lock every admin out either.
    """
    raw = _get(name, None)
    if raw is None:
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        val = None
    if val is None or not minimum <= val <= maximum:
        print("[config] WARNING: %s=%r is not a whole number from %d to %d; "
              "using the default %d." % (name, raw, minimum, maximum, default), flush=True)
        return default
    return val


# ---------------------------------------------------------------------------
# Security / app
# ---------------------------------------------------------------------------
# SECRET_KEY signs every session cookie, so it comes from the environment and
# nowhere else: there is no built-in fallback, and require_secret_key() stops
# the app from booting without a usable one. A fallback is worse than a crash.
# This repo is public, so a hardcoded key is a key anyone can sign an admin
# session with, and a randomly generated one silently logs everybody out on
# every restart.
SECRET_KEY = _get("SECRET_KEY", None)
# Anyone who knows the passcode can get a signed cookie from verify-code and
# brute-force a weak key offline, so short keys are refused too, as are
# degenerate ones ("x" * 40) -- a random 32+ character key never has this few
# distinct characters.
SECRET_KEY_MIN_LENGTH = 32
SECRET_KEY_MIN_DISTINCT = 8
# The value this file used to fall back to. It is in the public git history,
# so it is refused like an unset key.
_OLD_PUBLIC_SECRET_KEY = "dev-insecure-change-me"

# The hidden staff-access passcode. It is entered invisibly (no input field) on
# the public page, so it is necessarily embedded in the frontend bundle too --
# it is an obscurity gate, NOT a real secret. Actual security is the one-time
# code emailed to a registered admin. If you change it, change SEQ in
# frontend/src/pages/PublicHome.jsx to match.
ADMIN_SECRET_CODE = _get("ADMIN_SECRET_CODE", "202611")

# Deployment environment. Parsed for completeness, but nothing branches on it:
# the SECRET_KEY check above applies everywhere, and the remaining insecure
# defaults (see insecure_defaults()) are logged as warnings in every mode.
APP_ENV = (_get("APP_ENV", "development") or "development").lower()
IS_PRODUCTION = APP_ENV == "production"

# ---------------------------------------------------------------------------
# Resend (email) -- the only OTP delivery channel.
# ---------------------------------------------------------------------------
# Delivery goes over the Resend HTTPS API because Render blocks outbound SMTP on
# every port. RESEND_API_KEY is the only value required for login emails to work.
RESEND_API_KEY = _get("RESEND_API_KEY", None)
# Resend's shared test sender, which needs no domain verification but can only
# deliver to the address that owns the Resend account. Point this at an address
# on your own verified domain to send anywhere -- with more than one admin that
# is required, or only the account owner ever receives a login code.
RESEND_TEST_SENDER = "onboarding@resend.dev"
RESEND_FROM = _get("RESEND_FROM", RESEND_TEST_SENDER)

# ---------------------------------------------------------------------------
# SMTP (email) -- UNUSED, kept only to make a revert easy
# ---------------------------------------------------------------------------
# Nothing reads these any more; backend/auth.py sends via Resend. They are kept
# so SMTP delivery can be restored on a host that permits outbound SMTP (Render
# does not: "Network is unreachable" on both 587 and 465).
SMTP_HOST = _get("SMTP_HOST", None)
SMTP_PORT = _get_int("SMTP_PORT", 587)
SMTP_USER = _get("SMTP_USER", None)
SMTP_PASSWORD = _get("SMTP_PASSWORD", None)
SMTP_FROM = _get("SMTP_FROM", SMTP_USER)

# ---------------------------------------------------------------------------
# Filesystem locations
# ---------------------------------------------------------------------------
# Postgres connection URI (Supabase). When set, it is the database; the SQLite
# file below is then unused. Unset falls back to SQLite so local development and
# the test suite need no live database. See backend/README.md.
DATABASE_URL = _get("DATABASE_URL", None)

# SQLite fallback. Render's free tier has no persistent disk, so this file is
# recreated empty on every deploy -- which is exactly why DATABASE_URL exists.
DATABASE_PATH = os.path.join(HERE, "farmersforward.db")
OUTPUTS_DIR = os.path.join(REPO_ROOT, "outputs")
MODEL_DIR = os.path.join(HERE, "FarmersForwardModel")

# ---------------------------------------------------------------------------
# First owner (seeded only into an EMPTY admins table)
# ---------------------------------------------------------------------------
# Login is by emailed code, so an owner is just an email address. There is no
# built-in default: a placeholder address would create an owner nobody can sign
# in as, and block the real seed because the table is no longer empty. Every
# other admin is added from the Admins page.
DEFAULT_ADMIN_EMAIL = _get("DEFAULT_ADMIN_EMAIL", None)
# Display name for that seeded owner, and for the owner created by the one-time
# migration from the old single-admin table (which otherwise keeps the old
# username, e.g. "admin").
DEFAULT_ADMIN_NAME = _get("DEFAULT_ADMIN_NAME", None)

# ---------------------------------------------------------------------------
# Session / CORS
# ---------------------------------------------------------------------------
SESSION_COOKIE_SECURE = _get_bool("COOKIE_SECURE", False)

# Every browser origin allowed to call the API with credentials, comma
# separated. Renaming or recreating a Render service (farmers-forward ->
# farmers-forward-1 already happened once) is then a dashboard edit rather than
# a redeploy. The defaults are the origins actually in use, so an unset variable
# still serves production instead of silently allowing dev only.
DEFAULT_ALLOWED_ORIGINS = (
    "https://farmers-forward-1.onrender.com",   # current Render frontend service
    "https://farmersforward.org",               # intended production domain
    "https://www.farmersforward.org",
    "http://localhost:3000",                    # vite dev server (vite.config.js)
    "http://127.0.0.1:3000",
)
ALLOWED_ORIGINS = _get_list("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)

# FRONTEND_ORIGIN predates ALLOWED_ORIGINS and may still be set on a running
# deployment. Honour it rather than silently dropping an origin that works
# today, but only when it was set explicitly: merging its localhost default
# into a deliberately narrow ALLOWED_ORIGINS would widen the allow-list.
_FRONTEND_ORIGIN_EXPLICIT = _get("FRONTEND_ORIGIN", None)
FRONTEND_ORIGIN = _FRONTEND_ORIGIN_EXPLICIT or "http://localhost:3000"
if _FRONTEND_ORIGIN_EXPLICIT and _FRONTEND_ORIGIN_EXPLICIT not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append(_FRONTEND_ORIGIN_EXPLICIT)

# ---------------------------------------------------------------------------
# OTP policy
# ---------------------------------------------------------------------------
OTP_TTL_SECONDS = _get_bounded_int("OTP_TTL_SECONDS", 300, 60, 3600)
OTP_MAX_ATTEMPTS = _get_bounded_int("OTP_MAX_ATTEMPTS", 3, 1, 10)
# Login is passwordless, so the code is the only secret, and without limits a
# 6-digit code falls to anyone who keeps asking for fresh ones. Per email: at
# most one new code per cooldown, and no new codes at all once the wrong guesses
# inside the window reach the limit. The limits apply identically to emails
# that match no admin, so they reveal nothing about which emails are registered.
OTP_RESEND_COOLDOWN_SECONDS = _get_bounded_int("OTP_RESEND_COOLDOWN_SECONDS", 60, 10, 3600)
OTP_FAILURE_LIMIT = _get_bounded_int("OTP_FAILURE_LIMIT", 10, 1, 1000)
OTP_FAILURE_WINDOW_SECONDS = _get_bounded_int("OTP_FAILURE_WINDOW_SECONDS", 3600, 60, 86400)
# Wrong guesses per email per day, so a trickle that stays under the hourly
# limit forever is still capped: about 30 guesses a day instead of ~290, which
# puts a year-long attack on one 6-digit code space around 1%.
OTP_FAILURE_LIMIT_PER_DAY = _get_bounded_int("OTP_FAILURE_LIMIT_PER_DAY", 30, 1, 10000)
# Codes per email per hour: bounds how many emails anyone can make an admin
# receive (and how much of the shared Resend quota they can burn).
OTP_ISSUE_LIMIT_PER_HOUR = _get_bounded_int("OTP_ISSUE_LIMIT_PER_HOUR", 5, 1, 1000)
# Codes per hour across ALL emails. Every request writes a row, including for
# random addresses, so without a ceiling a flood fills the database.
OTP_GLOBAL_LIMIT_PER_HOUR = _get_bounded_int("OTP_GLOBAL_LIMIT_PER_HOUR", 500, 1, 100000)


# ---------------------------------------------------------------------------
# Startup safety checks
# ---------------------------------------------------------------------------
def secret_key_problem(value=None):
    """Why `value` (default: the configured SECRET_KEY) can't be used, or None."""
    key = SECRET_KEY if value is None else value
    if not key:
        if value is None and os.environ.get("SECRET_KEY") == "":
            # Set in the dashboard with no value: a different fix than "unset".
            return "SECRET_KEY is set but empty"
        return "SECRET_KEY is not set"
    if key == _OLD_PUBLIC_SECRET_KEY:
        return ("SECRET_KEY is the old built-in development value, which is "
                "published in this repository")
    if len(key) < SECRET_KEY_MIN_LENGTH:
        return ("SECRET_KEY is only %d characters; at least %d are required"
                % (len(key), SECRET_KEY_MIN_LENGTH))
    if len(set(key)) < SECRET_KEY_MIN_DISTINCT:
        return ("SECRET_KEY uses only %d distinct characters, so it is not a "
                "random value" % len(set(key)))
    return None


def require_secret_key() -> str:
    """Return SECRET_KEY, or raise if it is missing or unusable.

    Deliberately FATAL, unlike insecure_defaults(): booting without a real key
    means either forgeable admin sessions or, with a random one, everybody
    logged out on every restart -- both silent. A crashed boot is loud, and
    Render keeps the previous deploy serving while this one fails.
    """
    problem = secret_key_problem()
    if problem:
        raise RuntimeError(
            "%s. It signs every admin session cookie and must be set in the "
            "environment (on Render: the service's Environment tab). Generate "
            "one with:\n"
            "  python3 -c \"import secrets; print(secrets.token_hex(32))\""
            % problem)
    return SECRET_KEY


def insecure_defaults():
    """List of insecure built-in defaults currently in effect (empty if none)."""
    problems = []
    if not SESSION_COOKIE_SECURE:
        problems.append("COOKIE_SECURE is off (session cookies may travel over HTTP)")
    return problems


def assert_secure():
    """Return the list of insecure defaults in effect (empty if none).

    This is intentionally NON-fatal: it never aborts startup. A misconfigured
    env var (e.g. COOKIE_SECURE unset) must not crash-loop the deploy -- the
    caller logs these as loud warnings instead. SECRET_KEY is the exception and
    is checked by require_secret_key(), which IS fatal.
    """
    return insecure_defaults()
