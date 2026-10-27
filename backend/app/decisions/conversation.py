"""Collections conversations: every agent, one email thread per customer, and an officer's
approval before anything goes out.

    start      the LangGraph pipeline (orchestration/graph.py): Case Analyst -> Decision Agent
               -> message writer <-> guardrails <-> Red Team -> the first email, waiting for
               approval
    receive    the customer's reply is stored; the Communication Agent reads the whole thread,
               works out what the customer wants, and drafts the answer through the same loop,
               or hands the conversation to a person (a dispute, a complaint, a request to stop
               contact, or anything the policies say a person must handle)
    redraft    a new draft for the same point in the thread, with the officer's feedback
    approve    an officer accepts a draft, optionally edited (the edit is checked again). A
               draft that offers a payment plan, or answers a hardship report, needs a second
               approval by a different person who is a team lead (four eyes)
    reject     an officer turns a draft down, saying why
    send       the contact rules (contact_rules.py: hours, frequency, quiet after a call) are
               checked, then the email is sent (integrations/mail.py: Microsoft Graph, or
               recorded only); the Follow-up Agent (follow_up.py) then plans the next step
    poll       the collections mailbox's new emails are matched to their conversations and
               received like a pasted reply
    close      the officer ends the conversation

Do not contact: when a customer asks for contact to stop (the Communication Agent reads it in
their email, or an officer records it), no email can be started, written, approved, sent or
followed up until an officer lifts the request. The request is kept per customer, not per
conversation, so closing the conversation does not lift it.

Nothing reaches the customer without an officer's approval. Every draft is kept, with the Red
Team's findings, and every step, by an agent or a person, is written to the audit trail.
"""

from __future__ import annotations

import html
import json
import re
from enum import Enum

from pydantic import BaseModel, Field

from ..core.audit import AuditLog
from ..core.db import Database, now_iso
from ..customers.ledger import PROMISE_HORIZON_DAYS, Ledger
from ..customers.portfolio import Portfolio
from ..integrations.mail import MailError
from ..langchain.llm import AnswerGenerator
from ..langchain.memory import email_thread
from ..langchain.prompts import CONVERSATION_PROMPT, INTENT_PROMPT
from ..orchestration import graph
from ..rag.knowledge_base import KnowledgeBase
from . import compliance_loop, contact_rules, messages
from .decision import Action, Recommendation

MAX_SENT = 5  # after this many emails without a resolution, a person takes over
REPLY_REQUIRED = ("reference",)  # a reply needs the reference; the first email needs them all


class Intent(str, Enum):
    promise_to_pay = "promise_to_pay"
    payment_made = "payment_made"
    hardship = "hardship"
    dispute = "dispute"
    question = "question"
    callback_request = "callback_request"
    stop_contact = "stop_contact"
    complaint = "complaint"
    other = "other"


# Whatever the model says, these always go to a person. Hardship too: no agent may approve a
# hardship plan (HA-02), and the agent evaluation showed the model missing vulnerable customers.
ALWAYS_HUMAN = {Intent.dispute, Intent.complaint, Intent.stop_contact, Intent.hardship}


class ReplyDraft(BaseModel):
    intent: Intent
    intent_summary: str = Field(description="One sentence: what the customer said or asked.")
    needs_human: bool
    human_reason: str = Field(description='Why a person must handle it; "" when not.')
    subject: str = Field(description="The email subject line.")
    body: str = Field(description="The email body, with {placeholders} for amounts and links.")
    source_ids: list[int] = Field(description="Numbers of the policy passages it follows.")
    promised_date: str = Field(
        description='A promise to pay: the date as YYYY-MM-DD, worked out from today. "" if none.'
    )
    promised_amount: int = Field(description="The amount promised in NT$; 0 if not stated.")


class IntentOnly(BaseModel):
    intent: Intent
    intent_summary: str = Field(description="One sentence: what the customer said or asked.")


# A safety net that needs no model: plain requests to stop contact always stop it. A false
# match only pauses contact until an officer lifts it, which is the safe side to err on.
STOP_WORDS = re.compile(
    r"\b(stop|cease|quit)\s+(contacting|emailing|e-mailing|calling|messaging|texting|writing)\b"
    r"|\b(do not|don't|dont|never)\s+(contact|email|e-mail|call|message|text|write to)\s+me\b"
    r"|\bunsubscribe\b|\bleave me alone\b",
    re.IGNORECASE,
)


def asks_to_stop(text: str) -> bool:
    return bool(STOP_WORDS.search(text))


