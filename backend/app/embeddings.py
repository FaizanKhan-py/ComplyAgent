"""Embedding providers. All return L2-normalised float32 vectors, so dot product = cosine."""

from __future__ import annotations

import logging
import re
import threading
import zlib
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Settings

logger = logging.getLogger("complyagent.embeddings")


class EmbeddingError(Exception):
    """The embedding provider could not produce vectors."""


class Embedder(Protocol):
    name: str  # stored with every chunk; chunks from a different model are never searched
    default_min_score: float

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...
    def health(self) -> tuple[str, str]: ...  # ("ok" | "degraded" | "error", detail)


class FastEmbedEmbedder:
    """Local ONNX sentence-embedding model. Downloaded once into the storage dir; no API key."""

    default_min_score = 0.6  # measured on bge-small: unrelated text scores below ~0.57

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.name = f"fastembed:{model_name}"
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding

                    self._cache_dir.mkdir(parents=True, exist_ok=True)
                    self._model = TextEmbedding(self._model_name, cache_dir=str(self._cache_dir))
                    self._error = None
                    logger.info("Loaded embedding model %s", self._model_name)
                except Exception as exc:  # download, disk and ONNX failures all land here
                    self._error = f"{type(exc).__name__}: {exc}"[:300]
                    logger.exception("Could not load embedding model %s", self._model_name)
                    raise EmbeddingError(
                        f"Embedding model '{self._model_name}' could not be loaded. "
                        "The first run needs internet access to download it."
                    ) from exc
            return self._model

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        try:
            return np.asarray(list(model.passage_embed(texts)), dtype=np.float32)
        except Exception as exc:
            raise EmbeddingError(f"Embedding failed ({type(exc).__name__}).") from exc

    def embed_query(self, text: str) -> np.ndarray:
        model = self._load()
        try:
            return np.asarray(next(iter(model.query_embed([text]))), dtype=np.float32)
        except Exception as exc:
            raise EmbeddingError(f"Embedding failed ({type(exc).__name__}).") from exc

    def health(self) -> tuple[str, str]:
        if self._model is not None:
            return "ok", f"{self._model_name} loaded"
        if self._error:
            return "error", "Model failed to load; see server logs."
        return "degraded", f"{self._model_name} not loaded yet (loads on first use)"


_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its may must of on "
    "or our shall should that the their them there these they this to was we were what when "
    "where which who will with you your".split()
)


class HashingEmbedder:
    """Offline fallback: hashed bag of words and word pairs. Keyword matching, not semantics."""

    default_min_score = 0.12

    def __init__(self, dimension: int = 1024) -> None:
        self.name = f"hashing:{dimension}"
        self._dim = dimension

    def _embed(self, text: str) -> np.ndarray:
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS]
        words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]
        vec = np.zeros(self._dim, dtype=np.float32)
        for token in words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]:
            h = zlib.crc32(token.encode())
            vec[h % self._dim] += 1.0 if (h >> 16) & 1 else -1.0
        vec = np.sign(vec) * np.log1p(np.abs(vec))
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._embed(t) for t in texts]) if texts else np.zeros((0, self._dim))

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(text)

    def health(self) -> tuple[str, str]:
        return "degraded", "Keyword-hashing fallback in use; retrieval is not semantic."


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    if settings.embedding_provider == "fastembed":
        return FastEmbedEmbedder(settings.embedding_model, settings.models_dir)
    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER '{settings.embedding_provider}' (use fastembed or hashing)"
    )
