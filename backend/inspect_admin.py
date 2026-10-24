#!/usr/bin/env python3
"""Report who can sign in to Farmers Forward, and why a sign-in might fail.

Read-only: this script never writes to the database -- no init_db(), no
seeding -- so it is safe to run against production at any time, even against
a database this code has not booted against yet.

The login page gives the same answer for every well-formed email, so a failing
sign-in has to be diagnosed from here. It takes an address registered as an
ACTIVE admin and a code that can actually reach that inbox, and managing
admins takes an active owner. The report lists every admin (role, active or
deactivated, created, last login), counts the active owners, and shows the
settings that decide the rest: DEFAULT_ADMIN_EMAIL, which only ever seeds an
EMPTY admins table (so changing it later does nothing), and how login codes
are emailed.

Usage (from the repo root; Render's free tier has no shell, so run it on your
machine with DATABASE_URL set to the Supabase pooler URL):

    python3 -m backend.inspect_admin

Nothing secret is printed: every email is masked (ow***@example.com), and no
API key, passcode or database password appears.

Exit codes: 0 at least one active owner exists, 1 none does (the report says
how to fix it), 2 the database or its admins table can't be read.
"""

from __future__ import annotations

import argparse
import datetime
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
from backend.check_db_connection import admins_table_state  # noqa: E402


def default_email_status() -> str:
    """DEFAULT_ADMIN_EMAIL's state and source, with the address masked."""
    if not config.DEFAULT_ADMIN_EMAIL:
        return "unset"
    email = auth.normalize_email(config.DEFAULT_ADMIN_EMAIL)
    status = "set (from %s): %s" % (config.env_source("DEFAULT_ADMIN_EMAIL"),
                                    auth.mask_email(email))
    if not auth.is_valid_email(email):
        status += " -- NOT a valid email address, so it is ignored"
    return status


def _modified(path: str) -> str:
    """A file's modification time as ISO8601 UTC, like the timestamps in the tables."""
    return datetime.datetime.fromtimestamp(
        os.stat(path).st_mtime, datetime.timezone.utc).isoformat(timespec="seconds")


def main(argv=None) -> int:
    argparse.ArgumentParser(
        prog="python3 -m backend.inspect_admin",
        description="Read-only report of every admin and of the email settings in "
                    "this shell. Exit 0: an active owner exists; 1: none does; 2: "
                    "the database can't be read.").parse_args(argv)
    bar = "-" * 70
    print(bar)
    print("admins report (read-only)")
    print(bar)

    print("database: %s" % database.describe_backend())
    sqlite_missing = False
    if not database.using_postgres():
        path = config.DATABASE_PATH
        if os.path.exists(path):
            print("  exists: yes, %d bytes, last modified %s"
                  % (os.path.getsize(path), _modified(path)))
        else:
            # Checked first because connecting would create the file.
            sqlite_missing = True
            print("  exists: NO")
            print("  -> Nothing has been written here yet. On a host with an")
            print("     ephemeral filesystem this is what every restart looks")
            print("     like; set DATABASE_URL so the data persists.")

    print(".env file: %s" % (config.DOTENV_PATH if config.DOTENV_LOADED
                             else "not found (process environment only)"))
    print()
    print("settings in THIS shell (the deployed app's own are in its startup log,")
    print("'[admin-auth] startup admin report'):")
    print("  DEFAULT_ADMIN_EMAIL: %s" % default_email_status())
    print("  OTP email: %s" % auth.describe_delivery())
    for warning in auth.delivery_warnings():
        print("  WARNING: %s" % warning)
    print()

    if sqlite_missing:
        return 2
    try:
        state = admins_table_state()
        admins = auth.list_admins() if state == "current" else []
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print("could not read the admins table: %s: %s" % (type(exc).__name__, exc))
        return 2

    if state == "missing":
        print("admins table: does not exist yet. The app creates it on its next")
        print("  boot (seeding the first owner from DEFAULT_ADMIN_EMAIL), or create")
        print("  the tables now with:")
        print("    python3 -m backend.check_db_connection --init")
        return 2
    if state == "legacy":
        print("admins table: still the old single-admin shape (username + password),")
        print("  from before multi-admin profiles. Only the first boot of this")
        print("  version of the app migrates it (keeping the old table as")
        print("  admins_legacy); deploy it, then run this again.")
        return 2

    active = [admin for admin in admins if admin["is_active"]]
    owners = [admin for admin in active if admin["role"] == auth.ROLE_OWNER]
    print("admins table: %d row(s), %d active, %d active owner(s)"
          % (len(admins), len(active), len(owners)))
    for admin in admins:
        # %r: a name typed into the Supabase table editor could hold anything.
        print("  %s  %r" % (auth.mask_email(admin["email"]), admin["display_name"]))
        print("     %s, %s, created %s, last login %s"
              % (admin["role"], "active" if admin["is_active"] else "deactivated",
                 admin["created_at"], admin["last_login_at"] or "never"))
    print()

    default = auth.normalize_email(config.DEFAULT_ADMIN_EMAIL)
    if admins and auth.is_valid_email(default) and \
            not any(admin["email"] == default for admin in admins):
        print("note: DEFAULT_ADMIN_EMAIL matches no admin. It only seeds an EMPTY")
        print("  admins table, so changing it now has no effect: add admins on the")
        print("  Admins page, or restore an owner with python3 -m backend.reset_admin.")
        print()

    print("VERDICT:")
    if owners:
        print("  %d active owner(s) can sign in and manage admins: %s."
              % (len(owners), ", ".join(auth.mask_email(owner["email"]) for owner in owners)))
        print("  If a sign-in still fails, check that:")
        print("    - the address is an ACTIVE admin above; any other address gets")
        print("      the same reply on the login page, but no code")
        print("    - OTP email above can deliver to that inbox")
        print("    - the app log has no '[auth] login code ...' line for it saying")
        print("      the code was not issued (one per %ds per email; none after %d"
              % (config.OTP_RESEND_COOLDOWN_SECONDS, config.OTP_FAILURE_LIMIT))
        print("      wrong guesses within %ds)" % config.OTP_FAILURE_WINDOW_SECONDS)
        return 0

    print("  No active owner, so nobody can add, deactivate or reactivate admins.")
    print("  Fix: python3 -m backend.reset_admin --email you@example.com")
    if not admins:
        print("  (or set DEFAULT_ADMIN_EMAIL and restart the app: an empty admins")
        print("  table is seeded from it)")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
