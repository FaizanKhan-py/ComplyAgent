"""Split page text into overlapping chunks that keep their page number and position."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extraction import Page

# Tried in order: paragraph, line, sentence, word. The separator stays attached to the piece
# before it, so joining the pieces reproduces the original text.
_SEPARATORS = [r"(?<=\n\n)", r"(?<=\n)", r"(?<=[.!?;:] )", r"(?<= )"]


@dataclass(frozen=True)
class Chunk:
    index: int
    page_number: int | None
    text: str


def _pieces(text: str, size: int, level: int = 0) -> list[str]:
    if len(text) <= size:
        return [text]
    if level >= len(_SEPARATORS):
        return [text[i : i + size] for i in range(0, len(text), size)]
    out: list[str] = []
    for part in re.split(_SEPARATORS[level], text):
        if part:
            out.extend(_pieces(part, size, level + 1))
    return out


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")  # start the overlap on a word boundary
    return tail[space + 1 :] if space != -1 else tail


def split_text(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text, size):
        if current and len(current) + len(piece) > size:
            chunks.append(current.strip())
            tail = _overlap_tail(current, overlap)
            current = tail + piece if len(tail) + len(piece) <= size else piece
        else:
            current += piece
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


def chunk_pages(pages: list[Page], size: int, overlap: int) -> list[Chunk]:
    """Chunks never span pages, so every chunk's page number is exact."""
    chunks: list[Chunk] = []
    for page in pages:
        for text in split_text(page.text, size, overlap):
            chunks.append(Chunk(len(chunks), page.number, text))
    return chunks
