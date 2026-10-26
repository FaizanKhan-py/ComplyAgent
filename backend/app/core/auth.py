"""User accounts: password hashing, email normalisation, session tokens and rate limiting."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email

logger = logging.getLogger("complyagent.auth")

ROLES = ("user", "admin")
SESSION_COOKIE = "complyagent_session"
CSRF_HEADER = "X-CSRF-Token"
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128  # bounds the work an attacker can force on the hasher
NAME_MAX_LENGTH = 100

_hasher = PasswordHasher()  # Argon2id with the library's recommended parameters
# Verified when the email is unknown, so that case takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class AuthError(Exception):
    """A request-level problem. `message` is safe to show to the person signing in."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_email(raw: str) -> str:
    """Validated, lower-cased address: 'Ada@Example.COM ' and 'ada@example.com' are one account."""
    try:
        result = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise AuthError("invalid_email", "Enter a valid email address.") from exc
    return result.normalized.lower()


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise AuthError("invalid_name", "Enter your full name.")
    if len(name) > NAME_MAX_LENGTH:
        raise AuthError("invalid_name", f"The name must be at most {NAME_MAX_LENGTH} characters.")
    return name


def check_password_rules(password: str, email: str = "") -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(
            "weak_password", f"The password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise AuthError(
            "weak_password", f"The password must be at most {PASSWORD_MAX_LENGTH} characters."
        )
    if email and password.lower() == email.lower():
        raise AuthError("weak_password", "The password must not be your email address.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Always does one hash computation, whether or not the account exists."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Only this digest is stored, so a copy of the database cannot be used to sign in."""
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Sliding-window counter kept in memory: enough for one API process. Behind several
    workers or instances, replace it with a shared store."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str, limit: int, window_seconds: float) -> int:
        """0 if another attempt is allowed, otherwise the seconds to wait."""
        now = time.monotonic()
        with self._lock:
            events = self._events.get(key)
            if not events:
                return 0
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if not events:
                del self._events[key]
                return 0
            if len(events) < limit:
                return 0
            return max(1, int(window_seconds - (now - events[0])) + 1)

    def record(self, key: str) -> None:
        with self._lock:
            self._events.setdefault(key, deque()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