class ConversationError(Exception):
    """A request that cannot be done. `status` is the HTTP status; `message` is safe to show."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# --- storage --------------------------------------------------------------------------------


class Conversations:
    """The conversations, their messages and follow-ups, and the audit trail they write to."""

    def __init__(self, db: Database, audit: AuditLog | None = None) -> None:
        self.db = db
        self.audit = audit or AuditLog(db)
        self.ledger = Ledger(db)

    def create(self, customer_id: int, case_file: dict, decision: dict, user: str) -> int:
        now = now_iso()
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (customer_id, status, case_file, decision, created_by,"
                " created_at, updated_at) VALUES (?, 'open', ?, ?, ?, ?, ?)",
                (customer_id, json.dumps(case_file), json.dumps(decision), user, now, now),
            )
            return cur.lastrowid

    def get(self, conversation_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return None
            rows = conn.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
            follow_ups = conn.execute(
                "SELECT * FROM follow_ups WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        conversation = dict(row)
        conversation["case_file"] = json.loads(conversation["case_file"])
        conversation["decision"] = json.loads(conversation["decision"])
        conversation["messages"] = [self._message(r) for r in rows]
        conversation["follow_ups"] = [dict(f) for f in follow_ups]
        return conversation

    def latest_for(self, customer_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM conversations WHERE customer_id = ? ORDER BY id DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
        return self.get(row["id"]) if row else None

    def add_message(self, conversation_id: int, **fields) -> int:
        fields = {**fields, "conversation_id": conversation_id, "created_at": now_iso()}
        if "review" in fields:
            fields["review"] = json.dumps(fields["review"])
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.db.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO conversation_messages ({columns}) VALUES ({marks})",
                tuple(fields.values()),
            )
            self._touch(conn, conversation_id)
            return cur.lastrowid

    def get_message(self, message_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_messages WHERE id = ?", (message_id,)
            ).fetchone()
        return self._message(row) if row else None

    def update_message(self, message_id: int, **fields) -> None:
        sets = ", ".join(f"{name} = ?" for name in fields)
        with self.db.connect() as conn:
            row = conn.execute(
                f"UPDATE conversation_messages SET {sets} WHERE id = ? RETURNING conversation_id",
                (*fields.values(), message_id),
            ).fetchone()
            self._touch(conn, row["conversation_id"])

    def update(self, conversation_id: int, **fields) -> None:
        for name in ("decision", "case_file"):
            if name in fields:
                fields[name] = json.dumps(fields[name])
        sets = ", ".join(f"{name} = ?" for name in fields)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE conversations SET {sets} WHERE id = ?", (*fields.values(), conversation_id)
            )
            self._touch(conn, conversation_id)

    def supersede_drafts(self, conversation_id: int) -> None:
        """Drafts nobody acted on are replaced by a newer one, but kept."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE conversation_messages SET status = 'superseded' WHERE conversation_id = ?"
                " AND status IN ('pending_approval', 'awaiting_supervisor', 'blocked', 'approved')",
                (conversation_id,),
            )

    # follow-ups: what the Follow-up Agent planned after an email was sent

    def schedule_follow_up(self, conversation_id: int, customer_id: int, due_at: str,
                           action: str, reason: str) -> int:  # fmt: skip
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO follow_ups (conversation_id, customer_id, due_at, action, reason,"
                " status, created_at) VALUES (?, ?, ?, ?, ?, 'scheduled', ?)",
                (conversation_id, customer_id, due_at, action, reason, now_iso()),
            )
            return cur.lastrowid

    def get_follow_up(self, follow_up_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM follow_ups WHERE id = ?", (follow_up_id,)).fetchone()
        return dict(row) if row else None

    def list_follow_ups(self, status: str | None = None, due_before: str | None = None,
                        limit: int = 200) -> list[dict]:  # fmt: skip
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if due_before:
            where.append("due_at <= ?")
            args.append(due_before)
        sql = "SELECT * FROM follow_ups" + (f" WHERE {' AND '.join(where)}" if where else "")
        with self.db.connect() as conn:
            rows = conn.execute(f"{sql} ORDER BY due_at, id LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def finish_follow_up(self, follow_up_id: int, status: str, outcome: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE follow_ups SET status = ?, outcome = ?, done_at = ?, last_message_id ="
                " (SELECT MAX(id) FROM conversation_messages m"
                " WHERE m.conversation_id = follow_ups.conversation_id), last_contact_id ="
                " (SELECT MAX(id) FROM contacts c WHERE c.customer_id = follow_ups.customer_id)"
                " WHERE id = ?",
                (status, outcome, now_iso(), follow_up_id),
            )

    def cancel_follow_ups(self, conversation_id: int, reason: str) -> int:
        """Scheduled follow-ups that no longer apply (the customer replied, a person took
        over, the conversation closed). Returns how many were cancelled."""
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE follow_ups SET status = 'cancelled', outcome = ?, done_at = ?"
                " WHERE conversation_id = ? AND status = 'scheduled'",
                (reason, now_iso(), conversation_id),
            )
            return cur.rowcount

    # do not contact: a customer's request to stop, which blocks every email to them

    def do_not_contact(self, customer_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM do_not_contact WHERE customer_id = ?", (customer_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_do_not_contact(self, customer_id: int, reason: str, by: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO do_not_contact (customer_id, reason, set_by, set_at)"
                " VALUES (?, ?, ?, ?)",
                (customer_id, reason, by, now_iso()),
            )

    def lift_do_not_contact(self, customer_id: int) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM do_not_contact WHERE customer_id = ?", (customer_id,))
            return cur.rowcount > 0

    # the officers' inbox: everything across customers that waits for a person

    def inbox(self, now: str) -> dict:
        with self.db.connect() as conn:
            drafts = conn.execute(
                "SELECT m.id, m.conversation_id, c.customer_id, m.status, m.subject, m.body,"
                " m.created_at FROM conversation_messages m"
                " JOIN conversations c ON c.id = m.conversation_id"
                " WHERE m.status IN ('pending_approval', 'awaiting_supervisor', 'blocked',"
                " 'approved')"
                " AND c.status != 'closed' ORDER BY m.created_at, m.id"
            ).fetchall()
            escalated = conn.execute(
                "SELECT id, customer_id, status_reason, updated_at FROM conversations"
                " WHERE status = 'escalated' ORDER BY updated_at, id"
            ).fetchall()
            due = conn.execute(
                "SELECT * FROM follow_ups WHERE status = 'scheduled' AND due_at <= ?"
                " ORDER BY due_at, id",
                (now,),
            ).fetchall()
            calls = conn.execute(
                "SELECT f.* FROM follow_ups f JOIN conversations c ON c.id = f.conversation_id"
                " WHERE f.status = 'done' AND f.action = 'schedule_call' AND c.status = 'open'"
                # a call task is done once anything newer happens in the conversation
                " AND NOT EXISTS (SELECT 1 FROM conversation_messages m"
                " WHERE m.conversation_id = f.conversation_id"
                " AND m.id > COALESCE(f.last_message_id, 0))"
                # ...or once an officer logs a call
                " AND NOT EXISTS (SELECT 1 FROM contacts k WHERE k.customer_id = f.customer_id"
                " AND k.channel = 'phone' AND k.id > COALESCE(f.last_contact_id, 0))"
                " ORDER BY f.done_at, f.id"
            ).fetchall()
        return {
            "drafts": [dict(r) for r in drafts],
            "escalated": [dict(r) for r in escalated],
            "due_follow_ups": [dict(r) for r in due],
            "calls": [dict(r) for r in calls],
        }

    @staticmethod
    def _touch(conn, conversation_id: int) -> None:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), conversation_id)
        )

    @staticmethod
    def _message(row) -> dict:
        message = dict(row)
        message["review"] = json.loads(message["review"]) if message["review"] else None
        message["edited"] = message.pop("draft_body") is not None
        message.pop("draft_subject")
        message["needs_supervisor"] = bool(message.get("needs_supervisor"))
        message["delivered"] = bool(message.get("delivered"))
        return message


