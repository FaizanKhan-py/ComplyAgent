"""ComplyAgent Admin + RAG API.

Run from the backend directory:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
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
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, schemas
from .config import BACKEND_DIR, SUPPORTED_EXTENSIONS, Settings
from .db import Database, EmailTaken
from .embeddings import Embedder, EmbeddingError, build_embedder
from .extraction import ValidationError
from .knowledge_base import Busy, DuplicateDocument, KnowledgeBase, NotFound
from .llm import AnswerGenerator, build_generator

logger = logging.getLogger("complyagent.api")

_UNSET = object()
ERROR_RESPONSES = {
    code: {"model": schemas.ErrorResponse}
    for code in (400, 401, 403, 404, 409, 413, 415, 422, 429, 503)
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
        if not any(u["role"] == "admin" for u in kb.db.list_users()):
            logger.warning(
                "No administrator account exists yet. Create one with: "
                "python -m app.manage create-admin --email you@example.com --name 'Your Name'"
            )
        if generator is None:
            logger.info("No answer model configured: answers are retrieval-only.")
        else:
            status, detail = generator.health()
            logger.log(
                logging.INFO if status == "ok" else logging.WARNING, "Answer model: %s", detail
            )
        # Load the embedding model in the background so the first upload is not slow.
        if hasattr(embedder, "_load"):
            threading.Thread(target=_warm, args=(embedder,), daemon=True).start()
        yield

    app = FastAPI(title="ComplyAgent Admin & RAG API", version="1.0.0", lifespan=lifespan)
    app.state.kb = kb
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the session cookie; origins are an explicit list, never "*"
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", auth.CSRF_HEADER],
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
    #
    # Two ways to authenticate:
    #   * A person signs in and gets a session cookie (HttpOnly). Their role decides access.
    #   * A script sends "Authorization: Bearer <ADMIN_API_TOKEN>", if that token is configured.
    # Without one of these every private route answers 401.

    limiter = auth.RateLimiter()
    login_window = settings.login_window_minutes * 60
    service_account = {"id": 0, "email": "", "full_name": "API token", "role": "admin"}

    def _unauthorized(message: str = "Sign in to continue.") -> HTTPException:
        return HTTPException(
            401,
            {"code": "unauthorized", "message": message},
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _check_origin(request: Request) -> None:
        """Browsers send Origin on cross-site POSTs; refuse ones from sites we do not serve."""
        origin = request.headers.get("origin")
        if not origin or origin in settings.cors_origins:
            return
        if origin == f"{request.url.scheme}://{request.headers.get('host', '')}":
            return
        raise ApiError(403, "bad_origin", "This request came from an origin that is not allowed.")

    def _session(request: Request) -> dict | None:
        """The session behind the cookie, with CSRF enforced on state-changing requests."""
        token = request.cookies.get(auth.SESSION_COOKIE)
        session = kb.db.get_session(auth.token_hash(token)) if token else None
        if session and request.method not in ("GET", "HEAD", "OPTIONS"):
            _check_origin(request)
            sent = request.headers.get(auth.CSRF_HEADER, "")
            if not hmac.compare_digest(sent.encode(), session["csrf_token"].encode()):
                raise ApiError(
                    403, "csrf_failed", "The request could not be verified. Reload and try again."
                )
        return session

    def require_user(request: Request) -> dict:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            if settings.admin_api_token and hmac.compare_digest(
                token.strip().encode(), settings.admin_api_token.encode()
            ):
                return service_account
            raise _unauthorized("A valid API token is required.")
        session = _session(request)
        if not session:
            raise _unauthorized()
        return session

    def require_admin(user: dict = Depends(require_user)) -> dict:
        if user["role"] != "admin":
            raise ApiError(403, "forbidden", "This action needs an administrator account.")
        return user

    signed_in = Depends(require_user)
    admin = Depends(require_admin)

    def _limit(key: str, limit: int, window: float) -> None:
        wait = limiter.retry_after(key, limit, window)
        if wait:
            raise HTTPException(
                429,
                {
                    "code": "too_many_attempts",
                    "message": f"Too many attempts. Try again in {max(1, round(wait / 60))} min.",
                },
                headers={"Retry-After": str(wait)},
            )

    def _client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _user_view(row: dict) -> dict:
        return {k: row.get(k) for k in schemas.User.model_fields}

    # --- public ----------------------------------------------------------------------------

    @app.get("/api/health", response_model=schemas.PublicHealth, tags=["public"])
    def public_health():
        return {"status": kb.health()["status"]}

    @app.get("/api/auth/status", response_model=schemas.AuthStatus, tags=["auth"])
    def auth_status():
        return {"auth_required": True, "registration_open": settings.allow_registration}

    @app.post(
        "/api/auth/register",
        response_model=schemas.User,
        status_code=201,
        tags=["auth"],
        responses=ERROR_RESPONSES,
    )
    def register(body: schemas.RegisterRequest, request: Request):
        """Creates an ordinary account. The role is never taken from the request."""
        _check_origin(request)
        if not settings.allow_registration:
            raise ApiError(403, "registration_closed", "Sign-up is turned off on this server.")
        key = f"register:{_client(request)}"
        _limit(key, settings.register_max_per_hour, 3600)
        limiter.record(key)
        try:
            email = auth.normalize_email(body.email)
            full_name = auth.clean_name(body.full_name)
            auth.check_password_rules(body.password, email)
        except auth.AuthError as exc:
            raise ApiError(422, exc.code, exc.message) from exc
        try:
            user = kb.db.create_user(email, full_name, auth.hash_password(body.password), "user")
        except EmailTaken:
            raise ApiError(
                409, "email_taken", "An account with this email address already exists."
            ) from None
        logger.info("Registered user %d", user["id"])
        return _user_view(user)

    @app.post(
        "/api/auth/login",
        response_model=schemas.SessionInfo,
        tags=["auth"],
        responses=ERROR_RESPONSES,
    )
    def login(body: schemas.LoginRequest, request: Request, response: Response):
        _check_origin(request)
        email = body.email.strip().lower()
        client = _client(request)
        pair_key, client_key = f"login:{client}:{email}", f"login:{client}"
        _limit(pair_key, settings.login_max_attempts, login_window)
        _limit(client_key, settings.login_max_attempts * 10, login_window)

        user = kb.db.get_user_by_email(email, with_hash=True)
        if not auth.verify_password(user["password_hash"] if user else None, body.password):
            limiter.record(pair_key)
            limiter.record(client_key)
            logger.info("Failed sign-in from %s", client)
            # The same answer for an unknown email and a wrong password.
            raise ApiError(401, "invalid_credentials", "Incorrect email or password.")

        limiter.reset(pair_key)
        if auth.needs_rehash(user["password_hash"]):
            kb.db.set_password_hash(user["id"], auth.hash_password(body.password), False)
        token, csrf_token = auth.new_token(), auth.new_token()
        kb.db.create_session(
            user["id"],
            auth.token_hash(token),
            csrf_token,
            int(time.time()) + settings.session_ttl_seconds,
        )
        response.set_cookie(
            auth.SESSION_COOKIE,
            token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            path="/",
        )
        logger.info("User %d signed in", user["id"])
        return {"user": _user_view(user), "csrf_token": csrf_token}

    @app.get(
        "/api/auth/me", response_model=schemas.SessionInfo, tags=["auth"], responses=ERROR_RESPONSES
    )
    def me(request: Request):
        """Who is signed in. The panel calls this on load to restore the session."""
        session = _session(request)
        if not session:
            raise _unauthorized()
        return {"user": _user_view(session), "csrf_token": session["csrf_token"]}

    @app.post(
        "/api/auth/logout", response_model=schemas.Ok, tags=["auth"], responses=ERROR_RESPONSES
    )
    def logout(request: Request, response: Response):
        """Ends the session on the server, so the cookie is useless even if it was copied."""
        if _session(request):
            kb.db.delete_session(auth.token_hash(request.cookies[auth.SESSION_COOKIE]))
        response.delete_cookie(
            auth.SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
        )
        return {"ok": True}

    # --- admin: service --------------------------------------------------------------------

    @app.get("/api/admin/session", dependencies=[admin], tags=["admin"], responses=ERROR_RESPONSES)
    def session():
        """Lets a script check its API token, or the panel check for an administrator."""
        return {"ok": True}

    @app.get(
        "/api/admin/health", response_model=schemas.Health, dependencies=[admin], tags=["admin"]
    )
    def health():
        return kb.health()

    @app.get(
        "/api/admin/config",
        response_model=schemas.AdminConfig,
        dependencies=[signed_in],  # any signed-in user: the playground needs these defaults
        tags=["rag"],
        responses=ERROR_RESPONSES,
    )
    def config():
        return {
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "max_upload_mb": settings.max_upload_mb,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "embedding_model": embedder.name,
            "llm_provider": generator.provider if generator else None,
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
        dependencies=[signed_in],  # any signed-in user may ask questions
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
        # httpx logs every request at INFO, which would flood the log with health checks.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
