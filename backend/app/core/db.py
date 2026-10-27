"""The database: the knowledge base (documents, chunks, embeddings), user accounts, the
customer portfolio (customers, payment history, risk predictions) and the collections
conversations (each customer's email thread, with every draft and who approved it).

SQLite (one file in storage/) by default; PostgreSQL when DATABASE_URL is set (pg.py), which is
what a deployment with more than one server uses. The SQL is written once, for both.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:  # PostgreSQL support (requirements.txt); SQLite needs nothing extra
    import psycopg

    from . import pg

    INTEGRITY_ERRORS: tuple = (sqlite3.IntegrityError, psycopg.IntegrityError)
except ImportError:  # pragma: no cover - psycopg is installed with the backend
    pg = None
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,          -- first 16 hex chars of the content SHA-256
    filename        TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,
    stored_name     TEXT NOT NULL,
    status          TEXT NOT NULL,             -- pending | processing | indexed | failed
    error           TEXT,
    page_count      INTEGER,
    char_count      INTEGER,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    uploaded_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    indexed_at      TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    page_number     INTEGER,
    text            TEXT NOT NULL,
    embedding       BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    filename    TEXT NOT NULL,
    event       TEXT NOT NULL,                 -- uploaded | indexed | failed | reindex | deleted
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,          -- normalised: trimmed and lower-cased
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,                 -- Argon2id; the password itself is never stored
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,                -- SHA-256 of the cookie value
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  INTEGER NOT NULL                 -- Unix time
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS customers (
    customer_id        INTEGER PRIMARY KEY,      -- the ID column of the UCI dataset
    limit_bal          INTEGER NOT NULL,         -- credit limit, NT dollars
    sex                TEXT NOT NULL,
    education_code     INTEGER NOT NULL,         -- as the model sees it (notebook cleaning)
    education          TEXT NOT NULL,
    marriage           TEXT NOT NULL,
    age                INTEGER NOT NULL,
    default_next_month INTEGER NOT NULL,         -- what actually happened, 0 or 1
    data_split         TEXT,                     -- train | validation | test, set by training
    created_at         TEXT NOT NULL,            -- when the row was imported
    email              TEXT                      -- collections emails go here; never to an LLM
);
CREATE TABLE IF NOT EXISTS monthly_history (
    customer_id      INTEGER NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    month_no         INTEGER NOT NULL,           -- 1 is the most recent month
    repayment_status INTEGER NOT NULL,           -- -2 no use, -1 paid in full, 0 revolving, n late
    bill_amt         INTEGER NOT NULL,
    pay_amt          INTEGER NOT NULL,
    PRIMARY KEY (customer_id, month_no)
);
CREATE TABLE IF NOT EXISTS model_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name      TEXT NOT NULL,
    hyperparameters TEXT NOT NULL,               -- JSON
    features        TEXT NOT NULL,               -- JSON list, in training order
    metrics         TEXT NOT NULL,               -- JSON: validation and test scores
    trained_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    customer_id         INTEGER PRIMARY KEY REFERENCES customers(customer_id) ON DELETE CASCADE,
    run_id              INTEGER NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    default_probability REAL NOT NULL,
    predicted_default   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    status        TEXT NOT NULL,                 -- open | escalated | closed
    status_reason TEXT,
    case_file     TEXT NOT NULL,                 -- JSON: the Case Analyst's case file and facts
    decision      TEXT NOT NULL,                 -- JSON: the Decision Agent's recommendation
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_customer ON conversations(customer_id);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction       TEXT NOT NULL,               -- outbound (to the customer) | inbound
    -- outbound: pending_approval | blocked | approved | rejected | superseded | sent
    -- inbound:  received
    status          TEXT NOT NULL,
    subject         TEXT,
    body            TEXT NOT NULL,
    intent          TEXT,                        -- inbound: what the customer's email is about
    intent_summary  TEXT,
    review          TEXT,                        -- JSON: Red Team rounds, checks, sources
    draft_subject   TEXT,                        -- the agent's text, when an officer edited it
    draft_body      TEXT,
    reviewed_by     TEXT,                        -- the officer who approved or rejected it
    reviewed_at     TEXT,
    review_note     TEXT,
    sent_by         TEXT,
    sent_at         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON conversation_messages(conversation_id);
CREATE TABLE IF NOT EXISTS follow_ups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    customer_id     INTEGER NOT NULL,
    due_at          TEXT NOT NULL,               -- ISO time; the Follow-up Agent's wait
    action          TEXT NOT NULL,               -- send_reminder | schedule_call | escalate...
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL,               -- scheduled | done | cancelled
    outcome         TEXT,
    created_at      TEXT NOT NULL,
    done_at         TEXT,
    last_message_id INTEGER                      -- the thread's newest message when it ran
);
CREATE INDEX IF NOT EXISTS idx_follow_ups_due ON follow_ups(status, due_at);
-- Customers who asked not to be contacted. No foreign key on purpose: the request outlives a
-- re-import of the customer data. Lifting it deletes the row; the audit trail keeps the history.
CREATE TABLE IF NOT EXISTS do_not_contact (
    customer_id INTEGER PRIMARY KEY,
    reason      TEXT NOT NULL,
    set_by      TEXT NOT NULL,                   -- an officer, or the Communication Agent
    set_at      TEXT NOT NULL
);
-- The collections ledger: what happened after the 2005 dataset ends. No foreign keys to the
-- customers, like do_not_contact, so a re-import keeps it.
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    amount      INTEGER NOT NULL,                -- NT dollars
    paid_on     TEXT NOT NULL,                   -- YYYY-MM-DD
    recorded_by TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id);
CREATE TABLE IF NOT EXISTS promises (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    conversation_id INTEGER,
    amount          INTEGER,                     -- NULL: any payment keeps it
    due_on          TEXT NOT NULL,               -- YYYY-MM-DD
    status          TEXT NOT NULL,               -- open | kept | broken | cancelled
    recorded_by     TEXT NOT NULL,               -- an officer, or the Communication Agent
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,
    outcome         TEXT
);
CREATE INDEX IF NOT EXISTS idx_promises_customer ON promises(customer_id, status);
-- Every contact attempt (CP-01 counts all channels): sent emails, and calls an officer logs.
CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    conversation_id INTEGER,
    channel         TEXT NOT NULL,               -- email | phone | sms
    outcome         TEXT NOT NULL,               -- sent | reached | no_answer | voicemail
    made_by         TEXT NOT NULL,
    note            TEXT,
    made_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_customer ON contacts(customer_id, made_at);
"""