# --- the agents -----------------------------------------------------------------------------


def actor(user: dict) -> str:
    return user.get("email") or user.get("full_name") or "unknown"


def review_of(outcome: dict, passages: list[dict], values: dict, deadline: str | None) -> dict:
    """What is kept with a draft: every round, the checks it passed and the policies used."""
    draft = outcome["draft"]
    used = set(draft.source_ids) if draft else set()
    passed = outcome["status"] == "passed"
    return {
        "rounds": [_jsonable(r) for r in outcome["rounds"]],
        "checks": messages.checks(values, outcome["body"], deadline) if passed else [],
        "sources": [p for p in passages if p["number"] in used] or passages,
        "decision": outcome["decision"]["recommendation"].model_dump(mode="json"),
    }


def _jsonable(round_: dict) -> dict:
    return {**round_, "findings": [f.model_dump(mode="json") for f in round_["findings"]]}


def draft_status(outcome: dict) -> str:
    return "pending_approval" if outcome["status"] == "passed" else "blocked"


def needs_supervisor(outcome: dict) -> bool:
    """Four eyes: an email that offers a payment plan, or answers a hardship report, needs a
    team lead's approval too (the hardship policy: a team lead approves the larger plans, and
    no agent may approve one)."""
    action = outcome["decision"]["recommendation"].action
    intent = getattr(outcome["draft"], "intent", None)
    return action == Action.offer_payment_plan or intent == Intent.hardship


