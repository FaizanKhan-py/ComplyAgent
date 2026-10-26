"""The Decision Agent: recommends the next collections action for one customer.

Python states the facts, the policy knowledge base supplies the rules, and the model only
chooses: one action, channel and priority from fixed lists, with every reason pointing at a
fact or a policy passage it was given. The reply is then checked here, so a reason that cites
something that was never provided is caught before anyone sees the recommendation.
"""

from __future__ import annotations

import html
from enum import Enum

from pydantic import BaseModel, Field

from ..customers.portfolio import Portfolio
from ..langchain.llm import AnswerGenerator
from ..langchain.prompts import DECISION_PROMPT
from ..rag.knowledge_base import KnowledgeBase
from .guidance import describe, questions

PASSAGES_PER_QUESTION = 3


class Action(str, Enum):
    send_reminder = "send_reminder"
    offer_payment_plan = "offer_payment_plan"
    schedule_call = "schedule_call"
    escalate_to_human = "escalate_to_human"
    no_contact = "no_contact"


class Channel(str, Enum):
    sms = "sms"
    email = "email"
    phone = "phone"
    none = "none"


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Reason(BaseModel):
    text: str = Field(description="One reason, in plain words.")
    fact_ids: list[str] = Field(description="Facts it relies on, e.g. ['F1', 'F3'].")
    source_ids: list[int] = Field(description="Policy passages it relies on, e.g. [2].")


class Recommendation(BaseModel):
    action: Action
    channel: Channel
    priority: Priority
    reasons: list[Reason]
    needs_human_review: bool


class DecisionUnavailable(Exception):
    """No recommendation can be made. `message` is safe to show to an administrator."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def case_facts(customer: dict) -> list[dict]:
    """What the records say, worded for the model, each with an id it can cite.

    Only payment behaviour and the risk score: sex, education, marriage and age are left out
    on purpose, so they cannot influence a collections decision.
    """
    d = describe(customer)
    history = customer.get("history") or []  # month_no 1 is the most recent month
    lines = []
    months = d["months_late"]
    lines.append(
        f"The account is {months} month{'s' if months != 1 else ''} late "
        f"(about {d['days_overdue_estimate']} days overdue)."
        if months
        else "Nothing is overdue on the account."
    )
    if d["latest_bill"] is not None:
        lines.append(f"The latest bill is NT${d['latest_bill']:,}.")
    if history:
        latest = history[0]
        lines.append(f"The latest payment was NT${latest['pay_amt']:,}.")
        late = sum(1 for h in history if h["repayment_status"] > 0)
        lines.append(f"The account was late in {late} of the last {len(history)} months.")
        if len(history) >= 3:
            before = max(history[2]["repayment_status"], 0)
            if months != before:
                trend = "grew" if months > before else "fell"
                lines.append(
                    f"Lateness {trend} from {before} to {months} months over the last 3 months."
                )
    limit = customer.get("limit_bal")
    if limit and d["latest_bill"] is not None:
        lines.append(f"The latest bill uses {d['latest_bill'] / limit:.0%} of the credit limit.")
    if d["default_probability"] is not None:
        lines.append(
            f"The risk model gives a {d['default_probability']:.0%} chance of default next month "
            f"(risk level: {d['risk_level']})."
        )
    return [{"id": f"F{n}", "text": text} for n, text in enumerate(lines, start=1)]


def message_question(customer: dict) -> str:
    """The channel and tone rules for this stage: the Decision Agent picks the channel, and the
    message generator writes in that tone."""
    d = describe(customer)
    return (
        f"A collections message goes to a customer whose payment is about "
        f"{d['days_overdue_estimate']} days overdue. Which channel and tone must the message "
        "use at this delinquency stage, and what must it contain or never contain?"
    )


def policy_passages(kb: KnowledgeBase, customer: dict) -> list[dict]:
    """The passages for the customer's policy questions, without duplicates, numbered from 1."""
    seen: set[tuple[str, int]] = set()
    passages = []
    queries = [q["question"] for q in questions(customer)] + [message_question(customer)]
    for query in queries:
        for chunk in kb.search(query, PASSAGES_PER_QUESTION):
            key = (chunk["document_id"], chunk["chunk_index"])
            if key in seen:
                continue
            seen.add(key)
            passages.append(
                {
                    "number": len(passages) + 1,
                    "document_id": chunk["document_id"],
                    "filename": chunk["filename"],
                    "page_number": chunk["page_number"],
                    "score": chunk["score"],
                    "text": chunk["text"],
                }
            )
    return passages


