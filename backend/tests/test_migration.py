"""The one-time multi-admin migration, run against the schema production has now.

Every deployment that predates multi-admin profiles -- production included --
has the single-admin `admins` table (username, password_hash, phone, email).
init_db() upgrades it on the first boot of the new code, unattended, in every
gunicorn worker at once, against live data. The rest of the suite never sees
that path: its fixture always starts from an empty database. So every test
here starts from LEGACY_SCHEMA_SQLITE / LEGACY_SCHEMA_POSTGRES, the CREATE
TABLEs main ships today, and runs on both backends: SQLite in a temp file, and
Postgres in a throwaway schema of the FF_TEST_DATABASE_URL database (skipped
when that is unset).

init_db() keeps running on every boot after the migration, so the central check
is that a second run is a no-op. Every table's rows, the schema (sqlite_master
and its schema cookie; the Postgres catalogs, oids included) and the output are
compared after the first run and after the second.
"""

import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import REPO_ROOT, TEST_DATABASE_URL, database  # noqa: E402

# ---------------------------------------------------------------------------
# The legacy schema, copied verbatim from `git show main:backend/database.py`
# (SCHEMA_SQLITE and SCHEMA_POSTGRES there). It is what every existing
# deployment, production included, has today. Never edit it to follow the new
# code: it is the starting point the migration has to handle.
# ---------------------------------------------------------------------------
LEGACY_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone         TEXT,
    email         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS otps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    consumed   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS farms (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_name         TEXT NOT NULL,
    date              TEXT NOT NULL,
    n_sections        INTEGER NOT NULL,
    mean_health_score REAL,
    result_json       TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id       INTEGER NOT NULL,
    sample_id     TEXT NOT NULL,
    section_label TEXT NOT NULL,
    crop_type     TEXT,
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    ph            REAL,
    ec            REAL,
    nitrogen      REAL,
    phosphorus    REAL,
    potassium     REAL
);

CREATE TABLE IF NOT EXISTS results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    farm_id         INTEGER NOT NULL,
    section_label   TEXT NOT NULL,
    health_score    REAL,
    health_category TEXT,
    viable_crops    TEXT,
    recommendations TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

