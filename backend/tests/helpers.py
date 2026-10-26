"""Test helpers: a tiny PDF writer and a scripted answer generator."""

from __future__ import annotations

import textwrap

from app.langchain.llm import GeneratedAnswer, LLMError


def make_pdf(pages: list[str]) -> bytes:
    """Build a valid text PDF (one string per page) without any PDF library."""
    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        lines = []
        for paragraph in text.split("\n"):
            lines.extend(textwrap.wrap(paragraph, 88) or [""])
        body = "BT /F1 11 Tf 54 760 Td 14 TL\n"
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            body += f"({escaped}) Tj T*\n"
        body += "ET"
        stream = body.encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


class FakeGenerator:
    """Stands in for the LangChain generator: records requests, returns scripted answers."""

    provider = "fake"
    model = "fake-llm"
    chat_model = None  # a LangChain chat model, set by the agent tests

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply = GeneratedAnswer("Scripted answer [1].", False, [1])
        self.error: LLMError | None = None
        self.structured_calls: list[dict] = []
        self.structured_replies: list[dict] = []

    def generate(self, question, sources, history):
        self.calls.append({"question": question, "sources": sources, "history": history})
        if self.error:
            raise self.error
        return self.reply

    def structured(self, system, user, schema):
        """Returns the next of `structured_replies` (dicts), validated against `schema`."""
        self.structured_calls.append({"system": system, "user": user, "schema": schema})
        if self.error:
            raise self.error
        return schema.model_validate(self.structured_replies.pop(0))

    def health(self):
        return "ok", "fake"


class BrokenEmbedder:
    name = "broken"
    default_min_score = 0.1

    def embed_documents(self, texts):
        from app.rag.embeddings import EmbeddingError

        raise EmbeddingError("Embedding provider is down.")

    def embed_query(self, text):
        from app.rag.embeddings import EmbeddingError

        raise EmbeddingError("Embedding provider is down.")

    def health(self):
        return "error", "down"


NOTEBOOK_FEATURES = [
    "LIMIT_BAL",
    "AGE",
    "on_time_payment_rate",
    "avg_payment_delay",
    "avg_credit_utilization",
    "EDUCATION_cleaned",
    "sex_male",
]


def save_model_files(folder, splits: dict[int, str] | None = None) -> None:
    """A small model in the notebook's file format (model.json, metadata.json, data_split.csv)
    in which late payers default. Trained with xgboost alone, as the backend loads it."""
    import json

    import numpy as np
    import xgboost

    rng = np.random.default_rng(0)
    delay = rng.uniform(0, 3, 400)
    matrix = np.column_stack(
        [
            rng.uniform(1e4, 5e5, 400),
            rng.integers(21, 70, 400),
            rng.uniform(0, 1, 400),
            delay,
            rng.uniform(0, 1, 400),
            rng.integers(1, 5, 400),
            rng.integers(0, 2, 400),
        ]
    )
    data = xgboost.DMatrix(matrix, label=(delay > 0.5).astype(int), feature_names=NOTEBOOK_FEATURES)
    booster = xgboost.train({"objective": "binary:logistic", "max_depth": 2}, data, 20)
    folder.mkdir(parents=True, exist_ok=True)
    booster.save_model(folder / "model.json")
    scores = {"customers": 60, "accuracy": 0.74, "precision": 0.44, "recall": 0.62, "f1": 0.52,
              "roc_auc": 0.77, "tn": 30, "fp": 10, "fn": 8, "tp": 12}  # fmt: skip
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "model_name": "XGBoost (tuned)",
                "trained_at": "2026-10-26T12:00:00+00:00",
                "target": "default payment next month",
                "features": NOTEBOOK_FEATURES,
                "threshold": 0.5,
                "best_params": {"max_depth": 2},
                "metrics": {"threshold": 0.5, "validation": scores, "test": scores},
            }
        ),
        encoding="utf-8",
    )
    if splits:
        rows = "".join(f"{cid},{split}\n" for cid, split in sorted(splits.items()))
        (folder / "data_split.csv").write_text("ID,split\n" + rows, encoding="utf-8")
