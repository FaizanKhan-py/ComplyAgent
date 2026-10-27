"""ComplyAgent Admin + RAG API.

Run from the backend directory:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

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
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .core import auth, schemas
from .core.config import BACKEND_DIR, SUPPORTED_EXTENSIONS, Settings
from .core.db import Database, EmailTaken, now_iso
from .customers.portfolio import SORT_COLUMNS, CustomerExists, Portfolio
from .decisions import conversation, follow_up
from .decisions.decision import DecisionUnavailable, recommend
from .decisions.guidance import customer_guidance
from .decisions.messages import generate_message
from .langchain.agents import AssistantAgent
from .langchain.llm import AnswerGenerator, LLMError, build_generator
from .langchain.models import classify_error
from .orchestration import graph
from .rag.embeddings import Embedder, EmbeddingError, build_embedder
from .rag.extraction import ValidationError
from .rag.knowledge_base import Busy, DuplicateDocument, KnowledgeBase, NotFound
from .risk import counterfactual
from .risk.model_files import ModelMissing
from .risk.predictor import SUPPORTED_EXTENSIONS as DATASET_EXTENSIONS
from .risk.predictor import PredictionFailed, Predictor
from .risk.risk_model import score_customer

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
    portfolio = Portfolio(kb.db)
    predictor = Predictor(settings.risk_model_dir)

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
        stop = threading.Event()
        if settings.follow_up_interval_minutes > 0:
            threading.Thread(
                target=_follow_up_scheduler,
                args=(stop, settings.follow_up_interval_minutes * 60, kb, portfolio),
                daemon=True,
            ).start()
        yield
        stop.set()

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

    # This server is API-only (the UI is the Vite app); send browsers that land on it to the docs.
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

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
        """Creates an account. The role is never taken from the request: it is admin only for
        addresses listed in ADMIN_EMAILS, otherwise user."""
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
            role = "admin" if email in settings.admin_emails else "user"
            user = kb.db.create_user(email, full_name, auth.hash_password(body.password), role)
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

    # --- admin: customer portfolio ---------------------------------------------------------

    @app.get(
        "/api/admin/portfolio/summary",
        response_model=schemas.PortfolioSummary,
        dependencies=[admin],
        tags=["portfolio"],
    )
    def portfolio_summary():
        return portfolio.summary()

    @app.get(
        "/api/admin/customers",
        response_model=schemas.CustomerList,
        dependencies=[admin],
        tags=["portfolio"],
        responses=ERROR_RESPONSES,
    )
    def list_customers(
        search: str = Query("", max_length=50),
        risk: schemas.RiskLevel | None = None,
        actual: Literal["defaulted", "paid"] | None = None,
        payment: Literal["overdue", "current"] | None = Query(
            None, description="overdue: late in the latest month; current: not late"
        ),
        sort: str = Query("id", json_schema_extra={"enum": sorted(SORT_COLUMNS)}),
        order: Literal["asc", "desc"] = "asc",
        page: int = Query(1, ge=1, le=100_000),
        page_size: int = Query(25, ge=1, le=100),
    ):
        if sort not in SORT_COLUMNS:
            raise ApiError(422, "invalid_request", f"sort: must be one of {sorted(SORT_COLUMNS)}")
        return portfolio.list_customers(
            search, risk, actual, payment, sort, order == "desc", page, page_size
        )

    @app.post(
        "/api/admin/customers",
        response_model=schemas.CustomerCreated,
        status_code=201,
        dependencies=[admin],
        tags=["portfolio"],
        responses=ERROR_RESPONSES,
    )
    def create_customer(body: schemas.CustomerCreate):
        """Add one customer by hand, then score them with the trained model if there is one."""
        try:
            customer_id = portfolio.add_customer(body.model_dump())
        except CustomerExists as exc:
            raise ApiError(
                409,
                "customer_exists",
                f"Customer {exc.customer_id} already exists. Use another number or leave it blank.",
            ) from exc
        scored = score_customer(kb.db, settings.risk_model_dir, customer_id)
        return {"customer": portfolio.get_customer(customer_id), "scored": scored}

    @app.get(
        "/api/admin/customers/{customer_id}",
        response_model=schemas.CustomerDetail,
        dependencies=[admin],
        tags=["portfolio"],
        responses=ERROR_RESPONSES,
    )
    def get_customer(customer_id: int):
        customer = portfolio.get_customer(customer_id)
        if not customer:
            raise ApiError(404, "not_found", "Customer not found.")
        return {**customer, "do_not_contact": conversations.do_not_contact(customer_id)}

    @app.post(
        "/api/admin/customers/{customer_id}/guidance",
        response_model=schemas.CustomerGuidance,
        dependencies=[admin],
        tags=["portfolio"],
        responses=ERROR_RESPONSES,
    )
    def guidance(customer_id: int):
        """What the policies say about this customer's situation, with citations."""
        try:
            result = customer_guidance(kb, portfolio, customer_id)
        except EmbeddingError as exc:
            raise ApiError(503, "embeddings_unavailable", str(exc)) from exc
        if result is None:
            raise ApiError(404, "not_found", "Customer not found.")
        return result

    @app.post(
        "/api/admin/customers/{customer_id}/recommendation",
        response_model=schemas.CustomerRecommendation,
        dependencies=[admin],
        tags=["portfolio"],
        responses=ERROR_RESPONSES,
    )
    def recommendation(customer_id: int):
        """The Decision Agent's next action for this customer, with the evidence it used."""
        try:
            result = recommend(kb, portfolio, kb.generator, customer_id)
        except EmbeddingError as exc:
            raise ApiError(503, "embeddings_unavailable", str(exc)) from exc
        except DecisionUnavailable as exc:
            status = 502 if exc.code == "llm_bad_output" else 503
            raise ApiError(status, exc.code, exc.message) from exc
        except LLMError as exc:
            raise ApiError(503 if exc.retryable else 502, exc.code, exc.message) from exc
        if result is None:
            raise ApiError(404, "not_found", "Customer not found.")
        return result

    @app.post(
        "/api/admin/customers/{customer_id}/message",
        response_model=schemas.CustomerMessage,
        dependencies=[admin],
        tags=["portfolio"],
        responses=ERROR_RESPONSES,
    )
    def message(customer_id: int):
        """A checked draft collections message for this customer. Nothing is sent."""
        try:
            result = generate_message(kb, portfolio, kb.generator, customer_id)
        except EmbeddingError as exc:
            raise ApiError(503, "embeddings_unavailable", str(exc)) from exc
        except DecisionUnavailable as exc:
            status = 502 if exc.code == "llm_bad_output" else 503
            raise ApiError(status, exc.code, exc.message) from exc
        except LLMError as exc:
            raise ApiError(503 if exc.retryable else 502, exc.code, exc.message) from exc
        if result is None:
            raise ApiError(404, "not_found", "Customer not found.")
        return result

    # --- admin: collections conversations (all agents, with an officer's approval) ----------
    #
    # The agents run inside these requests: Case Analyst, Decision Agent, the writer and the
    # Red Team (the first email), and the Communication Agent (each reply). Drafts wait for an
    # officer: approve (optionally edited), reject, then send.

    def _conversation(run, *args):
        try:
            return run(*args)
        except conversation.ConversationError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
        except EmbeddingError as exc:
            raise ApiError(503, "embeddings_unavailable", str(exc)) from exc
        except DecisionUnavailable as exc:
            status = 502 if exc.code == "llm_bad_output" else 503
            raise ApiError(status, exc.code, exc.message) from exc
        except LLMError as exc:
            raise ApiError(503 if exc.retryable else 502, exc.code, exc.message) from exc

    conversations = conversation.Conversations(kb.db)
    conv = {"dependencies": [admin], "tags": ["conversations"], "responses": ERROR_RESPONSES}

    @app.get(
        "/api/admin/customers/{customer_id}/conversation",
        response_model=schemas.Conversation | None,
        **conv,
    )
    def latest_conversation(customer_id: int):
        """The customer's latest conversation, or null when there has been none."""
        get_customer(customer_id)
        return conversations.latest_for(customer_id)

    @app.post(
        "/api/admin/customers/{customer_id}/conversation",
        response_model=schemas.Conversation,
        **conv,
    )
    def start_conversation(customer_id: int, user: dict = admin):
        """Starts a conversation: the agents write the first email, which waits for approval."""
        get_customer(customer_id)
        return _conversation(
            conversation.start, kb, portfolio, kb.generator, conversations, customer_id, user
        )

    @app.post(
        "/api/admin/conversations/{conversation_id}/inbound",
        response_model=schemas.Conversation,
        **conv,
    )
    def customer_replied(conversation_id: int, email: schemas.InboundEmail, user: dict = admin):
        """Adds the customer's reply (pasted in until email is connected); the Communication
        Agent drafts the answer, or hands the conversation to a person."""
        return _conversation(
            conversation.receive, kb, portfolio, kb.generator, conversations, conversation_id,
            email.subject, email.body, user,
        )  # fmt: skip

    @app.post(
        "/api/admin/conversations/{conversation_id}/redraft",
        response_model=schemas.Conversation,
        **conv,
    )
    def redraft_message(conversation_id: int, body: schemas.RedraftRequest, user: dict = admin):
        """A new draft for the same point in the thread, following the officer's feedback."""
        return _conversation(
            conversation.redraft, kb, portfolio, kb.generator, conversations, conversation_id,
            body.feedback, user,
        )  # fmt: skip

    @app.post(
        "/api/admin/conversations/{conversation_id}/close",
        response_model=schemas.Conversation,
        **conv,
    )
    def close_conversation(conversation_id: int, body: schemas.CloseRequest, user: dict = admin):
        return _conversation(conversation.close, conversations, conversation_id, user, body.note)

    @app.post(
        "/api/admin/messages/{message_id}/approve", response_model=schemas.Conversation, **conv
    )
    def approve_message(message_id: int, body: schemas.ApproveRequest, user: dict = admin):
        """An officer approves a draft, optionally edited; an edit is checked again."""
        return _conversation(
            conversation.approve, portfolio, conversations, message_id, user,
            body.subject, body.body, body.note,
        )  # fmt: skip

    @app.post(
        "/api/admin/messages/{message_id}/reject", response_model=schemas.Conversation, **conv
    )
    def reject_message(message_id: int, body: schemas.RejectRequest, user: dict = admin):
        return _conversation(conversation.reject, conversations, message_id, user, body.note)

    @app.post("/api/admin/messages/{message_id}/send", response_model=schemas.Conversation, **conv)
    def send_message(message_id: int, user: dict = admin):
        """Marks an approved email as sent (email delivery is not connected yet); the Follow-up
        Agent then plans what happens if the customer does not reply."""
        sent = _conversation(conversation.send, portfolio, conversations, message_id, user)
        follow_up.plan(kb, portfolio, kb.generator, conversations, sent["id"])
        return conversations.get(sent["id"])

    @app.post(
        "/api/admin/customers/{customer_id}/do-not-contact",
        response_model=schemas.CustomerDetail,
        **conv,
    )
    def do_not_contact(customer_id: int, body: schemas.DoNotContactRequest, user: dict = admin):
        """Records the customer's request to stop contact: no email is written or sent until an
        officer lifts it. Waiting drafts and follow-ups are withdrawn."""
        get_customer(customer_id)
        conversation.stop_contact(conversations, customer_id, body.reason, conversation.actor(user))
        return get_customer(customer_id)

    @app.post(
        "/api/admin/customers/{customer_id}/do-not-contact/lift",
        response_model=schemas.CustomerDetail,
        **conv,
    )
    def lift_do_not_contact(customer_id: int, body: schemas.LiftRequest, user: dict = admin):
        """Allows contact again, saying why (e.g. the customer asked to be contacted)."""
        get_customer(customer_id)
        _conversation(conversation.lift_contact_block, conversations, customer_id, body.note, user)
        return get_customer(customer_id)

    @app.get("/api/admin/inbox", response_model=schemas.Inbox, **conv)
    def inbox():
        """Everything across customers that waits for an officer: drafts to approve, blocked
        drafts, conversations handed to a person, due follow-ups and calls to make."""
        found = conversations.inbox(now_iso())
        return {**found, "total": sum(len(items) for items in found.values())}

    # --- admin: the Follow-up Agent --------------------------------------------------------

    @app.get(
        "/api/admin/follow-ups",
        response_model=list[schemas.FollowUp],
        dependencies=[admin],
        tags=["follow-ups"],
    )
    def list_follow_ups(status: Literal["scheduled", "done", "cancelled"] | None = None):
        """Follow-ups, soonest first."""
        return conversations.list_follow_ups(status)

    @app.post(
        "/api/admin/follow-ups/run-due",
        response_model=schemas.FollowUpRun,
        dependencies=[admin],
        tags=["follow-ups"],
    )
    def run_due_follow_ups():
        """Carries out every scheduled follow-up whose time has come."""
        return follow_up.run_due(kb, portfolio, kb.generator, conversations)

    @app.post(
        "/api/admin/follow-ups/{follow_up_id}/run",
        response_model=schemas.Conversation,
        tags=["follow-ups"],
        dependencies=[admin],
        responses=ERROR_RESPONSES,
    )
    def run_follow_up(follow_up_id: int, user: dict = admin):
        """Carries out one follow-up now, without waiting for it to be due."""
        return _conversation(
            follow_up.run, kb, portfolio, kb.generator, conversations, follow_up_id,
            conversation.actor(user),
        )  # fmt: skip

    # --- admin: audit trail and the agent graph ---------------------------------------------

    @app.get(
        "/api/admin/audit", response_model=schemas.AuditList, dependencies=[admin], tags=["audit"]
    )
    def audit_trail(
        customer_id: int | None = None,
        conversation_id: int | None = None,
        event: str | None = Query(None, max_length=60),
        limit: int = Query(100, ge=1, le=500),
        before: int | None = None,
    ):
        """Every agent step and officer action, newest first."""
        events = conversations.audit.list(customer_id, conversation_id, event, limit, before)
        return {
            "events": events,
            "next_before": events[-1]["id"] if len(events) == limit else None,
        }

    @app.get(
        "/api/admin/agents/graph",
        response_model=schemas.AgentGraph,
        dependencies=[admin],
        tags=["audit"],
    )
    def agent_graph():
        """The LangGraph graph that connects the agents, as a Mermaid diagram."""
        return {"mermaid": graph.mermaid()}

    # --- admin: the Counterfactual Simulator -----------------------------------------------

    class _OneCustomer:
        """A portfolio of one changed record, for the Decision Agent's what-if."""

        def __init__(self, customer: dict) -> None:
            self.customer = customer

        def get_customer(self, customer_id: int) -> dict | None:
            return self.customer

    def _simulate(customer_id: int, body: schemas.SimulateRequest | None, user: dict) -> dict:
        customer = get_customer(customer_id)
        if not customer["history"]:
            raise ApiError(409, "no_history", "This customer has no payment history to change.")
        custom = body.model_dump(exclude={"decide"}, exclude_none=True) if body else None
        try:
            result = counterfactual.simulate(settings.risk_model_dir, customer, custom or None)
        except ModelMissing as exc:
            raise ApiError(503, "model_missing", str(exc)) from exc
        if result["custom"] is None:
            return result
        changed = result["custom"].pop("record")
        changed |= {
            "default_probability": result["custom"]["probability"],
            "risk_level": result["custom"]["risk_level"],
        }
        if body.decide:
            try:
                decided = recommend(kb, _OneCustomer(changed), kb.generator, customer_id)
                result["custom"]["recommendation"] = decided["recommendation"]
            except (DecisionUnavailable, LLMError) as exc:
                result["custom"]["decision_error"] = exc.message
            except EmbeddingError as exc:
                result["custom"]["decision_error"] = str(exc)
        rec = result["custom"].get("recommendation")
        would = f"; the Decision Agent would {rec.action.value.replace('_', ' ')}" if rec else ""
        conversations.audit.record(
            conversation.actor(user),
            "simulation.run",
            f"What-if {result['custom']['changes']}: chance of default "
            f"{result['base']['probability']:.0%} -> {result['custom']['probability']:.0%}"
            f"{would}.",
            customer_id=customer_id,
            detail={
                "changes": result["custom"]["changes"],
                "base": result["base"],
                "probability": result["custom"]["probability"],
                "recommendation": rec.model_dump(mode="json") if rec else None,
            },
        )
        return result

    @app.get(
        "/api/admin/customers/{customer_id}/counterfactuals",
        response_model=schemas.Simulation,
        dependencies=[admin],
        tags=["simulator"],
        responses=ERROR_RESPONSES,
    )
    def counterfactuals(customer_id: int, user: dict = admin):
        """The ready-made what-ifs and the fairness check for this customer. No LLM."""
        return _simulate(customer_id, None, user)

    @app.post(
        "/api/admin/customers/{customer_id}/counterfactuals",
        response_model=schemas.Simulation,
        dependencies=[admin],
        tags=["simulator"],
        responses=ERROR_RESPONSES,
    )
    def simulate_change(customer_id: int, body: schemas.SimulateRequest, user: dict = admin):
        """The officer's own what-if; with decide, the Decision Agent decides for it too."""
        return _simulate(customer_id, body, user)

    # --- admin: dataset prediction ---------------------------------------------------------

    @app.get(
        "/api/admin/predict/model",
        response_model=schemas.PredictionModelInfo,
        dependencies=[admin],
        tags=["prediction"],
    )
    def prediction_model():
        """The model saved by the notebook, or why it cannot be used."""
        return predictor.info()

    @app.post(
        "/api/admin/predict/dataset",
        response_model=schemas.DatasetPrediction,
        dependencies=[admin],
        tags=["prediction"],
        responses=ERROR_RESPONSES,
    )
    def predict_dataset(file: UploadFile = File(...)):
        """Scores every row of an uploaded CSV or Excel file and fills in the target column."""
        if not (file.filename or "").lower().endswith(DATASET_EXTENSIONS):
            raise ApiError(
                415,
                "unsupported_file_type",
                f"Upload the dataset as a {', '.join(DATASET_EXTENSIONS)} file.",
            )
        limit = settings.max_upload_bytes
        data = file.file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(
                413, "file_too_large", f"The file is larger than {settings.max_upload_mb} MB."
            )
        try:
            return predictor.predict_file(file.filename, data)
        except PredictionFailed as exc:
            code = {413: "file_too_large", 503: "model_unavailable"}.get(
                exc.status_code, "invalid_dataset"
            )
            raise ApiError(exc.status_code, code, str(exc)) from exc

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

    # --- AI Assistant: one chat for everyone (LangChain agent with tools) -------------------
    #
    # Administrators' assistant has every tool (policies, customers, portfolio, Decision Agent);
    # everyone else's can only search the policies. Without a model, or when the model fails,
    # the reply is the matching policy passages, as the policy search alone would give.

    assistants: dict[bool, AssistantAgent] = {}
    assistants_lock = threading.Lock()

    def _assistant(admin_tools: bool) -> AssistantAgent:
        with assistants_lock:
            if admin_tools not in assistants:
                assistants[admin_tools] = AssistantAgent(
                    kb.generator.chat_model,
                    kb.generator.model,
                    kb,
                    portfolio,
                    kb.generator,
                    customer_tools=admin_tools,
                )
            return assistants[admin_tools]

    def _passages_only(message: str, conversation_id: str | None, warning: str | None) -> dict:
        result = kb.answer(message, generate=False)
        keys = ("answer", "answer_mode", "insufficient_evidence", "sources", "model")
        return {
            **{k: result[k] for k in keys},
            "warning": warning,
            "conversation_id": conversation_id or uuid.uuid4().hex,
            "steps": [],
        }

    @app.post(
        "/api/assistant/chat",
        response_model=schemas.AssistantReply,
        tags=["assistant"],
        responses=ERROR_RESPONSES,
    )
    def assistant_chat(body: schemas.AssistantRequest, user: dict = signed_in):
        """Ask the AI Assistant. It answers with read-only tools, cites the policy passages it
        read, and remembers the conversation for the user who started it."""
        try:
            if getattr(kb.generator, "chat_model", None) is None:
                return _passages_only(body.message, body.conversation_id, None)
            try:
                return _assistant(user["role"] == "admin").ask(
                    body.message, owner=str(user["id"]), conversation_id=body.conversation_id
                )
            except EmbeddingError:
                raise
            except Exception as exc:
                code, message, _ = classify_error(exc)
                logger.warning("AI Assistant failed: %s", code)
                warning = (
                    f"The answer model is not available right now: {message} "
                    "The matching policy passages are shown instead."
                )
                return _passages_only(body.message, body.conversation_id, warning)
        except EmbeddingError as exc:
            raise ApiError(503, "embeddings_unavailable", str(exc)) from exc

    return app


def _warm(embedder) -> None:
    try:
        embedder._load()
    except EmbeddingError:
        pass  # already logged; surfaced through /api/admin/health


def _follow_up_scheduler(stop: threading.Event, seconds: int, kb, portfolio) -> None:
    """Runs the due follow-ups every `seconds` (FOLLOW_UP_INTERVAL_MINUTES) until shutdown."""
    store = conversation.Conversations(kb.db)
    while not stop.wait(seconds):
        try:
            result = follow_up.run_due(kb, portfolio, kb.generator, store)
            if result["due"]:
                logger.info("Follow-ups: %d due, %d done", result["due"], len(result["done"]))
        except Exception:  # keep the scheduler alive; the next run tries again
            logger.exception("The follow-up run failed")


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
