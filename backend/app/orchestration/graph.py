"""Agent orchestration with LangGraph: every collections agent as a node of one StateGraph.

    START --(first email)--> case_analyst --> decision_agent --(nothing to send)--> END
      |                                             |
      +--(reply or follow-up: a job is given)-------+--> writer --(needs a person)--> hand_over
                                                           |
                                                       guardrails --(broken)--> writer
                                                           |       (no rounds left: blocked)
                                                        red_team --(blocker)--> writer
                                                           |       (no rounds left: blocked)
                                                           |       (action challenged: the
                                                           |        Decision Agent decides again)
                                                          END  (passed: waits for an officer)

The graph ends where a person takes over: an officer approves, edits or rejects the draft hours
or days later, so that state lives in the database (decisions/conversation.py), not in a
running graph. Each node adds what it did to `trace`, which becomes the audit trail.

GET /api/admin/agents/graph returns this graph as a Mermaid diagram (drawn by LangGraph).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..decisions import messages, red_team
from ..decisions.case_analyst import analyse
from ..decisions.compliance_loop import MAX_ROUNDS, EmailJob, new_round
from ..decisions.decision import recommend
from ..langchain.prompts import MESSAGE_PROMPT

RECURSION_LIMIT = 60  # nodes per run: MAX_ROUNDS rounds of three nodes, with room to spare


class PipelineState(TypedDict, total=False):
    customer_id: int
    officer_feedback: str | None
    case: dict  # the Case Analyst's result
    job: EmailJob  # what the writer writes, and how it is checked
    decision: dict  # the Decision Agent's result; replaced when it decides again
    request: str  # the writer's request for the current decision
    feedback: str  # what the last round sent back
    round: int
    draft: Any
    subject: str | None
    body: str | None
    broken: list[str]
    # running | not_found | nothing_to_send | handed_over | passed | blocked | no_email
    status: str
    reason: str | None
    rounds: Annotated[list[dict], operator.add]
    lessons: Annotated[list[str], operator.add]  # every problem found in an earlier round
    trace: Annotated[list[dict], operator.add]


def _event(actor: str, event: str, summary: str, detail: dict | None = None) -> dict:
    return {"actor": actor, "event": event, "summary": summary, "detail": detail}


def _label(value: str) -> str:
    return value.replace("_", " ")


def first_email_job(kb, portfolio, generator, customer_id: int, case: dict, context: dict,
                    officer_feedback: str | None) -> EmailJob:  # fmt: skip
    """The writer's job for a conversation's first email (messages.prepare's context)."""
    risk_level = context["customer"].get("risk_level")
    level_deadline = context["level_deadline"]

    def deadline(decision: dict) -> str | None:
        return messages.deadline_for(decision["recommendation"], level_deadline)

    rule_text = messages.risk_rule_text(context["passages"], risk_level)

    def phrases(decision: dict) -> list[str]:
        return messages.required_phrases(rule_text, deadline(decision))

    def build(decision: dict) -> str | None:
        if decision["recommendation"].action not in messages.ACTION_BRIEF:
            return None
        request = messages.build_request(
            decision, context["facts"], context["passages"], risk_level, deadline(decision)
        )
        words = phrases(decision)
        if words:
            request += "\nUse these words from the policy, exactly: " + ", ".join(
                f'"{w}"' for w in words
            )
        return request + officer_note(officer_feedback)

    rule, _ = messages.risk_rule(context["passages"], risk_level)

    def policy_requires(decision: dict) -> list[str]:
        if not deadline(decision) or not rule_text:
            return []
        return [
            f"Source {rule} is the tone rule for this customer's risk level, and the email must "
            f'follow every part of it: "{rule_text}" Its urgency, its wording such as '
            '"immediate attention", and its deadline are required, not problems.'
        ]

    return EmailJob(
        system=MESSAGE_PROMPT,
        policy_requires=policy_requires,
        phrases=phrases,
        schema=messages.MessageDraft,
        build=build,
        decision=context["decision"],
        facts=context["facts"],
        passages=context["passages"],
        values=context["values"],
        required=messages.PLACEHOLDERS,
        deadline=deadline,
        redecide=lambda challenge: recommend(
            kb, portfolio, generator, customer_id, case=case, challenge=challenge
        ),
    )


def officer_note(text: str | None) -> str:
    import html

    if not text:
        return ""
    return (
        "\n\nA collections officer read the previous draft and asked for these changes:\n"
        f"{html.escape(text, quote=False)}\nFollow them, as long as every rule still holds."
    )


def _feedback(header: str, now: list[str], earlier: list[str]) -> str:
    """What the writer is told: this round's problems, and every earlier one, so a fix from an
    earlier round is not undone."""
    text = f"\n\n{header}\n" + "\n".join(f"- {n}" for n in now)
    older = [e for e in dict.fromkeys(earlier) if e not in now]
    if older:
        text += "\nEarlier drafts were also sent back for these; do not repeat them:\n"
        text += "\n".join(f"- {e}" for e in older)
    return text + "\nWrite it again, fixing every one and following every rule."


def build(kb, portfolio, generator):
    """The compiled graph. `kb` and `portfolio` are only needed for a first email."""

    def case_analyst(state: PipelineState) -> dict:
        case = analyse(portfolio, generator, state["customer_id"])
        if case is None:
            return {"status": "not_found", "reason": "Customer not found."}
        a = case["analysis"]
        return {
            "case": case,
            "trace": [
                _event(
                    "Case Analyst Agent",
                    "agent.case_analysis",
                    f"Case analysed: {_label(a.situation.value)}, {a.urgency.value} urgency.",
                    {
                        "analysed_by": case["analysed_by"],
                        "model": case["model"],
                        "analysis": a.model_dump(mode="json"),
                        "corrected": case["corrected"],
                    },
                )
            ],
        }

    def decision_agent(state: PipelineState) -> dict:
        cid = state["customer_id"]
        result, context = messages.prepare(kb, portfolio, generator, cid, case=state["case"])
        rec = result.get("decision")
        trace = []
        if rec is not None:
            trace.append(
                _event(
                    "Decision Agent",
                    "agent.decision",
                    f"Recommended {_label(rec.action.value)} by {rec.channel.value}, "
                    f"{rec.priority.value} priority.",
                    {"recommendation": rec.model_dump(mode="json"), "model": result["model"]},
                )
            )
        if context is None:
            trace.append(
                _event("Decision Agent", "agent.no_email", f"No email: {result['reason']}")
            )
            return {"status": "nothing_to_send", "reason": result["reason"], "trace": trace}
        job = first_email_job(
            kb, portfolio, generator, cid, state["case"], context, state.get("officer_feedback")
        )
        return {
            "job": job,
            "decision": context["decision"],
            "request": job.build(context["decision"]),
            "trace": trace,
        }

    def writer(state: PipelineState) -> dict:
        job = state["job"]
        n = state.get("round", 0) + 1
        draft = generator.structured(
            job.system, state["request"] + state.get("feedback", ""), job.schema,
            history=job.history,
        )  # fmt: skip
        return {
            "draft": draft,
            "round": n,
            "trace": [_event(job.writer_name, "agent.draft", f"Draft {n} written.")],
        }

    def hand_over(state: PipelineState) -> dict:
        draft = state["draft"]
        detail = {
            k: getattr(draft, k).value if hasattr(getattr(draft, k), "value") else getattr(draft, k)
            for k in ("intent", "intent_summary", "human_reason")
            if hasattr(draft, k)
        }
        return {
            "status": "handed_over",
            "trace": [
                _event(
                    state["job"].writer_name,
                    "conversation.handed_over",
                    "A person must handle this: no reply was drafted.",
                    detail,
                )
            ],
        }

    def guardrails(state: PipelineState) -> dict:
        job, draft, n = state["job"], state["draft"], state["round"]
        source_ids = {p["number"] for p in job.passages}
        deadline = job.deadline(state["decision"])
        broken = messages.problems(
            draft, job.values, source_ids, deadline, job.required, job.phrases(state["decision"])
        )
        update = {
            "subject": messages.fill(draft.subject, job.values),
            "body": messages.fill(draft.body, job.values),
            "broken": broken,
        }
        if not broken:
            return update
        return {
            **update,
            "rounds": [new_round(n, "guardrails", False, issues=broken)],
            "lessons": broken,
            "feedback": _feedback(
                "Your previous draft broke these rules:", broken, state.get("lessons", [])
            ),
            "trace": [
                _event(
                    "Guardrails",
                    "agent.guardrails",
                    f"Round {n}: the draft broke {len(broken)} fixed rule(s); sent back.",
                    {"issues": broken},
                )
            ],
        }

    def red_team_node(state: PipelineState) -> dict:
        job, n, decision = state["job"], state["round"], state["decision"]
        verdict = red_team.review(
            generator, state["subject"], state["body"], decision["recommendation"],
            job.facts, job.passages, job.customer_message, job.policy_requires(decision),
            [*job.phrases(decision), *filter(None, [job.deadline(decision)])],
        )  # fmt: skip
        this = new_round(n, "red_team", verdict["passed"], findings=verdict["findings"],
                         dropped=verdict["dropped"])  # fmt: skip
        blockers = [f for f in verdict["findings"] if f.severity == "blocker"]
        summary = (
            f"Round {n}: passed"
            + (f" with {len(verdict['findings'])} warning(s)." if verdict["findings"] else ".")
            if verdict["passed"]
            else f"Round {n}: {len(blockers)} blocker(s) found; sent back."
        )
        trace = [
            _event(
                "Compliance Red Team",
                "agent.red_team",
                summary,
                {
                    "findings": [f.model_dump(mode="json") for f in verdict["findings"]],
                    "dropped": verdict["dropped"],
                },
            )
        ]
        if verdict["passed"]:
            return {"status": "passed", "rounds": [this], "trace": trace}

        update: dict = {}
        challenged = [f"{f.issue} {f.fix}" for f in blockers if f.target == "decision"]
        if challenged and job.redecide:
            before = decision["recommendation"].action.value
            decision = job.redecide(challenged)
            after = decision["recommendation"].action.value
            this["decision_revised"] = {"from": before, "to": after}
            trace.append(
                _event(
                    "Decision Agent",
                    "agent.decision_revised",
                    f"Challenged by the Red Team: decided again, {_label(before)} -> "
                    f"{_label(after)}.",
                    {"challenge": challenged},
                )
            )
            update["decision"] = decision
            rebuilt = job.build(decision)
            if rebuilt is None:
                return {**update, "status": "no_email", "rounds": [this], "trace": trace}
            update["request"] = rebuilt
        # With the action fixed (a reply), objections to it are the writer's to answer too.
        found = [
            f'"{f.quote}": {f.issue} Fix: {f.fix}'
            for f in blockers
            if f.target == "message" or not job.redecide
        ]
        update["lessons"] = found
        update["feedback"] = (
            _feedback(
                "A compliance reviewer found these problems in your previous draft:",
                found,
                state.get("lessons", []),
            )
            if found
            else ""
        )
        return {**update, "rounds": [this], "trace": trace}

    def blocked(state: PipelineState) -> dict:
        return {
            "status": "blocked",
            "trace": [
                _event(
                    "Compliance loop",
                    "draft.blocked",
                    f"Still not compliant after {state['round']} rounds: the draft is blocked.",
                )
            ],
        }

    def route_start(state: PipelineState) -> str:
        return "writer" if "job" in state else "case_analyst"

    def route_case(state: PipelineState) -> str:
        return END if state.get("status") == "not_found" else "decision_agent"

    def route_decision(state: PipelineState) -> str:
        return END if state.get("status") == "nothing_to_send" else "writer"

    def route_writer(state: PipelineState) -> str:
        return "hand_over" if state["job"].hand_over(state["draft"]) else "guardrails"

    def route_guardrails(state: PipelineState) -> str:
        if not state["broken"]:
            return "red_team"
        return "writer" if state["round"] < MAX_ROUNDS else "blocked"

    def route_red_team(state: PipelineState) -> str:
        if state.get("status") in ("passed", "no_email"):
            return END
        return "writer" if state["round"] < MAX_ROUNDS else "blocked"

    graph = StateGraph(PipelineState)
    graph.add_node("case_analyst", case_analyst)
    graph.add_node("decision_agent", decision_agent)
    graph.add_node("writer", writer)
    graph.add_node("hand_over", hand_over)
    graph.add_node("guardrails", guardrails)
    graph.add_node("red_team", red_team_node)
    graph.add_node("blocked", blocked)
    graph.add_conditional_edges(START, route_start, ["case_analyst", "writer"])
    graph.add_conditional_edges("case_analyst", route_case, ["decision_agent", END])
    graph.add_conditional_edges("decision_agent", route_decision, ["writer", END])
    graph.add_conditional_edges("writer", route_writer, ["hand_over", "guardrails"])
    graph.add_conditional_edges("guardrails", route_guardrails, ["red_team", "writer", "blocked"])
    graph.add_conditional_edges("red_team", route_red_team, ["writer", "blocked", END])
    graph.add_edge("hand_over", END)
    graph.add_edge("blocked", END)
    return graph.compile()


def _invoke(graph, state: dict) -> dict:
    return graph.invoke(
        {"rounds": [], "trace": [], "lessons": [], "status": "running", **state},
        {"recursion_limit": RECURSION_LIMIT},
    )


def run_first_email(kb, portfolio, generator, customer_id: int,
                    officer_feedback: str | None = None) -> dict:  # fmt: skip
    """Case Analyst -> Decision Agent -> writer <-> guardrails <-> Red Team. The final state."""
    graph = build(kb, portfolio, generator)
    return _invoke(graph, {"customer_id": customer_id, "officer_feedback": officer_feedback})


def run_job(generator, job: EmailJob) -> dict:
    """A reply or a follow-up: the writer's job is given, so the graph starts at the writer."""
    graph = build(None, None, generator)
    request = job.build(job.decision)
    return _invoke(graph, {"job": job, "decision": job.decision, "request": request})


def mermaid() -> str:
    """The graph as a Mermaid diagram, drawn by LangGraph."""
    return build(None, None, None).get_graph().draw_mermaid()
