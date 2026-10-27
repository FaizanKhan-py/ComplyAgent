"""The Follow-up Agent: what happens when a customer does not reply.

    plan      after an email is sent: reads the thread, the facts and the contact and
              escalation policies, and schedules one follow-up (wait N days, then send a
              reminder, ask an officer to call, or hand the account to a person)
    run       when it is due: a reminder is drafted through the same LangGraph loop (writer,
              guardrails, Red Team) and waits for an officer's approval like every other email;
              a call becomes a task for the officer; an escalation hands the conversation over
    run_due   every scheduled follow-up whose time has come (the Follow-ups page, the
              run-follow-ups command, or the scheduler when FOLLOW_UP_INTERVAL_MINUTES is set)

A reply from the customer, a newer email, a hand-over or closing the conversation cancels the
scheduled follow-up. Planning never blocks sending: if the model cannot plan, the default (a
reminder after DEFAULT_WAIT_DAYS) is scheduled and the audit trail says why.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field

from ..customers.portfolio import Portfolio
from ..langchain.llm import AnswerGenerator
from ..langchain.memory import email_thread
from ..langchain.prompts import FOLLOW_UP_PLAN_PROMPT, FOLLOW_UP_PROMPT
from ..rag.knowledge_base import KnowledgeBase
from . import compliance_loop, messages
from .conversation import (
    REPLY_REQUIRED,
    ConversationError,
    Conversations,
    escalate,
    review_of,
    save_draft,
    thread,
    too_many_sent,
)
from .decision import Recommendation

logger = logging.getLogger("complyagent.follow_up")

DEFAULT_WAIT_DAYS = 7
MIN_WAIT_DAYS, MAX_WAIT_DAYS = 1, 30
PASSAGES_PER_QUESTION = 3
POLICY_QUESTIONS = (
    "How often may a customer be contacted about an overdue balance, and how long must staff "
    "wait before contacting them again?",
    "What must happen when a customer does not respond to collections messages? When is the "
    "account escalated to a person?",
)


class FollowUpAction(str, Enum):
    send_reminder = "send_reminder"
    schedule_call = "schedule_call"
    escalate_to_human = "escalate_to_human"


class FollowUpPlan(BaseModel):
    wait_days: int = Field(description="Days to wait for a reply, 1 to 30.")
    action: FollowUpAction
    reason: str = Field(description="One sentence: why this plan.")
    source_ids: list[int] = Field(description="Policy passages it follows.")


class FollowUpDraft(BaseModel):
    subject: str = Field(description="The email subject line.")
    body: str = Field(description="The email body, with {placeholders} for amounts and links.")
    source_ids: list[int] = Field(description="Numbers of the policy passages it follows.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _passages(kb: KnowledgeBase) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    passages = []
    for query in POLICY_QUESTIONS:
        for chunk in kb.search(query, PASSAGES_PER_QUESTION):
            key = (chunk["document_id"], chunk["chunk_index"])
            if key in seen:
                continue
            seen.add(key)
            passages.append({**chunk, "number": len(passages) + 1})
    return passages


def _blocks(facts: list[dict], passages: list[dict]) -> str:
    fact_lines = "\n".join(
        f'<fact id="{f["id"]}">{html.escape(f["text"], quote=False)}</fact>' for f in facts
    )
    sources = "\n".join(
        f'<source id="{p["number"]}" document="{html.escape(p["filename"], quote=True)}">\n'
        f"{html.escape(p['text'], quote=False)}\n</source>"
        for p in passages
    )
    return f"<facts>\n{fact_lines}\n</facts>\n\n<sources>\n{sources}\n</sources>"


def _ask_for_plan(kb, generator, customer: dict, conversation: dict) -> FollowUpPlan:
    emails = thread(conversation)
    lines = "\n\n".join(
        f'<email from="{"customer" if e["direction"] == "inbound" else "us"}">\n'
        f"{html.escape(e['body'], quote=False)}\n</email>"
        for e in emails
    )
    rec = Recommendation.model_validate(conversation["decision"])
    passages = _passages(kb)
    sent = sum(1 for e in emails if e["direction"] == "outbound")
    request = (
        f"<conversation>\n{lines}\n</conversation>\n\nEmails sent so far: {sent}.\n"
        f"Action chosen by the Decision Agent: {rec.action.value} "
        f"(priority {rec.priority.value})\n\n"
        f"{_blocks(messages.writer_facts(customer), passages)}\n\nPlan the follow-up."
    )
    plan = generator.structured(FOLLOW_UP_PLAN_PROMPT, request, FollowUpPlan)
    numbers = {p["number"] for p in passages}
    return plan.model_copy(
        update={
            "wait_days": max(MIN_WAIT_DAYS, min(MAX_WAIT_DAYS, plan.wait_days)),
            "source_ids": [s for s in plan.source_ids if s in numbers],
        }
    )


def plan(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    conversation_id: int,
) -> dict | None:
    """Schedules the follow-up after an email was sent. None: the conversation is not open."""
    conversation = store.get(conversation_id)
    if conversation is None or conversation["status"] != "open":
        return None
    if store.do_not_contact(conversation["customer_id"]):
        return None
    customer = portfolio.get_customer(conversation["customer_id"])
    planned_by = "model"
    try:
        if generator is None:
            raise RuntimeError("no language model is configured")
        result = _ask_for_plan(kb, generator, customer, conversation)
    except Exception as exc:  # planning must never stop an email from being sent
        logger.warning("Follow-up planning failed, using the default: %s", exc)
        planned_by = "default"
        result = FollowUpPlan(
            wait_days=DEFAULT_WAIT_DAYS,
            action=FollowUpAction.send_reminder,
            reason=f"The default plan: a reminder after {DEFAULT_WAIT_DAYS} days without a reply.",
            source_ids=[],
        )
    due = _iso(_now() + timedelta(days=result.wait_days))
    follow_up_id = store.schedule_follow_up(
        conversation_id, conversation["customer_id"], due, result.action.value, result.reason
    )
    store.audit.record(
        "Follow-up Agent",
        "follow_up.scheduled",
        f"In {result.wait_days} day(s) without a reply: {result.action.value.replace('_', ' ')}. "
        f"{result.reason}",
        customer_id=conversation["customer_id"],
        conversation_id=conversation_id,
        detail={"planned_by": planned_by, "due_at": due, **result.model_dump(mode="json")},
    )
    return store.get_follow_up(follow_up_id)


def _reminder(kb, portfolio, generator, store: Conversations, conversation: dict) -> str:
    """Drafts the follow-up email through the graph. Returns the outcome to record."""
    if generator is None:
        raise ConversationError(
            503,
            "llm_not_configured",
            "No language model is configured, so no follow-up can be written.",
        )
    waiting = [
        m
        for m in conversation["messages"]
        if m["status"] in ("pending_approval", "blocked", "approved")
    ]
    if waiting:
        return "A draft is already waiting for an officer, so no reminder was written."
    if too_many_sent(store, conversation, "Follow-up Agent"):
        return "Too many emails without a reply: a person takes over."
    customer = portfolio.get_customer(conversation["customer_id"])
    emails = thread(conversation)
    last_sent = next(e for e in reversed(emails) if e["direction"] == "outbound")
    passages = messages.message_passages(kb, customer)
    facts = messages.writer_facts(customer)
    values = messages.email_values(customer)
    decision = {"recommendation": Recommendation.model_validate(conversation["decision"])}
    rec = decision["recommendation"]

    def build(_: dict) -> str:
        return (
            f"The last email was sent on {last_sent['sent_at'][:10]} and the customer has not "
            f"replied.\nAction chosen by the Decision Agent: {rec.action.value} "
            f"(priority {rec.priority.value})\n\n{_blocks(facts, passages)}\n\n"
            "Write the follow-up email."
        )

    job = compliance_loop.EmailJob(
        system=FOLLOW_UP_PROMPT,
        schema=FollowUpDraft,
        build=build,
        decision=decision,
        facts=facts,
        passages=passages,
        values=values,
        required=REPLY_REQUIRED,
        history=email_thread(emails),
        writer_name="Follow-up Agent",
    )
    outcome = compliance_loop.run(generator, job)
    save_draft(store, conversation, outcome, review_of(outcome, passages, values, None))
    if outcome["status"] == "passed":
        return "A follow-up email was drafted and waits for an officer's approval."
    return "A follow-up email was drafted, but the Red Team blocked it: write it again."


def run(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    follow_up_id: int,
    by: str = "Follow-up Agent",
) -> dict:
    """Carries out one scheduled follow-up now. Returns the conversation."""
    follow_up = store.get_follow_up(follow_up_id)
    if follow_up is None:
        raise ConversationError(404, "not_found", "Follow-up not found.")
    if follow_up["status"] != "scheduled":
        raise ConversationError(409, "wrong_status", f"This follow-up is {follow_up['status']}.")
    conversation = store.get(follow_up["conversation_id"])
    ids = {"customer_id": conversation["customer_id"], "conversation_id": conversation["id"]}
    if conversation["status"] != "open":
        reason = f"The conversation is {conversation['status']}."
        store.finish_follow_up(follow_up_id, "cancelled", reason)
        return store.get(conversation["id"])
    if store.do_not_contact(conversation["customer_id"]):
        store.finish_follow_up(follow_up_id, "cancelled", "The customer asked not to be contacted.")
        return store.get(conversation["id"])

    action = follow_up["action"]
    if action == FollowUpAction.escalate_to_human.value:
        escalate(store, conversation, follow_up["reason"], "Follow-up Agent")
        result = "The conversation was handed to a person."
    elif action == FollowUpAction.schedule_call.value:
        result = f"An officer should call the customer now. {follow_up['reason']}"
    else:
        result = _reminder(kb, portfolio, generator, store, conversation)
    store.finish_follow_up(follow_up_id, "done", result)
    store.audit.record(
        by, "follow_up.done", result, **ids, detail={"follow_up_id": follow_up_id, "action": action}
    )
    return store.get(conversation["id"])


def run_due(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    store: Conversations,
    now: datetime | None = None,
) -> dict:
    """Every scheduled follow-up whose time has come. One failure does not stop the others: it
    stays scheduled, and the error is reported."""
    due = store.list_follow_ups("scheduled", _iso(now or _now()))
    done, failed = [], []
    for follow_up in due:
        try:
            run(kb, portfolio, generator, store, follow_up["id"])
            done.append(follow_up["id"])
        except Exception as exc:
            logger.warning("Follow-up %s failed: %s", follow_up["id"], exc)
            failed.append({"id": follow_up["id"], "error": getattr(exc, "message", str(exc))})
    return {"due": len(due), "done": done, "failed": failed}
