"""Policy guidance for one customer: the customer's facts turned into policy questions, each
answered by the RAG pipeline with citations.

The policies decide what to do; this module only states the facts. It does not encode any
policy rule itself, so changing a policy document changes the guidance.
"""

from __future__ import annotations

from ..customers.portfolio import Portfolio
from ..rag.knowledge_base import KnowledgeBase

CONTACT_QUESTION = (
    "What rules apply when contacting a customer about an overdue balance: permitted hours, "
    "how often, which channels, and what every message must contain?"
)


def _money(amount: int | None) -> str:
    return "an unknown amount" if amount is None else f"NT${amount:,}"


def describe(customer: dict) -> dict:
    """The facts the questions are built from. The data counts lateness in whole months."""
    months = customer["latest_status"] or 0
    return {
        "months_late": max(months, 0),
        "days_overdue_estimate": max(months, 0) * 30,
        "latest_bill": customer["latest_bill"],
        "default_probability": customer["default_probability"],
        "risk_level": customer["risk_level"],
    }


def questions(customer: dict) -> list[dict]:
    facts = describe(customer)
    months, days = facts["months_late"], facts["days_overdue_estimate"]
    if months == 0:
        return []  # nothing is overdue, so no collections policy applies
    lateness = f"{months} month{'s' if months != 1 else ''} late, about {days} days overdue"
    risk = (
        f", the risk model gives it a {facts['default_probability']:.0%} chance of default"
        if facts["default_probability"] is not None
        else ""
    )
    return [
        {
            "key": "actions",
            "title": "Required actions",
            "question": (
                f"An account is {lateness}. Which delinquency stage is it in, and what actions "
                "does the policy require at that stage?"
            ),
        },
        {
            "key": "escalation",
            "title": "Escalation to a human",
            "question": (
                f"An account is {lateness}, its latest bill is {_money(facts['latest_bill'])}"
                f"{risk}. Must it be escalated to a human reviewer, and under which conditions?"
            ),
        },
        {
            "key": "hardship",
            "title": "Hardship options",
            "question": (
                f"An account is {lateness}. Can the customer be offered a hardship plan, what may "
                "it offer, and who must approve it?"
            ),
        },
        {"key": "contact", "title": "Contacting the customer", "question": CONTACT_QUESTION},
    ]


def customer_guidance(kb: KnowledgeBase, portfolio: Portfolio, customer_id: int) -> dict | None:
    customer = portfolio.get_customer(customer_id)
    if customer is None:
        return None
    sections = [{**q, **kb.answer(q["question"])} for q in questions(customer)]
    return {"customer_id": customer_id, **describe(customer), "sections": sections}
