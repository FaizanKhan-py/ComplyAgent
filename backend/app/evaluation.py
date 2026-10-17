"""A repeatable check of the RAG system against questions with known answers.

    python -m app.manage evaluate-rag [--questions file.json]

Each question names the document that should be retrieved and phrases the answer must contain.
Questions with no expected document must come back with no evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from .knowledge_base import KnowledgeBase

DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "sample_policies" / "eval_questions.json"


def load_questions(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    for item in items:
        if not item.get("question"):
            raise ValueError(f"Every entry in {path} needs a question.")
    return items


def evaluate(kb: KnowledgeBase, items: list[dict]) -> dict:
    results = []
    for item in items:
        response = kb.answer(item["question"])
        expected = item.get("document")
        files = [s["filename"] for s in response["sources"]]
        generated = response["answer_mode"] == "generated" and not response["insufficient_evidence"]
        text = response["answer"].lower()
        if expected is None:
            # Off-topic: right when nothing is retrieved or the model says it is not covered.
            retrieved = not generated
            answered = not generated
        else:
            retrieved = bool(files) and files[0] == expected
            answered = generated and all(p.lower() in text for p in item.get("answer_contains", []))
        results.append(
            {
                "question": item["question"],
                "expected_document": expected,
                "top_document": files[0] if files else None,
                "retrieved": retrieved,
                "answer_mode": response["answer_mode"],
                "answered": answered,
                "answer": response["answer"],
            }
        )
    total = len(results)
    return {
        "questions": total,
        "retrieval_hits": sum(r["retrieved"] for r in results),
        "correct_answers": sum(r["answered"] for r in results),
        "generator": kb.generator.model if kb.generator else None,
        "results": results,
    }
