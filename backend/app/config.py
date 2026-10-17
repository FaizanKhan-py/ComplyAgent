"""Runtime configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Extension -> file type label. Only formats the extraction module can actually read.
SUPPORTED_EXTENSIONS: dict[str, str] = {".pdf": "pdf", ".txt": "txt", ".md": "md"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool | None) -> bool | None:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false")


def _opt_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def _opt_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else None


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    admin_api_token: str = ""
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    storage_dir: Path = BACKEND_DIR / "storage"
    max_upload_mb: int = 20
    chunk_size: int = 500
    chunk_overlap: int = 80
    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rag_top_k: int = 5
    rag_min_score: float | None = None
    llm_provider: str = "auto"
    anthropic_model: str = "claude-opus-5-5"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    ollama_num_ctx: int | None = None  # None: Ollama's own default context window
    llm_timeout_seconds: float = 60.0
    allow_registration: bool = True
    session_ttl_hours: int = 12
    cookie_secure: bool | None = None  # None: on in production, off in development
    login_max_attempts: int = 5  # failed sign-ins per email and client before a lock-out
    login_window_minutes: int = 15
    register_max_per_hour: int = 10  # sign-ups per client

    def __post_init__(self) -> None:
        if self.chunk_size < 100:
            raise ValueError("CHUNK_SIZE must be at least 100 characters")
        if not 0 <= self.chunk_overlap < self.chunk_size // 2:
            raise ValueError("CHUNK_OVERLAP must be >= 0 and less than half of CHUNK_SIZE")
        if not self.ollama_base_url.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        if self.ollama_num_ctx is not None and self.ollama_num_ctx < 1024:
            raise ValueError("OLLAMA_NUM_CTX must be at least 1024 tokens")
        if self.admin_api_token and len(self.admin_api_token) < 16:
            raise ValueError("ADMIN_API_TOKEN must be at least 16 characters, or empty")
        if self.session_ttl_hours < 1:
            raise ValueError("SESSION_TTL_HOURS must be at least 1")
        if min(self.login_max_attempts, self.login_window_minutes, self.register_max_per_hour) < 1:
            raise ValueError("The sign-in and sign-up limits must be at least 1")
        if self.is_production and self.cookie_secure is False:
            raise ValueError("COOKIE_SECURE cannot be false when APP_ENV=production")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_hours * 3600

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "knowledge_base.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def models_dir(self) -> Path:
        return self.storage_dir / "models"

    @classmethod
    def from_env(cls) -> Settings:
        origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
        storage = os.getenv("STORAGE_DIR", "").strip()
        kwargs: dict = {}
        if origins:
            kwargs["cors_origins"] = origins
        if storage:
            kwargs["storage_dir"] = Path(storage).expanduser().resolve()
        return cls(
            app_env=os.getenv("APP_ENV", "development").strip() or "development",
            admin_api_token=os.getenv("ADMIN_API_TOKEN", "").strip(),
            max_upload_mb=_int("MAX_UPLOAD_MB", 20),
            chunk_size=_int("CHUNK_SIZE", 500),
            chunk_overlap=_int("CHUNK_OVERLAP", 80),
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "fastembed").strip().lower(),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5").strip(),
            rag_top_k=_int("RAG_TOP_K", 5),
            rag_min_score=_opt_float("RAG_MIN_SCORE"),
            llm_provider=os.getenv("LLM_PROVIDER", "auto").strip().lower(),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5").strip(),
            ollama_base_url=(os.getenv("OLLAMA_BASE_URL", "").strip() or "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "").strip() or "llama3.2",
            ollama_num_ctx=_opt_int("OLLAMA_NUM_CTX"),
            llm_timeout_seconds=float(_int("LLM_TIMEOUT_SECONDS", 60)),
            allow_registration=bool(_bool("ALLOW_REGISTRATION", True)),
            session_ttl_hours=_int("SESSION_TTL_HOURS", 12),
            cookie_secure=_bool("COOKIE_SECURE", None),
            login_max_attempts=_int("LOGIN_MAX_ATTEMPTS", 5),
            login_window_minutes=_int("LOGIN_WINDOW_MINUTES", 15),
            register_max_per_hour=_int("REGISTER_MAX_PER_HOUR", 10),
            **kwargs,
        )