def save_draft(store: Conversations, conversation: dict, outcome: dict, review: dict) -> int:
    """Stores a draft the graph wrote, and its trace in the audit trail."""
    status = draft_status(outcome)
    message_id = store.add_message(
        conversation["id"],
        direction="outbound",
        status=status,
        subject=outcome["subject"],
        body=outcome["body"],
        review=review,
        needs_supervisor=int(needs_supervisor(outcome)),
    )
    ids = {"customer_id": conversation["customer_id"], "conversation_id": conversation["id"]}
    store.audit.record_all(outcome["trace"], **ids, message_id=message_id)
    store.audit.record(
        "Compliance loop",
        "draft.created",
        "Draft ready for an officer's approval."
        if status == "pending_approval"
        else "Draft blocked: it cannot be approved until it is written again.",
        **ids,
        message_id=message_id,
        detail={"status": status, "rounds": len(outcome["rounds"])},
    )
    return message_id


def _first_email(kb, portfolio, generator, customer_id: int, feedback: str | None = None):
    """(case file, outcome, review) for the first email, from the LangGraph pipeline."""
    state = graph.run_first_email(kb, portfolio, generator, customer_id, feedback)
    if state["status"] == "not_found":
        raise ConversationError(404, "not_found", "Customer not found.")
    if state["status"] == "nothing_to_send":
        raise ConversationError(409, "nothing_to_send", state["reason"])
    if state["status"] == "no_email":
        raise ConversationError(
            409,
            "nothing_to_send",
            "The Red Team challenged the action, and the Decision Agent now recommends no contact.",
        )
    outcome = compliance_loop.outcome(state)
    job = state["job"]
    review = review_of(outcome, job.passages, job.values, job.deadline(outcome["decision"]))
    case = state["case"]
    case_file = {
        "analysed_by": case["analysed_by"],
        "model": case["model"],
        "facts": case["facts"],
        "analysis": case["analysis"].model_dump(mode="json"),
    }
    return case_file, outcome, review


def start(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    customer_id: int,
    user: dict,
) -> dict:
    """A new conversation, with its first email waiting for approval."""
    contact_allowed(store, customer_id)
    latest = store.latest_for(customer_id)
    if latest and latest["status"] != "closed":
        raise ConversationError(
            409, "conversation_open", "This customer already has an open conversation."
        )
    case_file, outcome, review = _first_email(kb, portfolio, generator, customer_id)
    decision = outcome["decision"]["recommendation"].model_dump(mode="json")
    conversation_id = store.create(customer_id, case_file, decision, actor(user))
    store.audit.record(
        actor(user),
        "conversation.started",
        "Conversation started.",
        customer_id=customer_id,
        conversation_id=conversation_id,
    )
    save_draft(store, store.get(conversation_id), outcome, review)
    return store.get(conversation_id)


def open_conversation(store: Conversations, conversation_id: int) -> dict:
    conversation = store.get(conversation_id)
    if conversation is None:
        raise ConversationError(404, "not_found", "Conversation not found.")
    if conversation["status"] == "closed":
        raise ConversationError(409, "conversation_closed", "This conversation is closed.")
    return conversation


def thread(conversation: dict) -> list[dict]:
    """The emails that were really exchanged, oldest first."""
    return [m for m in conversation["messages"] if m["status"] in ("sent", "received")]


def escalate(store: Conversations, conversation: dict, reason: str, by: str) -> None:
    store.update(conversation["id"], status="escalated", status_reason=reason)
    store.cancel_follow_ups(conversation["id"], "A person took over the conversation.")
    store.audit.record(
        by,
        "conversation.escalated",
        f"Handed to a person: {reason}",
        customer_id=conversation["customer_id"],
        conversation_id=conversation["id"],
    )


def contact_allowed(store: Conversations, customer_id: int) -> None:
    """Raises when the customer asked not to be contacted."""
    flag = store.do_not_contact(customer_id)
    if flag:
        raise ConversationError(
            409,
            "do_not_contact",
            f"The customer asked not to be contacted ({flag['reason']}). An officer must lift "
            "the request before any email is written or sent.",
        )


def stop_contact(store: Conversations, customer_id: int, reason: str, by: str) -> None:
    """Records a request to stop contact: every waiting draft and follow-up is withdrawn."""
    store.set_do_not_contact(customer_id, reason, by)
    latest = store.latest_for(customer_id)
    if latest and latest["status"] != "closed":
        store.supersede_drafts(latest["id"])
        store.cancel_follow_ups(latest["id"], "The customer asked not to be contacted.")
    store.audit.record(
        by,
        "customer.do_not_contact",
        f"Do not contact: {reason}",
        customer_id=customer_id,
        conversation_id=latest["id"] if latest else None,
    )


def lift_contact_block(store: Conversations, customer_id: int, note: str, user: dict) -> None:
    flag = store.do_not_contact(customer_id)
    if not flag:
        raise ConversationError(409, "not_blocked", "This customer has no request to stop contact.")
    store.lift_do_not_contact(customer_id)
    store.audit.record(
        actor(user),
        "customer.contact_allowed",
        f"The request to stop contact was lifted: “{note}”",
        customer_id=customer_id,
        detail={"lifted": flag, "note": note},
    )


