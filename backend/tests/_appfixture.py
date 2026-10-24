"""One initialized app instance, shared by every backend test module.

backend.app runs init_db() and seeds the first owner at import time, and
database.py reads config.DATABASE_PATH at call time rather than caching it. So
if each test module pointed DATABASE_PATH at its own temp file, whichever module
imported the app first would create the tables and the rest would query an
empty path. The path is therefore chosen once, here, before the app is imported.

The suite runs against SQLite by default. Set FF_TEST_DATABASE_URL to a
throwaway Postgres database (its name must contain "test") to run the same
tests against Postgres instead; that database is wiped at the start of the run.
Tests that pin SQLite explicitly (DATABASE_URL=None) still use SQLite.

    FF_TEST_DATABASE_URL='postgresql://ff_app@127.0.0.1:54329/ff_test?sslmode=disable' \\
        /usr/bin/python3 -m unittest discover -p 'test_*.py'

Import this instead of backend.app directly:

    from _appfixture import app, auth, config, database, DATABASE_PATH, sign_in
"""

import contextlib
import io
import os
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend import config  # noqa: E402

# app.py refuses to import without a usable key; the tests never depend on the
# developer's .env for one.
TEST_SECRET_KEY = "farmers-forward-test-suite-secret-key-0123456789"
config.SECRET_KEY = TEST_SECRET_KEY
# The owner seeded into the empty test database at import, and no real email.
OWNER_EMAIL = "owner@example.com"
config.DEFAULT_ADMIN_EMAIL = OWNER_EMAIL
config.DEFAULT_ADMIN_NAME = "Test Owner"
config.RESEND_API_KEY = None

DATABASE_PATH = os.path.join(tempfile.mkdtemp(prefix="ff-backend-tests-"), "test.db")
config.DATABASE_PATH = DATABASE_PATH
# Never inherit a DATABASE_URL from the developer's .env: that could be the
# production database.
TEST_DATABASE_URL = os.environ.get("FF_TEST_DATABASE_URL") or None
config.DATABASE_URL = TEST_DATABASE_URL

# Everything this app has ever created, dropped before a Postgres run.
_ALL_TABLES = ("otps", "admins", "admins_legacy", "admins_v2",
               "farms", "samples", "results", "contacts")


def reset_postgres(url: str) -> None:
    """Drop every app table in the throwaway database at `url`.

    The name check runs on the database the connection actually reached, not on
    the URL: libpq lets a dbname= or service= parameter override the path.
    """
    import psycopg2
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            name = cur.fetchone()[0]
            if "test" not in name:
                raise RuntimeError("FF_TEST_DATABASE_URL must reach a database whose "
                                   "name contains 'test' (it reached %r); it is "
                                   "wiped by the test run." % name)
            cur.execute("DROP TABLE IF EXISTS %s CASCADE" % ", ".join(_ALL_TABLES))
        conn.commit()
    finally:
        conn.close()


if TEST_DATABASE_URL:
    reset_postgres(TEST_DATABASE_URL)

# Importing the app prints a startup banner; keep the test output readable.
with contextlib.redirect_stdout(io.StringIO()):
    from backend.app import app  # noqa: E402,F401
    from backend import auth, database  # noqa: E402,F401


def restore_database_path():
    """Re-point config at the test database after a config module reload."""
    config.DATABASE_PATH = DATABASE_PATH
    config.DATABASE_URL = TEST_DATABASE_URL
    config.SECRET_KEY = TEST_SECRET_KEY
    config.DEFAULT_ADMIN_EMAIL = OWNER_EMAIL
    config.DEFAULT_ADMIN_NAME = "Test Owner"
    config.RESEND_API_KEY = None


def owner_id() -> str:
    """Id of the owner seeded into whichever database is active."""
    return auth.find_admin_by_email(OWNER_EMAIL)["id"]


def sign_in(client, admin_id=None) -> str:
    """Give `client` a signed-in session, as a successful code check would.

    Defaults to the seeded owner. The admin must exist and be active: every
    protected request re-reads it. Returns the admin id used.
    """
    admin_id = admin_id or owner_id()
    with client.session_transaction() as session:
        session.clear()
        session["authed"] = True
        session["admin_id"] = admin_id
        session["login_at"] = database.now_iso()
    return admin_id
