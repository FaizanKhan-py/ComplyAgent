"""The autopilot's state: a task queue and each customer's saved context, both in the database.

agent_tasks         one row per unit of work (analyse, decide, converse, escalate). A worker
                    claims a pending task atomically, so several workers, or several servers,
                    never run the same task twice; a customer's tasks run one at a time.
customer_context    what the agents know and did for one customer: the analysis, the latest
                    decision, the conversation, an escalation, and the stage it is in. A reply
                    reuses this context instead of starting the analysis again.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..core.db import Database, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER,
    kind         TEXT NOT NULL,              -- analyse | decide | converse | escalate
    status       TEXT NOT NULL,              -- pending | processing | done | failed
    payload      TEXT,                       -- JSON: why the task exists
    result       TEXT,                       -- one line: what it did
    error        TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    worker       TEXT,
    available_at TEXT NOT NULL,              -- not before (a retry, or the contact hours)
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON agent_tasks(status, available_at);
CREATE INDEX IF NOT EXISTS idx_tasks_customer ON agent_tasks(customer_id);
CREATE TABLE IF NOT EXISTS customer_context (
    customer_id     INTEGER PRIMARY KEY,
    stage           TEXT NOT NULL,           -- see STAGES
    analysis        TEXT,                    -- JSON: the Analysis Agent's case file
    decision        TEXT,                    -- JSON: the Decision Agent's latest plan
    escalation      TEXT,                    -- JSON: why, who was told, when
    conversation_id INTEGER,
    last_inbound_id INTEGER NOT NULL DEFAULT 0,  -- newest customer reply already handled
    last_payment_id INTEGER NOT NULL DEFAULT 0,  -- newest payment already handled
    note            TEXT,                    -- one line: where the customer stands
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autopilot_workers (
    worker  TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL,
    task_id INTEGER                          -- what it is working on, if anything
);
"""

KINDS = ("analyse", "decide", "converse", "escalate")
# queued -> analysing -> deciding -> writing -> awaiting_reply -> (a reply) deciding -> ...
# and the ends: escalated (a person has it), waiting (no contact now), resolved, failed
STAGES = ("queued", "analysing", "deciding", "writing", "awaiting_reply", "escalated", "waiting",
          "resolved", "failed")  # fmt: skip
# Customers that hold one of the AUTOPILOT_MAX_CASES places. A failed customer keeps its place
# until an officer retries it, so a broken model key cannot pull in customer after customer.
IN_FLIGHT = ("queued", "analysing", "deciding", "writing", "awaiting_reply", "failed")
MAX_ATTEMPTS = 3
LEASE_MINUTES = 15  # a task processing longer than this was interrupted: it runs again


