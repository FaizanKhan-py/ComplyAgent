"""The Adversarial Compliance Loop: a writer and the Red Team, round after round.

    round n:  the writer drafts  ->  fixed guardrails (messages.problems)  ->  Red Team review
              broken rule or blocker found: the writer is told exactly what, and tries again
              the Red Team challenges the action itself: the Decision Agent decides again first
              nothing left to fix: the draft goes to a collections officer for approval

After MAX_ROUNDS the draft is "blocked": it is kept with every round's findings, so an officer
sees why, but it cannot be approved or sent.

The loop runs as part of the LangGraph graph (orchestration/graph.py): the writer, guardrails
and red_team nodes, joined by conditional edges. This module describes one email to write
(EmailJob) and runs it through that graph.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from ..langchain.llm import AnswerGenerator

MAX_ROUNDS = 4


@dataclass
class EmailJob:
    """One email to write. `build(decision)` makes the writer's request for a decision, or
    returns None when that decision means no email (e.g. no contact)."""

    system: str
    schema: type[BaseModel]
    build: Callable[[dict], str | None]
    decision: dict  # decision.recommend()'s result
    facts: list[dict]  # what the writer and the Red Team see (never the risk score)
    passages: list[dict]
    values: dict[str, str]
    required: tuple[str, ...]
    deadline: Callable[[dict], str | None] = lambda decision: None
    history: list | None = None  # the email thread, for a reply
    customer_message: str | None = None
    # Decides again when the Red Team challenges the action; None: the action is fixed.
    redecide: Callable[[list[str]], dict] | None = None
    # True for a draft that must go to a person instead (the conversation's escalations).
    hand_over: Callable[[BaseModel], bool] = lambda draft: False
    writer_name: str = "Message writer"  # who the audit trail says wrote it
    # What the policies required the email to say, for the Red Team (it does not see the risk
    # level, so it cannot know the deadline that level sets).
    policy_requires: Callable[[dict], list[str]] = lambda decision: []
    # Words the policy requires verbatim: the guardrails check they are there, and a Red Team
    # blocker that objects to them is only a warning (the model cannot overrule the policy).
    phrases: Callable[[dict], list[str]] = lambda decision: []


def new_round(n: int, checked_by: str, passed: bool, **extra) -> dict:
    return {
        "round": n,
        "checked_by": checked_by,
        "passed": passed,
        "issues": [],
        "findings": [],
        "dropped": 0,
        "decision_revised": None,
        **extra,
    }


def outcome(state: dict) -> dict:
    """The graph's final state, as the conversation stores it:

    status     passed (ready for approval) | blocked (cannot be sent) | handed_over (a person
               must take it) | no_email (the challenged Decision Agent chose no contact)
    """
    return {
        "status": state["status"],
        "draft": state.get("draft"),
        "subject": state.get("subject"),
        "body": state.get("body"),
        "decision": state.get("decision"),
        "rounds": state.get("rounds", []),
        "trace": state.get("trace", []),
    }


def run(generator: AnswerGenerator, job: EmailJob) -> dict:
    """Writes and checks one email through the graph; see outcome() for the result."""
    from ..orchestration.graph import run_job  # the graph imports this module

    return outcome(run_job(generator, job))
