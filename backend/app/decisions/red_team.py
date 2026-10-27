"""The Compliance Red Team Agent: attacks every draft before a collections officer sees it.

The fixed guardrails (messages.problems) catch what a rule can: placeholders, banned words,
capitals, length. The Red Team is a second model told to find what they cannot: implied
threats, unsupported urgency, a tone wrong for the stage, a reply that ignores what the
customer said, an action the policies do not support. Each finding quotes the words it objects
to and cites the policy behind it.

The Red Team is checked too: a finding whose quote is not in the email is dropped (the model
objected to something that is not there), and only passages from the request may be cited.
The draft passes when no blocker is left.
"""

from __future__ import annotations

import html
import re
from typing import Literal

from pydantic import BaseModel, Field

from ..langchain.llm import AnswerGenerator
from ..langchain.prompts import RED_TEAM_PROMPT


class RedTeamFinding(BaseModel):
    target: Literal["message", "decision"]
    severity: Literal["blocker", "warning"]
    issue: str = Field(description="What is wrong, in one sentence.")
    quote: str = Field(description="The exact words objected to.")
    source_ids: list[int] = Field(description="Policy passages behind the finding.")
    fix: str = Field(description="What must change, in one sentence.")


class RedTeamReview(BaseModel):
    findings: list[RedTeamFinding]


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def build_request(
    subject: str,
    body: str,
    decision,
    facts: list[dict],
    passages: list[dict],
    customer_message: str | None = None,
    required: list[str] | None = None,
) -> str:
    """Everything is escaped, so no part of the request can close its own tag."""
    reasons = "\n".join(f"- {html.escape(r.text, quote=False)}" for r in decision.reasons)
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
    customer = (
        f"<customer_message>\n{html.escape(customer_message, quote=False)}\n</customer_message>\n\n"
        if customer_message
        else ""
    )
    must = (
        "<required_by_policy>\n"
        + "\n".join(f"- {html.escape(r, quote=False)}" for r in required)
        + "\n</required_by_policy>\n\n"
        if required
        else ""
    )
    return (
        f"{customer}{must}<email>\nSubject: {html.escape(subject, quote=False)}\n\n"
        f"{html.escape(body, quote=False)}\n</email>\n\n"
        f"Action chosen by the Decision Agent: {decision.action.value} "
        f"(channel {decision.channel.value}, priority {decision.priority.value})\n"
        f"Why:\n{reasons}\n\n<facts>\n{fact_lines}\n</facts>\n\n"
        "<sources>\n" + "\n".join(blocks) + "\n</sources>\n\n"
        "Review the email and the action. Report every problem you find."
    )


def review(
    generator: AnswerGenerator,
    subject: str,
    body: str,
    decision,
    facts: list[dict],
    passages: list[dict],
    customer_message: str | None = None,
    required: list[str] | None = None,
    protected: list[str] | None = None,
) -> dict:
    """{passed, findings, dropped}: the findings that hold up, and how many did not.
    `required`: what the policies required the email to say, so it is not flagged.
    `protected`: words the policy requires verbatim (the guardrails check them). A blocker that
    quotes them objects to the policy itself, so it is kept only as a warning."""
    request = build_request(subject, body, decision, facts, passages, customer_message, required)
    result = generator.structured(RED_TEAM_PROMPT, request, RedTeamReview)
    email = _normal(f"{subject}\n{body}")
    source_ids = {p["number"] for p in passages}
    kept, dropped = [], 0
    for finding in result.findings:
        if finding.target == "message" and (
            not finding.quote.strip() or _normal(finding.quote) not in email
        ):
            dropped += 1  # it objects to words the email does not contain
            continue
        update = {"source_ids": [s for s in finding.source_ids if s in source_ids]}
        quote = _normal(finding.quote)
        if (
            finding.target == "message"
            and finding.severity == "blocker"
            and any(p.lower() in quote for p in protected or [])
        ):
            update |= {
                "severity": "warning",
                "issue": f"{finding.issue} (Only a warning: the policy requires these words.)",
            }
        kept.append(finding.model_copy(update=update))
    return {
        "passed": not any(f.severity == "blocker" for f in kept),
        "findings": kept,
        "dropped": dropped,
    }
