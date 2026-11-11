"""The autopilot's engine: workers, watchers and the Monitor Agent, all in background threads.

    workers    take the next pending task from the queue, run its agent, and record the result;
               a failure is retried (MAX_ATTEMPTS), then shown to the officers as failed
    watchers   every few seconds, turn what happened into tasks:
                 a customer became overdue       -> analyse (highest risk first, at most
                                                    AUTOPILOT_MAX_CASES in progress)
                 the customer replied            -> decide, reusing the saved analysis
                 a payment arrived               -> resolved if paid in full, else analyse again
                 a follow-up wrote a reminder    -> converse (send it)
                 an officer or agent escalated   -> escalate (notify the officers)
               and run the due follow-ups and read the mailbox, when nothing else does
    monitor    the Monitor Agent: requeues tasks a stopped server left behind, and reports the
               health of the whole system (snapshot) for the dashboard

Nothing here depends on a browser: it starts with the server (AUTOPILOT_ENABLED=true) or on its
own with `python -m app.manage run-autopilot`, and continues from the database after a restart.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

from ..core.config import Settings
from ..core.db import now_iso
from ..customers.portfolio import Portfolio
from ..decisions import conversation, follow_up
from ..rag.knowledge_base import KnowledgeBase
from .agents import AUTOPILOT, ROBOT, Agents, Later
from .store import Store, later

logger = logging.getLogger("complyagent.autopilot")

IDLE_SECONDS = 2  # a worker with nothing to do looks again after this
WATCH_SECONDS = 10  # how often the watchers and the Monitor Agent run
SLOW_SECONDS = 60  # how often the follow-ups run and the mailbox is read
REPLY_SETTLE_SECONDS = 120  # a reply the Conversation Agent has not read yet waits this long


class Autopilot:
    def __init__(self, settings: Settings, kb: KnowledgeBase, portfolio: Portfolio, mailer) -> None:
        self.settings = settings
        self.kb = kb
        self.portfolio = portfolio
        self.mailer = mailer
        self.store = Store(kb.db)
        self.agents = Agents(settings, kb, portfolio, mailer, self.store)
        self.audit = self.agents.audit
        self.stop = threading.Event()
        self.name = f"{socket.gethostname()}-{os.getpid()}"
        self._slow_at = 0.0

    def start(self) -> None:
        for n in range(self.settings.autopilot_workers):
            threading.Thread(target=self._work, args=(f"{self.name}-w{n + 1}",), daemon=True,
                             name=f"autopilot-worker-{n + 1}").start()  # fmt: skip
        threading.Thread(target=self._watch, daemon=True, name="autopilot-watch").start()
        logger.info("Autopilot started: %d worker(s), at most %d customer(s) in progress",
                    self.settings.autopilot_workers, self.settings.autopilot_max_cases)  # fmt: skip

    def run_forever(self) -> None:
        self.start()
        try:
            while not self.stop.wait(1):
                pass
        except KeyboardInterrupt:
            self.stop.set()

    # --- workers ----------------------------------------------------------------------------

    def _work(self, worker: str) -> None:
        while not self.stop.is_set():
            try:
                self.store.heartbeat(worker, None)
                task = self.store.claim(worker)
                if task is None:
                    self.stop.wait(IDLE_SECONDS)
                    continue
                self.store.heartbeat(worker, task["id"])
                self.run_task(task)
            except Exception:  # the database was busy or down: try again shortly
                logger.exception("Autopilot worker error")
                self.stop.wait(IDLE_SECONDS * 5)

    def run_task(self, task: dict) -> None:
        cid = task["customer_id"]
        try:
            self.store.finish(task["id"], self.agents.run(task))
        except Later as wait:
            self.store.retry_at(task["id"], wait.when, wait.reason)
        except Exception as exc:
            error = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            logger.warning("Task %s (%s) failed: %s", task["id"], task["kind"], error)
            retried = self.store.fail(task["id"], error)
            if cid is not None and retried:
                self.store.save(cid, note=f"Retrying {task['kind']}: {error}")
            elif cid is not None:
                self.store.save(cid, stage="failed", note=f"{task['kind']} failed: {error}")
            self.audit.record(
                "Monitor Agent", "autopilot.task_retry" if retried else "autopilot.task_failed",
                f"The {task['kind']} task failed{' and will be retried' if retried else ''}: "
                f"{error}",
                customer_id=cid, detail={"task_id": task["id"], "attempt": task["attempts"]},
            )  # fmt: skip

    # --- watchers ---------------------------------------------------------------------------

    def _watch(self) -> None:
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(WATCH_SECONDS)

    def tick(self) -> None:
        """One round of the watchers and the Monitor Agent (each one alone may fail)."""
        steps = [self.monitor, self.detect_overdue, self.watch_replies, self.watch_payments,
                 self.watch_conversations]  # fmt: skip
        if time.monotonic() - self._slow_at >= SLOW_SECONDS:
            self._slow_at = time.monotonic()
            steps += [self.run_follow_ups, self.read_mailbox]
        self.store.heartbeat(f"{self.name}-watch", None)
        for step in steps:
            try:
                step()
            except Exception:
                logger.exception("Autopilot step %s failed", step.__name__)

    def detect_overdue(self) -> None:
        room = self.settings.autopilot_max_cases - self.store.in_flight()
        if room <= 0:
            return
        with self.kb.db.connect() as conn:
            rows = conn.execute(
                "SELECT c.customer_id, h.repayment_status AS months,"
                " (SELECT COALESCE(MAX(id), 0) FROM payments y WHERE y.customer_id = c.customer_id)"
                " AS last_payment"
                " FROM customers c"
                " JOIN monthly_history h ON h.customer_id = c.customer_id AND h.month_no = 1"
                " LEFT JOIN predictions p ON p.customer_id = c.customer_id"
                " WHERE h.repayment_status > 0"
                " AND NOT EXISTS (SELECT 1 FROM customer_context x"
                "   WHERE x.customer_id = c.customer_id)"
                " AND NOT EXISTS (SELECT 1 FROM do_not_contact d"
                "   WHERE d.customer_id = c.customer_id)"
                " AND NOT EXISTS (SELECT 1 FROM conversations v"
                "   WHERE v.customer_id = c.customer_id AND v.status != 'closed')"
                " ORDER BY p.default_probability DESC NULLS LAST, c.customer_id LIMIT ?",
                (room,),
            ).fetchall()
        for row in rows:
            cid = row["customer_id"]
            try:
                self.store.save(cid, stage="queued", last_payment_id=row["last_payment"],
                                note="Overdue: waiting for the Analysis Agent.")  # fmt: skip
            except Exception:  # another server picked this customer first
                continue
            self.store.enqueue("analyse", cid, {"reason": "overdue"})
            self.audit.record(AUTOPILOT, "autopilot.detected",
                              f"Overdue ({row['months']} month(s) late): to the Analysis Agent.",
                              customer_id=cid)  # fmt: skip

    def watch_replies(self) -> None:
        """A reply the Conversation Agent has read (its intent is known), or one waiting
        longer than REPLY_SETTLE_SECONDS, goes to the Decision Agent."""
        with self.kb.db.connect() as conn:
            rows = conn.execute(
                "SELECT x.customer_id, MAX(m.id) AS message_id FROM customer_context x"
                " JOIN conversation_messages m ON m.conversation_id = x.conversation_id"
                " WHERE m.direction = 'inbound' AND m.id > x.last_inbound_id"
                " AND (m.intent IS NOT NULL OR m.created_at < ?)"
                " AND x.stage NOT IN ('resolved', 'escalated')"
                " GROUP BY x.customer_id",
                (later(-REPLY_SETTLE_SECONDS),),
            ).fetchall()
        for row in rows:
            cid = row["customer_id"]
            self.store.save(cid, last_inbound_id=row["message_id"], stage="deciding",
                            note="The customer replied: the Decision Agent reads it.")  # fmt: skip
            self.store.enqueue("decide", cid, {"reason": "reply", "message_id": row["message_id"]})

    def watch_payments(self) -> None:
        with self.kb.db.connect() as conn:
            rows = conn.execute(
                "SELECT x.customer_id, x.stage, MAX(p.id) AS payment_id FROM customer_context x"
                " JOIN payments p ON p.customer_id = x.customer_id"
                " WHERE p.id > x.last_payment_id AND x.stage != 'resolved'"
                " GROUP BY x.customer_id, x.stage"
            ).fetchall()
        for row in rows:
            cid = row["customer_id"]
            self.store.save(cid, last_payment_id=row["payment_id"])
            customer = self.portfolio.get_customer(cid)
            if (
                customer
                and customer["latest_bill"]
                and (customer["recorded_paid"] >= customer["latest_bill"])
            ):
                self._resolve(cid, "Paid in full.")
            elif row["stage"] not in ("escalated", "failed"):
                self.store.save(cid, stage="queued", note="A payment arrived: analysing again.")
                self.store.enqueue("analyse", cid, {"reason": "payment"})

    def watch_conversations(self) -> None:
        """Changes made outside the autopilot's tasks: a person or the Follow-up Agent
        escalated or closed the conversation, or a follow-up reminder is waiting to be sent."""
        for ctx in self.store.contexts():
            if not ctx["conversation_id"] or ctx["stage"] in ("resolved", "failed"):
                continue
            conv = self.agents.conversations.get(ctx["conversation_id"])
            if conv is None:
                continue
            cid = ctx["customer_id"]
            if conv["status"] == "escalated" and ctx["stage"] != "escalated":
                self.store.save(cid, stage="escalated", note=conv["status_reason"])
                self.store.enqueue("escalate", cid, {"reason": conv["status_reason"]})
            elif conv["status"] == "closed":
                self.store.save(cid, stage="resolved", note=conv["status_reason"])
            elif ctx["stage"] == "awaiting_reply" and self.agents.waiting(conv):
                self.store.save(cid, stage="writing", note="A follow-up reminder is ready to send.")
                self.store.enqueue("converse", cid, {"mode": "send"})

    def _resolve(self, cid: int, reason: str) -> None:
        ctx = self.store.context(cid)
        store = self.agents.conversations
        conv = store.get(ctx["conversation_id"]) if ctx and ctx["conversation_id"] else None
        if conv and conv["status"] != "closed":
            conversation.close(store, conv["id"], ROBOT, reason)
        self.store.save(cid, stage="resolved", note=reason)
        self.audit.record(AUTOPILOT, "autopilot.resolved", f"Resolved: {reason}",
                          customer_id=cid)  # fmt: skip

    def run_follow_ups(self) -> None:
        if self.settings.follow_up_interval_minutes == 0:  # otherwise its own scheduler runs
            follow_up.run_due(self.kb, self.portfolio, self.kb.generator,
                              self.agents.conversations)  # fmt: skip

    def read_mailbox(self) -> None:
        if self.mailer.provider != "none" and self.settings.email_poll_minutes == 0:
            conversation.poll_mailbox(self.kb, self.portfolio, self.kb.generator,
                                      self.agents.conversations, self.mailer)  # fmt: skip

    # --- Monitor Agent ----------------------------------------------------------------------

    def monitor(self) -> None:
        for task in self.store.recover_stale():
            self.audit.record(
                "Monitor Agent", "autopilot.task_recovered",
                f"The {task['kind']} task was interrupted (the server stopped): it runs again.",
                customer_id=task["customer_id"], detail={"task_id": task["id"]},
            )  # fmt: skip


def snapshot(store: Store, settings: Settings) -> dict:
    """The Monitor Agent's view of the whole system, read from the database, so it is right
    even when the workers run in another process."""
    with store.db.connect() as conn:
        tasks = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM agent_tasks GROUP BY status")}  # fmt: skip
        stages = {r["stage"]: r["n"] for r in conn.execute(
            "SELECT stage, COUNT(*) AS n FROM customer_context GROUP BY stage")}  # fmt: skip
        oldest = conn.execute(
            "SELECT MIN(available_at) AS t FROM agent_tasks WHERE status = 'pending'"
            " AND available_at <= ?",
            (now_iso(),),
        ).fetchone()["t"]
        today = now_iso()[:10]
        events = {r["event"]: r["n"] for r in conn.execute(
            "SELECT event, COUNT(*) AS n FROM audit_log WHERE created_at >= ? AND event IN"
            " ('draft.sent', 'autopilot.escalated', 'autopilot.task_failed', 'message.received',"
            " 'autopilot.resolved') GROUP BY event", (today,))}  # fmt: skip
    workers = store.workers()
    processing = store.tasks(status="processing", limit=20)
    failed = store.tasks(status="failed", limit=20)
    busy = sum(1 for w in workers if w["task_id"])
    if not settings.autopilot_enabled and not workers:
        health = "off"
    elif not workers:
        health = "down"
    elif failed:
        health = "degraded"
    else:
        health = "ok"
    return {
        "health": health,
        "enabled": settings.autopilot_enabled,
        "auto_send": settings.autopilot_auto_send,
        "max_cases": settings.autopilot_max_cases,
        "workers": workers,
        "busy_workers": busy,
        "tasks": {s: tasks.get(s, 0) for s in ("pending", "processing", "done", "failed")},
        "stages": stages,
        "oldest_pending": oldest,
        "today": {
            "emails_sent": events.get("draft.sent", 0),
            "replies": events.get("message.received", 0),
            "escalations": events.get("autopilot.escalated", 0),
            "resolved": events.get("autopilot.resolved", 0),
            "failures": events.get("autopilot.task_failed", 0),
        },
        "processing": processing,
        "failed": failed,
    }


def customer_rows(store: Store) -> list[dict]:
    """Every customer the autopilot handles, one row each, newest activity first."""
    from ..customers.portfolio import risk_level

    with store.db.connect() as conn:
        rows = conn.execute(
            "SELECT x.customer_id, x.stage, x.note, x.decision, x.updated_at, x.conversation_id,"
            " c.email, c.name, h.repayment_status AS months_late, h.bill_amt AS latest_bill,"
            " p.default_probability,"
            " (SELECT COALESCE(SUM(amount), 0) FROM payments y WHERE y.customer_id = x.customer_id)"
            " AS paid"
            " FROM customer_context x JOIN customers c ON c.customer_id = x.customer_id"
            " LEFT JOIN monthly_history h ON h.customer_id = x.customer_id AND h.month_no = 1"
            " LEFT JOIN predictions p ON p.customer_id = x.customer_id"
            " ORDER BY x.updated_at DESC"
        ).fetchall()
        active = conn.execute(
            "SELECT customer_id, kind, status FROM agent_tasks"
            " WHERE status IN ('processing', 'pending') ORDER BY status DESC, id"
        ).fetchall()
    tasks: dict[int, dict] = {}
    for t in active:  # processing first, so it wins over a pending task
        tasks.setdefault(t["customer_id"], {"kind": t["kind"], "status": t["status"]})
    out = []
    for r in rows:
        plan = (Store._context({**dict(r), "analysis": None, "escalation": None})["decision"]
                or {}).get("plan")  # fmt: skip
        bill = r["latest_bill"] or 0
        out.append({
            "customer_id": r["customer_id"],
            "email": r["email"],
            "name": r["name"],
            "risk_level": risk_level(r["default_probability"]),
            "default_probability": r["default_probability"],
            "months_late": max(r["months_late"] or 0, 0),
            "outstanding": max(bill - r["paid"], 0),
            "stage": r["stage"],
            "note": r["note"],
            "plan": plan,
            "task": tasks.get(r["customer_id"]),
            "conversation_id": r["conversation_id"],
            "updated_at": r["updated_at"],
        })  # fmt: skip
    return out


def customer_detail(store: Store, agents: Agents, customer_id: int) -> dict | None:
    """One customer's summary, saved context, tasks, emails and full activity timeline."""
    from .agents import case_story

    ctx = store.context(customer_id)
    if ctx is None:
        return None
    conversations = agents.conversations
    customer = agents.portfolio.get_customer(customer_id) or {"customer_id": customer_id}
    conv = conversations.get(ctx["conversation_id"]) if ctx["conversation_id"] else None
    emails = [
        {k: m.get(k) for k in ("id", "direction", "status", "subject", "body", "intent",
                               "intent_summary", "created_at", "sent_at", "delivered")}
        for m in (conv["messages"] if conv else [])
        if m["status"] in ("sent", "received", "pending_approval", "awaiting_supervisor",
                           "approved", "blocked")
    ]  # fmt: skip
    return {
        "name": customer.get("name"),
        "email": customer.get("email"),
        "summary": case_story(customer, ctx, agents._communication(customer_id)),
        "context": ctx,
        "conversation_status": conv and conv["status"],
        "emails": emails,
        "tasks": store.tasks(customer_id=customer_id, limit=50),
        "timeline": conversations.audit.list(customer_id=customer_id, limit=300),
    }
