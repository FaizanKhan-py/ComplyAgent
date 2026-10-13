"""ComplyAgent Admin + RAG API.

Run from the backend directory:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import hmac
import logging
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import schemas
from .config import BACKEND_DIR, SUPPORTED_EXTENSIONS, Settings
from .db import Database
from .embeddings import Embedder, EmbeddingError, build_embedder
from .extraction import ValidationError
from .knowledge_base import Busy, DuplicateDocument, KnowledgeBase, NotFound
from .llm import AnswerGenerator, build_generator

logger = logging.getLogger("complyagent.api")

_UNSET = object()
ERROR_RESPONSES = {
    code: {"model": schemas.ErrorResponse} for code in (400, 401, 404, 409, 413, 415, 422, 503)
}


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def _error(status_code: int, code: str, message: str, headers=None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code, headers=headers
    )


def create_app(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    generator: AnswerGenerator | None | object = _UNSET,
) -> FastAPI:
    """`embedder` and `generator` can be injected so tests run without models or API keys."""
    if settings is None:
        load_dotenv(BACKEND_DIR / ".env")
        settings = Settings.from_env()
    embedder = embedder or build_embedder(settings)
    if generator is _UNSET:
        generator = build_generator(settings)
    kb = KnowledgeBase(settings, Database(settings.db_path), embedder, generator)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        interrupted = kb.db.fail_interrupted()
        if interrupted:
            logger.warning("Marked %d interrupted ingestion(s) as failed", interrupted)
        if not settings.auth_required:
            logger.warning("ADMIN_API_TOKEN is not set: the admin API is open (development only).")
        # Load the embedding model in the background so the first upload is not slow.
        if hasattr(embedder, "_load"):
            threading.Thread(target=_warm, args=(embedder,), daemon=True).start()
        yield

    app = FastAPI(title="ComplyAgent Admin & RAG API", version="1.0.0", lifespan=lifespan)
    app.state.kb = kb
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # --- errors: one JSON shape, no stack traces or internals --------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return _error(exc.status_code, exc.detail["code"], exc.detail["message"], exc.headers)
        return _error(exc.status_code, "http_error", str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _error(422, "invalid_request", f"{field}: {first.get('msg', 'invalid value')}")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error", exc_info=exc)
        return _error(500, "internal_error", "An unexpected server error occurred.")

    # --- auth ------------------------------------------------------------------------------

    def require_admin(request: Request) -> None:
        if not settings.auth_required:
            return
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.strip().encode(), settings.admin_api_token.encode()
        ):
            raise HTTPException(
                401,
                {"code": "unauthorized", "message": "A valid admin token is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )

    admin = Depends(require_admin)

    # --- public ----------------------------------------------------------------------------

    @app.get("/api/health", response_model=schemas.PublicHealth, tags=["public"])
    def public_health():
        return {"status": kb.health()["status"]}

    @app.get("/api/auth/status", response_model=schemas.AuthStatus, tags=["public"])
    def auth_status():
        return {"auth_required": settings.auth_required}

    # --- admin: service --------------------------------------------------------------------

    @app.get("/api/admin/session", dependencies=[admin], tags=["admin"], responses=ERROR_RESPONSES)
    def session():
        """Lets the admin panel check a token before using it."""
        return {"ok": True}

    @app.get(
        "/api/admin/health", response_model=schemas.Health, dependencies=[admin], tags=["admin"]
    )
    def health():
        return kb.health()

    @app.get(
        "/api/admin/config",
        response_model=schemas.AdminConfig,
        dependencies=[admin],
        tags=["admin"],
    )
    def config():
        return {
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "max_upload_mb": settings.max_upload_mb,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "embedding_model": embedder.name,
            "llm_model": generator.model if generator else None,
            "default_top_k": settings.rag_top_k,
            "default_min_score": (
                settings.rag_min_score
                if settings.rag_min_score is not None
                else embedder.default_min_score
            ),
        }

    # --- admin: documents ------------------------------------------------------------------

    @app.get(
        "/api/admin/documents",
        response_model=schemas.DocumentList,
        dependencies=[admin],
        tags=["documents"],
    )
    def list_documents():
        return {"documents": kb.db.list_documents()}

    @app.post(
        "/api/admin/documents",
        response_model=schemas.Document,
        status_code=202,
        dependencies=[admin],
        tags=["documents"],
        responses=ERROR_RESPONSES,
    )
    async def upload_document(background: BackgroundTasks, file: UploadFile = File(...)):
        """Accepts the file and indexes it in the background; poll the document for status."""
        limit = settings.max_upload_bytes
        data = await file.read(limit + 1)
        try:
            doc = kb.register_upload(file.filename, data)
        except ValidationError as exc:
            code = {413: "file_too_large", 415: "unsupported_file_type"}.get(
                exc.status_code, "invalid_file"
            )
            raise ApiError(exc.status_code, code, str(exc)) from exc
        except DuplicateDocument as exc:
            existing = exc.existing
            hint = (
                " Its last ingestion failed; use Reindex to retry."
                if existing["status"] == "failed"
                else ""
            )
            raise ApiError(
                409,
                "duplicate_document",
                f"This file was already uploaded as '{existing['filename']}'.{hint}",
            ) from exc
        background.add_task(kb.ingest, doc["id"])
        return doc

    @app.get(
        "/api/admin/documents/{doc_id}",
        response_model=schemas.Document,
        dependencies=[admin],
        tags=["documents"],
        responses=ERROR_RESPONSES,
    )
    def get_document(doc_id: str):
        doc = kb.db.get_document(doc_id)
        if not doc:
            raise ApiError(404, "not_found", "Document not found.")
        return doc

    @app.delete(
        "/api/admin/documents/{doc_id}",
        response_model=schemas.DeleteResult,
        dependencies=[admin],
        tags=["documents"],
        responses=ERROR_RESPONSES,
    )
    def delete_document(doc_id: str):
        """Removes the document, its stored file and every indexed chunk."""
        try:
            removed = kb.delete(doc_id)
        except NotFound:
            raise ApiError(404, "not_found", "Document not found.") from None
        except Busy:
            raise ApiError(
                409, "document_busy", "The document is still being processed. Try again shortly."
            ) from None
        return {"id": doc_id, "deleted": True, "chunks_removed": removed}

    @app.post(
        "/api/admin/documents/{doc_id}/reindex",
        response_model=schemas.Document,
        status_code=202,
        dependencies=[admin],
        tags=["documents"],
        responses=ERROR_RESPONSES,
    )
    def reindex_document(doc_id: str, background: BackgroundTasks):
        try:
            doc = kb.prepare_reindex(doc_id)
        except NotFound:
            raise ApiError(404, "not_found", "Document not found.") from None
        except Busy:
            raise ApiError(
                409, "document_busy", "The document is already being processed."
            ) from None
        background.add_task(kb.ingest, doc_id)
        return doc

    @app.get(
        "/api/admin/activity",
        response_model=schemas.ActivityList,
        dependencies=[admin],
        tags=["documents"],
    )
    def activity(limit: int = Query(20, ge=1, le=100)):
        return {"events": kb.db.recent_activity(limit)}

    # --- admin: knowledge base -------------------------------------------------------------

    @app.get(
        "/api/admin/kb/stats",
        response_model=schemas.KnowledgeBaseStats,
        dependencies=[admin],
        tags=["knowledge base"],
    )
    def kb_stats():
        return kb.stats()

    @app.post(
        "/api/admin/kb/rebuild",
        response_model=schemas.RebuildResult,
        status_code=202,
        dependencies=[admin],
        tags=["knowledge base"],
    )
    def kb_rebuild(background: BackgroundTasks):
        """Re-extracts and re-embeds every document from its stored file. A document is not
        searchable while it is queued; its old chunks are swapped for new ones atomically."""
        ids = kb.prepare_rebuild()

        def run() -> None:
            for doc_id in ids:
                kb.ingest(doc_id)

        background.add_task(run)
        return {"queued": len(ids)}

    # --- admin: RAG ------------------------------------------------------------------------

    @app.post(
        "/api/admin/rag/query",
        response_model=schemas.QueryResponse,
        dependencies=[admin],
        tags=["rag"],
        responses=ERROR_RESPONSES,
    )
    def rag_query(body: schemas.QueryRequest):
        try:
            return kb.answer(
                body.question,
                history=[m.model_dump() for m in body.history],
                top_k=body.top_k,
                min_score=body.min_score,
                document_ids=body.document_ids,
            )
        except EmbeddingError as exc:
            raise ApiError(503, "embeddings_unavailable", str(exc)) from exc

    return app


def _warm(embedder) -> None:
    try:
        embedder._load()
    except EmbeddingError:
        pass  # already logged; surfaced through /api/admin/health


def __getattr__(name: str):
    # Build the app lazily so importing this module (e.g. in tests) has no side effects.
    if name == "app":
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
