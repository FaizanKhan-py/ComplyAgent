"""The Case Analyst Agent: reads one account and writes its case file.

Python computes every fact from the records (the Decision Agent's facts, plus patterns in the
six months of payments); the model only reads them and writes the case: a summary, the
situation, how urgent it is, the patterns it sees, signs the customer may need care, and what an
officer should ask. Every pattern must cite the facts behind it, and the reply is checked here,
so a claim the records do not support is caught before the Decision Agent reads it.
"""

from __future__ import annotations

import html
from enum import Enum

from pydantic import BaseModel, Field

from ..customers.portfolio import Portfolio
from ..langchain.llm import AnswerGenerator
from ..langchain.prompts import CASE_ANALYST_PROMPT
from .decision import DecisionUnavailable, Priority, case_facts
from .guidance import describe


class Situation(str, Enum):
    paid_up = "paid_up"
    early_arrears = "early_arrears"
    persistent_arrears = "persistent_arrears"
    worsening = "worsening"
    improving = "improving"
    partial_payer = "partial_payer"
    stopped_paying = "stopped_paying"


class Finding(BaseModel):
    text: str = Field(description="One finding, in plain words.")
    fact_ids: list[str] = Field(description="Facts it relies on, e.g. ['F2', 'F7'].")


class CaseAnalysis(BaseModel):
    summary: str = Field(description="Two or three sentences on the account.")
    situation: Situation
    urgency: Priority
    patterns: list[Finding]
    care_flags: list[Finding] = Field(description="Signs the customer may need careful handling.")
    open_questions: list[str] = Field(description="What an officer should ask the customer.")


def pattern_facts(customer: dict, first_id: int) -> list[dict]:
    """What the six months of payments show, beyond case_facts, numbered from `first_id`."""
    history = customer.get("history") or []  # month_no 1 is the most recent month
    owed = [h for h in history if h["bill_amt"] > 0]
    lines = []
    if owed:
        partial = sum(1 for h in owed if 0 < h["pay_amt"] < h["bill_amt"])
        nothing = sum(1 for h in owed if h["pay_amt"] == 0)
        lines.append(
            f"In {partial} of the {len(owed)} months with a bill, the customer paid part of it."
        )
        lines.append(f"In {nothing} of the {len(owed)} months with a bill, nothing was paid.")
    paid = [h["pay_amt"] for h in history]
    if len(paid) >= 4 and paid[0] == 0 and all(p > 0 for p in paid[1:4]):
        lines.append("Payments stopped this month after at least three months of payments.")
    if len(history) >= 2:
        latest, before = history[0]["bill_amt"], history[1]["bill_amt"]
        if before > 0 and latest != before:
            change = (latest - before) / before
            moved = "rose" if change > 0 else "fell"
            lines.append(f"The bill {moved} by {abs(change):.0%} since last month.")
    return [{"id": f"F{n}", "text": text} for n, text in enumerate(lines, start=first_id)]


def analyst_facts(customer: dict) -> list[dict]:
    facts = case_facts(customer)
    return facts + pattern_facts(customer, len(facts) + 1)


def build_request(facts: list[dict]) -> str:
    lines = "\n".join(
        f'<fact id="{f["id"]}">{html.escape(f["text"], quote=False)}</fact>' for f in facts
    )
    return f"<facts>\n{lines}\n</facts>\n\nWrite the case file for this account."


def problems(analysis: CaseAnalysis, fact_ids: set[str]) -> list[str]:
    """Rule breaks, worded so the model can fix them when asked again."""
    found = []
    if not analysis.summary.strip():
        found.append("write a summary")
    if not analysis.patterns:
        found.append("give at least one pattern")
    for kind, items in (("pattern", analysis.patterns), ("care flag", analysis.care_flags)):
        for n, item in enumerate(items, start=1):
            if not item.fact_ids:
                found.append(f"{kind} {n} cites no fact")
            unknown = [f for f in item.fact_ids if f not in fact_ids]
            if unknown:
                found.append(
                    f"{kind} {n} cites facts that are not in the request: {', '.join(unknown)}"
                )
    return found


def analyse(
    portfolio: Portfolio, generator: AnswerGenerator | None, customer_id: int
) -> dict | None:
    """The case file with the facts it is based on. None: no such customer.

    Raises DecisionUnavailable when no model is configured or its reply stays wrong, and
    LLMError when the model fails.
    """
    customer = portfolio.get_customer(customer_id)
    if customer is None:
        return None
    facts = analyst_facts(customer)
    result = {"customer_id": customer_id, "facts": facts, "model": None, "corrected": []}

    if describe(customer)["months_late"] == 0:
        analysis = CaseAnalysis(
            summary="Nothing is overdue on this account, so there is no collections case.",
            situation=Situation.paid_up,
            urgency=Priority.low,
            patterns=[Finding(text="Nothing is overdue.", fact_ids=["F1"])],
            care_flags=[],
            open_questions=[],
        )
        return {**result, "analysed_by": "rule", "analysis": analysis}

    if generator is None:
        raise DecisionUnavailable(
            "llm_not_configured", "No language model is configured, so no case can be analysed."
        )
    request = build_request(facts)
    fact_ids = {f["id"] for f in facts}
    analysis = generator.structured(CASE_ANALYST_PROMPT, request, CaseAnalysis)
    found = problems(analysis, fact_ids)
    if found:
        retry = (
            f"{request}\n\nYour previous reply broke these rules: {'; '.join(found)}. "
            "Reply again, following every rule."
        )
        analysis = generator.structured(CASE_ANALYST_PROMPT, retry, CaseAnalysis)
        if problems(analysis, fact_ids):
            raise DecisionUnavailable(
                "llm_bad_output",
                "The Case Analyst cited facts it was not given, twice. Try again.",
            )
    return {
        **result,
        "analysed_by": "model",
        "model": generator.model,
        "analysis": analysis,
        "corrected": found,
    }


def analysis_block(case: dict) -> str:
    """The case file as the Decision Agent reads it, escaped like every other input."""
    a: CaseAnalysis = case["analysis"]

    def items(findings: list[Finding]) -> str:
        return "".join(
            f"- {html.escape(f.text, quote=False)} ({', '.join(f.fact_ids)})\n" for f in findings
        )

    return (
        "<case_analysis>\n"
        f"Summary: {html.escape(a.summary, quote=False)}\n"
        f"Situation: {a.situation.value}. Urgency: {a.urgency.value}.\n"
        f"Patterns:\n{items(a.patterns)}"
        + (f"Care flags:\n{items(a.care_flags)}" if a.care_flags else "Care flags: none\n")
        + "</case_analysis>"
    )
