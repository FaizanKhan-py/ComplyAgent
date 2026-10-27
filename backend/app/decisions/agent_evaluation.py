"""Scores the agents against test cases, like rag/evaluation.py scores the RAG system.

    python -m app.manage evaluate-agents [--cases path.json]

    intents    the Communication Agent reads each customer email: is the intent right, and does
               it hand the conversation to a person when it must? Scored for the model alone and
               with the fixed stop-contact safety net (conversation.asks_to_stop) on top
    red_team   the Compliance Red Team reviews emails, some seeded with a known problem:
               detection rate (seeded problems it blocks) and false alarms (clean emails it
               blocks)

The cases are in sample_policies/agent_eval.json. They use the real model and the indexed
policies, so the scores show how the configured model does, and change when it changes.
"""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

from ..langchain.prompts import CONVERSATION_PROMPT
from . import red_team
from .conversation import ALWAYS_HUMAN, ReplyDraft, asks_to_stop
from .decision import Recommendation

DEFAULT_CASES = Path(__file__).resolve().parents[3] / "sample_policies" / "agent_eval.json"
PASSAGES_PER_QUERY = 3
QUERIES = (
    "What must every collections message contain, and what must it never contain?",
    "What tone must a collections message use for a payment about 30 days overdue?",
    "Rules for contacting customers: frequency, third parties, threats",
    "What happens when a customer disputes the debt or reports hardship?",
)
FACTS = [
    {"id": "F1", "text": "The account is 1 month late (about 30 days overdue)."},
    {"id": "F2", "text": "The latest bill is NT$4,000."},
]
DECISION = Recommendation.model_validate(
    {
        "action": "send_reminder",
        "channel": "email",
        "priority": "medium",
        "reasons": [
            {"text": "One month late: a Stage 1 reminder.", "fact_ids": ["F1"], "source_ids": []}
        ],  # fmt: skip
        "needs_human_review": False,
    }
)


def _passages(kb) -> list[dict]:
    seen, passages = set(), []
    for query in QUERIES:
        for chunk in kb.search(query, PASSAGES_PER_QUERY):
            key = (chunk["document_id"], chunk["chunk_index"])
            if key not in seen:
                seen.add(key)
                passages.append({**chunk, "number": len(passages) + 1})
    return passages


def _reply_request(email: str, passages: list[dict]) -> str:
    facts = "\n".join(f'<fact id="{f["id"]}">{f["text"]}</fact>' for f in FACTS)
    sources = "\n".join(
        f'<source id="{p["number"]}" document="{html.escape(p["filename"], quote=True)}">\n'
        f"{html.escape(p['text'], quote=False)}\n</source>"
        for p in passages
    )
    return (
        f"<customer_email>\n{html.escape(email, quote=False)}\n</customer_email>\n\n"
        f"Action chosen by the Decision Agent: send_reminder (priority medium)\n\n"
        f"<facts>\n{facts}\n</facts>\n\n<sources>\n{sources}\n</sources>\n\n"
        f"Today is {date.today():%A %Y-%m-%d}. Write the reply."
    )


def run(kb, generator, cases_path: Path = DEFAULT_CASES) -> dict:
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    passages = _passages(kb)

    intents = []
    for case in cases["intents"]:
        reply: ReplyDraft = generator.structured(
            CONVERSATION_PROMPT, _reply_request(case["email"], passages), ReplyDraft, history=[]
        )
        model_hand_over = reply.needs_human or reply.intent in ALWAYS_HUMAN
        net = asks_to_stop(case["email"])
        final_intent = "stop_contact" if net else reply.intent.value
        intents.append(
            {
                "email": case["email"],
                "expected": case["intent"],
                "got": reply.intent.value,
                "final": final_intent,
                "expected_hand_over": case["hand_over"],
                "hand_over": model_hand_over or net,
                "model_hand_over": model_hand_over,
            }
        )

    reviews = []
    for case in cases["red_team"]:
        verdict = red_team.review(
            generator, case["subject"], case["body"], DECISION, FACTS, passages,
            case.get("customer_message"),
        )  # fmt: skip
        blockers = [f for f in verdict["findings"] if f.severity == "blocker"]
        reviews.append(
            {
                "name": case["name"],
                "expect": case["expect"],
                "blocked": bool(blockers),
                "findings": [f"{f.severity}: {f.issue}" for f in verdict["findings"]],
                "dropped": verdict["dropped"],
            }
        )
    return {"model": generator.model, "intents": intents, "red_team": reviews,
            "scores": _scores(intents, reviews)}  # fmt: skip


def _ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def _scores(intents: list[dict], reviews: list[dict]) -> dict:
    scored = [i for i in intents if i["expected_hand_over"] is not None]
    seeded = [r for r in reviews if r["expect"] == "blocker"]
    clean = [r for r in reviews if r["expect"] == "pass"]
    return {
        "intent_accuracy_model": _ratio(
            sum(i["got"] == i["expected"] for i in intents), len(intents)
        ),
        "intent_accuracy_with_safety_net": _ratio(
            sum(i["final"] == i["expected"] for i in intents), len(intents)
        ),
        "hand_over_accuracy": _ratio(
            sum(i["hand_over"] == i["expected_hand_over"] for i in scored), len(scored)
        ),
        "stop_requests_caught": _ratio(
            sum(i["final"] == "stop_contact" for i in intents if i["expected"] == "stop_contact"),
            sum(i["expected"] == "stop_contact" for i in intents),
        ),
        "red_team_detection_rate": _ratio(sum(r["blocked"] for r in seeded), len(seeded)),
        "red_team_false_alarm_rate": _ratio(sum(r["blocked"] for r in clean), len(clean)),
    }


def format_report(report: dict) -> str:
    lines = [f"Agent evaluation with {report['model']}", ""]
    lines.append("Communication Agent: intents")
    for i in report["intents"]:
        mark = "ok " if i["final"] == i["expected"] else "XX "
        over = "" if i["expected_hand_over"] is None else (
            "" if i["hand_over"] == i["expected_hand_over"] else "  (hand-over wrong)"
        )  # fmt: skip
        got = i["got"] if i["got"] == i["final"] else f"{i['got']} -> {i['final']} (safety net)"
        lines.append(f"  {mark}{i['expected']:<16} got {got:<36} {i['email'][:50]}{over}")
    lines += ["", "Compliance Red Team"]
    for r in report["red_team"]:
        right = r["blocked"] == (r["expect"] == "blocker")
        lines.append(
            f"  {'ok ' if right else 'XX '}{r['name']:<36} expected {r['expect']:<7} "
            f"{'blocked' if r['blocked'] else 'passed'}"
            + ("" if right or not r["findings"] else f"  [{r['findings'][0][:70]}]")
        )
    lines += ["", "Scores"]
    for name, value in report["scores"].items():
        lines.append(f"  {name:<34} {'n/a' if value is None else f'{value:.0%}'}")
    return "\n".join(lines)
