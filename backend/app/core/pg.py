"""PostgreSQL behind the same interface the app uses for SQLite.

Every module writes SQL for SQLite (? placeholders, INSERT OR IGNORE, cursor.lastrowid) and
reads rows as row["column"] or row[0]. This adapter lets that code run unchanged on
PostgreSQL (Azure Database for PostgreSQL in production):

    ?                       -> %s (and a literal % -> %%)
    INSERT OR IGNORE        -> INSERT ... ON CONFLICT DO NOTHING
    INSERT OR REPLACE       -> INSERT ... ON CONFLICT (first column) DO UPDATE SET ...
    INSERT without RETURNING into a table with a serial key -> RETURNING it, for lastrowid
    the schema              -> AUTOINCREMENT as IDENTITY, BLOB as BYTEA, REAL as DOUBLE
                               PRECISION, and the audit log's append-only triggers in PL/pgSQL

The SQL itself is kept portable (no SQLite-only functions), so this is all the translation
needed. Tests run against both databases (TEST_DATABASE_URL).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import BaseRowFactory

# The column an INSERT returns as lastrowid, for tables whose key the database assigns.
SERIAL_KEYS = {
    "chunks": "id", "activity": "id", "users": "id", "model_runs": "run_id",
    "conversations": "id", "conversation_messages": "id", "follow_ups": "id",
    "payments": "id", "promises": "id", "contacts": "id", "audit_log": "id",
}  # fmt: skip


class Row(dict):
    """A row read as row["column"], row[0] or dict(row), like sqlite3.Row."""

    def __init__(self, names: list[str], values) -> None:
        super().__init__(zip(names, values, strict=True))
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        value = super().__getitem__(key)
        return bytes(value) if isinstance(value, memoryview) else value

    def keys(self):  # noqa: D102 - as sqlite3.Row
        return list(super().keys())


def _row_factory(cursor) -> BaseRowFactory:
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(names, values)


def translate(sql: str) -> tuple[str, str | None]:
    """(PostgreSQL SQL, the key it returns for lastrowid, or None)."""
    text = sql.replace("%", "%%").replace("?", "%s")
    # SQLite compares with NULL using IS / IS NOT; PostgreSQL spells it IS [NOT] DISTINCT FROM.
    text = re.sub(r"\bIS NOT %s", "IS DISTINCT FROM %s", text)
    text = re.sub(r"\bIS %s", "IS NOT DISTINCT FROM %s", text)
    head = text.lstrip()
    returning = None
    insert = re.match(r"INSERT\s+(OR\s+(IGNORE|REPLACE)\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", head, re.I)
    if insert:
        mode, table, columns = (insert[2] or "").upper(), insert[3], insert[4]
        text = re.sub(r"INSERT\s+OR\s+(IGNORE|REPLACE)\s+INTO", "INSERT INTO", text, count=1,
                      flags=re.I)  # fmt: skip
        names = [c.strip() for c in columns.split(",")]
        if mode == "IGNORE":
            text = f"{text.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"
        elif mode == "REPLACE":
            key, rest = names[0], names[1:]
            sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in rest)
            text = f"{text.rstrip().rstrip(';')} ON CONFLICT ({key}) DO UPDATE SET {sets}"
        if table in SERIAL_KEYS and "RETURNING" not in text.upper():
            returning = SERIAL_KEYS[table]
            text = f"{text.rstrip().rstrip(';')} RETURNING {returning}"
    return text, returning


class Cursor:
    def __init__(self, cursor, returning: str | None) -> None:
        self._cursor = cursor
        self.lastrowid = None
        self._first = None
        if returning:
            row = cursor.fetchone()
            self.lastrowid = row[0] if row else None

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone() if self._cursor.description else None

    def fetchall(self):
        return self._cursor.fetchall() if self._cursor.description else []

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    """What the app's code calls on a connection: execute, executemany, executescript."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params=()) -> Cursor:
        text, returning = translate(sql)
        cursor = self._conn.cursor(row_factory=_row_factory)
        cursor.execute(text, tuple(params) if params else None)
        return Cursor(cursor, returning)

    def executemany(self, sql: str, rows) -> None:
        text, _ = translate(sql)
        text = re.sub(r"\s+RETURNING\s+\w+$", "", text)
        with self._conn.cursor() as cursor:
            cursor.executemany(text, [tuple(r) for r in rows])

    def executescript(self, script: str) -> None:
        self._conn.execute(schema(script))


def schema(script: str) -> str:
    """The SQLite schema as PostgreSQL DDL."""
    text = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY "
                  "PRIMARY KEY", script)  # fmt: skip
    text = re.sub(r"\bBLOB\b", "BYTEA", text)
    text = re.sub(r"\bREAL\b", "DOUBLE PRECISION", text)
    # SQLite: CREATE TRIGGER IF NOT EXISTS name BEFORE UPDATE ON t BEGIN SELECT RAISE(...); END;
    triggers = re.findall(
        r"CREATE TRIGGER IF NOT EXISTS (\w+) BEFORE (UPDATE|DELETE) ON (\w+)\s+"
        r"BEGIN SELECT RAISE\(ABORT, '([^']*)'\); END;",
        text,
    )
    text = re.sub(r"CREATE TRIGGER IF NOT EXISTS .*?END;", "", text, flags=re.S)
    for name, when, table, message in triggers:
        text += (
            f"\nCREATE OR REPLACE FUNCTION {name}_fn() RETURNS trigger LANGUAGE plpgsql AS "
            f"$$ BEGIN RAISE EXCEPTION '{message}'; END $$;"
            f"\nDROP TRIGGER IF EXISTS {name} ON {table};"
            f"\nCREATE TRIGGER {name} BEFORE {when} ON {table} FOR EACH ROW"
            f" EXECUTE FUNCTION {name}_fn();"
        )
    return text


@contextmanager
def connect(url: str, schema_name: str | None = None) -> Iterator[Connection]:
    """One connection per operation; commits on success, rolls back on error."""
    # autocommit: the block below is then a real transaction, not a savepoint inside one
    conn = psycopg.connect(url, connect_timeout=30, autocommit=True)
    try:
        if schema_name:
            conn.execute(f'SET search_path TO "{schema_name}"')
        with conn.transaction():
            yield Connection(conn)
    finally:
        conn.close()


def columns(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?"
        " AND table_schema = current_schema()",
        (table,),
    ).fetchall()
    return {r[0] for r in rows}


def create_schema(url: str, schema_name: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')


def drop_schema(url: str, schema_name: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
