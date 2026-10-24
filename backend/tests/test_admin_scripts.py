"""The operator scripts: reset_admin, inspect_admin and check_db_connection.

They are the break-glass tools for a locked-out site, run by hand in a Render
shell against the production database. Nothing else exercises them (the HTTP
tests never import them), and a mistake in them costs the most, so each test
here gets an empty database of its own: a temp SQLite file or, when
FF_TEST_DATABASE_URL is set, a throwaway schema in that Postgres database.

Most assertions are about what must NOT happen: nobody but the target admin
changes, a dry run and the read-only report write nothing at all, and no
stored email is printed in full except the one typed on the command line.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import TEST_DATABASE_URL, auth, config, database  # noqa: E402
from backend import check_db_connection, inspect_admin, reset_admin  # noqa: E402

# A stored address that no script has any business printing.
CONTACT_EMAIL = "ada.contact@example.com"

# admins and otps as the old single-admin code created them (see `git show
# main:backend/database.py`): what the one-time migration starts from.
LEGACY_ADMIN_TABLES = """
CREATE TABLE admins (
    id            {id},
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone         TEXT,
    email         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE otps (
    id         {id},
    admin_id   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    consumed   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# A database per test
# ---------------------------------------------------------------------------
def _on_server(sql):
    """Run one statement on FF_TEST_DATABASE_URL itself, outside any test schema."""
    import psycopg2
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def use_empty_database(test):
    """Point config at a brand-new, empty database for the rest of `test`.

    Never the suite's shared one: these scripts create, migrate and rewrite
    tables. On Postgres that is a schema of its own, which the URL selects
    through search_path, so unqualified table names land in it.
    """
    if TEST_DATABASE_URL:
        schema = "ff_admin_scripts_%s" % uuid.uuid4().hex[:12]
        _on_server("CREATE SCHEMA %s" % schema)
        test.addCleanup(_on_server, "DROP SCHEMA %s CASCADE" % schema)
        url = "%s%soptions=-csearch_path%%3D%s" % (
            TEST_DATABASE_URL, "&" if "?" in TEST_DATABASE_URL else "?", schema)
        patcher = mock.patch.object(config, "DATABASE_URL", url)
    else:
        directory = tempfile.TemporaryDirectory(prefix="ff-admin-scripts-")
        test.addCleanup(directory.cleanup)
        patcher = mock.patch.multiple(config, DATABASE_URL=None,
                                      DATABASE_PATH=os.path.join(directory.name, "admins.db"))
    patcher.start()
    test.addCleanup(patcher.stop)


def table_names() -> set:
    """Tables in the test database, found without creating anything."""
    if database.using_postgres():
        rows = database.query_all("SELECT table_name FROM information_schema.tables "
                                  "WHERE table_schema = current_schema()")
    elif os.path.exists(config.DATABASE_PATH):
        rows = database.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    else:
        return set()
    return {row[0] for row in rows}


def initialize():
    """The current schema, as the app's boot creates it."""
    with contextlib.redirect_stdout(io.StringIO()):
        database.init_db()


def make_legacy_database(email="Legacy.Owner@Example.com"):
    """The schema the old single-admin code left behind, with its one admin."""
    postgres = database.using_postgres()
    legacy = LEGACY_ADMIN_TABLES.format(
        id="INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY" if postgres
        else "INTEGER PRIMARY KEY")
    # The field tables come from today's schema, minus its two login tables.
    today = [part.strip() for part in database.schema().split(";") if part.strip()]
    statements = [part.strip() for part in legacy.split(";") if part.strip()] + [
        s for s in today if not s.startswith(("CREATE TABLE IF NOT EXISTS admins (",
                                              "CREATE TABLE IF NOT EXISTS otps ("))]
    conn = database.get_db()
    try:
        for statement in statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO admins (username, password_hash, phone, email, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("admin", "$2b$12$" + "x" * 53, None, email, "2026-01-01T00:00:00+00:00"))
        conn.commit()
    finally:
        conn.close()


def add_admin(email, name, role=auth.ROLE_ADMIN, active=True) -> str:
    """An admin in any state; returns its id.

    Deactivation is written directly: the scripts must cope with states the
    Admins page refuses to create, such as no active owner at all (a database
    edited by hand, or every owner deactivated before that guard existed).
    """
    admin = auth.create_admin(email, name, role=role)
    if not active:
        database.execute("UPDATE admins SET is_active = ? WHERE id = ?", (False, admin["id"]))
    return admin["id"]


def add_field_data():
    """One row in each table reset_admin must never touch."""
    now = database.now_iso()
    farm_id = database.execute(
        "INSERT INTO farms (farm_name, date, n_sections, mean_health_score, "
        "result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("North 40", "2026-10-01", 1, 71.5, "{}", now))
    database.execute(
        "INSERT INTO samples (farm_id, sample_id, section_label, crop_type, latitude, "
        "longitude, ph, ec, nitrogen, phosphorus, potassium) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (farm_id, "%s-A1" % farm_id, "A1", "Corn", 30.62, -96.33, 6.5, 0.4, 20.0, 30.0, 150.0))
    database.execute(
        "INSERT INTO results (farm_id, section_label, health_score, health_category, "
        "viable_crops, recommendations) VALUES (?, ?, ?, ?, ?, ?)",
        (farm_id, "A1", 71.5, "Good", "[]", "[]"))
    database.execute(
        "INSERT INTO contacts (name, email, message, created_at) VALUES (?, ?, ?, ?)",
        ("Ada", CONTACT_EMAIL, "hello", now))


def everything() -> dict:
    """Every row of every table a script could conceivably write, in order."""
    return {table: [tuple(row) for row in database.query_all(
                "SELECT * FROM %s ORDER BY id" % table)]
            for table in ("admins",) + reset_admin.PRESERVED_TABLES}


# ---------------------------------------------------------------------------
# Running the scripts
# ---------------------------------------------------------------------------
def _captured(call, reply=None):
    """Run `call` with stdout and stderr captured and input() replaced.

    Returns (exit code, everything printed, the input() mock). `reply` is what
    the operator types at the prompt, or an exception such as EOFError. With
    no reply, a confirmation prompt fails the test instead of waiting forever.
    """
    if reply is None:
        prompt = mock.Mock(side_effect=AssertionError("unexpected confirmation prompt"))
    elif isinstance(reply, str):
        prompt = mock.Mock(return_value=reply)
    else:
        prompt = mock.Mock(side_effect=reply)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
            mock.patch("builtins.input", prompt):
        code = call()
    return code, output.getvalue(), prompt


def run_reset(*argv, reply=None):
    return _captured(lambda: reset_admin.main(list(argv)), reply)


def run_inspect(*argv):
    code, output, _ = _captured(lambda: inspect_admin.main(list(argv)))
    return code, output


def run_check(*argv):
    code, output, _ = _captured(lambda: check_db_connection.main(list(argv)))
    return code, output


class ScriptTestCase(unittest.TestCase):
    """An empty database of its own per test, and no settings from the fixture."""

    def setUp(self):
        use_empty_database(self)
        # The fixture's seeded owner and the developer's .env must not decide the
        # default target, a new owner's name or whether email is configured.
        settings = mock.patch.multiple(
            config, DEFAULT_ADMIN_EMAIL=None, DEFAULT_ADMIN_NAME=None,
            RESEND_API_KEY=None, RESEND_FROM=config.RESEND_TEST_SENDER)
        settings.start()
        self.addCleanup(settings.stop)

    def assertNotPrinted(self, output, *secrets):
        for secret in secrets:
            self.assertNotIn(secret, output)


# ---------------------------------------------------------------------------
# reset_admin
# ---------------------------------------------------------------------------
class ResetAdminTests(ScriptTestCase):
    def test_creates_an_owner_in_a_brand_new_database(self):
        """The fresh-disk case: no tables at all, and no DEFAULT_ADMIN_EMAIL."""
        code, output, _ = run_reset("--email", "  New.Owner@Example.COM ", "--yes")
        self.assertEqual(code, 0, output)
        self.assertTrue(set(database.TABLES) <= table_names())
        [admin] = auth.list_admins()
        self.assertEqual(
            (admin["email"], admin["display_name"], admin["role"], admin["is_active"],
             admin["last_login_at"]),
            ("new.owner@example.com", "new.owner", "owner", True, None))
        self.assertIn("created new.owner@example.com as an active owner", output)

    def test_a_new_owner_is_named_by_name_or_default_admin_name(self):
        initialize()
        with mock.patch.object(config, "DEFAULT_ADMIN_NAME", "Farm Manager"):
            self.assertEqual(run_reset("--email", "first@example.com", "--yes")[0], 0)
            self.assertEqual(run_reset("--email", "second@example.com",
                                       "--name", "  Bea Lopez ", "--yes")[0], 0)
        self.assertEqual(auth.find_admin_by_email("first@example.com")["display_name"],
                         "Farm Manager")
        self.assertEqual(auth.find_admin_by_email("second@example.com")["display_name"],
                         "Bea Lopez")

    def test_promotes_and_reactivates_an_existing_admin(self):
        initialize()
        cases = [("admin", True, ["role admin -> owner"]),
                 ("owner", False, ["reactivated"]),
                 ("admin", False, ["role admin -> owner", "reactivated"])]
        for number, (role, active, expected) in enumerate(cases):
            email = "person%d@example.com" % number
            with self.subTest(role=role, active=active):
                add_admin(email, "Person %d" % number, role=role, active=active)
                code, output, _ = run_reset("--email", email, "--yes")
                self.assertEqual(code, 0, output)
                admin = auth.find_admin_by_email(email)
                # Promoted and reactivated, but not renamed: --name wasn't given.
                self.assertEqual((admin["role"], admin["is_active"], admin["display_name"]),
                                 ("owner", True, "Person %d" % number))
                self.assertIn("Done: %s is an active owner (%s)."
                              % (email, ", ".join(expected)), output)

    def test_name_renames_an_existing_admin(self):
        initialize()
        add_admin("alice@example.com", "Alice", role=auth.ROLE_OWNER)
        code, output, _ = run_reset("--email", "alice@example.com",
                                    "--name", "Alice Smith", "--yes")
        self.assertEqual(code, 0, output)
        self.assertIn("rename: 'Alice' -> 'Alice Smith'", output)
        self.assertEqual(auth.find_admin_by_email("alice@example.com")["display_name"],
                         "Alice Smith")

    def test_nothing_to_do_for_an_active_owner(self):
        """Exit 0 with no prompt (run_reset fails on one) and no write."""
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        before = everything()
        for argv in (["--email", "owner@example.com"],
                     ["--email", "owner@example.com", "--name", "Olive"]):
            with self.subTest(argv=argv):
                code, output, _ = run_reset(*argv)
                self.assertEqual(code, 0, output)
                self.assertIn("Nothing to do: it is already an active owner.", output)
        self.assertEqual(everything(), before)

    def test_dry_run_changes_nothing(self):
        initialize()
        add_admin("alice@example.com", "Alice", active=False)
        add_field_data()
        before = everything()
        code, output, _ = run_reset("--email", "alice@example.com",
                                    "--name", "Alice Smith", "--dry-run")
        self.assertEqual(code, 0, output)
        for planned in ("promote: role admin -> owner", "reactivate",
                        "rename: 'Alice' -> 'Alice Smith'"):
            self.assertIn(planned, output)
        self.assertIn("--dry-run: nothing was changed.", output)
        self.assertEqual(everything(), before)

    def test_dry_run_creates_no_tables(self):
        """Not even through the init_db() a real run starts with."""
        code, output, _ = run_reset("--email", "new@example.com", "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("no admins table yet", output)
        self.assertIn("as an active owner named 'new'", output)
        self.assertEqual(table_names(), set())
        if not database.using_postgres():
            self.assertFalse(os.path.exists(config.DATABASE_PATH))

    def test_dry_run_does_not_migrate_a_legacy_database(self):
        make_legacy_database()
        tables_before = table_names()
        code, output, _ = run_reset("--email", "new@example.com", "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("old single-admin shape", output)
        self.assertEqual(check_db_connection.admins_table_state(), "legacy")
        self.assertEqual(table_names(), tables_before)

    def test_declining_the_prompt_changes_nothing(self):
        initialize()
        add_admin("alice@example.com", "Alice", active=False)
        before = everything()
        # "n", a bare Enter, and Ctrl-D all mean no.
        for reply in ("n", "", EOFError):
            with self.subTest(reply=reply):
                code, output, prompt = run_reset("--email", "alice@example.com", reply=reply)
                self.assertEqual(code, 2, output)
                self.assertIn("Aborted -- no admin was changed.", output)
                self.assertIn("alice@example.com", prompt.call_args[0][0])
                self.assertEqual(everything(), before)

        code, output, _ = run_reset("--email", "alice@example.com", reply="y")
        self.assertEqual(code, 0, output)
        self.assertTrue(auth.find_admin_by_email("alice@example.com")["is_active"])

    def test_bad_arguments_exit_2_before_the_database_is_opened(self):
        cases = [
            ([], None),                                     # no --email, no default
            ([], "not-an-email"),                           # an unusable default
            (["--email", "not-an-email"], None),
            # An explicit empty --email is a typo, not "use DEFAULT_ADMIN_EMAIL".
            (["--email", ""], "owner@example.com"),
            (["--email", "a@example.com", "--name", "   "], None),
            (["--email", "a@example.com", "--name", "x" * 101], None),
            (["--email", "a@example.com", "--name", "tab\there"], None),
        ]
        for argv, default in cases:
            with self.subTest(argv=argv, default=default), \
                    mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", default):
                code, output, _ = run_reset(*argv, "--yes")
                self.assertEqual(code, 2, output)
                self.assertIn("error:", output)
                self.assertEqual(table_names(), set())

    def test_an_unusable_default_name_asks_for_name(self):
        initialize()
        with mock.patch.object(config, "DEFAULT_ADMIN_NAME", "x" * 101):
            code, output, _ = run_reset("--email", "new@example.com", "--yes")
        self.assertEqual(code, 2, output)
        self.assertIn("Pass --name.", output)
        self.assertEqual(auth.list_admins(), [])

    def test_default_admin_email_is_the_default_target(self):
        initialize()
        with mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", " Boss@Example.com "):
            code, output, _ = run_reset("--yes")
        self.assertEqual(code, 0, output)
        self.assertEqual(auth.find_admin_by_email("boss@example.com")["role"], "owner")
        # The operator's own setting: shown in full, with where it came from, so
        # they can tell which address is about to become an owner.
        self.assertIn("boss@example.com (from DEFAULT_ADMIN_EMAIL in", output)

    def test_other_admins_and_field_data_are_untouched(self):
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        add_admin("alice@example.com", "Alice")
        add_admin("bob@example.com", "Bob", active=False)
        database.execute("UPDATE admins SET last_login_at = ? WHERE email = ?",
                         (database.now_iso(), "owner@example.com"))
        add_field_data()
        before = everything()

        code, output, _ = run_reset("--email", "bob@example.com", "--name", "Robert", "--yes")
        self.assertEqual(code, 0, output)
        after = everything()
        for table in reset_admin.PRESERVED_TABLES:
            self.assertEqual(after[table], before[table], table)

        def others(snapshot):
            return [row for row in snapshot["admins"] if "bob@example.com" not in row]
        self.assertEqual(others(after), others(before))
        bob = auth.find_admin_by_email("bob@example.com")
        self.assertEqual((bob["role"], bob["is_active"], bob["display_name"]),
                         ("owner", True, "Robert"))
        self.assertIn("Other admins verified unchanged: 2 row(s).", output)

    def test_guard_fails_loudly_when_anything_else_changes(self):
        """ensure_owner() only touches the target today; prove the guard would notice."""
        initialize()
        owner_id = add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        add_admin("alice@example.com", "Alice")
        add_field_data()
        real_ensure_owner = auth.ensure_owner

        def deactivates_the_owner_too(email, name=None):
            result = real_ensure_owner(email, name)
            auth.set_admin_active(owner_id, False)
            return result

        def deletes_the_contacts_too(email, name=None):
            result = real_ensure_owner(email, name)
            database.execute("DELETE FROM contacts")
            return result

        cases = [("alice@example.com", deactivates_the_owner_too,
                  "other admin row(s) CHANGED: ow***@example.com"),
                 ("carol@example.com", deletes_the_contacts_too,
                  "preserved table counts CHANGED")]
        for target, misbehaving, complaint in cases:
            with self.subTest(misbehaving.__name__), \
                    mock.patch.object(auth, "ensure_owner", side_effect=misbehaving):
                code, output, _ = run_reset("--email", target, "--yes")
                self.assertEqual(code, 1, output)
                self.assertIn(complaint, output)

    def test_refuses_to_migrate_a_legacy_database(self):
        """Only the app's own boot runs the one-time migration. Pointed at
        production from a newer checkout, this script must not rewrite the
        schema under the deploy that is still serving it."""
        make_legacy_database(email="Legacy.Owner@Example.com")
        before = table_names()
        code, output, _ = run_reset("--email", "new@example.com", "--yes")
        self.assertEqual(code, 1, output)
        self.assertIn("old single-admin schema", output)
        self.assertIn("Nothing was changed", output)
        self.assertEqual(table_names(), before)
        self.assertNotIn("admins_legacy", table_names())
        self.assertTrue(database.needs_admin_migration())
        self.assertNotPrinted(output, "legacy.owner@example.com", "Legacy.Owner@Example.com")

    def test_restores_an_owner_once_the_app_has_migrated(self):
        make_legacy_database(email="Legacy.Owner@Example.com")
        with contextlib.redirect_stdout(io.StringIO()):
            database.init_db(migrate=True)          # what the app's first boot does
        code, output, _ = run_reset("--email", "new@example.com", "--yes")
        self.assertEqual(code, 0, output)
        roles = {admin["email"]: admin["role"] for admin in auth.list_admins()}
        self.assertEqual(roles, {"legacy.owner@example.com": "owner",
                                 "new@example.com": "owner"})
        self.assertNotPrinted(output, "legacy.owner@example.com", "Legacy.Owner@Example.com")

    def test_prints_no_full_email_but_the_one_typed(self):
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        add_admin("alice@example.com", "Alice")
        add_admin("bob@example.com", "Bob", active=False)
        add_field_data()
        with mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", "owner@example.com"):
            code, output, _ = run_reset("--email", "alice@example.com", "--yes")
            self.assertEqual(code, 0, output)
            self.assertIn("alice@example.com", output)
            self.assertNotPrinted(output, "owner@example.com", "bob@example.com", CONTACT_EMAIL)

            # Without --email the target is the operator's own DEFAULT_ADMIN_EMAIL,
            # shown in full (with where it came from) so they can check it before
            # confirming; everyone else stays masked.
            code, output, _ = run_reset()
            self.assertEqual(code, 0, output)
            self.assertIn("owner@example.com", output)
            self.assertIn("DEFAULT_ADMIN_EMAIL in", output)
            self.assertNotPrinted(output, "alice@example.com", "bob@example.com", CONTACT_EMAIL)

    def test_a_real_run_lifts_the_login_throttle_on_that_address(self):
        """Someone hammering an owner's address can keep new codes from being
        issued; restoring owner access has to clear that, or the documented
        remedy for "locked out" does nothing."""
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)

        def burnt_codes(email, n):
            for _ in range(n):
                database.execute(
                    "INSERT INTO otps (id, email, admin_id, code, expires_at, attempts, "
                    "consumed, created_at) VALUES (?, ?, NULL, '000000', ?, 3, 1, ?)",
                    (database.new_id(), email, database.now_iso(), database.now_iso()))

        def rows(email):
            return database.query_one("SELECT COUNT(*) AS n FROM otps WHERE email = ?",
                                      (email,))["n"]

        burnt_codes("owner@example.com", 4)
        burnt_codes("someone.else@example.com", 2)
        code, output, _ = run_reset("--email", "owner@example.com", "--dry-run")
        self.assertEqual((code, rows("owner@example.com")), (0, 4), output)
        code, output, _ = run_reset("--email", "owner@example.com", "--yes")
        self.assertEqual(code, 0, output)
        self.assertEqual(rows("owner@example.com"), 0)
        self.assertEqual(rows("someone.else@example.com"), 2)

    def test_a_lost_race_is_an_error_that_changes_nothing(self):
        """Someone else added the same address between the check and the insert."""
        initialize()
        with mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", "boss@example.com"), \
                mock.patch.object(auth, "ensure_owner",
                                  side_effect=auth.AdminExists("boss@example.com")):
            code, output, _ = run_reset("--yes")
        self.assertEqual(code, 1, output)
        self.assertIn("boss@example.com was added by someone else", output)
        self.assertIn("nothing was changed", output)


# ---------------------------------------------------------------------------
# inspect_admin
# ---------------------------------------------------------------------------
class InspectAdminTests(ScriptTestCase):
    def test_exit_0_and_every_admin_listed_when_an_active_owner_exists(self):
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        add_admin("alice@example.com", "Alice", active=False)
        database.execute("UPDATE admins SET last_login_at = ? WHERE email = ?",
                         ("2026-10-20T08:00:00+00:00", "alice@example.com"))
        code, output = run_inspect()
        self.assertEqual(code, 0, output)
        self.assertIn("admins table: 2 row(s), 1 active, 1 active owner(s)", output)
        self.assertIn("ow***@example.com  'Olive'", output)
        self.assertRegex(output, r"owner, active, created \S+, last login never")
        self.assertIn("al***@example.com  'Alice'", output)
        self.assertRegex(output, r"admin, deactivated, created \S+, "
                                 r"last login 2026-10-20T08:00:00\+00:00")
        self.assertIn("1 active owner(s) can sign in", output)

    def test_exit_1_without_an_active_owner(self):
        initialize()
        code, output = run_inspect()
        self.assertEqual(code, 1, output)
        self.assertIn("Fix: python3 -m backend.reset_admin --email", output)
        self.assertIn("an empty admins", output)     # ... table is seeded from it

        # Admins, but no ACTIVE owner among them.
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER, active=False)
        add_admin("alice@example.com", "Alice")
        code, output = run_inspect()
        self.assertEqual(code, 1, output)
        self.assertIn("No active owner", output)
        self.assertIn("Fix: python3 -m backend.reset_admin --email", output)

    def test_exit_2_when_the_admins_table_cannot_be_read(self):
        code, output = run_inspect()                  # nothing created yet
        self.assertEqual(code, 2, output)

        make_legacy_database()
        code, output = run_inspect()
        self.assertEqual(code, 2, output)
        self.assertIn("old single-admin shape", output)

        with mock.patch.object(database, "get_db", side_effect=RuntimeError("no route to host")):
            code, output = run_inspect()
        self.assertEqual(code, 2, output)
        self.assertIn("could not read the admins table: RuntimeError: no route to host", output)

    def test_never_writes(self):
        """No init_db(), no seeding, and not even an empty SQLite file."""
        with mock.patch.object(database, "init_db") as init_db, \
                mock.patch.object(auth, "seed_owner") as seed_owner:
            self.assertEqual(run_inspect()[0], 2)
        init_db.assert_not_called()
        seed_owner.assert_not_called()
        self.assertEqual(table_names(), set())
        if not database.using_postgres():
            self.assertFalse(os.path.exists(config.DATABASE_PATH))

        # An empty table stays empty even with an owner to seed it from.
        initialize()
        with mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", "owner@example.com"):
            self.assertEqual(run_inspect()[0], 1)
        self.assertEqual(auth.list_admins(), [])

        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        add_field_data()
        before = everything()
        file_before = (None if database.using_postgres()
                       else open(config.DATABASE_PATH, "rb").read())
        self.assertEqual(run_inspect()[0], 0)
        self.assertEqual(everything(), before)
        if file_before is not None:
            self.assertEqual(open(config.DATABASE_PATH, "rb").read(), file_before)

    def test_never_prints_a_full_stored_email_or_key(self):
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        add_admin("alice@example.com", "Alice")
        add_admin("bob@example.com", "Bob", active=False)
        add_field_data()
        with mock.patch.multiple(config, DEFAULT_ADMIN_EMAIL="owner@example.com",
                                 RESEND_API_KEY="re_test_key_never_printed"):
            code, output = run_inspect()
        self.assertEqual(code, 0, output)
        self.assertIn("ow***@example.com", output)
        self.assertNotPrinted(output, "owner@example.com", "alice@example.com",
                              "bob@example.com", CONTACT_EMAIL, "re_test_key_never_printed")

    def test_reports_default_admin_email_and_email_delivery(self):
        initialize()
        add_admin("owner@example.com", "Olive", role=auth.ROLE_OWNER)
        output = run_inspect()[1]
        self.assertIn("DEFAULT_ADMIN_EMAIL: unset", output)
        self.assertIn("OTP email: NOT configured", output)

        # Set, but changing it after the first boot does nothing; and Resend's
        # test sender only reaches the Resend account owner.
        with mock.patch.multiple(config, DEFAULT_ADMIN_EMAIL="someone.else@example.com",
                                 RESEND_API_KEY="re_test"):
            output = run_inspect()[1]
        self.assertIn("DEFAULT_ADMIN_EMAIL: set (from ", output)
        self.assertIn("so***@example.com", output)
        self.assertIn("DEFAULT_ADMIN_EMAIL matches no admin", output)
        self.assertIn("WARNING: RESEND_FROM is Resend's shared test sender", output)

        with mock.patch.multiple(config, RESEND_API_KEY="re_test",
                                 RESEND_FROM="noreply@farmersforward.org"):
            output = run_inspect()[1]
        self.assertIn("OTP email: Resend from noreply@farmersforward.org", output)
        self.assertNotIn("WARNING", output)

        with mock.patch.object(config, "DEFAULT_ADMIN_EMAIL", "not-an-email"):
            self.assertIn("NOT a valid email address", run_inspect()[1])


# ---------------------------------------------------------------------------
# check_db_connection
# ---------------------------------------------------------------------------
class CheckDbConnectionTests(ScriptTestCase):
    def test_works_before_and_after_the_migration(self):
        make_legacy_database()
        code, output = run_check()
        self.assertEqual(code, 0, output)
        self.assertIn("admins still has the old single-admin shape", output)
        self.assertNotRegex(output, r"admins_legacy +\d+ rows")

        # --init creates missing tables but never migrates: that is the app's
        # first boot's job, so it can't break a deploy still on the old schema.
        code, output = run_check("--init")
        self.assertEqual(code, 0, output)
        self.assertIn("nothing created", output)
        self.assertEqual(check_db_connection.admins_table_state(), "legacy")

        with contextlib.redirect_stdout(io.StringIO()):
            database.init_db(migrate=True)          # the app's first boot
        code, output = run_check()
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r"admins_legacy +1 rows \(legacy, kept for rollback\)")
        self.assertNotIn("not ours", output)
        self.assertIn("python3 -m backend.inspect_admin", output)
        self.assertIn("python3 -m backend.reset_admin --email", output)
        self.assertNotPrinted(output, "DEFAULT_ADMIN_USERNAME", "DEFAULT_ADMIN_PASSWORD")
        self.assertEqual(check_db_connection.admins_table_state(), "current")

    def test_an_empty_admins_table_points_at_reset_admin(self):
        code, output = run_check()                    # nothing created yet
        self.assertEqual(code, 1, output)
        self.assertIn("MISSING expected table(s)", output)

        code, output = run_check("--init")
        self.assertEqual(code, 0, output)
        self.assertIn("The admins table is empty", output)
        self.assertIn("DEFAULT_ADMIN_EMAIL (in this shell it is unset)", output)
        self.assertIn("python3 -m backend.reset_admin --email", output)

    def test_tables_that_are_not_ours_are_still_flagged(self):
        initialize()
        database.execute("CREATE TABLE scratch (id INTEGER)")
        code, output = run_check()
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r"scratch +0 rows \(not ours\)")


if __name__ == "__main__":
    unittest.main()
