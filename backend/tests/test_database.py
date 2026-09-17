"""Data-layer tests: SQL translation, the Postgres wrapper, SQLite round-trips.

There is no Postgres server in this environment, so the psycopg2 path is driven
against a recording stub. That still covers the parts most likely to be wrong:
placeholder translation, literal `%` escaping, and the RETURNING id rewrite that
stands in for sqlite3's lastrowid.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import database  # noqa: E402


class SqlTranslationTests(unittest.TestCase):
    def test_placeholders_become_psycopg2_style(self):
        self.assertEqual(
            database.to_postgres_sql("SELECT * FROM admins WHERE username = ?"),
            "SELECT * FROM admins WHERE username = %s")
        self.assertEqual(
            database.to_postgres_sql(
                "INSERT INTO contacts (name, email, message, created_at) "
                "VALUES (?, ?, ?, ?)"),
            "INSERT INTO contacts (name, email, message, created_at) "
            "VALUES (%s, %s, %s, %s)")

    def test_literal_percent_is_escaped(self):
        """psycopg2 interprets % in any statement that carries parameters."""
        self.assertEqual(
            database.to_postgres_sql("SELECT * FROM farms WHERE farm_name LIKE ?"),
            "SELECT * FROM farms WHERE farm_name LIKE %s")
        self.assertEqual(
            database.to_postgres_sql("SELECT '100%' AS pct WHERE id = ?"),
            "SELECT '100%%' AS pct WHERE id = %s")

    def test_question_mark_inside_a_string_literal_is_left_alone(self):
        self.assertEqual(
            database.to_postgres_sql("SELECT * FROM t WHERE note = 'why?' AND id = ?"),
            "SELECT * FROM t WHERE note = 'why?' AND id = %s")

    def test_escaped_quote_does_not_end_the_literal(self):
        self.assertEqual(
            database.to_postgres_sql("SELECT 'it''s ok?' AS s WHERE id = ?"),
            "SELECT 'it''s ok?' AS s WHERE id = %s")

    def test_returning_id_is_added_to_inserts_only(self):
        statement, returns = database._with_returning_id(
            "INSERT INTO farms (farm_name) VALUES (?)")
        self.assertTrue(returns)
        self.assertTrue(statement.endswith("RETURNING id"))

        for sql in ("SELECT 1", "UPDATE farms SET date = ?", "DELETE FROM otps"):
            statement, returns = database._with_returning_id(sql)
            self.assertFalse(returns, sql)
            self.assertEqual(statement, sql)

        # An INSERT that already returns something is not rewritten twice.
        already = "INSERT INTO farms (farm_name) VALUES (?) RETURNING id"
        statement, returns = database._with_returning_id(already)
        self.assertFalse(returns)
        self.assertEqual(statement, already)

    def test_dsn_password_is_redacted(self):
        self.assertEqual(
            database.safe_dsn("postgresql://postgres.abc:hunter2@aws.pooler.supabase.com:6543/postgres"),
            "postgresql://postgres.abc:***@aws.pooler.supabase.com:6543/postgres")
        self.assertNotIn("hunter2", database.safe_dsn(
            "postgresql://u:hunter2@h:6543/db"))


class SchemaTests(unittest.TestCase):
    def test_both_dialects_define_the_same_tables(self):
        for script in (database.SCHEMA_SQLITE, database.SCHEMA_POSTGRES):
            for table in database.TABLES:
                self.assertIn("CREATE TABLE IF NOT EXISTS %s" % table, script)

    def test_postgres_schema_uses_identity_not_autoincrement(self):
        self.assertNotIn("AUTOINCREMENT", database.SCHEMA_POSTGRES)
        self.assertNotIn("REAL", database.SCHEMA_POSTGRES)
        self.assertEqual(database.SCHEMA_POSTGRES.count("GENERATED ALWAYS AS IDENTITY"),
                         len(database.TABLES))

    def test_creation_is_idempotent_on_sqlite(self):
        """init_db runs on every boot, so a second pass must not raise."""
        path = os.path.join(tempfile.mkdtemp(), "idempotent.db")
        with mock.patch.object(database.config, "DATABASE_PATH", path):
            database.init_db()
            database.init_db()
            conn = sqlite3.connect(path)
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
        self.assertTrue(set(database.TABLES).issubset(names))


class PortableSqlTests(unittest.TestCase):
    """Catch SQLite-only SQL, which only fails once it reaches Postgres.

    /api/history shipped with `ORDER BY datetime(created_at)`. SQLite accepts it,
    Postgres answers `function datetime(text) does not exist`, and the endpoint
    500s. Nothing in the SQLite test run could have caught that.
    """

    # Functions and syntax SQLite has and Postgres does not.
    SQLITE_ONLY = (
        r"\bdatetime\s*\(", r"\bjulianday\s*\(", r"\bstrftime\s*\(",
        r"\bIFNULL\s*\(", r"\bGROUP_CONCAT\s*\(", r"\bINSERT\s+OR\s+",
        r"\bAUTOINCREMENT\b",
    )

    def test_application_sql_avoids_sqlite_only_constructs(self):
        import re
        backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        # database.py holds the SQLite dialect on purpose; check_db_connection
        # branches on the backend before using sqlite_master.
        skip = {"database.py", "check_db_connection.py"}
        offenders = []
        for name in sorted(os.listdir(backend_dir)):
            if not name.endswith(".py") or name in skip:
                continue
            text = open(os.path.join(backend_dir, name)).read()
            for pattern in self.SQLITE_ONLY:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    line = text[:match.start()].count("\n") + 1
                    offenders.append("%s:%d %s" % (name, line, match.group(0)))
        self.assertEqual(offenders, [], "SQLite-only SQL that will fail on Postgres")

    def test_iso_timestamps_sort_chronologically_as_text(self):
        """Why ORDER BY created_at is a valid replacement for datetime()."""
        from datetime import datetime, timedelta, timezone
        base = datetime(2026, 9, 13, 16, 17, 46, tzinfo=timezone.utc)
        stamps = [(base + timedelta(microseconds=n)).isoformat()
                  for n in (0, 1, 999999)]
        stamps.append((base + timedelta(seconds=1)).isoformat())
        self.assertEqual(sorted(stamps), stamps)


class SqliteRoundTripTests(unittest.TestCase):
    """The fallback path still has to work, including lastrowid."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "roundtrip.db")
        self.patcher = mock.patch.object(database.config, "DATABASE_PATH", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        database.init_db()

    def test_insert_returns_a_usable_row_id(self):
        new_id = database.execute(
            "INSERT INTO contacts (name, email, message, created_at) "
            "VALUES (?, ?, ?, ?)", ("Ada", "ada@example.com", "hello", database.now_iso()))
        self.assertIsInstance(new_id, int)
        row = database.query_one("SELECT * FROM contacts WHERE id = ?", (new_id,))
        self.assertEqual(row["name"], "Ada")
        self.assertEqual(len(database.query_all("SELECT * FROM contacts")), 1)

    def test_connection_transaction_context_commits_and_rolls_back(self):
        conn = database.get_db()
        try:
            with conn:
                conn.execute("INSERT INTO contacts (name, email, message, created_at) "
                             "VALUES (?, ?, ?, ?)", ("Kept", "k@x.com", "m", database.now_iso()))
            with self.assertRaises(sqlite3.IntegrityError):
                with conn:
                    conn.execute("INSERT INTO contacts (name, email, message, created_at) "
                                 "VALUES (?, ?, ?, ?)", ("Dropped", "d@x.com", "m", None))
        finally:
            conn.close()
        names = [r["name"] for r in database.query_all("SELECT name FROM contacts")]
        self.assertEqual(names, ["Kept"])


class FakeCursor:
    """Records what psycopg2 would have been asked to run."""

    def __init__(self, log, result):
        self.log = log
        self._result = result
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchone(self):
        return self._result

    def fetchall(self):
        return [self._result] if self._result is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, result=None):
        self.log = []
        self.committed = 0
        self.rolled_back = 0
        self._result = result

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._result)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


