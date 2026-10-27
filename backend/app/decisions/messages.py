"""The message generator: writes a draft collections email for one overdue customer.

The Decision Agent chooses the action (a reminder, a payment-plan offer, a call); the messaging
policy (MG-04, or whatever the knowledge base holds) supplies the tone and the rules. The model
writes the email with {placeholders} for every amount, link and reference, and Python fills them
from the record, so the model can never invent a figure or a link. The draft is then checked
against fixed guardrails and by the Red Team (compliance_loop.py), and waits for an officer's
approval (conversation.py).
"""

from __future__ import annotations

import html
import re

from pydantic import BaseModel, Field

from ..customers.portfolio import Portfolio
from ..langchain.llm import AnswerGenerator
from ..rag.knowledge_base import KnowledgeBase
from .decision import Action, DecisionUnavailable, case_facts, recommend
from .guidance import describe

ORGANISATION = "ComplyAgent Finance"
# Fictional demo values: the dataset has no names, phone numbers or payment pages.
SUPPORT_PHONE = "0800-000-123"
PAYMENT_URL = "https://pay.complyagent.example/{reference}"

EMAIL_MAX_WORDS = 150
MAX_ATTEMPTS = 3  # one draft and up to two corrections
PASSAGES_PER_QUESTION = 3

PLACEHOLDERS = ("amount_due", "months_overdue", "payment_link", "support_phone", "reference")
# Words no collections message may use: threats, and anything about the model behind it.
FORBIDDEN = re.compile(
    r"\b(legal action|court|police|lawsuit|sue|seiz\w*|arrest|consequences?|"
    r"further (?:action|actions|steps|complications|escalation)|"
    r"risk score|risk model|risk level|(?:low|medium|high)[- ]risk|"
    r"probability|default|artificial intelligence|automated|AI)\b",
    re.IGNORECASE,
)
# Upper-case words that are fine: the currency and the reference prefix.
ALLOWED_CAPS = {"NT", "CA"}
# How the email carries out each action the Decision Agent can choose.
ACTION_BRIEF = {
    Action.send_reminder: "remind the customer of the overdue payment and how to pay",
    Action.offer_payment_plan: (
        "invite the customer to discuss a payment plan with a collections officer, "
        "and say how to pay"
    ),
    Action.schedule_call: (
        "ask the customer to call {support_phone} or reply to arrange a call with a "
        "collections officer, and say how to pay"
    ),
    Action.escalate_to_human: (
        "tell the customer that the account has been passed to a collections officer, who will "
        "contact them, and how to pay or call {support_phone} in the meantime"
    ),
}


class MessageDraft(BaseModel):
    subject: str = Field(description="The email subject line.")
    body: str = Field(description="The email body, with {placeholders} for amounts and links.")
    source_ids: list[int] = Field(description="Numbers of the policy passages it follows.")


def _money(amount: int) -> str:
    return f"{amount:,}"


def message_queries(customer: dict) -> list[str]:
    """What the writer needs from the policies: the tone for this stage and risk level, and the
    email rules."""
    days = describe(customer)["days_overdue_estimate"]
    level = customer.get("risk_level")
    risk = (
        [f"What tone must a collections message use for a {level}-risk customer?"] if level else []
    )
    return risk + [
        f"What tone must a collections message use for a payment about {days} days overdue, "
        "at this delinquency stage?",
        "What must every collections message contain, and what must it never contain?",
        "What are the rules for a collections email, and is there an email template for this "
        "stage?",
    ]


def message_passages(
    kb: KnowledgeBase, customer: dict, extra_queries: tuple[str, ...] = ()
) -> list[dict]:
    """The writer's policy passages, numbered from 1. `extra_queries` come first: for a reply,
    the customer's own email, so a hardship report finds the hardship policy."""
    seen: set[tuple[str, int]] = set()
    passages = []
    for query in [*extra_queries, *message_queries(customer)]:
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


def build_request(
    decision: dict,
    facts: list[dict],
    passages: list[dict],
    risk_level: str | None,
    deadline: str | None = None,
) -> str:
    rec = decision["recommendation"]
    # Point at the passage that holds this level's rule, so the writer does not blend the
    # neighbouring levels' rules into it. The rule itself stays in the policy.
    rule, _ = risk_rule(passages, risk_level)
    risk = (
        f"Risk level: {risk_level}. Write in the policy's tone for a {risk_level}-risk customer"
        + (f"; that rule is in source {rule}: include every element it lists" if rule else "")
        + ", and never mention the risk level in the email."
        + (
            f' Ask the customer to pay or get in touch "{deadline}", in those words.'
            if deadline
            else ""
        )
        if risk_level
        else "Risk level: not scored. Use the tone for the delinquency stage."
    )
    reasons = "\n".join(f"- {html.escape(r.text, quote=False)}" for r in rec.reasons)
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
        f"The email must {ACTION_BRIEF[rec.action]}.\n{risk}\n"
        f"Action chosen by the Decision Agent: {rec.action.value} (priority {rec.priority.value})\n"
        f"Why:\n{reasons}\n\n<facts>\n{fact_lines}\n</facts>\n\n"
        "<sources>\n" + "\n".join(blocks) + "\n</sources>\n\n"
        "Write the email draft."
    )


