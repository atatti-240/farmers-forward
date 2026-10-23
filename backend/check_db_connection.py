#!/usr/bin/env python3
"""Check the configured database: can we connect, what is in it, is it persistent.

Reads DATABASE_URL from the environment, exactly as the app does. Nothing is
hardcoded and no credential is ever printed: the host and database name are
shown with the password replaced by ***.

Usage (set the variable in your own shell, never in a file that is committed):

    export DATABASE_URL='postgresql://USER:PASSWORD@HOST:6543/postgres'
    python3 -m backend.check_db_connection

    python3 -m backend.check_db_connection --init    # create missing tables first

With DATABASE_URL unset it reports on the local SQLite fallback instead, which
is a quick way to confirm the fallback still works.

It reports on a database from either side of the one-time multi-admin
migration. One this code has not booted against yet still has the old
single-admin `admins` table and is reported as such. Only the app's own first
boot migrates it -- --init never does, so this script can't rewrite a schema an
older deploy is still serving. Afterwards the old table is listed as
admins_legacy, kept for rollback.

Exit codes: 0 connected, 1 connected but the schema is missing, 2 cannot connect.
"""

from __future__ import annotations

import argparse
import os
import sys

# Match wsgi.py: absolute `from backend import ...` imports need the repo root
# on sys.path whatever directory this was launched from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend import config  # noqa: E402
from backend import database  # noqa: E402

POOLER_PORT = "6543"
DIRECT_PORT = "5432"


def check_pooler(url: str) -> None:
    """Warn when a Supabase URL uses the direct port instead of the pooler.

    gunicorn runs several sync worker processes and this app opens a connection
    per query, so the direct endpoint's connection cap is reached quickly.
    """
    if "supabase" not in url:
        return
    if ":%s" % DIRECT_PORT in url:
        print("  WARNING: this is the direct connection (port %s)." % DIRECT_PORT)
        print("           Use the Transaction pooler (port %s) instead, or the"
              % POOLER_PORT)
        print("           free-tier connection cap will be exhausted by gunicorn.")
    elif ":%s" % POOLER_PORT in url:
        print("  endpoint: transaction pooler (port %s), as recommended" % POOLER_PORT)


def list_tables(conn) -> list:
    """Table names in the active database, whichever backend it is.

    On Postgres that is the schema unqualified names resolve to -- the one the
    app reads and writes -- rather than a hardcoded 'public'.
    """
    if database.using_postgres():
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() ORDER BY table_name").fetchall()
    else:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [row[0] for row in rows]


def admins_table_state(conn=None) -> str:
    """'current', 'legacy' or 'missing': the shape of the admins table. Read-only.

    'legacy' is the single-admin table from before multi-admin profiles
    (username + password hash), which init_db() migrates on the next boot. The
    test is the migration's own, so the two can never disagree. Without
    `conn` a connection is opened and closed here -- but not to a SQLite file
    that doesn't exist, because connecting would create it.
    """
    if conn is None:
        if not database.using_postgres() and not os.path.exists(config.DATABASE_PATH):
            return "missing"
        conn = database.get_db()
        try:
            return admins_table_state(conn)
        finally:
            conn.close()
    return database.admins_shape(conn)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m backend.check_db_connection",
        description="Report connectivity, tables and row counts for the "
                    "configured database.")
    parser.add_argument("--init", action="store_true",
                        help="create any missing tables before reporting (never "
                             "runs the one-time admins migration)")
    args = parser.parse_args(argv)

    bar = "-" * 70
    print(bar)
    print("database connectivity check")
    print(bar)
    print("DATABASE_URL: %s" % ("set (from %s)" % config.env_source("DATABASE_URL")
                                if config.DATABASE_URL else "unset"))
    print("backend: %s" % database.describe_backend())
    if database.using_postgres():
        check_pooler(config.DATABASE_URL)
    else:
        print("  NOTE: this is the SQLite fallback. On Render's free tier this")
        print("        file is wiped on every deploy. Set DATABASE_URL to make")
        print("        the data persist.")
    print()

    try:
        conn = database.get_db()
    except Exception as exc:  # noqa: BLE001 - a readable message beats a traceback
        print("CANNOT CONNECT: %s: %s" % (type(exc).__name__, exc))
        print()
        print("Things worth checking:")
        print("  - the password is URL-encoded if it contains @ : / or ?")
        print("  - the host is the pooler host, not the project dashboard URL")
        print("  - Supabase project is not paused (free tier pauses when idle)")
        return 2

    try:
        if args.init:
            print("--init: creating any missing tables ...")
            try:
                # Never the one-time admins migration: only the app's own boot
                # runs that, so this can't rewrite a schema an older deploy
                # is still serving.
                database.init_db(migrate=False)
            except database.LegacySchemaError:
                print("--init: nothing created: admins still has the old "
                      "single-admin shape, which only the app's first boot "
                      "migrates (see below).")
            print()
        print("connected.")
        tables = list_tables(conn)
        print("tables in the database: %d" % len(tables))

        missing = [name for name in database.TABLES if name not in tables]
        for name in sorted(tables):
            try:
                # The name comes from the database's own catalog, not from input.
                row = conn.execute("SELECT COUNT(*) AS c FROM %s" % name).fetchone()
                count = row[0] if row is not None else "?"
            except Exception as exc:  # noqa: BLE001
                count = "unreadable (%s)" % type(exc).__name__
            if name in database.TABLES:
                marker = ""
            elif name in database.LEGACY_TABLES:
                # Left by the one-time admins migration; never read by the app.
                marker = " (legacy, kept for rollback)"
            else:
                marker = " (not ours)"
            print("  %-14s %8s rows%s" % (name, count, marker))

        print()
        if missing:
            print("MISSING expected table(s): %s" % ", ".join(missing))
            print("Run this again with --init, or just start the app: init_db()")
            print("runs on every boot and is idempotent.")
            return 1

        admins = conn.execute("SELECT COUNT(*) AS c FROM admins").fetchone()[0]
        print("All %d expected tables are present." % len(database.TABLES))
        if admins_table_state(conn) == "legacy":
            print("admins still has the old single-admin shape (username + password).")
            print("The first boot of this version of the app migrates it, keeping the")
            print("old table as admins_legacy. This script (even with --init) won't.")
        elif admins == 0:
            print("The admins table is empty, so the app's next boot seeds the first")
            print("owner from the SERVER's DEFAULT_ADMIN_EMAIL (in this shell it is %s)."
                  % ("set" if config.DEFAULT_ADMIN_EMAIL else "unset"))
            print("Or create one now with:")
            print("  python3 -m backend.reset_admin --email you@example.com")
        else:
            print("%d admin row(s) already exist, so seeding is a no-op." % admins)
            print("Inspect them with:     python3 -m backend.inspect_admin")
            print("Restore owner access:  python3 -m backend.reset_admin --email you@example.com")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
