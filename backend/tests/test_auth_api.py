"""Accounts, sessions and role-based access, through the real API and a real SQLite database."""

from __future__ import annotations

import io
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, manage
from app.config import Settings
from app.db import Database
from app.embeddings import HashingEmbedder
from app.main import create_app

from .helpers import FakeGenerator
from .test_admin_api import ADMIN_ROUTES

PASSWORD = "correct horse battery"
ADA = {"full_name": "Ada Lovelace", "email": "ada@example.com", "password": PASSWORD}
# What an ordinary signed-in user may call; everything else under /api/admin is admin-only.
USER_ROUTES = {("get", "/api/admin/config"), ("post", "/api/admin/rag/query")}


@pytest.fixture
def settings(tmp_path) -> Settings:
    # No ADMIN_API_TOKEN: the only way in is a real account.
    return Settings(storage_dir=tmp_path / "storage", max_upload_mb=1)


@pytest.fixture
def app(settings):
    return create_app(settings, embedder=HashingEmbedder(), generator=FakeGenerator())


@pytest.fixture
def anon(app):
    with TestClient(app) as client:
        yield client


def sign_in(app, email: str, password: str = PASSWORD) -> TestClient:
    """A browser-like client: keeps the session cookie and sends the CSRF header."""
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.headers[auth.CSRF_HEADER] = response.json()["csrf_token"]
    return client


@pytest.fixture
def user(app, anon):
    assert anon.post("/api/auth/register", json=ADA).status_code == 201
    return sign_in(app, ADA["email"])


@pytest.fixture
def admin(app, settings, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    manage.main(
        ["create-admin", "--email", "root@example.com", "--name", "Root Admin"],
        db=Database(settings.db_path),
    )
    return sign_in(app, "root@example.com")


def error(response) -> str:
    return response.json()["error"]["code"]


# --- registration -----------------------------------------------------------------------------


def test_registration_creates_an_ordinary_user_and_does_not_sign_them_in(anon):
    response = anon.post(
        "/api/auth/register",
        json={**ADA, "full_name": "  Ada   Lovelace ", "email": " Ada@Example.COM "},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com" and body["full_name"] == "Ada Lovelace"
    assert body["role"] == "user"
    assert "password" not in response.text.lower()
    assert auth.SESSION_COOKIE not in response.cookies
    assert anon.get("/api/auth/me").status_code == 401


def test_registration_cannot_grant_itself_a_role(anon, app):
    payload = {**ADA, "role": "admin", "is_admin": True, "id": 0}
    assert anon.post("/api/auth/register", json=payload).json()["role"] == "user"
    client = sign_in(app, ADA["email"])
    assert client.get("/api/auth/me").json()["user"]["role"] == "user"
    assert client.get("/api/admin/documents").status_code == 403


def test_duplicate_email_is_rejected_whatever_its_case(anon):
    assert anon.post("/api/auth/register", json=ADA).status_code == 201
    again = anon.post("/api/auth/register", json={**ADA, "email": "ADA@example.com"})
    assert again.status_code == 409 and error(again) == "email_taken"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"email": "not-an-email"}, "invalid_email"),
        ({"email": "a@b"}, "invalid_email"),
        ({"full_name": "   "}, "invalid_name"),
        ({"password": "short"}, "weak_password"),
        ({"password": "x" * 129}, "weak_password"),
        ({"password": "ADA@example.com"}, "weak_password"),
    ],
)
def test_registration_validates_its_input(anon, change, code):
    response = anon.post("/api/auth/register", json={**ADA, **change})
    assert response.status_code == 422 and error(response) == code
    assert anon.post("/api/auth/register", json={"email": "a@example.com"}).status_code == 422


def test_registration_can_be_turned_off(tmp_path):
    settings = Settings(storage_dir=tmp_path, allow_registration=False)
    with TestClient(create_app(settings, embedder=HashingEmbedder(), generator=None)) as client:
        assert client.get("/api/auth/status").json()["registration_open"] is False
        response = client.post("/api/auth/register", json=ADA)
        assert response.status_code == 403 and error(response) == "registration_closed"