def fill(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


# Wording a risk level's tone rule may require. Only the phrases the policy's rule actually
# contains are required, so a policy change changes the check.
RULE_PHRASES = ("immediate attention", "payment plan", "passed to a collections officer")


def required_phrases(rule_text: str | None, deadline: str | None) -> list[str]:
    """The words the email must use because the risk level's rule says them (none for an
    escalated account, whose deadline does not apply either)."""
    if not rule_text or not deadline:
        return []
    return [p for p in RULE_PHRASES if p in rule_text.lower()]


def risk_rule_text(passages: list[dict], risk_level: str | None) -> str | None:
    """The words of this risk level's tone rule, as the policy states them."""
    if not risk_level:
        return None
    for p in passages:
        for block in p["text"].split("\n\n"):
            if f"{risk_level}-risk customer" in block:
                return block.strip()
    return None


def risk_rule(passages: list[dict], risk_level: str | None) -> tuple[int | None, str | None]:
    """The passage holding this risk level's tone rule, and the deadline the rule sets
    ("within 7 days"), read from the policy text so a policy change changes the check."""
    if not risk_level:
        return None, None
    for p in passages:
        for block in p["text"].split("\n\n"):
            if f"{risk_level}-risk customer" in block:
                deadline = re.search(r"within \d+ (?:business )?days?", block)
                return p["number"], deadline.group(0) if deadline else None
    return None, None


def problems(
    draft: MessageDraft,
    values: dict,
    source_ids: set[int],
    deadline: str | None = None,
    required: tuple[str, ...] = PLACEHOLDERS,
    phrases: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Guardrail breaks, worded so the model can fix them when asked again. `required`: the
    placeholders the body must use (a reply in a conversation needs fewer than a first email).
    `phrases`: words the risk level's rule requires (required_phrases)."""
    found = []
    if deadline and deadline.lower() not in draft.body.lower():
        found.append(
            f'the policy for this risk level asks the customer to act "{deadline}"; say so'
        )
    for phrase in phrases:
        if phrase not in draft.body.lower():
            found.append(f'the policy for this risk level requires the words "{phrase}"; use them')
    text = f"{draft.subject}\n{draft.body}"
    unknown = sorted(set(re.findall(r"\{(\w+)\}", text)) - set(PLACEHOLDERS))
    if unknown:
        found.append(f"unknown placeholders: {', '.join(unknown)}")
    # The month count may be written out, but only as the true value from the record.
    months = values["months_overdue"]
    written = {m.group(0) for m in re.finditer(r"\b\d+ months?\b", draft.body)}
    if written - {months}:
        found.append(f"the account is {months} overdue; write it as {{months_overdue}}")
    # {months_overdue} already reads "2 months": "{months_overdue} month(s)" would say it twice.
    if re.search(r"\{months_overdue\}\s*months?\b|\bmonths?\s*\(s\)", draft.body, re.IGNORECASE):
        found.append(
            f'{{months_overdue}} already reads "{months}": write it alone, never followed by '
            '"month" or "month(s)"'
        )
    missing = [
        p
        for p in required
        if "{" + p + "}" not in draft.body and not (p == "months_overdue" and months in written)
    ]
    if missing:
        found.append(f"the body must contain {', '.join('{' + p + '}' for p in missing)}")
    if ORGANISATION not in draft.body:
        found.append(f'the body must name the organisation, "{ORGANISATION}"')
    if re.search(r"(NT\$\s?)?\d[\d,]{2,}", draft.body.replace("{amount_due}", "")):
        found.append("write amounts only as NT${amount_due}, never as a number")
    if re.search(r"https?://|www\.", text):
        found.append("write the link only as {payment_link}")
    words = len(fill(draft.body, values).split())
    if words > EMAIL_MAX_WORDS:
        found.append(f"the email body is {words} words; the limit is {EMAIL_MAX_WORDS}")
    if not draft.subject.strip():
        found.append("an email needs a subject line")
    if re.search(r"\bdebt\b|\{amount_due\}|\d", draft.subject, re.IGNORECASE):
        found.append('the subject must not contain the amount or the word "debt"')
    if banned := sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text)}):
        found.append(f"remove these words: {', '.join(banned)}")
    caps = [w for w in re.findall(r"\b[A-Z]{2,}\b", text) if w not in ALLOWED_CAPS]
    if caps:
        found.append(f"no words in capitals: {', '.join(sorted(set(caps)))}")
    if text.count("!") > 1:
        found.append("use at most one exclamation mark")
    if any(s not in source_ids for s in draft.source_ids):
        found.append("source_ids may only list passages from this request")
    return found