LEGACY_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone         TEXT,
    email         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS otps (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admin_id   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    consumed   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS farms (
    id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    farm_name         TEXT NOT NULL,
    date              TEXT NOT NULL,
    n_sections        INTEGER NOT NULL,
    mean_health_score DOUBLE PRECISION,
    result_json       TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS samples (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    farm_id       INTEGER NOT NULL,
    sample_id     TEXT NOT NULL,
    section_label TEXT NOT NULL,
    crop_type     TEXT,
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    ph            DOUBLE PRECISION,
    ec            DOUBLE PRECISION,
    nitrogen      DOUBLE PRECISION,
    phosphorus    DOUBLE PRECISION,
    potassium     DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS results (
    id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    farm_id         INTEGER NOT NULL,
    section_label   TEXT NOT NULL,
    health_score    DOUBLE PRECISION,
    health_category TEXT,
    viable_crops    TEXT,
    recommendations TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# The rollback in README.md ("Admin accounts: one-time migration"). On Supabase
# it runs once in the SQL editor, after the previous deploy is already live; the
# parked table's name is README's example (any unused name works).
ROLLBACK_SQL_POSTGRES = """BEGIN;
DROP TABLE otps;
ALTER TABLE admins RENAME TO admins_v2_20261023;
ALTER TABLE admins_legacy RENAME TO admins;
CREATE TABLE otps (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admin_id   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    consumed   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
ALTER TABLE otps ENABLE ROW LEVEL SECURITY;
COMMIT;"""
# For the SQLite file README says: the same SQL minus the statements that
# recreate otps, run with the app stopped. The previous version, started
# afterwards, recreates otps itself.
ROLLBACK_SQL_SQLITE = """BEGIN;
DROP TABLE otps;
ALTER TABLE admins RENAME TO admins_v2_20261023;
ALTER TABLE admins_legacy RENAME TO admins;
COMMIT;"""
PARKED_TABLE = "admins_v2_20261023"

LEGACY_ADMIN_COLUMNS = ["id", "username", "password_hash", "phone", "email", "created_at"]
LEGACY_OTP_COLUMNS = ["id", "admin_id", "code", "expires_at", "attempts", "consumed",
                      "created_at"]
INSERT_LEGACY_ADMIN = ("INSERT INTO admins (username, password_hash, phone, email, created_at) "
                       "VALUES (?, ?, ?, ?, ?)")
# (username, password_hash, phone, email, created_at). The hashes stand in for
# bcrypt ones. The owner's email is stored the way someone typed it, which the
# migration has to normalize.
LEGACY_OWNER = ("admin", "$2b$12$legacy-owner-password-hash", "+15555550100",
                "  Owner@Example.COM ", "2025-09-13T16:17:46.123456+00:00")
LEGACY_HELPER = ("helper", "$2b$12$legacy-helper-password-hash", None,
                 "Helper@Example.com", "2026-01-05T08:00:00.000001+00:00")
# A login code still in flight when the new code deploys.
INSERT_LEGACY_OTP = ("INSERT INTO otps (admin_id, code, expires_at, attempts, consumed, "
                     "created_at) VALUES (1, '482913', '2026-10-23T18:46:35+00:00', 1, 0, "
                     "'2026-10-23T18:41:35+00:00')")

# The same two rows in the new shape.
INSERT_ADMIN = ("INSERT INTO admins (id, email, display_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)")
INSERT_OTP = ("INSERT INTO otps (id, email, admin_id, code, expires_at, attempts, consumed, "
              "created_at) VALUES (?, ?, ?, '482913', '2026-10-23T18:46:35+00:00', 0, 0, "
              "'2026-10-23T18:41:35+00:00')")

# One submitted field. The migration must leave these tables' rows and columns
# exactly as they were.
DATA_TABLES = ("farms", "samples", "results", "contacts")
FIELD_DATA = (
    ("INSERT INTO farms (farm_name, date, n_sections, mean_health_score, result_json, "
     "created_at) VALUES (?, ?, ?, ?, ?, ?)",
     ("North 40", "2026-05-01", 1, 71.5, '{"results": []}', "2026-05-01T15:00:00+00:00")),
    ("INSERT INTO samples (farm_id, sample_id, section_label, crop_type, latitude, longitude, "
     "ph, ec, nitrogen, phosphorus, potassium) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
     (1, "1-A1", "A1", "Corn", 30.628, -96.3344, 6.5, 1.0, 10.0, 25.0, 180.0)),
    ("INSERT INTO results (farm_id, section_label, health_score, health_category, "
     "viable_crops, recommendations) VALUES (?, ?, ?, ?, ?, ?)",
     (1, "A1", 71.5, "Good", '["Corn"]', "[]")),
    ("INSERT INTO contacts (name, email, message, created_at) VALUES (?, ?, ?, ?)",
     ("Ada", "ada@example.com", "Is spring sampling open?", "2026-05-02T09:30:00+00:00")),
)

LOGIN_TABLES = ("admins", "otps")
ADMIN_TABLES = LOGIN_TABLES + ("admins_legacy",)
# The address main's config filled in when DEFAULT_ADMIN_EMAIL was unset, so a
# legacy row may well carry it. Nobody reads that inbox.
OLD_PLACEHOLDER_EMAIL = "admin@farmersforward.org"
BOOTING_WORKERS = 6


def without_email(admin):
    """A legacy admin row with its email column NULL."""
    return admin[:3] + (None,) + admin[4:]


def boot_in_thread(errors, barrier=None):
    """init_db() as one gunicorn worker runs it; exceptions are collected."""
    try:
        if barrier is not None:
            barrier.wait(timeout=30)
        database.init_db()
    except Exception as exc:  # noqa: BLE001 - the caller asserts there were none
        errors.append(exc)


class MigrationChecks:
    """The migration's behaviour, written once and run against both backends.

    A subclass supplies the backend. It makes the databases (new_database),
    reads them back over its own driver connection, so nothing checked here goes
    through the code under test (query, run_script, snapshot, shape,
    column_types, table_names), and performs README's rollback the way it is
    documented for that backend (roll_back).
    """

    legacy_schema = None
    # otps in its new shape: (column, type) in table order, per the contract.
    otps_columns = None

    def setUp(self):
        self.configure(DEFAULT_ADMIN_EMAIL=None, DEFAULT_ADMIN_NAME=None)
        self.switch_to(self.new_database())

    def configure(self, **settings):
        """Patch config for the rest of the test."""
        patcher = mock.patch.multiple(database.config, **settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def switch_to(self, settings):
        """Point config, and this test's own queries, at another database."""
        self.configure(**settings)
        self.settings = settings

    def boot(self):
        """init_db(), as app.py runs it on every boot; returns what it printed."""
        with contextlib.redirect_stdout(io.StringIO()) as out:
            database.init_db()
        return out.getvalue()

    def create_legacy_database(self, *admins):
        """The legacy schema with rows in every table: `admins` (by default the
        owner alone), a login code in flight, and one submitted field."""
        self.run_script(self.legacy_schema)
        for admin in admins or (LEGACY_OWNER,):
            self.query(INSERT_LEGACY_ADMIN, admin)
        self.query(INSERT_LEGACY_OTP)
        self.add_field_data()

    def add_field_data(self):
        for statement, params in FIELD_DATA:
            self.query(statement, params)

    def admins(self):
        """The new admins table as dicts, oldest first."""
        keys = ("id", "email", "display_name", "role", "is_active", "created_at",
                "last_login_at")
        rows = self.query("SELECT %s FROM admins ORDER BY created_at, email" % ", ".join(keys))
        return [dict(zip(keys, row)) for row in rows]

    def legacy_rows(self):
        return self.query("SELECT %s FROM admins_legacy ORDER BY id"
                          % ", ".join(LEGACY_ADMIN_COLUMNS))

    # -- the user's requirement: a second boot is a no-op ------------------
    def test_second_boot_after_the_migration_is_a_no_op(self):
        self.create_legacy_database(LEGACY_OWNER, LEGACY_HELPER)
        legacy = self.snapshot()
        self.assertIn("migrated 2 legacy admin row(s)", self.boot())
        migrated = self.snapshot()

        self.assertEqual(self.boot(), "", "the second boot printed something")
        self.assertEqual(self.snapshot(), migrated)
        # And the data that neither boot was meant to change is all there.
        self.assertEqual(migrated["rows"]["admins_legacy"], legacy["rows"]["admins"])
        for table in DATA_TABLES:
            self.assertEqual(migrated["rows"][table], legacy["rows"][table], table)

    def test_second_boot_of_a_fresh_database_is_a_no_op(self):
        """A brand-new database (local dev, a new Supabase project) has no
        legacy table at all, and boots over and over like any other."""
        self.assertNotIn("migrated", self.boot())
        owner_id = str(uuid.uuid4())
        self.query(INSERT_ADMIN, (owner_id, "owner@example.com", "Owner", "owner", True,
                                  "2026-10-23T18:41:35+00:00"))
        self.query(INSERT_OTP, (str(uuid.uuid4()), "owner@example.com", owner_id))
        self.add_field_data()
        first = self.snapshot()

        self.assertEqual(self.boot(), "", "the second boot printed something")
        self.assertEqual(self.snapshot(), first)

    # -- what the migration produces ----------------------------------------
    def test_legacy_admin_becomes_the_owner(self):
        # DEFAULT_ADMIN_EMAIL only stands in for a missing email; this row has one.
        self.configure(DEFAULT_ADMIN_NAME="  Ada Lovelace ",
                       DEFAULT_ADMIN_EMAIL="fallback@example.com")
        self.create_legacy_database()
        legacy = self.snapshot()
        field_columns = {table: self.column_types(table) for table in DATA_TABLES}
        output = self.boot()

        self.assertIn("[admin-auth] migrated 1 legacy admin row(s) into the new admins table "
                      "(owner: ow***@example.com); the old table is kept as admins_legacy\n",
                      output)
        [owner] = self.admins()
        self.assertEqual(str(uuid.UUID(owner["id"])), owner["id"], "not a canonical uuid")
        self.assertEqual({key: value for key, value in owner.items() if key != "id"}, {
            "email": "owner@example.com",        # trimmed and lowercased
            "display_name": "Ada Lovelace",      # DEFAULT_ADMIN_NAME, trimmed
            "role": "owner",
            "is_active": True,
            "created_at": LEGACY_OWNER[4],       # preserved
            "last_login_at": None,
        })
        # The legacy row stays exactly as it was, password hash and all, so the
        # previous deploy can be restored.
        self.assertEqual(self.legacy_rows(), [(1,) + LEGACY_OWNER])
        # otps comes back in its new shape, and the code in flight is gone.
        self.assertEqual(self.column_types("otps"), self.otps_columns)
        self.assertEqual(self.query("SELECT COUNT(*) FROM otps"), [(0,)])
        # Field data and its tables' columns are untouched. (On Postgres the boot
        # does enable row-level security on them; see the RLS test.)
        self.assertEqual({table: self.column_types(table) for table in DATA_TABLES},
                         field_columns)
        after = self.snapshot()
        for table in DATA_TABLES:
            self.assertEqual(after["rows"][table], legacy["rows"][table], table)

    def test_owner_name_falls_back_to_the_old_username(self):
        self.create_legacy_database()
        self.boot()
        [owner] = self.admins()
        self.assertEqual(owner["display_name"], "admin")

    def test_oldest_legacy_admin_becomes_owner_and_the_rest_admins(self):
        self.configure(DEFAULT_ADMIN_NAME="Ada Lovelace")
        self.create_legacy_database(LEGACY_OWNER, LEGACY_HELPER)
        self.assertIn("migrated 2 legacy admin row(s)", self.boot())
        owner, helper = self.admins()
        self.assertEqual((owner["email"], owner["role"], owner["display_name"]),
                         ("owner@example.com", "owner", "Ada Lovelace"))
        # DEFAULT_ADMIN_NAME names the owner only; everyone else keeps a username.
        self.assertEqual((helper["email"], helper["role"], helper["display_name"]),
                         ("helper@example.com", "admin", "helper"))
        self.assertEqual((helper["is_active"], helper["created_at"], helper["last_login_at"]),
                         (True, LEGACY_HELPER[4], None))
        self.assertNotEqual(owner["id"], helper["id"])

    def test_owner_without_a_usable_email_gets_default_admin_email(self):
        """Missing, blank, malformed, or the placeholder the old config filled
        in: none of them reaches anyone, so the owner gets DEFAULT_ADMIN_EMAIL."""
        self.configure(DEFAULT_ADMIN_EMAIL=" Fallback@Example.com ")
        for email in (None, "   ", "not-an-email", OLD_PLACEHOLDER_EMAIL):
            with self.subTest(email=email):
                self.switch_to(self.new_database())
                self.create_legacy_database(LEGACY_OWNER[:3] + (email,) + LEGACY_OWNER[4:])
                self.boot()
                [owner] = self.admins()
                self.assertEqual((owner["email"], owner["role"]),
                                 ("fallback@example.com", "owner"))

    def test_the_old_default_address_is_kept_when_nothing_replaces_it(self):
        """admin@farmersforward.org was the old built-in default, but it is on the
        production domain and the old code emailed codes there, so it may be a
        real inbox. Without a DEFAULT_ADMIN_EMAIL to replace it, the owner keeps
        it (with a warning) instead of being dropped, which would leave nobody
        able to sign in."""
        self.configure(DEFAULT_ADMIN_EMAIL=None)
        self.create_legacy_database(LEGACY_OWNER[:3] + (OLD_PLACEHOLDER_EMAIL,) + LEGACY_OWNER[4:])
        output = self.boot()
        self.assertIn("migrated 1 legacy admin row(s)", output)
        self.assertIn("old default address", output)
        [owner] = self.admins()
        self.assertEqual((owner["email"], owner["role"]), (OLD_PLACEHOLDER_EMAIL, "owner"))

    def test_owner_without_any_email_is_skipped_with_a_warning(self):
        """Nobody could ever sign in as an owner with no address, so the row is
        left in admins_legacy instead of becoming a dead account."""
        no_email = without_email(LEGACY_OWNER)
        self.create_legacy_database(no_email)
        output = self.boot()
        self.assertIn("WARNING", output)
        self.assertIn("'admin'", output)
        self.assertIn("migrated 0 legacy admin row(s)", output)
        self.assertEqual(self.admins(), [])
        self.assertEqual(self.legacy_rows(), [(1,) + no_email])

    def test_duplicate_legacy_email_is_skipped_with_a_warning(self):
        twin = ("admin2", "$2b$12$legacy-twin-password-hash", None, "owner@EXAMPLE.com",
                "2026-02-01T00:00:00+00:00")
        self.create_legacy_database(LEGACY_OWNER, twin)
        output = self.boot()
        self.assertIn("WARNING", output)
        self.assertIn("'admin2'", output)
        self.assertIn("migrated 1 legacy admin row(s)", output)
        [owner] = self.admins()
        self.assertEqual((owner["email"], owner["display_name"]), ("owner@example.com", "admin"))
        self.assertEqual(self.legacy_rows(), [(1,) + LEGACY_OWNER, (2,) + twin])

    def test_other_admin_without_a_usable_email_is_skipped_with_a_warning(self):
        """DEFAULT_ADMIN_EMAIL is the owner's address, never anyone else's."""
        self.configure(DEFAULT_ADMIN_EMAIL="fallback@example.com")
        unusable = LEGACY_HELPER[:3] + ("not-an-email",) + LEGACY_HELPER[4:]
        self.create_legacy_database(LEGACY_OWNER, unusable)
        output = self.boot()
        self.assertIn("WARNING", output)
        self.assertIn("'helper'", output)
        self.assertIn("migrated 1 legacy admin row(s)", output)
        self.assertEqual([admin["email"] for admin in self.admins()], ["owner@example.com"])
        self.assertEqual(self.legacy_rows(), [(1,) + LEGACY_OWNER, (2,) + unusable])

    def test_leftover_admins_legacy_stops_the_migration_and_changes_nothing(self):
        """Two tables that both look like the legacy one need a person to
        decide. init_db() must refuse, and leave the database exactly as it was."""
        self.create_legacy_database()
        self.run_script("CREATE TABLE admins_legacy (id INTEGER PRIMARY KEY, username TEXT)")
        self.query("INSERT INTO admins_legacy (id, username) VALUES (1, 'admin')")
        # init_db() creates missing tables before it reaches the migration, so
        # with contacts gone the transaction has real work to roll back.
        self.run_script("DROP TABLE contacts")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "admins_legacy"):
            self.boot()
        self.assertEqual(self.snapshot(), before)

    def test_crash_part_way_through_leaves_the_legacy_schema_intact(self):
        self.create_legacy_database()
        before = self.snapshot()
        # new_id() is first called after admins has been renamed and otps
        # dropped and both recreated: a crash with most of the work done.
        with mock.patch.object(database, "new_id", side_effect=RuntimeError("simulated crash")):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                self.boot()
        self.assertEqual(self.snapshot(), before)
        # The next boot migrates as though nothing had happened.
        self.assertIn("migrated 1 legacy admin row(s)", self.boot())
        self.assertEqual(len(self.admins()), 1)

    def test_boot_failing_after_the_migration_rolls_it_back_and_logs_nothing(self):
        """The migration's log line waits for COMMIT, so the deploy log never
        claims a migration that was rolled back."""
        self.create_legacy_database()
        before = self.snapshot()
        # The indexes are created after the migration, in the same transaction.
        broken_indexes = "CREATE INDEX broken_idx ON no_such_table (x);"
        with mock.patch.object(database, "INDEXES", broken_indexes), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaisesRegex(Exception, "no_such_table"):
                database.init_db()
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("migrated", out.getvalue())

    def test_init_db_without_migrate_leaves_a_legacy_database_alone(self):
        """The CLI scripts call init_db(migrate=False). Run from a newer checkout
        against production, they must not migrate it under the deploy that is
        still serving the old schema; only the app's own boot does that."""
        self.create_legacy_database()
        before = self.snapshot()
        self.assertTrue(database.needs_admin_migration())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(database.LegacySchemaError):
                database.init_db(migrate=False)
        self.assertEqual((out.getvalue(), self.snapshot()), ("", before))
        # Once the app has migrated, the scripts' call is a no-op like any boot.
        self.boot()
        self.assertFalse(database.needs_admin_migration())
        migrated = self.snapshot()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            database.init_db(migrate=False)
        self.assertEqual((out.getvalue(), self.snapshot()), ("", migrated))

    def test_workers_booting_together_migrate_exactly_once(self):
        """gunicorn starts its workers together, and each one imports app.py
        and runs init_db() against the same legacy database."""
        self.create_legacy_database()
        barrier = threading.Barrier(BOOTING_WORKERS)
        errors = []
        threads = [threading.Thread(target=boot_in_thread, args=(errors, barrier), daemon=True)
                   for _ in range(BOOTING_WORKERS)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
        self.assertEqual([thread for thread in threads if thread.is_alive()], [],
                         "a boot never finished")
        self.assertEqual(errors, [])
        self.assertEqual(out.getvalue().count("migrated 1 legacy admin row(s)"), 1,
                         out.getvalue())
        [owner] = self.admins()
        self.assertEqual(owner["role"], "owner")
        self.assertEqual(self.legacy_rows(), [(1,) + LEGACY_OWNER])

    def test_migrated_login_tables_match_a_fresh_database(self):
        """An upgraded database and a brand-new one must not drift apart: the
        next schema change will be written against one and run on both."""
        self.create_legacy_database()
        self.boot()
        migrated = self.shape(LOGIN_TABLES)
        self.switch_to(self.new_database())
        self.boot()
        self.assertEqual(self.shape(LOGIN_TABLES), migrated)

    # -- the documented rollback (each backend's roll_back() follows README) --
    def test_rollback_restores_what_the_previous_deploy_expects(self):
        self.create_legacy_database()
        self.boot()
        [migrated] = self.admins()
        self.roll_back()

        self.assertEqual([name for name, _ in self.column_types("admins")], LEGACY_ADMIN_COLUMNS)
        self.assertEqual(self.query("SELECT %s FROM admins" % ", ".join(LEGACY_ADMIN_COLUMNS)),
                         [(1,) + LEGACY_OWNER])
        self.assertEqual([name for name, _ in self.column_types("otps")], LEGACY_OTP_COLUMNS)
        # The previous deploy signs in the old way: by username, then a code
        # keyed by the integer admin id.
        self.assertEqual(self.query("SELECT password_hash FROM admins WHERE username = ?",
                                    ("admin",)), [(LEGACY_OWNER[1],)])
        self.query(INSERT_LEGACY_OTP)
        # What the new code added is parked, for re-adding later.
        self.assertEqual(self.query("SELECT id, email FROM %s" % PARKED_TABLE),
                         [(migrated["id"], migrated["email"])])

    def test_redeploy_after_a_rollback_migrates_again(self):
        """README: deploying this version again after a rollback re-runs the
        migration from the legacy row, and ends up exactly like a first
        migration -- including its constraint and index names, which the parked
        table would otherwise keep (Postgres would then name the new ones
        admins_pkey1 and so on)."""
        self.create_legacy_database()
        self.boot()
        first_columns = {table: self.column_types(table) for table in LOGIN_TABLES}
        first_shape = self.shape(LOGIN_TABLES)
        self.roll_back()
        self.query(INSERT_LEGACY_OTP)  # someone signs in with the previous deploy

        self.assertIn("migrated 1 legacy admin row(s)", self.boot())
        [owner] = self.admins()
        self.assertEqual((owner["email"], owner["role"]), ("owner@example.com", "owner"))
        self.assertEqual(self.legacy_rows(), [(1,) + LEGACY_OWNER])
        self.assertEqual(self.query("SELECT COUNT(*) FROM otps"), [(0,)])
        self.assertEqual({table: self.column_types(table) for table in LOGIN_TABLES},
                         first_columns)
        self.assertEqual(self.shape(LOGIN_TABLES), first_shape)
        self.assertEqual(self.boot(), "")


class SqliteMigrationTests(MigrationChecks, unittest.TestCase):
    """Local development and the fallback when DATABASE_URL is unset."""

    legacy_schema = LEGACY_SCHEMA_SQLITE
    otps_columns = [("id", "TEXT"), ("email", "TEXT"), ("admin_id", "TEXT"), ("code", "TEXT"),
                    ("expires_at", "TEXT"), ("attempts", "INTEGER"), ("consumed", "INTEGER"),
                    ("created_at", "TEXT")]

    def new_database(self):
        directory = tempfile.mkdtemp(prefix="ff-migration-tests-")
        self.addCleanup(shutil.rmtree, directory, True)
        return {"DATABASE_URL": None, "DATABASE_PATH": os.path.join(directory, "test.db")}

    def connect(self):
        return sqlite3.connect(self.settings["DATABASE_PATH"])

    def query(self, sql, params=()):
        conn = self.connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def run_script(self, script):
        conn = self.connect()
        try:
            conn.executescript(script)
        finally:
            conn.close()

    def snapshot(self):
        conn = self.connect()
        try:
            master = conn.execute("SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master "
                                  "ORDER BY type, name").fetchall()
            tables = [name for kind, name, _, _, _ in master if kind == "table"]
            return {
                # The schema cookie. SQLite bumps it on every schema change, so it
                # also catches a change that ends up looking identical.
                "schema_version": conn.execute("PRAGMA schema_version").fetchone()[0],
                "sqlite_master": master,
                "rows": {table: sorted(conn.execute('SELECT * FROM "%s"' % table).fetchall(),
                                       key=repr) for table in tables},
            }
        finally:
            conn.close()

    def shape(self, tables):
        tables = tuple(tables)
        return self.query("SELECT type, name, tbl_name, sql FROM sqlite_master "
                          "WHERE tbl_name IN (%s) ORDER BY type, name"
                          % ", ".join("?" * len(tables)), tables)

    def column_types(self, table):
        return [(row[1], row[2]) for row in self.query("PRAGMA table_info(%s)" % table)]

    def table_names(self):
        return {name for (name,) in self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    def roll_back(self):
        """README, for the SQLite file: with the app stopped, run the SQL, then
        start the previous version, whose boot recreates otps in the old shape."""
        self.run_script(ROLLBACK_SQL_SQLITE)
        self.run_script(self.legacy_schema)

    def test_foreign_keys_hold_after_the_migration(self):
        self.create_legacy_database()
        self.boot()
        [owner] = self.admins()
        self.query(INSERT_OTP, (str(uuid.uuid4()), owner["email"], owner["id"]))
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.query("PRAGMA integrity_check"), [("ok",)])
        # With foreign keys on (get_db() turns them on), renaming a table makes
        # SQLite rewrite other tables' REFERENCES to it, so an otps created
        # before the rename would point at admins_legacy.
        self.assertEqual([row[2:5] for row in self.query("PRAGMA foreign_key_list(otps)")],
                         [("admins", "admin_id", "id")])


@unittest.skipUnless(TEST_DATABASE_URL, "Postgres migration tests need FF_TEST_DATABASE_URL "
                                        "(a throwaway Postgres database)")
class PostgresMigrationTests(MigrationChecks, unittest.TestCase):
    """Production: Supabase Postgres. Each test gets its own schema."""

    legacy_schema = LEGACY_SCHEMA_POSTGRES
    otps_columns = [("id", "uuid"), ("email", "text"), ("admin_id", "uuid"), ("code", "text"),
                    ("expires_at", "text"), ("attempts", "integer"), ("consumed", "integer"),
                    ("created_at", "text")]

    def new_database(self):
        """A throwaway schema of the FF_TEST_DATABASE_URL database; the suite's
        own tables, in its default schema, are never touched."""
        self.schemas_made = getattr(self, "schemas_made", 0) + 1
        schema = "mig_%s_%d_%d" % (self._testMethodName[5:35], os.getpid(), self.schemas_made)
        self.run_on_database("DROP SCHEMA IF EXISTS %s CASCADE; CREATE SCHEMA %s"
                             % (schema, schema))
        self.addCleanup(self.run_on_database, "DROP SCHEMA %s CASCADE" % schema)
        separator = "&" if "?" in TEST_DATABASE_URL else "?"
        return {"DATABASE_URL": "%s%soptions=-csearch_path%%3D%s"
                                % (TEST_DATABASE_URL, separator, schema)}

    def run_on_database(self, sql):
        import psycopg2
        conn = psycopg2.connect(TEST_DATABASE_URL)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                # A connection left open by a failed test fails the DROP, not hangs it.
                cur.execute("SET lock_timeout = '10s'")
                cur.execute(sql)
        finally:
            conn.close()

    def connect(self, autocommit=True):
        import psycopg2
        conn = psycopg2.connect(self.settings["DATABASE_URL"])
        conn.autocommit = autocommit
        return conn

    def query(self, sql, params=()):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql.replace("?", "%s"), tuple(params) or None)
                return cur.fetchall() if cur.description else []
        finally:
            conn.close()

    def run_script(self, script):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(script)
        finally:
            conn.close()

    def snapshot(self):
        # Every relation in the schema: tables, their indexes, identity
        # sequences. An oid changes when an object is dropped and recreated,
        # however identically.
        relations = self.query(
            "SELECT relname, relkind, oid::bigint, relrowsecurity, relforcerowsecurity, "
            "relacl::text FROM pg_class WHERE relnamespace = current_schema()::regnamespace "
            "ORDER BY relname")
        tables = [row[0] for row in relations if row[1] == "r"]
        sequences = [row[0] for row in relations if row[1] == "S"]
        return {
            "relations": relations,
            "catalog": self.shape(tables),
            "rows": {name: sorted(self.query('SELECT * FROM "%s"' % name), key=repr)
                     for name in tables + sequences},
        }

    def shape(self, tables):
        """Columns, indexes, constraints and row-level security of `tables`,
        free of oids and of the schema name, so two schemas can be compared."""
        tables = list(tables)
        return {
            "columns": self.query(
                "SELECT table_name, column_name, ordinal_position, data_type, is_nullable, "
                "column_default, is_identity FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY(?) "
                "ORDER BY table_name, ordinal_position", (tables,)),
            "indexes": self.query(
                "SELECT tablename, indexname, replace(indexdef, current_schema() || '.', '') "
                "FROM pg_indexes WHERE schemaname = current_schema() AND tablename = ANY(?) "
                "ORDER BY indexname", (tables,)),
            "constraints": self.query(
                "SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE connamespace = current_schema()::regnamespace "
                "AND conrelid::regclass::text = ANY(?) ORDER BY conname, 1", (tables,)),
            "row_level_security": self.query(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = current_schema()::regnamespace AND relname = ANY(?) "
                "ORDER BY relname", (tables,)),
        }

    def column_types(self, table):
        return self.query("SELECT column_name, data_type FROM information_schema.columns "
                          "WHERE table_schema = current_schema() AND table_name = ? "
                          "ORDER BY ordinal_position", (table,))

    def table_names(self):
        return {name for (name,) in self.query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()")}

    def roll_back(self):
        """README, on Supabase: the previous deploy goes live first and boots on
        the new schema (its init_db() is the legacy schema script, which only
        creates missing tables), then the SQL runs once. Nothing restarts."""
        self.run_script(self.legacy_schema)
        self.run_script(ROLLBACK_SQL_POSTGRES)

    def tables_without_row_level_security(self):
        return [name for name, enabled in self.query(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = current_schema()::regnamespace AND relkind = 'r' "
            "ORDER BY relname") if not enabled]

    def test_constraint_and_index_names_are_clean(self):
        """A renamed table keeps its index names, which are unique per schema.
        Without the renames the new primary key would be admins_pkey1, and a
        later migration naming admins_pkey would alter the legacy table."""
        self.create_legacy_database()
        self.boot()
        tables = list(ADMIN_TABLES)
        # Key, unique, check and foreign-key constraints. (From Postgres 18 on,
        # NOT NULL constraints are listed in pg_constraint too.)
        self.assertEqual(set(self.query(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE connamespace = current_schema()::regnamespace "
            "AND contype IN ('p', 'u', 'c', 'f') AND conrelid::regclass::text = ANY(?)",
            (tables,))), {
            ("admins", "admins_pkey"), ("admins", "admins_email_key"),
            ("admins", "admins_email_check"),
            ("admins_legacy", "admins_legacy_pkey"),
            ("admins_legacy", "admins_legacy_username_key"),
            ("otps", "otps_pkey"), ("otps", "otps_admin_id_fkey"),
        })
        self.assertEqual(set(self.query(
            "SELECT tablename, indexname FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = ANY(?)", (tables,))), {
            ("admins", "admins_pkey"), ("admins", "admins_email_key"),
            ("admins_legacy", "admins_legacy_pkey"),
            ("admins_legacy", "admins_legacy_username_key"),
            ("otps", "otps_pkey"), ("otps", "otps_email_created_idx"),
            ("otps", "otps_created_idx"),
        })

    def test_row_level_security_hides_every_table_from_the_anon_role(self):
        """Supabase grants its anon role every table in public, so anyone holding
        the project's anon key could read them over the REST API: otps with live
        login codes, admins_legacy with password hashes, contacts with visitors'
        details. With RLS on and no policies that role (ff_anon stands in for
        it) reads zero rows, while the app, connecting as the tables' owner,
        still reads everything."""
        if not self.query("SELECT 1 FROM pg_roles WHERE rolname = 'ff_anon' "
                          "AND pg_has_role(current_user, oid, 'MEMBER')"):
            self.skipTest("needs a role ff_anon that the test user is a member of")
        self.create_legacy_database()
        self.boot()
        [owner] = self.admins()
        # A live login code: the worst thing an anonymous read could return.
        self.query(INSERT_OTP, (str(uuid.uuid4()), owner["email"], owner["id"]))
        tables = sorted(self.table_names())
        self.assertEqual(self.tables_without_row_level_security(), [])
        # The grants Supabase gives anon in public.
        [(schema,)] = self.query("SELECT current_schema()")
        self.run_script("GRANT USAGE ON SCHEMA %s TO ff_anon; "
                        "GRANT SELECT ON ALL TABLES IN SCHEMA %s TO ff_anon" % (schema, schema))

        conn = self.connect(autocommit=False)
        try:
            with conn.cursor() as cur:
                cur.execute("SET ROLE ff_anon")
                cur.execute("SELECT current_user")
                self.assertEqual(cur.fetchone(), ("ff_anon",))
                self.assertEqual(self.row_counts(cur, tables), {table: 0 for table in tables})
                cur.execute("RESET ROLE")
                # Every table holds exactly one row: one admin, one login code,
                # the legacy row and one submitted field.
                self.assertEqual(self.row_counts(cur, tables), {table: 1 for table in tables})
        finally:
            conn.rollback()
            conn.close()

    @staticmethod
    def row_counts(cur, tables):
        counts = {}
        for table in tables:
            cur.execute("SELECT COUNT(*) FROM %s" % table)
            counts[table] = cur.fetchone()[0]
        return counts

    def test_row_level_security_survives_a_rollback_and_a_redeploy(self):
        """After a rollback, admins holds the password hash again and otps is a
        brand-new table; both must stay closed to the REST API, and so must the
        tables the redeploy creates."""
        self.create_legacy_database()
        self.boot()
        self.roll_back()
        self.assertEqual(self.tables_without_row_level_security(), [])
        self.boot()
        self.assertEqual(self.tables_without_row_level_security(), [])

    def test_boot_waits_for_the_init_lock(self):
        """Racing workers alone can't show the advisory lock is there: the
        ALTER TABLE samples that init_db() runs before the migration takes an
        ACCESS EXCLUSIVE lock, which happens to serialize them as well. So hold
        the lock, and watch a boot wait for it without touching anything."""
        self.create_legacy_database()
        before = self.snapshot()
        errors = []
        thread = threading.Thread(target=boot_in_thread, args=(errors,), daemon=True)
        holder = self.connect(autocommit=False)
        output = io.StringIO()
        try:
            with holder.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (database._INIT_LOCK_KEY,))
            with contextlib.redirect_stdout(output):
                thread.start()
                self.wait_for_init_lock_waiter()
                self.assertEqual(self.snapshot(), before, "init_db() ran without the lock")
                holder.rollback()  # releases the lock
                thread.join(timeout=60)
        finally:
            holder.close()
            thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "the boot never finished")
        self.assertEqual(errors, [])
        self.assertIn("migrated 1 legacy admin row(s)", output.getvalue())

    def wait_for_init_lock_waiter(self):
        """Return once another session is queued on init_db()'s advisory lock."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            [(waiting,)] = self.query(
                "SELECT COUNT(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
                "AND objid = ?", (database._INIT_LOCK_KEY & 0xFFFFFFFF,))
            if waiting:
                return
            time.sleep(0.05)
        self.fail("no boot ever waited for the init lock")


class RollbackDocumentationTests(unittest.TestCase):
    """An operator copies the rollback out of README.md, under pressure. That
    copy has to be the SQL these tests prove works."""

    def test_postgres_rollback_is_the_documented_one(self):
        with open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8") as handle:
            self.assertIn(ROLLBACK_SQL_POSTGRES, handle.read())

    def test_sqlite_rollback_is_the_documented_one_minus_the_otps_statements(self):
        documented = [statement for statement in ROLLBACK_SQL_POSTGRES.split(";\n")
                      if not statement.startswith(("CREATE TABLE otps", "ALTER TABLE otps"))]
        self.assertEqual(ROLLBACK_SQL_SQLITE.split(";\n"), documented)


if __name__ == "__main__":
    unittest.main()
