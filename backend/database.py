"""Persistence layer for Farmers Forward: Postgres in production, SQLite locally.

`config.DATABASE_URL` decides which. When it is set (Supabase on Render) every
query runs against Postgres through psycopg2; when it is unset the original
SQLite file is used, so local development and the tests need no live database.

Render's free tier has no persistent disk, so the SQLite file is recreated empty
on every deploy and the seeded admin disappears with it. That is the reason this
module grew a second backend.

Call sites are unchanged. Queries are written once, in SQLite's `?` placeholder
style, and translated to psycopg2's `%s` here. Writing `%s` in the source
instead would have broken the SQLite fallback, and `%s` also collides with
Python's own `%` formatting, which this codebase already uses to build SQL
identifiers (see reset_admin._counts). One translation point is safer than
~25 edited query strings.

The connection object returned by `get_db()` is a thin wrapper so that both
backends support the same small surface the callers already use: `.execute()`
directly on the connection (psycopg2 only offers cursors), `.lastrowid` after an
INSERT (psycopg2 has no such attribute; `RETURNING id` is added instead),
`with conn:` as a transaction, and `IntegrityError` from either driver.

Timestamps stay ISO8601 UTC strings in TEXT columns on both backends, so the
parsing code in auth.py is untouched.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from backend import config

def _driver():
    """Import psycopg2 on demand.

    A local checkout with no DATABASE_URL never needs the driver, so importing
    it at module scope would make it a hard dependency of running the tests. It
    is imported here instead, which also keeps the Postgres code path reachable
    (and testable) in a process configured for SQLite.
    """
    try:
        import psycopg2
        import psycopg2.extras  # noqa: F401  (registers the cursor factories)
        return psycopg2
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "DATABASE_URL is set but psycopg2 is not installed. "
            "Install it with: pip install -r backend/requirements.txt") from exc


def using_postgres() -> bool:
    """True when queries are going to Postgres rather than the SQLite file."""
    return bool(config.DATABASE_URL)


def describe_backend() -> str:
    """One line naming the backend and target, with no credentials in it."""
    if not using_postgres():
        return "SQLite file %s" % config.DATABASE_PATH
    return "Postgres %s" % safe_dsn(config.DATABASE_URL)


def safe_dsn(url: str) -> str:
    """A DATABASE_URL with the password removed, safe to print in a log."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", str(url or ""))


# ---------------------------------------------------------------------------
# Schema. Identical shape on both backends; only the dialect differs.
# ---------------------------------------------------------------------------
# `attempts` and `consumed` stay INTEGER rather than becoming BOOLEAN: the
# queries compare them against 0 and 1 (`SET consumed = 1`, `WHERE consumed =
# 0`), which Postgres rejects for a real boolean column.
SCHEMA_SQLITE = """
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

SCHEMA_POSTGRES = """
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

TABLES = ("admins", "otps", "farms", "samples", "results", "contacts")


def schema() -> str:
    """The CREATE TABLE script for the active backend."""
    return SCHEMA_POSTGRES if using_postgres() else SCHEMA_SQLITE


# ---------------------------------------------------------------------------
# SQL translation
# ---------------------------------------------------------------------------
_INSERT_RE = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
_RETURNING_RE = re.compile(r"\bRETURNING\b", re.IGNORECASE)


def to_postgres_sql(sql: str) -> str:
    """Rewrite a `?`-placeholder statement for psycopg2.

    Every literal `%` is doubled, including inside a quoted string: psycopg2
    interpolates across the whole statement, not just outside literals. `?` is
    translated only outside a literal, so a real question mark in text survives.
    """
    out = []
    in_string = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'":
            # '' inside a string literal is an escaped quote, not a terminator.
            if in_string and index + 1 < len(sql) and sql[index + 1] == "'":
                out.append("''")
                index += 2
                continue
            in_string = not in_string
            out.append(char)
        elif char == "%":
            out.append("%%")
        elif char == "?":
            out.append("%s" if not in_string else "?")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _with_returning_id(sql: str):
    """Append RETURNING id to an INSERT so lastrowid has something to report.

    Returns (statement, returns_id).
    """
    if not _INSERT_RE.match(sql) or _RETURNING_RE.search(sql):
        return sql, False
    return sql.rstrip().rstrip(";") + " RETURNING id", True


