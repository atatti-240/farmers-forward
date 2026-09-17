#!/usr/bin/env python3
"""Rotate the Farmers Forward admin credentials -- and nothing else.

`seed_default_admin()` only ever runs when the admins table is EMPTY, so once an
account exists, changing DEFAULT_ADMIN_PASSWORD in the environment has no effect
and there is no way back in. This script is that way back in: it deletes the
admins row(s) (plus their now-orphaned OTP rows) and re-runs the normal seeding
path, leaving farms / samples / results / contacts completely untouched.

The row counts of those four tables are read before and after the reset and
compared; if any of them changed, the script exits non-zero and says so.

Usage (from the repo root):

    python3 -m backend.reset_admin                  # interactive; uses DEFAULT_ADMIN_PASSWORD
    python3 -m backend.reset_admin --yes            # no confirmation prompt (CI / Render shell)
    python3 -m backend.reset_admin --dry-run        # show what would happen, change nothing
    python3 -m backend.reset_admin --password 'S3cret!' --yes
    printf '%s' 'S3cret!' | python3 -m backend.reset_admin --password-stdin --yes

`python3 backend/reset_admin.py` and `python3 reset_admin.py` (from backend/)
work too -- the repo root is put on sys.path below, as in wsgi.py.

Password precedence: --password / --password-stdin, then DEFAULT_ADMIN_PASSWORD
from the environment or .env, then a random 12-char password printed once.

Exit codes: 0 success, 1 error, 2 aborted or bad arguments.
"""

from __future__ import annotations

import argparse
import os
import sys

# The app uses absolute package imports (`from backend import ...`), which
# require the repo root (the parent of backend/) on sys.path -- guarantee it
# regardless of the working directory this was launched from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend import auth  # noqa: E402
from backend import config  # noqa: E402
from backend import database  # noqa: E402

# Tables this script must never modify. Counted before and after as a guard.
PRESERVED_TABLES = ("farms", "samples", "results", "contacts")


def _counts(conn) -> dict:
    """Row count per preserved table.

    The table name is interpolated because SQLite cannot parameterize an
    identifier; the values come from the PRESERVED_TABLES constant above, never
    from user input.
    """
    return {name: conn.execute("SELECT COUNT(*) AS c FROM %s" % name).fetchone()["c"]
            for name in PRESERVED_TABLES}


def _fmt_counts(counts: dict) -> str:
    return ", ".join("%s=%d" % (name, counts[name]) for name in PRESERVED_TABLES)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m backend.reset_admin",
        description="Delete the admin row(s) and re-seed them. Farms, samples, "
                    "results and contacts are left untouched.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--username", default=None,
        help="Username for the re-created admin (default: DEFAULT_ADMIN_USERNAME, "
             "currently %r). Every existing admin row is removed either way."
             % config.DEFAULT_ADMIN_USERNAME)
    parser.add_argument(
        "--password", default=None,
        help="Password for the re-created admin. Beware shell history; prefer "
             "--password-stdin or DEFAULT_ADMIN_PASSWORD.")
    parser.add_argument(
        "--password-stdin", action="store_true",
        help="Read the new password from the first line of stdin.")
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip the confirmation prompt (for non-interactive shells).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan and exit without changing anything.")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.password is not None and args.password_stdin:
        print("error: use either --password or --password-stdin, not both",
              file=sys.stderr)
        return 2

    password = args.password
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            print("error: --password-stdin got an empty first line", file=sys.stderr)
            return 2
    # Reject an empty --password outright rather than silently falling through to
    # DEFAULT_ADMIN_PASSWORD/a random one, which would contradict what is printed.
    if password == "":
        print("error: --password may not be empty; omit the flag to use "
              "DEFAULT_ADMIN_PASSWORD", file=sys.stderr)
        return 2

    username = args.username or config.DEFAULT_ADMIN_USERNAME

    # Idempotent: creates the tables only if they are missing (e.g. a brand new
    # database on a fresh disk), never drops or alters an existing one.
    database.init_db()

    conn = database.get_db()
    try:
        before = _counts(conn)
        admins = conn.execute(
            "SELECT id, username, created_at FROM admins ORDER BY id").fetchall()
        n_otps = conn.execute("SELECT COUNT(*) AS c FROM otps").fetchone()["c"]
    finally:
        conn.close()

    print("Database        : %s" % database.describe_backend())
    print("Preserved tables: %s  (untouched by this script)" % _fmt_counts(before))
    print("")

    if admins:
        print("Admin row(s) to be deleted and re-seeded:")
        for row in admins:
            print("  id=%-4d username=%-24r created_at=%s"
                  % (row["id"], row["username"], row["created_at"]))
        print("Plus %d OTP row(s), which reference those admins and would be "
              "orphaned." % n_otps)
    else:
        print("No admin rows exist yet -- one will simply be created.")
    print("")

    print("New admin username: %r (from %s)"
          % (username, "--username" if args.username
             else "DEFAULT_ADMIN_USERNAME / built-in default"))
    if password is not None:
        print("New admin password: supplied via %s (%d chars)"
              % ("--password-stdin" if args.password_stdin else "--password",
                 len(password)))
    else:
        print("New admin password: %s" % auth.describe_admin_password_source())
    print("")

    if args.dry_run:
        print("--dry-run: nothing was changed.")
        return 0

    if not args.yes:
        prompt = ("Delete %d admin row(s) + %d OTP row(s) and re-seed? [y/N] "
                  % (len(admins), n_otps))
        try:
            reply = input(prompt)
        except EOFError:
            reply = ""
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted -- nothing was changed.")
            return 2

    # One transaction: both deletes land together or neither does.
    conn = database.get_db()
    try:
        with conn:
            deleted_otps = conn.execute("DELETE FROM otps").rowcount
            deleted_admins = conn.execute("DELETE FROM admins").rowcount
    finally:
        conn.close()
    print("Deleted %d admin row(s) and %d OTP row(s)."
          % (deleted_admins, deleted_otps))

    result = auth.seed_default_admin(username=username, password=password)
    if not result["created"]:
        print("error: re-seed did not create an admin (the table was unexpectedly "
              "non-empty). Nothing else was changed.", file=sys.stderr)
        return 1

    conn = database.get_db()
    try:
        after = _counts(conn)
    finally:
        conn.close()

    if after != before:
        print("error: preserved table counts CHANGED -- before: %s / after: %s"
              % (_fmt_counts(before), _fmt_counts(after)), file=sys.stderr)
        return 1

    print("Preserved tables verified unchanged: %s" % _fmt_counts(after))
    print("Done. Log in as %r with the password shown above." % username)
    if result["generated"]:
        print("That password was randomly generated and is shown ONCE. Set "
              "DEFAULT_ADMIN_PASSWORD and re-run this script to pick your own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
