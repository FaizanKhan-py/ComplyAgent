"""What each autopilot task does. Every agent reads and writes the customer's saved context, so
a later event (a reply, a payment) builds on what is known instead of starting again.

    analyse    Analysis Agent: the Case Analyst's case file from the payment history, plus a
               cross-check of the risk model's prediction against that history, and the
               communication so far (emails, replies, promises)
    decide     Decision Agent: the next action from the analysis, the policies (RAG) and the
               newest reply: tone (low / medium / hard), channel (email / call), whether to
               offer a hardship plan, and whether a person must take over
    converse   Conversation Agent: writes the email through the compliance loop (guardrails and
               Red Team), sends it within the contact rules, and plans the follow-up
    escalate   hands the customer to an officer and emails the officers
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..core.config import Settings
from ..core.db import now_iso
from ..customers.portfolio import Portfolio
from ..decisions import compliance_loop, contact_rules, conversation, follow_up, messages
from ..decisions.case_analyst import CaseAnalysis, analyse
from ..decisions.conversation import ALWAYS_HUMAN, AUTOPILOT, ConversationError, Intent
from ..decisions.decision import Action, Channel, Recommendation, recommend
from ..integrations.mail import MailError
from ..orchestration import graph
from ..rag.knowledge_base import KnowledgeBase
from .store import Store

logger = logging.getLogger("complyagent.autopilot")

ROBOT = {"email": AUTOPILOT, "role": "admin"}
TONE = {"low": "low", "medium": "medium", "high": "hard", "critical": "hard"}
WAITING_DRAFT = ("pending_approval", "awaiting_supervisor", "approved")


class Later(Exception):
    """The task must wait (e.g. outside the contact hours) and run again at `when`."""

    def __init__(self, when: str, reason: str) -> None:
        super().__init__(reason)
        self.when = when
        self.reason = reason


def cross_check(customer: dict) -> dict:
    """Does the risk model's prediction agree with the last three months of payments?"""
    p = customer.get("default_probability")
    recent = [h for h in customer.get("history", []) if h["month_no"] <= 3]
    late = sum(1 for h in recent if h["repayment_status"] > 0)
    billed = sum(max(h["bill_amt"], 0) for h in recent)
    paid = sum(h["pay_amt"] for h in recent) + (customer.get("recorded_paid") or 0)
    ratio = round(paid / billed, 2) if billed else 1.0
    base = {"default_probability": p, "late_months_last_3": late, "paid_ratio_last_3": ratio}
    if p is None:
        return {**base, "verdict": "no_prediction",
                "note": "The customer has no risk score yet."}  # fmt: skip
    model_high, history_bad = p >= 0.5, late >= 2 or ratio < 0.1
    if model_high == history_bad:
        note = (f"The model ({p:.0%}) and the history ({late} late month(s) of the last 3, "
                f"{ratio:.0%} of the bills paid) agree.")  # fmt: skip
        return {**base, "verdict": "agrees", "note": note}
    if model_high:
        note = (f"The model predicts default ({p:.0%}), but the customer paid {ratio:.0%} of the "
                f"last 3 bills with {late} late month(s): the risk may be overstated.")  # fmt: skip
    else:
        note = (f"The model predicts a low risk ({p:.0%}), but {late} of the last 3 months were "
                f"late and {ratio:.0%} of the bills paid: risk may be understated.")  # fmt: skip
    return {**base, "verdict": "disagrees", "note": note}


def plan_of(rec: Recommendation) -> dict:
    """The Decision Agent's recommendation as the autopilot carries it out."""
    escalate = rec.action == Action.escalate_to_human
    call = rec.channel == Channel.phone or rec.action == Action.schedule_call
    if escalate:
        next_step = "escalate"
    elif rec.action == Action.no_contact:
        next_step = "wait"
    elif call:
        next_step = "call"
    else:
        next_step = "email"
    return {
        "tone": TONE[rec.priority.value],
        "channel": "none" if next_step == "wait" else ("call" if call else "email"),
        "hardship_offer": rec.action == Action.offer_payment_plan,
        "escalate": escalate,
        "next": next_step,
    }


