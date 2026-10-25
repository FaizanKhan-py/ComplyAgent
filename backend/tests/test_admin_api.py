import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.embeddings import HashingEmbedder
from app.main import create_app

from .conftest import upload

ADMIN_ROUTES = [
    ("get", "/api/admin/session"),
    ("get", "/api/admin/health"),
    ("get", "/api/admin/config"),
    ("get", "/api/admin/documents"),
    ("get", "/api/admin/documents/abc"),
    ("delete", "/api/admin/documents/abc"),
    ("post", "/api/admin/documents/abc/reindex"),
    ("post", "/api/admin/documents"),
    ("get", "/api/admin/activity"),
    ("get", "/api/admin/kb/stats"),
    ("post", "/api/admin/kb/rebuild"),
    ("get", "/api/admin/portfolio/summary"),
    ("get", "/api/admin/customers"),
    ("post", "/api/admin/customers"),
    ("get", "/api/admin/customers/1"),
    ("post", "/api/admin/customers/1/guidance"),
    ("post", "/api/admin/customers/1/recommendation"),
    ("post", "/api/admin/customers/1/message"),
    ("get", "/api/admin/predict/model"),
    ("post", "/api/admin/predict/dataset"),
    ("post", "/api/admin/rag/query"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_admin_routes_require_the_token(client, method, path):
    for headers in (
        {"Authorization": ""},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "x"},
    ):
        response = client.request(method, path, headers=headers)
        assert response.status_code == 401, (method, path)
        assert response.json()["error"]["code"] == "unauthorized"


def test_every_admin_route_is_covered_by_the_auth_test(client):
    routes = {
        (method.lower(), route.path.replace("{doc_id}", "abc").replace("{customer_id}", "1"))
        for route in client.app.routes
        if getattr(route, "path", "").startswith("/api/admin")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    assert routes == set(ADMIN_ROUTES)


def test_public_endpoints_need_no_token_and_expose_no_detail(client):
    health = client.get("/api/health", headers={"Authorization": ""})
    assert health.status_code == 200 and set(health.json()) == {"status"}
    status = client.get("/api/auth/status", headers={"Authorization": ""})
    assert status.json() == {"auth_required": True, "registration_open": True}
    assert client.get("/api/admin/session").json() == {"ok": True}


def test_without_a_token_or_a_session_nothing_is_open(tmp_path):
    app = create_app(Settings(storage_dir=tmp_path), embedder=HashingEmbedder(), generator=None)
    with TestClient(app) as c:
        assert c.get("/api/auth/status").json()["auth_required"] is True
        assert c.get("/api/admin/documents").status_code == 401


def test_stats_for_empty_then_populated_knowledge_base(client):
    empty = client.get("/api/admin/kb/stats").json()
    assert empty["status"] == "empty"
    assert empty["total_documents"] == 0 and empty["total_chunks"] == 0
    assert empty["last_indexed_at"] is None

    upload(client, "a.txt", b"Late fees are waived during a hardship plan.")
    upload(client, "b.pdf", b"%PDF-1.4 broken")
    stats = client.get("/api/admin/kb/stats").json()
    assert stats["status"] == "degraded"  # one indexed, one failed
    assert stats["total_documents"] == 2
    assert stats["by_status"] == {"pending": 0, "processing": 0, "indexed": 1, "failed": 1}
    assert stats["by_file_type"] == {"txt": 1, "pdf": 1}
    assert stats["total_chunks"] == stats["searchable_chunks"] == 1
    assert stats["embedding_model"] == HashingEmbedder().name and stats["last_indexed_at"]


def test_chunks_from_another_embedding_model_are_reported_stale(client, indexed):
    with client.app.state.kb.db.connect() as conn:
        conn.execute("UPDATE chunks SET embedding_model = 'old-model'")
        conn.execute("UPDATE documents SET embedding_model = 'old-model'")
    stats = client.get("/api/admin/kb/stats").json()
    assert stats["status"] == "unavailable"
    assert stats["searchable_chunks"] == 0 and stats["stale_documents"] == 4
    client.post("/api/admin/kb/rebuild")
    stats = client.get("/api/admin/kb/stats").json()
    assert stats["status"] == "ready" and stats["stale_documents"] == 0


def test_health_reports_each_dependency(client):
    health = client.get("/api/admin/health").json()
    assert set(health["components"]) == {"database", "storage", "embeddings", "llm"}
    assert health["components"]["database"]["status"] == "ok"
    assert health["components"]["storage"]["status"] == "ok"
    assert health["status"] == "degraded"  # the hashing embedder reports itself as a fallback


def test_config_exposes_limits_but_no_secrets(client, settings):
    config = client.get("/api/admin/config").json()
    assert config["supported_extensions"] == [".docx", ".md", ".pdf", ".txt"]
    assert config["max_upload_mb"] == 1 and config["llm_model"] == "fake-llm"
    assert settings.admin_api_token not in str(config)


def test_activity_log_records_the_document_lifecycle(client):
    doc_id = upload(client, "a.txt", b"Late fees are waived during a hardship plan.").json()["id"]
    client.post(f"/api/admin/documents/{doc_id}/reindex")
    client.delete(f"/api/admin/documents/{doc_id}")
    events = client.get("/api/admin/activity").json()["events"]
    assert [e["event"] for e in events] == ["deleted", "indexed", "reindex", "indexed", "uploaded"]
    assert all(e["filename"] == "a.txt" for e in events)
    assert len(client.get("/api/admin/activity?limit=2").json()["events"]) == 2


def test_errors_use_one_json_shape(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "http_error", "message": "Not Found"}}
    assert client.get("/api/admin/activity?limit=0").json()["error"]["code"] == "invalid_request"
