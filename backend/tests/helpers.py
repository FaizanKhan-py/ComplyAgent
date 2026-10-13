"""Test helpers: a tiny PDF writer and a scripted answer generator."""

from __future__ import annotations

import textwrap

from app.llm import GeneratedAnswer, LLMError


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
    """Stands in for the Claude client. Records what it was asked and returns a scripted answer."""

    model = "fake-llm"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply = GeneratedAnswer("Scripted answer [1].", False, [1])
        self.error: LLMError | None = None

    def generate(self, question, sources, history):
        self.calls.append({"question": question, "sources": sources, "history": history})
        if self.error:
            raise self.error
        return self.reply

    def health(self):
        return "ok", "fake"


class BrokenEmbedder:
    name = "broken"
    default_min_score = 0.1

    def embed_documents(self, texts):
        from app.embeddings import EmbeddingError

        raise EmbeddingError("Embedding provider is down.")

    def embed_query(self, text):
        from app.embeddings import EmbeddingError

        raise EmbeddingError("Embedding provider is down.")

    def health(self):
        return "error", "down"