def too_many_sent(store: Conversations, conversation: dict, by: str) -> bool:
    """After MAX_SENT emails without a resolution, a person takes over."""
    sent = sum(1 for m in thread(conversation) if m["direction"] == "outbound")
    if sent < MAX_SENT:
        return False
    reason = f"{sent} emails were sent without a resolution, so a person takes over."
    escalate(store, conversation, reason, by)
    return True


def _reply(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    conversation: dict,
    feedback: str | None = None,
) -> None:
    """The Communication Agent's answer to the customer's newest email."""
    if generator is None:
        raise ConversationError(
            503,
            "llm_not_configured",
            "No language model is configured, so no reply can be written.",
        )
    emails = thread(conversation)
    latest = emails[-1]
    if store.do_not_contact(conversation["customer_id"]):
        reason = "The customer asked not to be contacted, so a person must answer them."
        escalate(store, conversation, reason, "Communication Agent")
        return
    if too_many_sent(store, conversation, "Communication Agent"):
        return
    customer = portfolio.get_customer(conversation["customer_id"])
    if customer is None:
        raise ConversationError(404, "not_found", "Customer not found.")
    passages = messages.message_passages(kb, customer, (latest["body"][:500],))
    facts = messages.writer_facts(customer)
    values = messages.email_values(customer)
    decision = {"recommendation": Recommendation.model_validate(conversation["decision"])}
    rec = decision["recommendation"]
    today = contact_rules.local_time(contact_rules.load_default()).date()

    def build(_: dict) -> str:
        fact_lines = "\n".join(
            f'<fact id="{f["id"]}">{html.escape(f["text"], quote=False)}</fact>' for f in facts
        )
        blocks = "\n".join(
            f'<source id="{p["number"]}" document="{html.escape(p["filename"], quote=True)}">\n'
            f"{html.escape(p['text'], quote=False)}\n</source>"
            for p in passages
        )
        subject = (
            f"Subject: {html.escape(latest['subject'], quote=False)}\n\n"
            if latest["subject"]
            else ""
        )
        return (
            f"<customer_email>\n{subject}{html.escape(latest['body'], quote=False)}\n"
            "</customer_email>\n\n"
            f"Action chosen by the Decision Agent: {rec.action.value} "
            f"(priority {rec.priority.value})\n\n<facts>\n{fact_lines}\n</facts>\n\n"
            f"<sources>\n{blocks}\n</sources>\n\nToday is {today:%A %Y-%m-%d}. Write the reply."
        ) + graph.officer_note(feedback)

    job = compliance_loop.EmailJob(
        system=CONVERSATION_PROMPT,
        schema=ReplyDraft,
        build=build,
        decision=decision,
        facts=facts,
        passages=passages,
        values=values,
        required=REPLY_REQUIRED,
        history=email_thread(emails[:-1]),
        customer_message=latest["body"],
        hand_over=lambda draft: draft.needs_human or draft.intent in ALWAYS_HUMAN,
        writer_name="Communication Agent",
    )
    outcome = compliance_loop.run(generator, job)
    draft: ReplyDraft = outcome["draft"]
    store.update_message(
        latest["id"], intent=draft.intent.value, intent_summary=draft.intent_summary
    )
    ids = {"customer_id": conversation["customer_id"], "conversation_id": conversation["id"]}
    store.audit.record(
        "Communication Agent",
        "agent.intent",
        f"The customer's email: {draft.intent.value.replace('_', ' ')}. {draft.intent_summary}",
        **ids,
        message_id=latest["id"],
        detail={"intent": draft.intent.value, "needs_human": draft.needs_human},
    )
    if outcome["status"] == "handed_over":
        store.audit.record_all(outcome["trace"], **ids)
        reason = draft.human_reason.strip() or f"The customer's email is a {draft.intent.value}."
        if draft.intent == Intent.stop_contact:
            stop_contact(
                store, conversation["customer_id"], draft.intent_summary, "Communication Agent"
            )
        escalate(store, conversation, reason, "Communication Agent")
        return
    _record_promise(store, conversation, draft, today)
    save_draft(store, conversation, outcome, review_of(outcome, passages, values, None))


def _record_promise(store: Conversations, conversation: dict, draft: ReplyDraft, today) -> None:
    """A promise to pay with a date goes into the ledger; the Follow-up Agent checks it."""
    if draft.intent != Intent.promise_to_pay or not draft.promised_date:
        return
    from datetime import date, timedelta

    try:
        due = date.fromisoformat(draft.promised_date)
    except ValueError:
        return  # not a date: nothing is recorded rather than a guess
    if not today <= due <= today + timedelta(days=PROMISE_HORIZON_DAYS):
        return
    amount = draft.promised_amount if draft.promised_amount > 0 else None
    promise = store.ledger.record_promise(
        conversation["customer_id"], due, "Communication Agent", amount, conversation["id"]
    )
    store.audit.record(
        "Communication Agent",
        "ledger.promise",
        f"Promise to pay {f'NT${amount:,}' if amount else 'the balance'} by {due.isoformat()}.",
        customer_id=conversation["customer_id"],
        conversation_id=conversation["id"],
        detail=promise,
    )


