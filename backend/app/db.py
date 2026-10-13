"""SQLite store for documents, chunks and their embeddings (the knowledge base)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

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
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per operation; commits on success, rolls back on error."""
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
                "SELECT COUNT(*) AS n, COALESCE(SUM(c.embedding_model = ?), 0) AS current "
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