# ---------------------------------------------------------------------------
# Connection wrapper
# ---------------------------------------------------------------------------
class _PostgresCursor:
    """A psycopg2 cursor presenting the two attributes sqlite3 callers use."""

    def __init__(self, cursor, lastrowid):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        try:
            return self._cursor.fetchone()
        except _driver().ProgrammingError:
            # Statement returned no result set (an UPDATE, say).
            return None

    def fetchall(self):
        try:
            return self._cursor.fetchall()
        except _driver().ProgrammingError:
            return []


class _PostgresConnection:
    """Gives a psycopg2 connection the sqlite3 surface the callers expect."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: Iterable[Any] = ()):
        params = tuple(params)
        statement, wants_id = _with_returning_id(sql)
        cursor = self._conn.cursor(cursor_factory=_driver().extras.DictCursor)
        if params:
            cursor.execute(to_postgres_sql(statement), params)
        else:
            # No parameters means no interpolation, so `%` must stay literal.
            cursor.execute(statement)
        lastrowid = None
        if wants_id:
            row = cursor.fetchone()
            lastrowid = row[0] if row else None
        return _PostgresCursor(cursor, lastrowid)

    def executescript(self, script: str):
        with self._conn.cursor() as cursor:
            cursor.execute(script)
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Matches sqlite3: commit on success, roll back on error, stay open.
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False


def _postgres_dsn() -> str:
    """The configured URL, with SSL required unless the caller said otherwise.

    Supabase terminates TLS and psycopg2 defaults to sslmode=prefer, which will
    silently fall back to plaintext if the handshake fails.
    """
    url = config.DATABASE_URL
    if "sslmode=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "sslmode=require"


def get_db():
    """Open a connection to the active backend.

    Callers close it themselves, as before. On Postgres this is a new pooled
    connection per call, which is why the Supabase transaction pooler (port
    6543) is the documented endpoint: gunicorn's sync workers would otherwise
    exhaust the direct-connection cap.
    """
    if using_postgres():
        return _PostgresConnection(_driver().connect(_postgres_dsn()))
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def integrity_error():
    """The active driver's IntegrityError, for `except` clauses."""
    return _driver().IntegrityError if using_postgres() else sqlite3.IntegrityError


def init_db() -> None:
    """Create all tables if they do not already exist. Safe on every boot."""
    conn = get_db()
    try:
        conn.executescript(schema())
        # Existing readings have no recorded crop; preserve that missingness.
        # New submissions require a crop in the API, while old reports stay valid.
        if using_postgres():
            conn.execute("ALTER TABLE samples ADD COLUMN IF NOT EXISTS crop_type TEXT")
        else:
            # Serialize the check + ALTER across simultaneous local workers.
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(samples)")}
            if "crop_type" not in columns:
                conn.execute("ALTER TABLE samples ADD COLUMN crop_type TEXT")
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    """Current time as an ISO8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Small query helpers
# ---------------------------------------------------------------------------
def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run an INSERT/UPDATE/DELETE; return lastrowid."""
    conn = get_db()
    try:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Any]:
    """Return the first row, or None."""
    conn = get_db()
    try:
        cur = conn.execute(sql, tuple(params))
        return cur.fetchone()
    finally:
        conn.close()


def query_all(sql: str, params: Iterable[Any] = ()) -> List[Any]:
    """Return every row."""
    conn = get_db()
    try:
        cur = conn.execute(sql, tuple(params))
        return cur.fetchall()
    finally:
        conn.close()