def edited_problems(subject: str, body: str, values: dict[str, str]) -> list[str]:
    """The guardrails that still apply to an email an officer edited by hand, where the amounts
    and links are already written out."""
    found = []
    text = f"{subject}\n{body}"
    if not subject.strip() or not body.strip():
        found.append("an email needs a subject and a body")
    if ORGANISATION not in body:
        found.append(f'the body must name the organisation, "{ORGANISATION}"')
    if values["reference"] not in body:
        found.append(f"the body must give the reference, {values['reference']}")
    links = set(re.findall(r"https?://\S+|www\.\S+", text))
    if any(link.rstrip(".,)") != values["payment_link"] for link in links):
        found.append(f"the only link allowed is the payment link, {values['payment_link']}")
    if banned := sorted({m.group(0).lower() for m in FORBIDDEN.finditer(text)}):
        found.append(f"remove these words: {', '.join(banned)}")
    caps = [w for w in re.findall(r"\b[A-Z]{2,}\b", text) if w not in ALLOWED_CAPS]
    if caps:
        found.append(f"no words in capitals: {', '.join(sorted(set(caps)))}")
    if text.count("!") > 1:
        found.append("use at most one exclamation mark")
    if re.search(r"\bdebt\b", subject, re.IGNORECASE):
        found.append('the subject must not contain the word "debt"')
    if len(body.split()) > EMAIL_MAX_WORDS:
        found.append(f"the email body is {len(body.split())} words; the limit is {EMAIL_MAX_WORDS}")
    return found


def email_values(customer: dict) -> dict[str, str]:
    """What Python writes into the placeholders, from the record."""
    facts = describe(customer)
    reference = f"CA-{customer['customer_id']:06d}"
    return {
        "amount_due": _money(facts["amount_due"] or 0),
        "months_overdue": f"{facts['months_late']} month{'s' if facts['months_late'] != 1 else ''}",
        "payment_link": PAYMENT_URL.format(reference=reference),
        "support_phone": SUPPORT_PHONE,
        "reference": reference,
    }


def writer_facts(customer: dict) -> list[dict]:
    """The facts a writer sees: the email must never mention the risk score, so it is left out."""
    return [f for f in case_facts(customer) if "risk model" not in f["text"]]


def deadline_for(rec, level_deadline: str | None) -> str | None:
    """An escalated account has already been passed on, so the level's deadline does not apply."""
    return None if rec.action == Action.escalate_to_human else level_deadline


def checks(values: dict, body: str, deadline: str | None) -> list[str]:
    return [
        f"Names {ORGANISATION}, the amount (NT${values['amount_due']}) and the reference",
        "Amounts and links come from the record, not from the model",
        "No threats, capitals, risk level or score, or mention of automation",
        f"{len(body.split())} of {EMAIL_MAX_WORDS} words; the subject has no amount",
        *(
            [f'Asks the customer to act "{deadline}", as the policy sets for this risk level']
            if deadline
            else []
        ),
    ]


def prepare(
    kb: KnowledgeBase,
    portfolio: Portfolio,
    generator: AnswerGenerator | None,
    customer_id: int,
    case: dict | None = None,
) -> tuple[dict, dict | None] | None:
    """Everything a first email needs, before it is written. None: no such customer.

    Returns (result, context). When context is None, result is final: no email is written, and
    result says why. Otherwise context holds the customer, the decision, the passages, the
    placeholder values and the risk level's deadline, and result is the shared part of the reply.
    `case`: the Case Analyst's result, passed on to the Decision Agent.
    """
    customer = portfolio.get_customer(customer_id)
    if customer is None:
        return None
    facts = describe(customer)
    base = {
        "customer_id": customer_id,
        "risk_level": customer.get("risk_level"),
        "decision": None,
        "subject": None,
        "body": None,
        "sources": [],
        "checks": [],
        "corrected": [],
        "model": None,
    }
    if facts["months_late"] == 0:
        return {**base, "status": "not_written", "reason": "The customer is not overdue."}, None
    bill = facts["latest_bill"]
    if bill is None or bill <= 0:
        reason = (
            f"The latest bill shows nothing owed (NT${_money(bill or 0)}), so there is no "
            "amount to ask for. A collections officer should check this account."
        )
        return {**base, "status": "not_written", "reason": reason}, None
    if facts["amount_due"] == 0:
        reason = (
            f"The payments recorded in the ledger (NT${_money(facts['recorded_paid'])}) cover "
            "the latest bill, so there is nothing to ask for."
        )
        return {**base, "status": "not_written", "reason": reason}, None

    decision = recommend(kb, portfolio, generator, customer_id, case=case)
    rec = decision["recommendation"]
    result = {**base, "decision": rec, "model": decision["model"]}
    if rec.action not in ACTION_BRIEF:
        reason = "The Decision Agent recommends no contact."
        return {**result, "status": "not_written", "reason": reason}, None

    passages = message_passages(kb, customer)
    if not passages:
        raise DecisionUnavailable(
            "no_policy_evidence",
            "No messaging policy matches this account. Upload and index the messaging policy.",
        )
    _, level_deadline = risk_rule(passages, customer.get("risk_level"))
    return result, {
        "customer": customer,
        "decision": decision,
        "passages": passages,
        "values": email_values(customer),
        "facts": writer_facts(customer),
        "level_deadline": level_deadline,
    }