def receive(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    conversation_id: int,
    subject: str | None,
    body: str,
    user: dict,
) -> dict:
    """The customer's email, stored; then the Communication Agent's answer, if it may write one."""
    conversation = open_conversation(store, conversation_id)
    store.supersede_drafts(conversation_id)
    store.cancel_follow_ups(conversation_id, "The customer replied.")
    message_id = store.add_message(
        conversation_id, direction="inbound", status="received", subject=subject, body=body
    )
    store.audit.record(
        actor(user),
        "message.received",
        "The customer's reply was added.",
        customer_id=conversation["customer_id"],
        conversation_id=conversation_id,
        message_id=message_id,
    )
    conversation = store.get(conversation_id)
    customer_id = conversation["customer_id"]
    if asks_to_stop(body):
        store.update_message(
            message_id, intent=Intent.stop_contact.value, intent_summary="Asks for contact to stop."
        )
        quote = body if len(body) <= 120 else f"{body[:117]}..."
        stop_contact(store, customer_id, f"The customer wrote: “{quote}”", "Communication Agent")
        if conversation["status"] == "open":
            reason = "The customer asked for contact to stop, so a person must answer them."
            escalate(store, conversation, reason, "Communication Agent")
    elif conversation["status"] == "open":
        _reply(kb, portfolio, generator, store, conversation)
    elif generator is not None:
        _classify(generator, store, conversation, message_id, subject, body)
    return store.get(conversation_id)


def _classify(generator, store: Conversations, conversation: dict, message_id: int,
              subject: str | None, body: str) -> None:  # fmt: skip
    """A person has the conversation, so nothing is drafted; the email is still read, so a
    request to stop contact is never missed."""
    text = f"Subject: {subject}\n\n{body}" if subject else body
    request = f"<customer_email>\n{html.escape(text, quote=False)}\n</customer_email>"
    found = generator.structured(INTENT_PROMPT, request, IntentOnly)
    store.update_message(message_id, intent=found.intent.value, intent_summary=found.intent_summary)
    store.audit.record(
        "Communication Agent",
        "agent.intent",
        f"The customer's email: {found.intent.value.replace('_', ' ')}. {found.intent_summary}",
        customer_id=conversation["customer_id"],
        conversation_id=conversation["id"],
        message_id=message_id,
        detail={"intent": found.intent.value, "drafted": False},
    )
    if found.intent == Intent.stop_contact:
        stop_contact(
            store, conversation["customer_id"], found.intent_summary, "Communication Agent"
        )


def redraft(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    conversation_id: int,
    feedback: str | None,
    user: dict,
) -> dict:
    """A new draft for the same point in the thread: the first email, or the latest reply."""
    conversation = open_conversation(store, conversation_id)
    contact_allowed(store, conversation["customer_id"])
    if conversation["status"] == "escalated":
        raise ConversationError(
            409, "escalated", "A person has taken over this conversation: write to them directly."
        )
    emails = thread(conversation)
    if emails and emails[-1]["direction"] == "outbound":
        raise ConversationError(
            409, "waiting_for_customer", "The last email was sent: wait for the customer's reply."
        )
    store.supersede_drafts(conversation_id)
    store.audit.record(
        actor(user),
        "draft.redraft_requested",
        "A new draft was asked for" + (f": “{feedback}”" if feedback else "."),
        customer_id=conversation["customer_id"],
        conversation_id=conversation_id,
        detail={"feedback": feedback},
    )
    if not emails:
        case_file, outcome, review = _first_email(
            kb, portfolio, generator, conversation["customer_id"], feedback
        )
        decision = outcome["decision"]["recommendation"].model_dump(mode="json")
        store.update(conversation_id, case_file=case_file, decision=decision)
        save_draft(store, conversation, outcome, review)
    else:
        _reply(kb, portfolio, generator, store, conversation, feedback)
    return store.get(conversation_id)


# --- human approval -------------------------------------------------------------------------


def _draft(store: Conversations, message_id: int, status: str) -> dict:
    message = store.get_message(message_id)
    if message is None or message["direction"] != "outbound":
        raise ConversationError(404, "not_found", "Draft not found.")
    if message["status"] != status:
        raise ConversationError(
            409, "wrong_status", f"This draft is {message['status'].replace('_', ' ')}."
        )
    open_conversation(store, message["conversation_id"])
    return message


