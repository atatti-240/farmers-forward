#!/usr/bin/env python3
"""Restore owner access to Farmers Forward -- and change nothing else.

Admins sign in with a code emailed to their address, and only an active owner
can add, deactivate or reactivate admins on the Admins page. If no active owner
is left (DEFAULT_ADMIN_EMAIL was unset when the database was created, one owner
deactivated the other, the only owner lost their mailbox), nothing on the site
can fix it. This script is the way back in. It makes one email address an
active owner: the admin is created if nobody has that address, otherwise the
existing one is promoted and/or reactivated (and renamed, with --name). Nobody
is ever deleted, deactivated or demoted, and farms / samples / results /
contacts are not touched.

As a guard, the row counts of those four tables and every admins row other
than the target's are read just before and just after the change. If any of
them differ, the script exits non-zero and says so.

Usage (from the repo root):

    python3 -m backend.reset_admin --email you@example.com            # interactive
    python3 -m backend.reset_admin --email you@example.com --yes      # no prompt
    python3 -m backend.reset_admin --email you@example.com --name 'Your Name' --yes
    python3 -m backend.reset_admin --email you@example.com --dry-run  # show the plan, change nothing
    python3 -m backend.reset_admin --yes                              # uses DEFAULT_ADMIN_EMAIL

`python3 backend/reset_admin.py` and `python3 reset_admin.py` (from backend/)
work too -- the repo root is put on sys.path below, as in wsgi.py.

--email defaults to DEFAULT_ADMIN_EMAIL. Missing tables are created first
(init_db(migrate=False)). A database from before multi-admin profiles is left
alone with an error instead: only the app's own first boot runs that one-time
migration, so this script can't rewrite a schema an older deploy is still
serving. --dry-run doesn't even create tables, and writes nothing at all.

The owner being restored is printed in full, whether it was typed with --email
or came from your own DEFAULT_ADMIN_EMAIL (on a laptop, the local .env's: check
it before confirming). Every other email is masked. A real run also lifts any
login throttle on that address, so an owner locked out by someone hammering
their email can sign in at once.

Exit codes: 0 success (including nothing to do), 1 error, 2 aborted or bad arguments.
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
from backend.check_db_connection import admins_table_state  # noqa: E402

# Tables this script must never modify. Counted before and after as a guard.
PRESERVED_TABLES = ("farms", "samples", "results", "contacts")

# Every admins column, so the guard notices a change to any of them. Email
# comes first so that a row which changed can be named (masked) in the error.
_ADMIN_COLUMNS = ("email, id, display_name, role, is_active, created_at, last_login_at, "
                  "sessions_valid_after")


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


def _snapshot(email: str):
    """(preserved table counts, every other admins row): what must not change.

    Taken right around the write rather than before the confirmation prompt,
    so an operator taking their time can't make live traffic look like a
    failure of the guard.
    """
    conn = database.get_db()
    try:
        others = conn.execute(
            "SELECT %s FROM admins WHERE email <> ? ORDER BY id" % _ADMIN_COLUMNS,
            (email,)).fetchall()
        return _counts(conn), [tuple(row) for row in others]
    finally:
        conn.close()


def _guard_problems(before, after) -> list:
    """What changed apart from the target admin -- which should be nothing."""
    (counts_before, others_before), (counts_after, others_after) = before, after
    problems = []
    if counts_after != counts_before:
        problems.append("preserved table counts CHANGED -- before: %s / after: %s"
                        % (_fmt_counts(counts_before), _fmt_counts(counts_after)))
    if others_after != others_before:
        changed = sorted({row[0] for row in set(others_before) ^ set(others_after)})
        problems.append("other admin row(s) CHANGED: %s"
                        % ", ".join(auth.mask_email(address) for address in changed))
    return problems


def _target_problem(admin, name, shown: str) -> str:
    """Why the target is not what ensure_owner() promised, or ''."""
    if admin is None:
        return "%s does not exist afterwards" % shown
    if admin["role"] != auth.ROLE_OWNER or not admin["is_active"]:
        return "%s is not an active owner afterwards (%s)" % (shown, _describe(admin))
    if name is not None and admin["display_name"] != name:
        return "%s was not renamed to %r" % (shown, name)
    return ""


def _target(arg_email):
    """(email, source, error) for the admin to restore; error is '' if usable."""
    if arg_email is not None:
        # An explicit --email is never silently replaced by DEFAULT_ADMIN_EMAIL,
        # not even an empty one: the plan printed would not be what was asked.
        email = auth.normalize_email(arg_email)
        if not auth.is_valid_email(email):
            return None, None, "error: --email %r is not a valid email address" % arg_email
        return email, "--email", ""
    if not config.DEFAULT_ADMIN_EMAIL:
        return None, None, ("error: no email given. Pass --email you@example.com "
                            "(its default, DEFAULT_ADMIN_EMAIL, is unset).")
    email = auth.normalize_email(config.DEFAULT_ADMIN_EMAIL)
    if not auth.is_valid_email(email):
        return None, None, ("error: DEFAULT_ADMIN_EMAIL (%s) is not a valid email "
                            "address; pass --email instead." % auth.mask_email(email))
    return email, "DEFAULT_ADMIN_EMAIL in %s" % config.env_source("DEFAULT_ADMIN_EMAIL"), ""


def _new_owner_name(email: str, name):
    """(display name, error) that ensure_owner() gives a NEW owner."""
    if name is not None:
        return name, ""
    # auth's own rule (DEFAULT_ADMIN_NAME, else the part of the email before the
    # @), called rather than copied so the plan can't drift from what happens.
    name, error = auth.clean_display_name(auth.default_display_name(email))
    if error:
        return "", ("error: the default display name for a new owner (DEFAULT_ADMIN_NAME, "
                    "else the part of the email before the @) can't be used: %s. "
                    "Pass --name." % error)
    return name, ""


def _changes(existing: dict, name) -> list:
    """What ensure_owner() will change on an existing admin, in words."""
    changes = []
    if existing["role"] != auth.ROLE_OWNER:
        changes.append("promote: role %s -> %s" % (existing["role"], auth.ROLE_OWNER))
    if not existing["is_active"]:
        changes.append("reactivate")
    if name is not None and name != existing["display_name"]:
        changes.append("rename: %r -> %r" % (existing["display_name"], name))
    return changes


def _describe(admin: dict) -> str:
    return "%r, role %s, %s" % (admin["display_name"], admin["role"],
                                "active" if admin["is_active"] else "deactivated")


def _explain_pending_schema(state: str, shown: str, new_name) -> None:
    """--dry-run on a database init_db() still has work to do on."""
    if state == "missing":
        print("The database has no admins table yet. A real run first creates the")
        print("tables (init_db(), the step every app boot runs), then creates")
        print("%s as an active owner named %r." % (shown, new_name))
        return
    print("The admins table still has the old single-admin shape (username +")
    print("password). A real run would refuse and change nothing: only the first")
    print("boot of this version of the app runs the one-time migration (the old")
    print("rows are copied into the new table, the oldest as owner, and the old")
    print("table is kept as admins_legacy). Deploy it, then run this for %s." % shown)


def _confirm(prompt: str) -> bool:
    try:
        reply = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("")
        reply = ""
    return reply.strip().lower() in ("y", "yes")


def _sign_in_help(shown: str) -> None:
    """How the owner signs in, and whether a code can reach them."""
    print("")
    print("Sign in as %s: the hidden passcode, then that email, then the" % shown)
    print("6-digit code emailed to it.")
    print("Email settings in THIS shell (the deployed app's own are in its startup")
    print("log, '[admin-auth] startup admin report'):")
    print("  OTP email: %s" % auth.describe_delivery())
    for warning in auth.delivery_warnings():
        print("  WARNING: %s" % warning)
    print("If no code arrives, check RESEND_FROM on the server: Resend's test sender")
    print("only delivers to the Resend account owner's inbox.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m backend.reset_admin",
        description="Make one email address an active owner, creating the admin "
                    "if needed. Nobody is deleted, deactivated or demoted, and "
                    "farms, samples, results and contacts are left untouched.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--email", default=None,
        help="Email address of the owner to restore (default: DEFAULT_ADMIN_EMAIL, "
             "currently %s)." % ("set" if config.DEFAULT_ADMIN_EMAIL else "unset"))
    parser.add_argument(
        "--name", default=None,
        help="Display name. Renames an existing admin; a new owner otherwise gets "
             "DEFAULT_ADMIN_NAME, else the part of the email before the @.")
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip the confirmation prompt (for non-interactive shells).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan and exit without writing anything, not even "
             "missing tables.")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    # Arguments are checked before the database is opened, so a typo can't
    # create tables or run the migration.
    email, source, error = _target(args.email)
    if error:
        print(error, file=sys.stderr)
        return 2
    name = None
    if args.name is not None:
        name, error = auth.clean_display_name(args.name)
        if error:
            print("error: --name: %s" % error, file=sys.stderr)
            return 2
    # Only an address the operator typed is shown in full.
    # Only an address the operator chose is shown in full: one typed with
    # --email, or their own DEFAULT_ADMIN_EMAIL, so they can see which address
    # (on a laptop that is the local .env's) is about to become an owner.
    shown = email

    print("Database        : %s" % database.describe_backend())
    try:
        if args.dry_run:
            # Not even init_db(): on a database this code has never booted
            # against, that would create tables or run the one-time migration.
            state = admins_table_state()
        else:
            # Idempotent: creates any missing table. migrate=False because only
            # the app's own boot may run the one-time migration -- this script
            # may be pointed at production from a newer checkout while the old
            # deploy is still serving the old schema.
            database.init_db(migrate=False)
            state = "current"
    except database.LegacySchemaError:
        print("error: the database still has the old single-admin schema "
              "(username + password). Deploy this version of the app first: its "
              "first boot migrates the schema. Then run this again. Nothing was "
              "changed.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a readable message beats a traceback
        print("error: could not open the database: %s: %s"
              % (type(exc).__name__, exc), file=sys.stderr)
        return 1

    if state != "current":
        new_name = None
        if state == "missing":
            # An empty admins table means the real run would create the owner.
            new_name, error = _new_owner_name(email, name)
            if error:
                print(error, file=sys.stderr)
                return 2
        print("Owner to restore: %s (from %s)" % (shown, source))
        print("")
        _explain_pending_schema(state, shown, new_name)
        print("--dry-run: nothing was changed.")
        return 0

    conn = database.get_db()
    try:
        counts = _counts(conn)
    finally:
        conn.close()
    admins = auth.list_admins()
    active = [admin for admin in admins if admin["is_active"]]
    owners = [admin for admin in active if admin["role"] == auth.ROLE_OWNER]
    existing = auth.find_admin_by_email(email)

    print("Preserved tables: %s  (untouched by this script)" % _fmt_counts(counts))
    print("Admins          : %d row(s), %d active; active owner(s): %s"
          % (len(admins), len(active),
             ", ".join(auth.mask_email(owner["email"]) for owner in owners) or "none"))
    print("")
    print("Owner to restore: %s (from %s)" % (shown, source))

    if existing is None:
        new_name, error = _new_owner_name(email, name)
        if error:
            print(error, file=sys.stderr)
            return 2
        print("Currently       : no admin has this email")
        print("Changes         :")
        print("  - create it as an active owner named %r" % new_name)
        prompt = "Create %s as an active owner? [y/N] " % shown
    else:
        print("Currently       : %s" % _describe(existing))
        changes = _changes(existing, name)
        if not changes:
            print("Nothing to do: it is already an active owner.")
            if not args.dry_run:
                # The usual "locked out" case: the account is fine, but someone
                # hammering the address may be holding back new codes. Its
                # challenge rows are the throttle's only state.
                database.execute("DELETE FROM otps WHERE email = ?", (email,))
                print("Cleared any login throttle on it, so a new code can be sent now.")
            _sign_in_help(shown)
            return 0
        print("Changes         :")
        for change in changes:
            print("  - %s" % change)
        prompt = "Apply %d change(s) to %s? [y/N] " % (len(changes), shown)
    print("No other admin is changed, and nobody is deleted, deactivated or demoted.")
    print("")

    if args.dry_run:
        print("--dry-run: nothing was changed.")
        return 0

    if not args.yes and not _confirm(prompt):
        print("Aborted -- no admin was changed.")
        return 2

    before = _snapshot(email)
    try:
        result = auth.ensure_owner(email, name)
        # Lift any login throttle on this address (cooldown, hourly caps, failure
        # windows): its challenge rows are the throttle's only state, so an owner
        # locked out by someone hammering their email can sign in straight away.
        database.execute("DELETE FROM otps WHERE email = ?", (email,))
    except auth.AdminExists:
        # Its message is the full address, so it is not printed.
        print("error: %s was added by someone else at the same moment; nothing "
              "was changed. Run this again." % shown, file=sys.stderr)
        return 1
    except ValueError as exc:
        # Input was validated above, so this is an address the database's
        # lower() disagrees with Python about.
        print("error: %s could not be made an owner: %s" % (shown, exc), file=sys.stderr)
        return 1
    after = _snapshot(email)
    admin = auth.find_admin_by_email(email)

    problems = _guard_problems(before, after)
    problem = _target_problem(admin, name, shown)
    if problem:
        problems.append(problem)
    if problems:
        for problem in problems:
            print("error: %s" % problem, file=sys.stderr)
        print("error: this script should only ever change %s. Check the admins "
              "table by hand: python3 -m backend.inspect_admin" % shown, file=sys.stderr)
        return 1

    if result["created"]:
        print("Done: created %s as an active owner named %r." % (shown, admin["display_name"]))
    elif result["changes"]:
        print("Done: %s is an active owner (%s)." % (shown, ", ".join(result["changes"])))
    else:
        # Someone else made the same change while the prompt was waiting.
        print("Done: %s was already an active owner by the time of the write." % shown)
    print("Other admins verified unchanged: %d row(s)." % len(after[1]))
    print("Preserved tables verified unchanged: %s" % _fmt_counts(after[0]))
    _sign_in_help(shown)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