def later(seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _loads(value):
    return json.loads(value) if value else None


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db.connect() as conn:
            conn.executescript(SCHEMA)

    # --- tasks ------------------------------------------------------------------------------

    def enqueue(self, kind: str, customer_id: int | None, payload: dict | None = None,
                when: str | None = None) -> int | None:  # fmt: skip
        """A new pending task; None when the same kind is already waiting for this customer."""
        now = now_iso()
        with self.db.connect() as conn:
            waiting = conn.execute(
                "SELECT id FROM agent_tasks WHERE kind = ? AND customer_id = ?"
                " AND status = 'pending'",
                (kind, customer_id),
            ).fetchone()
            if waiting:
                return None
            cur = conn.execute(
                "INSERT INTO agent_tasks (customer_id, kind, status, payload, available_at,"
                " created_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                (customer_id, kind, json.dumps(payload or {}), when or now, now),
            )
            return cur.lastrowid

    def claim(self, worker: str) -> dict | None:
        """The oldest task that may run now, marked processing; None when there is none. A
        customer with a task already processing waits, so their tasks run in order."""
        now = now_iso()
        with self.db.connect() as conn:
            row = conn.execute(
                "UPDATE agent_tasks SET status = 'processing', worker = ?, started_at = ?,"
                " attempts = attempts + 1"
                " WHERE id = (SELECT t.id FROM agent_tasks t WHERE t.status = 'pending'"
                "   AND t.available_at <= ? AND NOT EXISTS (SELECT 1 FROM agent_tasks b"
                "     WHERE b.status = 'processing' AND b.customer_id = t.customer_id)"
                "   ORDER BY t.id LIMIT 1)"
                " AND status = 'pending' RETURNING *",
                (worker, now, now),
            ).fetchone()
        return self._task(row) if row else None

    def finish(self, task_id: int, result: str) -> None:
        self._set(task_id, status="done", result=result, error=None, finished_at=now_iso())

    def retry_at(self, task_id: int, when: str, reason: str) -> None:
        """Runs again later without counting as a failure (e.g. outside the contact hours)."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE agent_tasks SET status = 'pending', available_at = ?, result = ?,"
                " attempts = attempts - 1 WHERE id = ?",
                (when, reason, task_id),
            )

    def fail(self, task_id: int, error: str) -> bool:
        """Records a failure. True: it will be retried; False: it gave up."""
        task = self.task(task_id)
        if task and task["attempts"] < MAX_ATTEMPTS:
            self._set(task_id, status="pending", error=error,
                      available_at=later(30 * task["attempts"]))  # fmt: skip
            return True
        self._set(task_id, status="failed", error=error, finished_at=now_iso())
        return False

    def retry(self, task_id: int) -> bool:
        """An officer runs a failed task again."""
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE agent_tasks SET status = 'pending', attempts = 0, error = NULL,"
                " available_at = ?, finished_at = NULL WHERE id = ? AND status = 'failed'",
                (now_iso(), task_id),
            )
            return cur.rowcount > 0

    def recover_stale(self) -> list[dict]:
        """Tasks left processing by a server that stopped: back to pending."""
        cutoff = later(-LEASE_MINUTES * 60)
        with self.db.connect() as conn:
            rows = conn.execute(
                "UPDATE agent_tasks SET status = 'pending', available_at = ?"
                " WHERE status = 'processing' AND started_at < ? RETURNING *",
                (now_iso(), cutoff),
            ).fetchall()
        return [self._task(r) for r in rows]

    def task(self, task_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def tasks(self, customer_id: int | None = None, status: str | None = None,
              limit: int = 100) -> list[dict]:  # fmt: skip
        where, args = [], []
        if customer_id is not None:
            where.append("customer_id = ?")
            args.append(customer_id)
        if status:
            where.append("status = ?")
            args.append(status)
        sql = "SELECT * FROM agent_tasks" + (f" WHERE {' AND '.join(where)}" if where else "")
        with self.db.connect() as conn:
            rows = conn.execute(f"{sql} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._task(r) for r in rows]

    def _set(self, task_id: int, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE agent_tasks SET {sets} WHERE id = ?", (*fields.values(), task_id))

    @staticmethod
    def _task(row) -> dict:
        task = dict(row)
        task["payload"] = _loads(task["payload"]) or {}
        return task

    # --- customer context ------------------------------------------------------------------

    def context(self, customer_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM customer_context WHERE customer_id = ?", (customer_id,)
            ).fetchone()
        return self._context(row) if row else None

    def save(self, customer_id: int, **fields) -> None:
        """Creates or updates the customer's context; JSON fields are given as dicts."""
        for name in ("analysis", "decision", "escalation"):
            if name in fields and fields[name] is not None:
                fields[name] = json.dumps(fields[name], default=str)
        now = now_iso()
        with self.db.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM customer_context WHERE customer_id = ?", (customer_id,)
            ).fetchone()
            if exists:
                sets = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE customer_context SET {sets}, updated_at = ? WHERE customer_id = ?",
                    (*fields.values(), now, customer_id),
                )
            else:
                fields = {"stage": "queued", **fields}
                columns = ", ".join(fields)
                marks = ", ".join("?" for _ in fields)
                conn.execute(
                    f"INSERT INTO customer_context (customer_id, {columns}, created_at,"
                    f" updated_at) VALUES (?, {marks}, ?, ?)",
                    (customer_id, *fields.values(), now, now),
                )

    def contexts(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM customer_context ORDER BY updated_at DESC"
            ).fetchall()
        return [self._context(r) for r in rows]

    def in_flight(self) -> int:
        marks = ", ".join("?" for _ in IN_FLIGHT)
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM customer_context WHERE stage IN ({marks})", IN_FLIGHT
            ).fetchone()
        return row["n"]

    @staticmethod
    def _context(row) -> dict:
        context = dict(row)
        for name in ("analysis", "decision", "escalation"):
            context[name] = _loads(context[name])
        return context

    # --- workers ---------------------------------------------------------------------------

    def heartbeat(self, worker: str, task_id: int | None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO autopilot_workers (worker, seen_at, task_id) VALUES (?, ?, ?)"
                " ON CONFLICT (worker) DO UPDATE SET seen_at = excluded.seen_at,"
                " task_id = excluded.task_id",
                (worker, now_iso(), task_id),
            )

    def workers(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autopilot_workers WHERE seen_at > ? ORDER BY worker",
                (later(-60),),
            ).fetchall()
        return [dict(r) for r in rows]

    def version(self) -> str:
        """Changes whenever anything the dashboard shows changes: it streams on a change."""
        with self.db.connect() as conn:
            a = conn.execute("SELECT MAX(id) AS v FROM audit_log").fetchone()["v"]
            t = conn.execute(
                "SELECT COUNT(*) AS n, MAX(COALESCE(finished_at, started_at, created_at)) AS v,"
                " SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS p FROM agent_tasks"
            ).fetchone()
            c = conn.execute("SELECT MAX(updated_at) AS v FROM customer_context").fetchone()["v"]
        return f"{a}|{t['n']}|{t['v']}|{t['p']}|{c}"
