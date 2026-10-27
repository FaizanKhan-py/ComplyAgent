"""The audit trail: who did what, when, and why, for every collections decision.

Every agent step (case analysis, decision, each Red Team round, each draft, a hand-over to a
person, a follow-up plan, a simulation) and every officer action (start, approve, edit, reject,
send, close) is one row. Rows are append-only: SQLite triggers refuse any UPDATE or DELETE, and
the table has no foreign keys, so deleting a customer does not delete their history.

    actor     a person's email, or the agent's name ("Case Analyst Agent", "Red Team", ...)
    event     a dotted name: agent.decision, draft.approved, follow_up.scheduled, ...
    summary   one plain sentence for the audit page
    detail    JSON: the inputs and outputs that explain the event (never the customer's email
              address)
"""

from __future__ import annotations

import csv
import io
import json

from .db import Database, now_iso

EXPORT_COLUMNS = ("id", "created_at", "actor", "event", "customer_id", "conversation_id",
                  "message_id", "summary", "detail")  # fmt: skip
EXPORT_LIMIT = 100_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    event           TEXT NOT NULL,
    customer_id     INTEGER,
    conversation_id INTEGER,
    message_id      INTEGER,
    summary         TEXT NOT NULL,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_customer ON audit_log(customer_id);
CREATE INDEX IF NOT EXISTS idx_audit_conversation ON audit_log(conversation_id);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
"""

MAX_LIMIT = 500


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db.connect() as conn:
            conn.executescript(SCHEMA)

    def record(
        self,
        actor: str,
        event: str,
        summary: str,
        *,
        customer_id: int | None = None,
        conversation_id: int | None = None,
        message_id: int | None = None,
        detail: dict | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO audit_log (created_at, actor, event, customer_id, conversation_id,"
                " message_id, summary, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now_iso(),
                    actor,
                    event,
                    customer_id,
                    conversation_id,
                    message_id,
                    summary,
                    json.dumps(detail, default=str) if detail is not None else None,
                ),
            )
            return cur.lastrowid

    def record_all(self, events: list[dict], **ids) -> None:
        """Events an agent run collected before the conversation existed (graph.py's trace)."""
        for e in events:
            self.record(e["actor"], e["event"], e["summary"], detail=e.get("detail"), **ids)

    def export_csv(
        self,
        customer_id: int | None = None,
        event: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> tuple[str, int]:
        """The trail as CSV, oldest first, for a compliance review: (text, rows). `since` and
        `until` are dates (YYYY-MM-DD, UTC), both included."""
        where, args = [], []
        if customer_id is not None:
            where.append("customer_id = ?")
            args.append(customer_id)
        if event:
            where.append("(event = ? OR event LIKE ?)")
            args += [event, f"{event}.%"]
        if since:
            where.append("created_at >= ?")
            args.append(since)
        if until:
            where.append("created_at < ?")
            args.append(f"{until}T99")  # every time on that day sorts before this
        sql = "SELECT * FROM audit_log" + (f" WHERE {' AND '.join(where)}" if where else "")
        with self.db.connect() as conn:
            rows = conn.execute(f"{sql} ORDER BY id LIMIT ?", (*args, EXPORT_LIMIT)).fetchall()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow([_cell(row[c]) for c in EXPORT_COLUMNS])
        return out.getvalue(), len(rows)

    def list(
        self,
        customer_id: int | None = None,
        conversation_id: int | None = None,
        event: str | None = None,
        limit: int = 100,
        before: int | None = None,
    ) -> list[dict]:
        """Newest first. `event` matches a prefix ("draft" finds draft.approved, ...);
        `before` pages back from an id."""
        where, args = [], []
        for column, value in (("customer_id", customer_id), ("conversation_id", conversation_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if event:
            where.append("(event = ? OR event LIKE ?)")
            args += [event, f"{event}.%"]
        if before is not None:
            where.append("id < ?")
            args.append(before)
        sql = "SELECT * FROM audit_log" + (f" WHERE {' AND '.join(where)}" if where else "")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(limit, MAX_LIMIT)))
        with self.db.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [
            {**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows
        ]


def _cell(value) -> str:
    """A CSV cell a spreadsheet will not run: text that starts like a formula (customer emails
    end up in summaries) gets a leading apostrophe."""
    if value is None:
        return ""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
