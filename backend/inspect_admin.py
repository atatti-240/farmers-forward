#!/usr/bin/env python3
"""Report what is actually in the admins table, and why a login is failing.

Read-only: this script never writes to the database. It answers the question
`seed_default_admin()` cannot, because seeding is a no-op once a row exists --
namely whether the credentials currently in the environment are the ones the
stored row was created with.

Usage (from the repo root, or a Render shell):

    python3 -m backend.inspect_admin

Nothing secret is printed: no password, no password hash. Only the bcrypt
algorithm and cost prefix, the username, and whether a verification succeeds.

Exit codes: 0 the env credentials would log in, 1 they would not, 2 error.
"""

from __future__ import annotations

import os
import sys

# Match wsgi.py / reset_admin.py: absolute `from backend import ...` imports
# need the repo root on sys.path whatever directory this was launched from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend import auth  # noqa: E402
from backend import config  # noqa: E402
from backend import database  # noqa: E402


def mask(value: str, keep: int = 2) -> str:
    """Show enough of a contact field to recognize it, not enough to reuse it."""
    text = str(value or "")
    if "@" in text:
        local, _, domain = text.partition("@")
        return "%s%s@%s" % (local[:keep], "*" * max(0, len(local) - keep), domain)
    if len(text) <= keep:
        return "*" * len(text)
    return "%s%s" % ("*" * (len(text) - keep), text[-keep:])


def hash_scheme(stored: str) -> str:
    """The bcrypt algorithm and cost prefix, e.g. '$2b$12$'. Never the digest."""
    parts = str(stored or "").split("$")
    if len(parts) < 4:
        return "unrecognized (not a bcrypt hash)"
    return "$%s$%s$ (bcrypt, cost %s)" % (parts[1], parts[2], parts[2])


def main() -> int:
    bar = "-" * 70
    print(bar)
    print("admins table report (read-only)")
    print(bar)

    print("database: %s" % database.describe_backend())
    if not database.using_postgres():
        path = config.DATABASE_PATH
        if not os.path.exists(path):
            print("  exists: NO")
            print("  -> Nothing has been written here yet. On a host with an")
            print("     ephemeral filesystem this is what every restart looks")
            print("     like; set DATABASE_URL so the data persists.")
            return 2
        stat = os.stat(path)
        print("  exists: yes, %d bytes, last modified %s"
              % (stat.st_size, __import__("datetime").datetime.fromtimestamp(
                  stat.st_mtime).isoformat(timespec="seconds")))

    print(".env file: %s" % (config.DOTENV_PATH if config.DOTENV_LOADED
                             else "not found (process environment only)"))
    print()
    print("environment:")
    print("  DEFAULT_ADMIN_USERNAME: %r (from %s)"
          % (config.DEFAULT_ADMIN_USERNAME, config.env_source("DEFAULT_ADMIN_USERNAME")))
    print("  DEFAULT_ADMIN_PASSWORD: %s" % auth.describe_admin_password_source())
    print()

    try:
        rows = database.query_all(
            "SELECT id, username, password_hash, phone, email, created_at "
            "FROM admins ORDER BY id")
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print("could not read the admins table: %s" % exc)
        return 2

    print("admins table: %d row(s)" % len(rows))
    for row in rows:
        print("  id=%s username=%r created_at=%s" % (row["id"], row["username"], row["created_at"]))
        print("     email=%s phone=%s" % (mask(row["email"]), mask(row["phone"])))
        print("     password hash: %s" % hash_scheme(row["password_hash"]))
    print()

    if not rows:
        print("VERDICT: the table is empty, so the next boot will seed it.")
        print("  If login still fails after that, the row is being created and")
        print("  then lost again, which means the disk is not persistent.")
        return 1

    # The decisive check: would the configured credentials actually log in?
    match = next((r for r in rows if r["username"] == config.DEFAULT_ADMIN_USERNAME), None)
    print("VERDICT:")
    if match is None:
        print("  DEFAULT_ADMIN_USERNAME %r matches NO row."
              % config.DEFAULT_ADMIN_USERNAME)
        print("  Stored username(s): %s" % ", ".join(repr(r["username"]) for r in rows))
        print("  /api/auth/login answers 401 'Invalid credentials' for an unknown")
        print("  username, the same reply a wrong password gets.")
        print("  Fix: set DEFAULT_ADMIN_USERNAME to a stored value, or rotate with")
        print("       python3 -m backend.reset_admin --yes")
        return 1

    if not config.DEFAULT_ADMIN_PASSWORD:
        print("  Username %r exists, but DEFAULT_ADMIN_PASSWORD is not set, so the"
              % match["username"])
        print("  stored hash cannot be checked. The row was almost certainly")
        print("  seeded with a random password, printed once at its first boot.")
        print("  Fix: set DEFAULT_ADMIN_PASSWORD, then")
        print("       python3 -m backend.reset_admin --yes")
        return 1

    if auth.check_password(config.DEFAULT_ADMIN_PASSWORD, match["password_hash"]):
        print("  Username %r exists and DEFAULT_ADMIN_PASSWORD verifies against"
              % match["username"])
        print("  its stored hash. These credentials WOULD log in.")
        print("  If the browser still fails, the cause is later in the flow:")
        print("    - the secret-code gate (ADMIN_SECRET_CODE) answers 403")
        print("      'forbidden', not 401, so check which status you actually get")
        print("    - the OTP step after login, or the session cookie")
        print("    - a different database than this one on the serving host")
        return 0

    print("  Username %r exists, but DEFAULT_ADMIN_PASSWORD does NOT match its"
          % match["username"])
    print("  stored hash. This is the expected result when the password was")
    print("  changed in the environment after the row was created: seeding is")
    print("  create-only and never rewrites an existing row.")
    print("  Fix: python3 -m backend.reset_admin --yes")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
