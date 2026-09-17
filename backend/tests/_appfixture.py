"""One initialized app instance, shared by every backend test module.

backend.app runs init_db() and seeds an admin at import time, and database.py
reads config.DATABASE_PATH at call time rather than caching it. So if each test
module pointed DATABASE_PATH at its own temp file, whichever module imported the
app first would create the tables and the rest would query an empty path. The
path is therefore chosen once, here, before the app is imported.

Import this instead of backend.app directly:

    from _appfixture import app, auth, config, database, DATABASE_PATH
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

DATABASE_PATH = os.path.join(tempfile.mkdtemp(prefix="ff-backend-tests-"), "test.db")
config.DATABASE_PATH = DATABASE_PATH

# Importing the app prints a startup banner; keep the test output readable.
with contextlib.redirect_stdout(io.StringIO()):
    from backend.app import app  # noqa: E402,F401
    from backend import auth, database  # noqa: E402,F401


def restore_database_path():
    """Re-point config at the test database after a config module reload."""
    config.DATABASE_PATH = DATABASE_PATH