USER_COLUMNS = "id, email, full_name, role, supervisor, created_at, last_login_at"

# Columns added after a table was first released, created on older databases at start-up.
_ADDED_COLUMNS = {
    "customers": {"email": "TEXT"},
    "follow_ups": {"last_message_id": "INTEGER", "last_contact_id": "INTEGER"},
    # A team lead: may give the second approval that payment-plan emails need (four eyes).
    "users": {"supervisor": "INTEGER NOT NULL DEFAULT 0"},
    "conversation_messages": {
        "needs_supervisor": "INTEGER NOT NULL DEFAULT 0",
        "first_approved_by": "TEXT",
        "first_approved_at": "TEXT",
        "delivered": "INTEGER NOT NULL DEFAULT 0",  # really emailed (Microsoft Graph)
        "external_id": "TEXT",  # the mailbox's message id
        "external_conversation_id": "TEXT",  # the mailbox's thread id, to match replies
    },
}


class EmailTaken(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """`path`: the SQLite file. `url`: a PostgreSQL URL, used instead when given; `schema`: the
    PostgreSQL schema to use (tests give each test its own)."""

    def __init__(self, path: Path, url: str | None = None, schema: str | None = None) -> None:
        self.path = path
        self.url = url or None
        self.schema = schema or None
        if self.url and pg is None:
            raise RuntimeError("DATABASE_URL needs psycopg: pip install -r requirements.txt")
        if self.url:
            if self.schema:
                pg.create_schema(self.url, self.schema)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            if self.url:  # several servers may start at once: one creates the tables
                conn.execute("SELECT pg_advisory_xact_lock(7210731)")
            conn.executescript(SCHEMA)
            for table, added in _ADDED_COLUMNS.items():
                columns = self._columns(conn, table)
                for name, kind in added.items():
                    if name not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    @classmethod
    def from_settings(cls, settings) -> Database:
        return cls(settings.db_path, settings.database_url, settings.database_schema)

    @property
    def dialect(self) -> str:
        return "postgresql" if self.url else "sqlite"

    def _columns(self, conn, table: str) -> set[str]:
        if self.url:
            return pg.columns(conn, table)
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per operation; commits on success, rolls back on error."""
        if self.url:
            with pg.connect(self.url, self.schema) as conn:
                yield conn
            return
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ping(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT 1").fetchone()

    # --- users and sessions ----------------------------------------------------------------

    def create_user(self, email: str, full_name: str, password_hash: str, role: str) -> dict:
        now = now_iso()
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO users (email, full_name, password_hash, role, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (email, full_name, password_hash, role, now, now),
                )
        except INTEGRITY_ERRORS as exc:
            raise EmailTaken from exc
        return self.get_user_by_email(email)

    def get_user_by_email(self, email: str, with_hash: bool = False) -> dict | None:
        columns = USER_COLUMNS + (", password_hash" if with_hash else "")
        with self.connect() as conn:
            row = conn.execute(f"SELECT {columns} FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def set_role(self, user_id: int, role: str) -> None:
        """Open sessions are closed so the new role applies from the next sign-in."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now_iso(), user_id)
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def set_supervisor(self, user_id: int, supervisor: bool) -> None:
        """Open sessions are closed so the change applies from the next sign-in."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET supervisor = ?, updated_at = ? WHERE id = ?",
                (int(supervisor), now_iso(), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def set_password_hash(self, user_id: int, password_hash: str, end_sessions: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, now_iso(), user_id),
            )
            if end_sessions:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def create_session(self, user_id: int, token_hash: str, csrf_token: str, expires_at: int):
        now = now_iso()
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_hash, user_id, csrf_token, now, expires_at),
            )
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))

    def get_session(self, token_hash: str) -> dict | None:
        """The signed-in user plus the session's CSRF token, or None if absent or expired."""
        columns = ", ".join(f"u.{c}" for c in USER_COLUMNS.split(", "))
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT {columns}, s.csrf_token FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (token_hash, int(time.time())),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # --- documents -------------------------------------------------------------------------

    def get_document(self, doc_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM documents ORDER BY uploaded_at DESC, id").fetchall()
        return [dict(r) for r in rows]

    def insert_document(self, doc: dict) -> None:
        cols = ", ".join(doc)
        marks = ", ".join("?" for _ in doc)
        with self.connect() as conn:
            conn.execute(f"INSERT INTO documents ({cols}) VALUES ({marks})", tuple(doc.values()))
            self._log(conn, doc["id"], doc["filename"], "uploaded", None)

    def set_status(self, doc_id: str, status: str, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE documents SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, now_iso(), doc_id),
            )

    def mark_failed(self, doc_id: str, error: str) -> None:
        """A failed document keeps no chunks, so it can never be retrieved."""
        with self.connect() as conn:
            row = conn.execute("SELECT filename FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            conn.execute(
                "UPDATE documents SET status = 'failed', error = ?, chunk_count = 0, "
                "indexed_at = NULL, updated_at = ? WHERE id = ?",
                (error, now_iso(), doc_id),
            )
            self._log(conn, doc_id, row["filename"], "failed", error)

    def replace_chunks(
        self,
        doc_id: str,
        chunks: list[tuple[int, int | None, str]],
        embeddings: np.ndarray,
        embedding_model: str,
        page_count: int | None,
        char_count: int,
    ) -> bool:
        """Swap a document's chunks in one transaction. False if it was deleted meanwhile."""
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT filename FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO chunks (document_id, chunk_index, page_number, text, embedding, "
                "embedding_model) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (doc_id, idx, page, text, emb.astype(np.float32).tobytes(), embedding_model)
                    for (idx, page, text), emb in zip(chunks, embeddings, strict=True)
                ],
            )
            conn.execute(
                "UPDATE documents SET status = 'indexed', error = NULL, chunk_count = ?, "
                "page_count = ?, char_count = ?, embedding_model = ?, indexed_at = ?, "
                "updated_at = ? WHERE id = ?",
                (len(chunks), page_count, char_count, embedding_model, now, now, doc_id),
            )
            self._log(conn, doc_id, row["filename"], "indexed", f"{len(chunks)} chunks")
        return True

    def delete_document(self, doc_id: str) -> int:
        """Remove a document and its chunks. Returns the number of chunks removed."""
        with self.connect() as conn:
            row = conn.execute("SELECT filename FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return 0
            removed = conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,)).rowcount
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._log(conn, doc_id, row["filename"], "deleted", f"{removed} chunks removed")
        return removed

    def fail_interrupted(self) -> int:
        """Called at startup: anything still mid-ingestion was cut off by a restart."""
        with self.connect() as conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM documents WHERE status IN ('pending', 'processing')"
                )
            ]
        for doc_id in ids:
            self.mark_failed(
                doc_id, "Ingestion was interrupted by a server restart. Reindex to retry."
            )
        return len(ids)

    # --- activity --------------------------------------------------------------------------

    @staticmethod
    def _log(conn: sqlite3.Connection, doc_id: str, filename: str, event: str, detail) -> None:
        conn.execute(
            "INSERT INTO activity (document_id, filename, event, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, filename, event, detail, now_iso()),
        )

    def log_event(self, doc_id: str, filename: str, event: str, detail: str | None = None) -> None:
        with self.connect() as conn:
            self._log(conn, doc_id, filename, event, detail)

    def recent_activity(self, limit: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # --- retrieval and stats ---------------------------------------------------------------

    def searchable_chunks(
        self, embedding_model: str, document_ids: list[str] | None
    ) -> tuple[list[dict], np.ndarray]:
        """Chunks of indexed documents embedded with the current model, plus their matrix."""
        sql = (
            "SELECT c.document_id, c.chunk_index, c.page_number, c.text, c.embedding, "
            "d.filename, d.file_type FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.status = 'indexed' AND c.embedding_model = ?"
        )
        params: list = [embedding_model]
        if document_ids:
            sql += f" AND c.document_id IN ({', '.join('?' for _ in document_ids)})"
            params += document_ids
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        meta = [{k: r[k] for k in r.keys() if k != "embedding"} for r in rows]
        return meta, matrix

    def stats(self, embedding_model: str) -> dict:
        with self.connect() as conn:
            by_status = {
                r["status"]: r["n"]
                for r in conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status")
            }
            by_type = {
                r["file_type"]: r["n"]
                for r in conn.execute(
                    "SELECT file_type, COUNT(*) AS n FROM documents GROUP BY file_type"
                )
            }
            totals = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes, "
                "MAX(indexed_at) AS last_indexed FROM documents"
            ).fetchone()
            chars = conn.execute(
                "SELECT COALESCE(SUM(char_count), 0) AS n FROM documents WHERE status = 'indexed'"
            ).fetchone()["n"]
            chunks = conn.execute(
                "SELECT COUNT(*) AS n,"
                " COALESCE(SUM(CASE WHEN c.embedding_model = ? THEN 1 ELSE 0 END), 0) AS current "
                "FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.status = 'indexed'",
                (embedding_model,),
            ).fetchone()
            stale_docs = conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE status = 'indexed' "
                "AND embedding_model IS NOT ?",
                (embedding_model,),
            ).fetchone()["n"]
        return {
            "total_documents": totals["n"],
            "by_status": {
                s: by_status.get(s, 0) for s in ("pending", "processing", "indexed", "failed")
            },
            "by_file_type": by_type,
            "total_size_bytes": totals["bytes"],
            "indexed_characters": chars,
            "total_chunks": chunks["n"],
            "searchable_chunks": chunks["current"],
            "stale_documents": stale_docs,
            "last_indexed_at": totals["last_indexed"],
        }