def test_passwords_are_stored_only_as_argon2_hashes(anon, settings):
    anon.post("/api/auth/register", json=ADA)
    with sqlite3.connect(settings.db_path) as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
        dump = "\n".join(conn.iterdump())
    assert stored.startswith("$argon2id$") and PASSWORD not in dump
    assert auth.verify_password(stored, PASSWORD) is True
    assert auth.verify_password(stored, PASSWORD + "x") is False
    assert auth.verify_password(None, PASSWORD) is False  # unknown account
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)  # salted


# --- sign-in ----------------------------------------------------------------------------------


def test_login_sets_a_hardened_session_cookie_and_me_restores_the_session(anon):
    anon.post("/api/auth/register", json=ADA)
    response = anon.post(
        "/api/auth/login", json={"email": " ADA@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "ada@example.com" and len(body["csrf_token"]) >= 32
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=43200" in cookie
    assert "Secure" not in cookie  # development; see the production test below

    me = anon.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "user": {**body["user"], "last_login_at": me.json()["user"]["last_login_at"]},
        "csrf_token": body["csrf_token"],
    }
    assert me.json()["user"]["last_login_at"] is not None


def test_production_marks_the_cookie_secure(tmp_path):
    settings = Settings(storage_dir=tmp_path, app_env="production")
    app = create_app(settings, embedder=HashingEmbedder(), generator=None)
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/register", json=ADA)
        response = client.post(
            "/api/auth/login", json={"email": ADA["email"], "password": PASSWORD}
        )
        assert "Secure" in response.headers["set-cookie"]
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(storage_dir=tmp_path, app_env="production", cookie_secure=False)


def test_wrong_password_and_unknown_email_get_the_same_answer(anon):
    anon.post("/api/auth/register", json=ADA)
    wrong = anon.post("/api/auth/login", json={"email": ADA["email"], "password": "wrong password"})
    unknown = anon.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    malformed = anon.post("/api/auth/login", json={"email": "not-an-email", "password": PASSWORD})
    for response in (wrong, unknown, malformed):
        assert response.status_code == 401
        assert response.json() == wrong.json()
        assert "set-cookie" not in response.headers
    assert error(wrong) == "invalid_credentials"


def test_the_session_token_is_stored_hashed(anon, settings):
    anon.post("/api/auth/register", json=ADA)
    anon.post("/api/auth/login", json={"email": ADA["email"], "password": PASSWORD})
    token = anon.cookies[auth.SESSION_COOKIE]
    with sqlite3.connect(settings.db_path) as conn:
        stored = conn.execute("SELECT token_hash FROM sessions").fetchall()
    assert stored == [(auth.token_hash(token),)] and token not in stored[0][0]


# --- brute force ------------------------------------------------------------------------------


def test_repeated_failed_logins_are_locked_out_even_with_the_right_password(anon):
    anon.post("/api/auth/register", json=ADA)
    for _ in range(5):
        attempt = anon.post(
            "/api/auth/login", json={"email": ADA["email"], "password": "nope nope"}
        )
        assert attempt.status_code == 401
    locked = anon.post("/api/auth/login", json={"email": ADA["email"], "password": PASSWORD})
    assert locked.status_code == 429 and error(locked) == "too_many_attempts"
    assert int(locked.headers["retry-after"]) > 0
    # Another account from the same client is not locked.
    anon.post("/api/auth/register", json={**ADA, "email": "grace@example.com"})
    other = anon.post("/api/auth/login", json={"email": "grace@example.com", "password": PASSWORD})
    assert other.status_code == 200


def test_a_successful_login_clears_the_failure_count(anon):
    anon.post("/api/auth/register", json=ADA)
    for _ in range(3):
        for _ in range(4):
            anon.post("/api/auth/login", json={"email": ADA["email"], "password": "nope nope"})
        ok = anon.post("/api/auth/login", json={"email": ADA["email"], "password": PASSWORD})
        assert ok.status_code == 200


def test_sign_up_from_an_admin_email_creates_an_admin(tmp_path):
    settings = Settings(storage_dir=tmp_path, admin_emails=frozenset({"ada@example.com"}))
    with TestClient(create_app(settings, embedder=HashingEmbedder(), generator=None)) as client:
        admin = client.post("/api/auth/register", json={**ADA, "email": "ADA@example.com"})
        other = client.post("/api/auth/register", json={**ADA, "email": "grace@example.com"})
    assert admin.json()["role"] == "admin"
    assert other.json()["role"] == "user"


def test_sign_up_is_rate_limited_per_client(tmp_path):
    settings = Settings(storage_dir=tmp_path, register_max_per_hour=3)
    with TestClient(create_app(settings, embedder=HashingEmbedder(), generator=None)) as client:
        codes = [
            client.post(
                "/api/auth/register", json={**ADA, "email": f"u{i}@example.com"}
            ).status_code
            for i in range(4)
        ]
    assert codes == [201, 201, 201, 429]


def test_rate_limiter_window_slides():
    limiter = auth.RateLimiter()
    for _ in range(2):
        limiter.record("k")
    assert limiter.retry_after("k", 3, 60) == 0
    limiter.record("k")
    assert 0 < limiter.retry_after("k", 3, 60) <= 61
    assert limiter.retry_after("k", 3, 0) == 0  # the window has passed
    limiter.record("k")
    limiter.reset("k")
    assert limiter.retry_after("k", 1, 60) == 0


# --- protected endpoints and roles ------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_private_routes_reject_requests_without_a_session(anon, method, path):
    assert anon.request(method, path).status_code == 401
    # No ADMIN_API_TOKEN is configured, so no bearer token can work either.
    for token in ("", "anything", "test-admin-token"):
        response = anon.request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token
    anon.cookies.set(auth.SESSION_COOKIE, "forged-session-value")
    assert anon.request(method, path).status_code == 401


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_ordinary_users_reach_only_the_playground_endpoints(user, method, path):
    response = user.request(method, path, json={"question": "What are the contact hours?"})
    if (method, path) in USER_ROUTES:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403 and error(response) == "forbidden"


def test_an_ordinary_user_can_ask_questions_but_not_upload(user):
    upload = user.post("/api/admin/documents", files={"file": ("policy.txt", b"Some policy text.")})
    assert upload.status_code == 403
    answer = user.post("/api/admin/rag/query", json={"question": "What are the contact hours?"})
    assert answer.status_code == 200 and answer.json()["answer_mode"] == "no_evidence"


def test_an_administrator_can_manage_documents_and_users_can_then_query_them(admin, user):
    policy = b"Customers may only be contacted between 8:00 a.m. and 9:00 p.m."
    upload = admin.post("/api/admin/documents", files={"file": ("contact.txt", policy)})
    assert upload.status_code == 202
    assert admin.get("/api/admin/kb/stats").json()["by_status"]["indexed"] == 1
    assert admin.get("/api/admin/health").status_code == 200

    answer = user.post(
        "/api/admin/rag/query", json={"question": "When may customers be contacted?"}
    )
    assert answer.json()["answer_mode"] == "generated"
    assert answer.json()["sources"][0]["filename"] == "contact.txt"
    assert user.delete(f"/api/admin/documents/{upload.json()['id']}").status_code == 403
    assert admin.delete(f"/api/admin/documents/{upload.json()['id']}").status_code == 200


def test_the_api_token_still_works_for_scripts_when_configured(tmp_path):
    token = "a-long-service-token"
    settings = Settings(storage_dir=tmp_path, admin_api_token=token)
    with TestClient(create_app(settings, embedder=HashingEmbedder(), generator=None)) as client:
        assert client.get("/api/admin/documents").status_code == 401
        ok = client.get("/api/admin/documents", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        assert (
            client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )
    with pytest.raises(ValueError, match="ADMIN_API_TOKEN"):
        Settings(storage_dir=tmp_path, admin_api_token="short")


# --- CSRF -------------------------------------------------------------------------------------


def test_state_changing_requests_need_the_csrf_token(app, admin):
    browser = TestClient(app, cookies=dict(admin.cookies))  # has the cookie, not the token
    assert browser.get("/api/admin/documents").status_code == 200  # reads are not affected
    for headers in ({}, {auth.CSRF_HEADER: "guess"}):
        response = browser.post("/api/admin/kb/rebuild", headers=headers)
        assert response.status_code == 403 and error(response) == "csrf_failed"
        logout = browser.post("/api/auth/logout", headers=headers)
        assert logout.status_code == 403
    assert admin.post("/api/admin/kb/rebuild").status_code == 202
    assert admin.get("/api/auth/me").status_code == 200  # the forged logouts did nothing


def test_requests_from_other_origins_are_refused(app, anon, admin):
    evil = {"Origin": "https://evil.example"}
    assert error(anon.post("/api/auth/login", json=ADA, headers=evil)) == "bad_origin"
    assert error(anon.post("/api/auth/register", json=ADA, headers=evil)) == "bad_origin"
    assert error(admin.post("/api/admin/kb/rebuild", headers=evil)) == "bad_origin"
    for origin in ("http://localhost:5173", "http://testserver"):  # configured, and same-origin
        assert admin.post("/api/admin/kb/rebuild", headers={"Origin": origin}).status_code == 202

    preflight = anon.options(
        "/api/admin/kb/rebuild",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token",
        },
    )
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert preflight.headers["access-control-allow-credentials"] == "true"
    blocked = anon.options(
        "/api/admin/kb/rebuild",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in blocked.headers


# --- sign-out and expiry ----------------------------------------------------------------------


def test_logout_invalidates_the_session_on_the_server(app, user):
    stolen = TestClient(app, cookies=dict(user.cookies), headers=dict(user.headers))
    response = user.post("/api/auth/logout")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert f'{auth.SESSION_COOKIE}=""' in response.headers["set-cookie"]
    assert user.get("/api/auth/me").status_code == 401
    # A copy of the old cookie is useless too: the session is gone, not just the cookie.
    assert stolen.get("/api/auth/me").status_code == 401
    assert stolen.post("/api/admin/rag/query", json={"question": "Anything?"}).status_code == 401
    assert user.post("/api/auth/logout").status_code == 200  # signing out twice is harmless


def test_sessions_expire(settings, user):
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
    response = user.get("/api/auth/me")
    assert response.status_code == 401 and error(response) == "unauthorized"
    assert user.post("/api/admin/rag/query", json={"question": "Anything?"}).status_code == 401


# --- the admin command line -------------------------------------------------------------------


def test_cli_promotes_demotes_and_resets_passwords(app, settings, user, monkeypatch, capsys):
    db = Database(settings.db_path)
    assert user.get("/api/admin/documents").status_code == 403

    manage.main(["set-role", "--email", "ADA@example.com", "--role", "admin"], db=db)
    assert user.get("/api/auth/me").status_code == 401  # the old session was ended
    promoted = sign_in(app, ADA["email"])
    assert promoted.get("/api/admin/documents").status_code == 200

    manage.main(["set-role", "--email", ADA["email"], "--role", "user"], db=db)
    assert sign_in(app, ADA["email"]).get("/api/admin/documents").status_code == 403

    session = sign_in(app, ADA["email"])
    monkeypatch.setattr("sys.stdin", io.StringIO("a brand new password\n"))
    manage.main(["reset-password", "--email", ADA["email"]], db=db)
    assert session.get("/api/auth/me").status_code == 401
    old = TestClient(app).post(
        "/api/auth/login", json={"email": ADA["email"], "password": PASSWORD}
    )
    assert old.status_code == 401
    sign_in(app, ADA["email"], "a brand new password")

    manage.main(["list-users"], db=db)
    assert "ada@example.com" in capsys.readouterr().out


def test_cli_refuses_bad_input(settings, anon, monkeypatch):
    db = Database(settings.db_path)
    anon.post("/api/auth/register", json=ADA)
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit, match="at least 8"):
        manage.main(["create-admin", "--email", "root@example.com", "--name", "Root"], db=db)
    with pytest.raises(SystemExit, match="already has an account"):
        manage.main(["create-admin", "--email", ADA["email"], "--name", "Ada"], db=db)
    with pytest.raises(SystemExit, match="No account"):
        manage.main(["set-role", "--email", "ghost@example.com", "--role", "admin"], db=db)
    assert [u["role"] for u in db.list_users()] == ["user"]