def approve(
    portfolio: Portfolio,
    store: Conversations,
    message_id: int,
    user: dict,
    subject: str | None = None,
    body: str | None = None,
    note: str | None = None,
) -> dict:
    """An officer accepts a draft. An edited draft is checked against the guardrails again.

    Four eyes: a draft that needs a team lead goes to awaiting_supervisor after the first
    approval, and only a supervisor who is not the first approver can give the second."""
    message = store.get_message(message_id)
    if message and message["status"] == "awaiting_supervisor":
        return _second_approval(store, message, user, note)
    message = _draft(store, message_id, "pending_approval")
    conversation = store.get(message["conversation_id"])
    contact_allowed(store, conversation["customer_id"])
    subject = message["subject"] if subject is None else subject.strip()
    body = message["body"] if body is None else body.strip()
    edited = (subject, body) != (message["subject"], message["body"])
    if edited:
        customer = portfolio.get_customer(conversation["customer_id"])
        found = messages.edited_problems(subject, body, messages.email_values(customer))
        if found:
            raise ConversationError(
                422, "edit_breaks_rules", f"The edited email breaks the rules: {'; '.join(found)}."
            )
    fields = {
        "status": "approved",
        "reviewed_by": actor(user),
        "reviewed_at": now_iso(),
        "review_note": note,
    }
    if message["needs_supervisor"]:
        fields = {
            "status": "awaiting_supervisor",
            "first_approved_by": actor(user),
            "first_approved_at": now_iso(),
            "review_note": note,
        }
    if edited:
        fields |= {
            "subject": subject,
            "body": body,
            "draft_subject": message["subject"],
            "draft_body": message["body"],
        }
    store.update_message(message_id, **fields)
    first = message["needs_supervisor"]
    store.audit.record(
        actor(user),
        "draft.first_approval" if first else "draft.approved",
        ("Approved after editing it" if edited else "Approved")
        + (" (a team lead must approve it too)" if first else "")
        + (f": “{note}”" if note else "."),
        customer_id=conversation["customer_id"],
        conversation_id=conversation["id"],
        message_id=message_id,
        detail={"edited": edited, "agent_body": message["body"] if edited else None},
    )
    return store.get(conversation["id"])


def _second_approval(store: Conversations, message: dict, user: dict, note: str | None) -> dict:
    conversation = open_conversation(store, message["conversation_id"])
    contact_allowed(store, conversation["customer_id"])
    if not user.get("supervisor"):
        raise ConversationError(
            403,
            "supervisor_required",
            "This email offers a payment plan or answers a hardship report: a team lead must "
            "give the second approval.",
        )
    if actor(user) == message["first_approved_by"]:
        raise ConversationError(
            403,
            "four_eyes",
            "Four eyes: the second approval must come from someone other than the first approver.",
        )
    store.update_message(
        message["id"],
        status="approved",
        reviewed_by=actor(user),
        reviewed_at=now_iso(),
        review_note=note or message["review_note"],
    )
    store.audit.record(
        actor(user),
        "draft.approved",
        f"Second approval by a team lead (first: {message['first_approved_by']})"
        + (f": “{note}”" if note else "."),
        customer_id=conversation["customer_id"],
        conversation_id=conversation["id"],
        message_id=message["id"],
    )
    return store.get(conversation["id"])


def reject(store: Conversations, message_id: int, user: dict, note: str) -> dict:
    message = store.get_message(message_id)
    if message is None or message["direction"] != "outbound":
        raise ConversationError(404, "not_found", "Draft not found.")
    if message["status"] not in ("pending_approval", "awaiting_supervisor", "blocked", "approved"):
        raise ConversationError(
            409, "wrong_status", f"This draft is {message['status'].replace('_', ' ')}."
        )
    conversation = open_conversation(store, message["conversation_id"])
    store.update_message(
        message_id,
        status="rejected",
        reviewed_by=actor(user),
        reviewed_at=now_iso(),
        review_note=note,
    )
    store.audit.record(
        actor(user),
        "draft.rejected",
        f"Rejected: “{note}”",
        customer_id=conversation["customer_id"],
        conversation_id=conversation["id"],
        message_id=message_id,
    )
    return store.get(message["conversation_id"])