class Agents:
    def __init__(self, settings: Settings, kb: KnowledgeBase, portfolio: Portfolio, mailer,
                 store: Store) -> None:  # fmt: skip
        self.settings = settings
        self.kb = kb
        self.portfolio = portfolio
        self.mailer = mailer
        self.store = store
        self.conversations = conversation.Conversations(kb.db)
        self.audit = self.conversations.audit

    def run(self, task: dict) -> str:
        """Carries out one task; returns one line saying what it did."""
        return getattr(self, task["kind"])(task["customer_id"], task["payload"])

    # --- Analysis Agent ---------------------------------------------------------------------

    def analyse(self, cid: int, payload: dict) -> str:
        customer = self.portfolio.get_customer(cid)
        if customer is None:
            self.store.save(cid, stage="failed", note="The customer no longer exists.")
            return "Customer not found."
        self.store.save(cid, stage="analysing", note="The Analysis Agent is reading the records.")
        case = analyse(self.portfolio, self.kb.generator, cid)
        a: CaseAnalysis = case["analysis"]
        check = cross_check(customer)
        analysis = {
            "analysed_by": case["analysed_by"],
            "model": case["model"],
            "facts": case["facts"],
            "analysis": a.model_dump(mode="json"),
            "prediction_check": check,
            "communication": self._communication(cid),
            "trigger": payload.get("reason", "overdue"),
            "at": now_iso(),
        }
        resolved = a.situation.value == "paid_up"
        self.store.save(cid, analysis=analysis, stage="resolved" if resolved else "deciding",
                        note=a.summary)  # fmt: skip
        self.audit.record(
            "Analysis Agent", "agent.case_analysis",
            f"{a.situation.value.replace('_', ' ').capitalize()}, {a.urgency.value} urgency. "
            f"Prediction check: {check['verdict'].replace('_', ' ')}. {check['note']}",
            customer_id=cid, detail=analysis,
        )  # fmt: skip
        if resolved:
            return "Nothing is overdue: resolved."
        self.store.enqueue("decide", cid, {"reason": analysis["trigger"]})
        return f"Analysed: {a.situation.value}, {a.urgency.value} urgency."

    def _communication(self, cid: int) -> dict:
        latest = self.conversations.latest_for(cid)
        emails = conversation.thread(latest) if latest else []
        replies = [m for m in emails if m["direction"] == "inbound"]
        last = replies[-1] if replies else None
        ledger = self.conversations.ledger
        return {
            "emails_sent": sum(1 for m in emails if m["direction"] == "outbound"),
            "replies": len(replies),
            "last_reply_intent": last and last.get("intent"),
            "last_reply_summary": last and last.get("intent_summary"),
            "broken_promises": ledger.broken_count(cid),
            "open_promise": ledger.open_promise(cid),
            "paid_since": ledger.paid_total(cid),
        }

    # --- Decision Agent ---------------------------------------------------------------------

    def decide(self, cid: int, payload: dict) -> str:
        ctx = self.store.context(cid)
        if not ctx or not ctx["analysis"]:
            self.store.enqueue("analyse", cid, {"reason": "no analysis yet"})
            return "No analysis yet: the Analysis Agent runs first."
        if self.conversations.do_not_contact(cid):
            return self._escalate_now(cid, "The customer asked not to be contacted.")
        reply = self.conversations.get_message(payload["message_id"]) if payload.get(
            "message_id") else None  # fmt: skip
        if reply:
            latest = self.conversations.get(reply["conversation_id"])
            if latest["status"] == "escalated":
                return self._escalate_now(cid, latest["status_reason"] or "A person took over.")
            if reply.get("intent") and Intent(reply["intent"]) in ALWAYS_HUMAN:
                why = f"The customer's reply is a {reply['intent'].replace('_', ' ')}"
                summary = reply.get("intent_summary") or ""
                return self._escalate_now(cid, f"{why}: {summary}".rstrip(": "))
        self.store.save(cid, stage="deciding", note="The Decision Agent is choosing the next step.")
        case = self._case(ctx)
        result = recommend(self.kb, self.portfolio, self.kb.generator, cid, case=case,
                           extra=self._context_text(ctx, reply))  # fmt: skip
        rec: Recommendation = result["recommendation"]
        plan = plan_of(rec)
        decision = {
            "recommendation": rec.model_dump(mode="json"),
            "plan": plan,
            "model": result.get("model"),
            "trigger": "reply" if reply else payload.get("reason", "analysis"),
            "reply_id": reply and reply["id"],
            "sources": [
                {"number": p["number"], "filename": p["filename"]}
                for p in result.get("sources", [])
            ],  # fmt: skip
            "at": now_iso(),
        }
        self.store.save(cid, decision=decision)
        reasons = " ".join(r.text for r in rec.reasons[:2])
        self.audit.record(
            "Decision Agent", "agent.decision",
            f"Next step: {plan['next']} (tone {plan['tone']}, channel {plan['channel']}"
            f"{', hardship plan offered' if plan['hardship_offer'] else ''}). {reasons}",
            customer_id=cid, conversation_id=ctx["conversation_id"], detail=decision,
        )  # fmt: skip
        if plan["next"] == "escalate":
            return self._escalate_now(cid, reasons or "The Decision Agent escalated the case.")
        if plan["next"] == "call":
            self.store.enqueue("escalate", cid, {"kind": "call", "reason":
                               f"Call the customer ({plan['tone']} tone). {reasons}"})  # fmt: skip
            return "Decided: an officer should call."
        if plan["next"] == "wait":
            self.store.save(cid, stage="waiting", note=f"No contact for now. {reasons}")
            return "Decided: no contact for now."
        self.store.save(cid, stage="writing", note="The Conversation Agent is writing the email.")
        self.store.enqueue("converse", cid, {"mode": "reply" if reply else "first"})
        return f"Decided: email, {plan['tone']} tone."

    @staticmethod
    def _case(ctx: dict) -> dict:
        a = ctx["analysis"]
        return {**a, "analysis": CaseAnalysis.model_validate(a["analysis"])}

    def _context_text(self, ctx: dict, reply: dict | None) -> str:
        """What the Decision Agent is told besides the records: the prediction check, the
        communication so far and the newest reply."""
        a = ctx["analysis"]
        comms = self._communication(ctx["customer_id"])
        lines = [
            f"Risk model cross-check: {a['prediction_check']['note']}",
            f"Communication so far: {comms['emails_sent']} email(s) sent, {comms['replies']} "
            f"repl(ies), {comms['broken_promises']} broken promise(s), NT${comms['paid_since']:,} "
            "paid since the records.",
        ]
        if comms["open_promise"]:
            lines.append(f"Open promise to pay by {comms['open_promise']['due_on']}.")
        if reply:
            lines.append(
                f"The customer just replied ({(reply.get('intent') or 'unclassified')}): "
                f"{reply.get('intent_summary') or reply['body'][:300]}"
            )
        return "<context>\n" + "\n".join(lines) + "\n</context>"

    # --- Conversation Agent -----------------------------------------------------------------

    def converse(self, cid: int, payload: dict) -> str:
        ctx = self.store.context(cid)
        if self.conversations.do_not_contact(cid):
            return self._escalate_now(cid, "The customer asked not to be contacted.")
        conv = self.conversations.get(ctx["conversation_id"]) if ctx["conversation_id"] else None
        mode = payload.get("mode", "send")
        if mode == "first" and (conv is None or conv["status"] == "closed"):
            done = self._first_email(cid, ctx)
            if done:
                return done
        elif mode == "reply" and conv and conv["status"] == "open" and not self.waiting(conv):
            rec = ctx["decision"]["recommendation"]
            self.conversations.update(conv["id"], decision=rec)
            conversation._reply(self.kb, self.portfolio, self.kb.generator, self.conversations,
                                self.conversations.get(conv["id"]))  # fmt: skip
            conv = self.conversations.get(conv["id"])
            if conv["status"] == "escalated":
                return self._escalate_now(cid, conv["status_reason"] or "A person must answer.")
            if conv["messages"][-1]["status"] == "blocked":
                return self._escalate_now(cid, "The compliance loop could not write a safe reply.")
        return self._send_waiting(cid)

    def _first_email(self, cid: int, ctx: dict) -> str | None:
        """Writes the first email from the saved analysis and decision. A line when nothing
        is sent; None when a draft is waiting to be sent."""
        case = self._case(ctx)
        decision = {"recommendation": Recommendation.model_validate(
            ctx["decision"]["recommendation"]), "model": ctx["decision"].get("model"),
            "facts": case["facts"], "sources": []}  # fmt: skip
        g = self.kb.generator
        result, context = messages.prepare(self.kb, self.portfolio, g, cid, case=case,
                                           decision=decision)  # fmt: skip
        if context is None:
            self.store.save(cid, stage="waiting", note=result["reason"])
            return f"No email: {result['reason']}"
        job = graph.first_email_job(self.kb, self.portfolio, g, cid, case, context, None)
        outcome = compliance_loop.outcome(graph.run_job(g, job))
        if outcome["status"] == "no_email":
            note = "The Red Team challenged the action; the Decision Agent chose no contact."
            self.store.save(cid, stage="waiting", note=note)
            return note
        rec = outcome["decision"]["recommendation"]
        case_file = {k: ctx["analysis"][k] for k in ("analysed_by", "model", "facts", "analysis")}
        conv_id = self.conversations.create(cid, case_file, rec.model_dump(mode="json"), AUTOPILOT)
        self.store.save(cid, conversation_id=conv_id)
        self.audit.record(AUTOPILOT, "conversation.started", "Conversation started.",
                          customer_id=cid, conversation_id=conv_id)  # fmt: skip
        review = conversation.review_of(outcome, job.passages, job.values,
                                        job.deadline(outcome["decision"]))  # fmt: skip
        conversation.save_draft(self.conversations, self.conversations.get(conv_id), outcome,
                                review)  # fmt: skip
        if outcome["status"] != "passed":
            return self._escalate_now(cid, "The compliance loop could not write a safe email.")
        return None

    @staticmethod
    def waiting(conv: dict) -> dict | None:
        """The newest draft waiting to go out, if it is newer than the customer's last email."""
        last_in = max((m["id"] for m in conv["messages"] if m["direction"] == "inbound"),
                      default=0)  # fmt: skip
        drafts = [m for m in conv["messages"]
                  if m["direction"] == "outbound" and m["status"] in WAITING_DRAFT
                  and m["id"] > last_in]  # fmt: skip
        return drafts[-1] if drafts else None

    def _send_waiting(self, cid: int) -> str:
        ctx = self.store.context(cid)
        conv = self.conversations.get(ctx["conversation_id"]) if ctx["conversation_id"] else None
        draft = self.waiting(conv) if conv and conv["status"] == "open" else None
        if draft is None:
            return "Nothing to send."
        if not self.settings.autopilot_auto_send:
            self.store.save(cid, stage="writing", note="The email waits for an officer's approval.")
            return "Draft waiting for an officer's approval."
        ids = {"customer_id": cid, "conversation_id": conv["id"], "message_id": draft["id"]}
        if draft["status"] != "approved":
            self.conversations.update_message(
                draft["id"],
                status="approved",
                reviewed_by=AUTOPILOT,
                reviewed_at=now_iso(),
                review_note="Passed the compliance checks; approved automatically.",
            )
            self.audit.record(AUTOPILOT, "draft.approved",
                              "Approved automatically: it passed the guardrails and the Red Team.",
                              **ids)  # fmt: skip
        rules = contact_rules.load(self.settings, self.kb)
        try:
            conversation.send(self.portfolio, self.conversations, draft["id"], ROBOT, rules,
                              self.mailer)  # fmt: skip
        except ConversationError as exc:
            if exc.code == "contact_rules":
                soonest = datetime.now(timezone.utc) + timedelta(hours=1)
                when = max(contact_rules.next_window(rules), soonest).isoformat(timespec="seconds")
                self.store.save(cid, note=f"Waiting for the contact rules: {exc.message}")
                raise Later(when, exc.message) from exc
            if exc.code == "no_email_address":
                return self._escalate_now(cid, "The customer has no email address on record.")
            raise
        follow_up.plan(self.kb, self.portfolio, self.kb.generator, self.conversations, conv["id"])
        self.store.save(cid, stage="awaiting_reply", note="Email sent; waiting for a reply.")
        return "Email sent."

    # --- escalation -------------------------------------------------------------------------

    def _escalate_now(self, cid: int, reason: str) -> str:
        return self.escalate(cid, {"reason": reason})

    def escalate(self, cid: int, payload: dict) -> str:
        reason, kind = payload.get("reason") or "A person must handle this case.", payload.get(
            "kind", "escalation")  # fmt: skip
        ctx = self.store.context(cid) or {}
        conv = (
            self.conversations.get(ctx["conversation_id"]) if ctx.get("conversation_id") else None
        )
        if conv and conv["status"] == "open":
            conversation.escalate(self.conversations, conv, reason, "Decision Agent")
        officers = self._officers()
        subject = (f"[ComplyAgent] Call customer #{cid}" if kind == "call"
                   else f"[ComplyAgent] Customer #{cid} needs an officer")  # fmt: skip
        summary = ((ctx.get("analysis") or {}).get("analysis") or {}).get("summary", "")
        body = (f"Customer #{cid} was handed to a person by the autopilot.\n\nWhy: {reason}\n\n"
                f"Case summary: {summary}\n\nOpen ComplyAgent > Live operations to see the full "
                "timeline.")  # fmt: skip
        delivered, errors = 0, []
        for to in officers:
            try:
                delivered += int(self.mailer.send(to, subject, body, cid)["delivered"])
            except MailError as exc:
                errors.append(exc.message)
        escalation = {"reason": reason, "kind": kind, "notified": officers,
                      "delivered": delivered, "errors": errors, "at": now_iso()}  # fmt: skip
        self.store.save(cid, stage="escalated", escalation=escalation, note=reason)
        how = (
            f"emailed {delivered} officer(s)"
            if delivered
            else f"{len(officers)} officer(s) notified (recorded: email is not connected)"
        )
        self.audit.record(
            AUTOPILOT, "autopilot.escalated",
            f"{'Call task for an officer' if kind == 'call' else 'Escalated to an officer'}: "
            f"{reason} ({how}).",
            customer_id=cid, conversation_id=conv and conv["id"], detail=escalation,
        )  # fmt: skip
        return "Escalated to an officer."

    def _officers(self) -> list[str]:
        if self.settings.escalation_email:
            return [e.strip() for e in self.settings.escalation_email.split(",") if e.strip()]
        return [u["email"] for u in self.kb.db.list_users() if u["role"] == "admin"]
