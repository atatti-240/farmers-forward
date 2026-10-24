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


# ---------------------------------------------------------------------------
# Security / app
# ---------------------------------------------------------------------------
# Insecure built-in fallbacks used ONLY for a zero-config local dev run. They are
# rejected at startup when APP_ENV=production (see assert_secure()).
_DEV_SECRET_KEY = "dev-insecure-change-me"

SECRET_KEY = _get("SECRET_KEY", _DEV_SECRET_KEY)
# The hidden staff-access passcode. It is entered invisibly (no input field) on
# the public page, so it is necessarily embedded in the frontend bundle too --
# it is an obscurity gate, NOT a real secret. Actual security is the admin
# password + OTP. If you change it, change SEQ in frontend/src/pages/PublicHome.jsx
# to match.
ADMIN_SECRET_CODE = _get("ADMIN_SECRET_CODE", "202611")

# Deployment environment. Defaults to development so a fresh clone runs locally;
# production deployments MUST set APP_ENV=production, which turns the insecure
# built-in defaults below into hard startup errors.
APP_ENV = (_get("APP_ENV", "development") or "development").lower()
IS_PRODUCTION = APP_ENV == "production"

# The public, no-login deployment (the public-site branch only). When on, the
# process serves backend.public_app -- /api/public/* and nothing else: no admin,
# auth, history or report routes, no admin/otp tables, no admin seeding. See
# backend/public_app.py. Off (the default), the app is exactly the admin app.
PUBLIC_ONLY = _get_bool("PUBLIC_ONLY", False)

# ---------------------------------------------------------------------------
# Resend (email) -- the only OTP delivery channel.
# ---------------------------------------------------------------------------
# Delivery goes over the Resend HTTPS API because Render blocks outbound SMTP on
# every port. RESEND_API_KEY is the only value required for login emails to work.
RESEND_API_KEY = _get("RESEND_API_KEY", None)
# Resend's shared test sender, which needs no domain verification but can only
# deliver to the address that owns the Resend account. Point this at an address
# on your own verified domain to send anywhere.
RESEND_FROM = _get("RESEND_FROM", "onboarding@resend.dev")

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
# Default admin (seeded on first run)
# ---------------------------------------------------------------------------
DEFAULT_ADMIN_USERNAME = _get("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = _get("DEFAULT_ADMIN_PASSWORD", None)
DEFAULT_ADMIN_PHONE = _get("DEFAULT_ADMIN_PHONE", "+10000000000")
DEFAULT_ADMIN_EMAIL = _get("DEFAULT_ADMIN_EMAIL", "admin@farmersforward.org")

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
OTP_TTL_SECONDS = _get_int("OTP_TTL_SECONDS", 300)
OTP_MAX_ATTEMPTS = _get_int("OTP_MAX_ATTEMPTS", 3)


# ---------------------------------------------------------------------------
# Startup safety check
# ---------------------------------------------------------------------------
def insecure_defaults():
    """List of insecure built-in defaults currently in effect (empty if none)."""
    problems = []
    if SECRET_KEY == _DEV_SECRET_KEY:
        problems.append("SECRET_KEY is unset (using the insecure built-in default)")
    if not SESSION_COOKIE_SECURE:
        problems.append("COOKIE_SECURE is off (session cookies may travel over HTTP)")
    return problems


def assert_secure():
    """Return the list of insecure defaults in effect (empty if none).

    This is intentionally NON-fatal: it never aborts startup. A misconfigured
    env var (e.g. COOKIE_SECURE unset) must not crash-loop the deploy -- the
    caller logs these as loud warnings instead. Harden by setting a strong
    SECRET_KEY and COOKIE_SECURE=true in production; generate a key with:
      python3 -c "import secrets; print(secrets.token_hex(32))"
    """
    return insecure_defaults()