def build_request(facts: list[dict], passages: list[dict]) -> str:
    """Everything is escaped, so no fact or passage can close its own tag."""
    fact_lines = "\n".join(
        f'<fact id="{f["id"]}">{html.escape(f["text"], quote=False)}</fact>' for f in facts
    )
    blocks = []
    for p in passages:
        page = f' page="{p["page_number"]}"' if p["page_number"] is not None else ""
        name = html.escape(p["filename"], quote=True)
        blocks.append(
            f'<source id="{p["number"]}" document="{name}"{page}>\n'
            f"{html.escape(p['text'], quote=False)}\n</source>"
        )
    return (
        f"<facts>\n{fact_lines}\n</facts>\n\n<sources>\n" + "\n".join(blocks) + "\n</sources>\n\n"
        "Recommend the next collections action for this account."
    )


def problems(rec: Recommendation, fact_ids: set[str], source_ids: set[int]) -> list[str]:
    """Rule breaks in a reply, worded so the model can fix them when asked again."""
    found = []
    if (rec.action == Action.no_contact) != (rec.channel == Channel.none):
        found.append("action no_contact must go with channel none, and only with it")
    if not rec.reasons:
        found.append("give at least one reason")
    for n, reason in enumerate(rec.reasons, start=1):
        if not reason.fact_ids and not reason.source_ids:
            found.append(f"reason {n} cites no fact and no source")
        unknown = [f for f in reason.fact_ids if f not in fact_ids]
        unknown += [str(s) for s in reason.source_ids if s not in source_ids]
        if unknown:
            found.append(f"reason {n} cites ids that are not in the request: {', '.join(unknown)}")
    return found


def recommend(
    kb: KnowledgeBase, portfolio: Portfolio, generator: AnswerGenerator | None, customer_id: int
) -> dict | None:
    """The recommendation with the facts and passages it is based on. None: no such customer.

    Raises DecisionUnavailable when no model or no policy evidence is available, and LLMError
    when the model fails.
    """
    customer = portfolio.get_customer(customer_id)
    if customer is None:
        return None
    facts = case_facts(customer)
    result = {"customer_id": customer_id, "facts": facts, "model": None, "corrected": []}

    if describe(customer)["months_late"] == 0:
        # Nothing to collect: no policy applies and no model is needed.
        rec = Recommendation(
            action=Action.no_contact,
            channel=Channel.none,
            priority=Priority.low,
            reasons=[Reason(text="Nothing is overdue.", fact_ids=["F1"], source_ids=[])],
            needs_human_review=False,
        )
        return {**result, "decided_by": "rule", "recommendation": rec, "sources": []}

    if generator is None:
        raise DecisionUnavailable(
            "llm_not_configured", "No language model is configured, so no decision can be made."
        )
    passages = policy_passages(kb, customer)
    if not passages:
        raise DecisionUnavailable(
            "no_policy_evidence",
            "No policy passage matches this account. Upload and index the collections policies.",
        )

    request = build_request(facts, passages)
    fact_ids = {f["id"] for f in facts}
    source_ids = {p["number"] for p in passages}
    rec = generator.structured(DECISION_PROMPT, request, Recommendation)
    found = problems(rec, fact_ids, source_ids)
    if found:
        # One more try, told exactly what was wrong. Still wrong: nobody sees it.
        retry = (
            f"{request}\n\nYour previous reply broke these rules: {'; '.join(found)}. "
            "Reply again, following every rule."
        )
        rec = generator.structured(DECISION_PROMPT, retry, Recommendation)
        if problems(rec, fact_ids, source_ids):
            raise DecisionUnavailable(
                "llm_bad_output",
                "The model's recommendation cited evidence it was not given, twice. Try again.",
            )
    return {
        **result,
        "decided_by": "model",
        "model": generator.model,
        "recommendation": rec,
        "sources": passages,
        "corrected": found,
    }