class PostgresWrapperTests(unittest.TestCase):
    """Drive the psycopg2 path without a server."""

    def wrap(self, result=None):
        fake = FakeConnection(result)
        return database._PostgresConnection(fake), fake

    def test_parameters_are_translated_on_the_way_out(self):
        conn, fake = self.wrap()
        conn.execute("SELECT * FROM admins WHERE username = ?", ("admin",))
        sql, params = fake.log[0]
        self.assertEqual(sql, "SELECT * FROM admins WHERE username = %s")
        self.assertEqual(params, ("admin",))

    def test_parameterless_statements_are_sent_verbatim(self):
        """No parameters means no interpolation, so % must not be doubled."""
        conn, fake = self.wrap()
        conn.execute("SELECT COUNT(*) AS c FROM admins")
        sql, params = fake.log[0]
        self.assertEqual(sql, "SELECT COUNT(*) AS c FROM admins")
        self.assertIsNone(params)

    def test_insert_gains_returning_id_and_reports_lastrowid(self):
        conn, fake = self.wrap(result=[42])
        cur = conn.execute(
            "INSERT INTO farms (farm_name, date, n_sections, mean_health_score, "
            "result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("North 40", "2026-01-01", 1, None, "{}", "now"))
        sql, _ = fake.log[0]
        self.assertTrue(sql.endswith("RETURNING id"), sql)
        self.assertIn("VALUES (%s, %s, %s, %s, %s, %s)", sql)
        self.assertEqual(cur.lastrowid, 42)

    def test_non_insert_has_no_lastrowid_and_is_not_rewritten(self):
        conn, fake = self.wrap()
        cur = conn.execute("UPDATE otps SET consumed = 1 WHERE id = ?", (7,))
        sql, _ = fake.log[0]
        self.assertNotIn("RETURNING", sql)
        self.assertIsNone(cur.lastrowid)

    def test_transaction_context_commits_on_success_and_rolls_back_on_error(self):
        conn, fake = self.wrap()
        with conn:
            conn.execute("DELETE FROM otps")
        self.assertEqual((fake.committed, fake.rolled_back), (1, 0))
        with self.assertRaises(ValueError):
            with conn:
                raise ValueError("boom")
        self.assertEqual((fake.committed, fake.rolled_back), (1, 1))

    def test_ssl_is_required_when_the_url_does_not_say_otherwise(self):
        with mock.patch.object(database.config, "DATABASE_URL",
                               "postgresql://u:p@h:6543/db"):
            self.assertTrue(database._postgres_dsn().endswith("?sslmode=require"))
        with mock.patch.object(database.config, "DATABASE_URL",
                               "postgresql://u:p@h:6543/db?a=1"):
            self.assertTrue(database._postgres_dsn().endswith("&sslmode=require"))
        # An explicit choice is respected rather than appended to.
        explicit = "postgresql://u:p@h:6543/db?sslmode=verify-full"
        with mock.patch.object(database.config, "DATABASE_URL", explicit):
            self.assertEqual(database._postgres_dsn(), explicit)


if __name__ == "__main__":
    unittest.main()
