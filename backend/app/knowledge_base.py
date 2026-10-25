"""The RAG pipeline: ingestion (extract -> chunk -> embed -> store), retrieval and answering."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import numpy as np

from .chunking import chunk_pages
from .config import SUPPORTED_EXTENSIONS, Settings
from .db import Database, now_iso
from .embeddings import Embedder, EmbeddingError
from .extraction import (
    ExtractionError,
    ValidationError,
    detect_file_type,
    extract_pages,
    safe_filename,
    validate_content,
)
from .llm import AnswerGenerator, LLMError, SourcePassage

logger = logging.getLogger("complyagent.kb")

NO_EVIDENCE_ANSWER = (
    "I couldn't find anything in the indexed policy documents that answers this question."
)
EMPTY_KB_ANSWER = (
    "The knowledge base has no indexed documents yet. Upload a policy document and wait for it "
    "to finish indexing, then ask again."
)
RETRIEVAL_ONLY_ANSWER = (
    "No generated answer is available, so this is not a summary. The most relevant passages "
    "retrieved from the policy documents are shown below."
)


class DuplicateDocument(Exception):
    def __init__(self, existing: dict) -> None:
        super().__init__("duplicate")
        self.existing = existing


class NotFound(Exception):
    pass


class Busy(Exception):
    """The document is being ingested right now."""


class KnowledgeBase:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        embedder: Embedder,
        generator: AnswerGenerator | None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.embedder = embedder
        self.generator = generator
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    # --- documents -------------------------------------------------------------------------

    def _file_path(self, doc: dict) -> Path:
        return self.settings.uploads_dir / doc["stored_name"]

    def register_upload(self, raw_filename: str | None, data: bytes) -> dict:
        """Validate and store an upload. Ingestion is run separately via `ingest`."""
        filename = safe_filename(raw_filename)
        file_type = detect_file_type(filename)
        if len(data) > self.settings.max_upload_bytes:
            raise ValidationError(
                f"The file is larger than the {self.settings.max_upload_mb} MB limit.", 413
            )
        validate_content(file_type, data)

        sha256 = hashlib.sha256(data).hexdigest()
        doc_id = sha256[:16]  # stable: the same bytes always get the same document ID
        existing = self.db.get_document(doc_id)
        if existing:
            raise DuplicateDocument(existing)

        ext = next(e for e, t in SUPPORTED_EXTENSIONS.items() if t == file_type)
        now = now_iso()
        doc = {
            "id": doc_id,
            "filename": filename,
            "file_type": file_type,
            "size_bytes": len(data),
            "sha256": sha256,
            "stored_name": f"{doc_id}{ext}",
            "status": "pending",
            "uploaded_at": now,
            "updated_at": now,
        }
        self._file_path(doc).write_bytes(data)
        try:
            self.db.insert_document(doc)
        except Exception:
            self._file_path(doc).unlink(missing_ok=True)
            raise
        logger.info("Accepted upload %s (%s, %d bytes)", doc_id, file_type, len(data))
        return self.db.get_document(doc_id)

    def ingest(self, doc_id: str) -> None:
        """Extract, chunk, embed and index one document. Never raises: failures are recorded
        on the document so they are visible in the admin panel and can be retried."""
        doc = self.db.get_document(doc_id)
        if not doc:
            return
        self.db.set_status(doc_id, "processing")
        try:
            path = self._file_path(doc)
            if not path.is_file():
                raise ExtractionError("The stored file is missing. Delete and upload it again.")
            pages = extract_pages(doc["file_type"], path.read_bytes())
            chunks = chunk_pages(pages, self.settings.chunk_size, self.settings.chunk_overlap)
            if not chunks:
                raise ExtractionError("The document produced no text chunks.")
            embeddings = self.embedder.embed_documents([c.text for c in chunks])
            if len(embeddings) != len(chunks) or not np.isfinite(embeddings).all():
                raise EmbeddingError("The embedding provider returned invalid vectors.")
            stored = self.db.replace_chunks(
                doc_id,
                [(c.index, c.page_number, c.text) for c in chunks],
                embeddings,
                self.embedder.name,
                page_count=len(pages) if doc["file_type"] == "pdf" else None,
                char_count=sum(len(p.text) for p in pages),
            )
            if stored:
                logger.info("Indexed %s: %d chunks", doc_id, len(chunks))
        except (ExtractionError, EmbeddingError, ValidationError) as exc:
            logger.warning("Ingestion failed for %s: %s", doc_id, exc)
            self.db.mark_failed(doc_id, str(exc))
        except Exception as exc:
            logger.exception("Unexpected ingestion failure for %s", doc_id)
            self.db.mark_failed(
                doc_id, f"Unexpected error during ingestion ({type(exc).__name__})."
            )

    def prepare_reindex(self, doc_id: str) -> dict:
        doc = self.db.get_document(doc_id)
        if not doc:
            raise NotFound
        if doc["status"] in ("pending", "processing"):
            raise Busy
        self.db.set_status(doc_id, "pending")
        self.db.log_event(doc_id, doc["filename"], "reindex", "queued")
        return self.db.get_document(doc_id)

    def prepare_rebuild(self) -> list[str]:
        """Queue every document that is not already being ingested."""
        ids = []
        for doc in self.db.list_documents():
            if doc["status"] not in ("pending", "processing"):
                self.db.set_status(doc["id"], "pending")
                ids.append(doc["id"])
        return ids

    def delete(self, doc_id: str) -> int:
        doc = self.db.get_document(doc_id)
        if not doc:
            raise NotFound
        if doc["status"] in ("pending", "processing"):
            raise Busy
        removed = self.db.delete_document(doc_id)
        self._file_path(doc).unlink(missing_ok=True)
        logger.info("Deleted %s and %d chunks", doc_id, removed)
        return removed

    # --- retrieval and answering -----------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int,
        min_score: float,
        document_ids: list[str] | None = None,
    ) -> tuple[list[dict], int]:
        """Top-k chunks scoring at least `min_score`, and how many chunks were searched."""
        meta, matrix = self.db.searchable_chunks(self.embedder.name, document_ids)
        if not meta:
            return [], 0
        scores = matrix @ self.embedder.embed_query(query)
        order = np.argsort(-scores)[:top_k]
        results = [
            {**meta[i], "score": round(float(scores[i]), 4)}
            for i in order
            if scores[i] >= min_score
        ]
        return results, len(meta)

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """Retrieval with the configured limits, without generating an answer."""
        return self.retrieve(query, *self._limits(top_k, None))[0]

    def _limits(self, top_k: int | None, min_score: float | None) -> tuple[int, float]:
        top_k = top_k or self.settings.rag_top_k
        if min_score is None:
            min_score = self.settings.rag_min_score
        if min_score is None:
            min_score = self.embedder.default_min_score
        return top_k, min_score

    def answer(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
        top_k: int | None = None,
        min_score: float | None = None,
        document_ids: list[str] | None = None,
    ) -> dict:
        history = history or []
        top_k, min_score = self._limits(top_k, min_score)

        # Follow-up questions ("what about 60 days?") retrieve better with the previous question.
        previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
        search_query = f"{previous}\n{question}" if previous else question
        chunks, searched = self.retrieve(search_query, top_k, min_score, document_ids)
        logger.info("Query retrieved %d of %d chunks", len(chunks), searched)

        sources = [
            {
                "rank": rank,
                "document_id": c["document_id"],
                "filename": c["filename"],
                "file_type": c["file_type"],
                "page_number": c["page_number"],
                "chunk_index": c["chunk_index"],
                "score": c["score"],
                "text": c["text"],
                "cited": False,
            }
            for rank, c in enumerate(chunks, start=1)
        ]
        result = {
            "answer": "",
            "answer_mode": "no_evidence",
            "insufficient_evidence": True,
            "sources": sources,
            "model": None,
            "warning": None,
            "retrieval": {
                "top_k": top_k,
                "min_score": min_score,
                "embedding_model": self.embedder.name,
                "chunks_searched": searched,
            },
        }

        if not sources:
            result["answer"] = EMPTY_KB_ANSWER if searched == 0 else NO_EVIDENCE_ANSWER
            return result

        if self.generator is None:
            result.update(answer=RETRIEVAL_ONLY_ANSWER, answer_mode="retrieval_only")
            result["insufficient_evidence"] = False
            return result

        passages = [
            SourcePassage(s["rank"], s["filename"], s["page_number"], s["text"]) for s in sources
        ]
        try:
            generated = self.generator.generate(question, passages, history)
        except LLMError as exc:
            result.update(answer=RETRIEVAL_ONLY_ANSWER, answer_mode="retrieval_only")
            result["insufficient_evidence"] = False
            result["warning"] = f"Answer generation failed: {exc.message}"
            return result

        valid = {s["rank"] for s in sources}
        cited = set() if generated.insufficient_evidence else set(generated.cited_source_ids)
        # Trust the text too, but only for sources that were actually retrieved.
        cited |= {int(n) for n in re.findall(r"\[(\d+)\]", generated.answer)}
        cited &= valid
        if generated.insufficient_evidence:
            cited = set()
        for s in sources:
            s["cited"] = s["rank"] in cited
        # Drop citation markers that point at sources which do not exist.
        answer = re.sub(
            r"\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in valid else "", generated.answer
        )
        result.update(
            answer=answer.strip(),
            answer_mode="generated",
            insufficient_evidence=generated.insufficient_evidence,
            model=self.generator.model,
        )
        return result

    # --- stats and health ------------------------------------------------------------------

    def stats(self) -> dict:
        stats = self.db.stats(self.embedder.name)
        by = stats["by_status"]
        if stats["total_documents"] == 0:
            status = "empty"
        elif by["pending"] or by["processing"]:
            status = "processing"
        elif stats["searchable_chunks"] == 0:
            status = "unavailable"
        elif by["failed"] or stats["stale_documents"]:
            status = "degraded"
        else:
            status = "ready"
        stats["status"] = status
        stats["embedding_model"] = self.embedder.name
        stats["chunk_size"] = self.settings.chunk_size
        stats["chunk_overlap"] = self.settings.chunk_overlap
        return stats

    def health(self) -> dict:
        components = {}
        try:
            self.db.ping()
            components["database"] = {"status": "ok", "detail": "SQLite knowledge base reachable"}
        except Exception:
            logger.exception("Database health check failed")
            components["database"] = {"status": "error", "detail": "Knowledge base is unreachable"}

        uploads = self.settings.uploads_dir
        writable = uploads.is_dir() and _is_writable(uploads)
        components["storage"] = (
            {"status": "ok", "detail": "Upload storage is writable"}
            if writable
            else {"status": "error", "detail": "Upload storage is not writable"}
        )

        status, detail = self.embedder.health()
        components["embeddings"] = {"status": status, "detail": detail}

        if self.generator is None:
            components["llm"] = {
                "status": "degraded",
                "detail": (
                    "Not configured: answers are retrieval-only "
                    "(set ANTHROPIC_API_KEY or LLM_PROVIDER=ollama)"
                ),
            }
        else:
            status, detail = self.generator.health()
            components["llm"] = {"status": status, "detail": detail}

        critical = [components[k]["status"] for k in ("database", "storage", "embeddings")]
        if "error" in critical:
            overall = "error"
        elif any(c["status"] != "ok" for c in components.values()):
            overall = "degraded"
        else:
            overall = "ok"
        return {"status": overall, "components": components}


def _is_writable(directory: Path) -> bool:
    probe = directory / ".write_test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False