def send(
    portfolio: Portfolio,
    store: Conversations,
    message_id: int,
    user: dict,
    rules: contact_rules.Rules | None = None,
    mailer=None,
) -> dict:
    """Sends an approved email: the contact rules first, then the mailbox (Microsoft Graph,
    or recorded only when email is not connected)."""
    message = _draft(store, message_id, "approved")
    conversation = store.get(message["conversation_id"])
    customer_id = conversation["customer_id"]
    contact_allowed(store, customer_id)
    customer = portfolio.get_customer(customer_id)
    if not customer or not customer.get("email"):
        raise ConversationError(
            409, "no_email_address", "This customer has no email address on record."
        )
    rules = rules or contact_rules.load_default()
    broken = contact_rules.problems(rules, store.db, customer_id)
    if broken:
        when = contact_rules.next_window(rules)
        store.audit.record(
            actor(user),
            "contact.refused",
            f"Send refused: {'; '.join(broken)}.",
            customer_id=customer_id,
            conversation_id=conversation["id"],
            message_id=message_id,
            detail={"rules": rules.describe(), "problems": broken},
        )
        raise ConversationError(
            409,
            "contact_rules",
            f"Not sent: {'; '.join(broken)}. Contact hours open again at "
            f"{contact_rules.local_time(rules, when):%Y-%m-%d %H:%M} customer time.",
        )
    result = {"delivered": False, "external_id": None, "external_conversation_id": None}
    if mailer is not None:
        try:
            result = mailer.send(
                customer["email"], message["subject"], message["body"], customer_id
            )
        except MailError as exc:
            raise ConversationError(502, "email_failed", f"Not sent: {exc.message}") from exc
    store.update_message(
        message_id,
        status="sent",
        sent_by=actor(user),
        sent_at=now_iso(),
        delivered=int(result["delivered"]),
        external_id=result["external_id"],
        external_conversation_id=result["external_conversation_id"],
    )
    store.ledger.record_contact(customer_id, "email", "sent", actor(user), conversation["id"])
    store.cancel_follow_ups(conversation["id"], "A newer email was sent.")
    store.audit.record(
        actor(user),
        "draft.sent",
        "Email sent through Microsoft 365."
        if result["delivered"]
        else "Email sent (recorded: email delivery is not connected).",
        customer_id=customer_id,
        conversation_id=conversation["id"],
        message_id=message_id,
        detail={"delivered": result["delivered"]},
    )
    return store.get(conversation["id"])


# --- the mailbox ----------------------------------------------------------------------------

REFERENCE = re.compile(r"\bCA-(\d{6})\b")


def match_email(store: Conversations, portfolio: Portfolio, email: dict) -> dict | None:
    """The open conversation an incoming email belongs to: the mailbox thread it replies to,
    or else its reference (CA-000123) when it comes from that customer's address."""
    if email.get("conversation_id"):
        with store.db.connect() as conn:
            row = conn.execute(
                "SELECT conversation_id FROM conversation_messages"
                " WHERE external_conversation_id = ? ORDER BY id DESC LIMIT 1",
                (email["conversation_id"],),
            ).fetchone()
        if row:
            conversation = store.get(row["conversation_id"])
            return conversation if conversation["status"] != "closed" else None
    found = REFERENCE.search(f"{email.get('subject') or ''}\n{email['body']}")
    if not found:
        return None
    customer = portfolio.get_customer(int(found[1]))
    if not customer or (customer.get("email") or "").lower() != email["from"]:
        return None  # a reference alone is not enough: anyone can type one
    conversation = store.latest_for(customer["customer_id"])
    return conversation if conversation and conversation["status"] != "closed" else None


def poll_mailbox(kb, portfolio: Portfolio, generator, store: Conversations, mailer) -> dict:
    """Reads the collections mailbox: each new email that belongs to an open conversation is
    received like a pasted reply (the Communication Agent answers it) and marked read. The
    others stay unread for a person to look at."""
    try:
        emails = mailer.unread()
    except MailError as exc:
        raise ConversationError(503, "email_unavailable", exc.message) from exc
    matched, unmatched, failed = [], 0, []
    mailbox = {"email": "Collections mailbox"}
    for email in emails:
        conversation = match_email(store, portfolio, email)
        if conversation is None:
            unmatched += 1
            continue
        try:
            receive(kb, portfolio, generator, store, conversation["id"], email.get("subject"),
                    email["body"][:5000] or "(empty email)", mailbox)  # fmt: skip
            mailer.mark_read(email["id"])
            matched.append(conversation["id"])
        except (ConversationError, MailError) as exc:
            failed.append({"conversation_id": conversation["id"], "error": exc.message})
    return {"fetched": len(emails), "matched": matched, "unmatched": unmatched, "failed": failed}


def close(store: Conversations, conversation_id: int, user: dict, note: str | None) -> dict:
    conversation = open_conversation(store, conversation_id)
    store.supersede_drafts(conversation_id)
    store.cancel_follow_ups(conversation_id, "The conversation was closed.")
    reason = f"Closed by {actor(user)}" + (f": {note}" if note else ".")
    store.update(conversation_id, status="closed", status_reason=reason)
    store.audit.record(
        actor(user),
        "conversation.closed",
        reason,
        customer_id=conversation["customer_id"],
        conversation_id=conversation_id,
    )
    return store.get(conversation_id)
