from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.rag.embeddings import HashingEmbedder

from .helpers import FakeGenerator

SAMPLES = Path(__file__).resolve().parents[2] / "sample_policies"
TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# Set TEST_DATABASE_URL to run the tests against PostgreSQL: each test gets a schema of its
# own, dropped afterwards. Without it they use SQLite files in tmp_path.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


@pytest.fixture
def settings(tmp_path):
    # A model folder of its own, so tests never use the real notebook model in backend/models.
    schema = f"test_{uuid.uuid4().hex[:12]}" if TEST_DATABASE_URL else ""
    yield Settings(
        storage_dir=tmp_path / "storage",
        risk_model_dir=tmp_path / "risk_model",
        admin_api_token=TOKEN,
        max_upload_mb=1,
        database_url=TEST_DATABASE_URL,
        database_schema=schema,
    )
    if schema:
        from app.core import pg

        pg.drop_schema(TEST_DATABASE_URL, schema)


@pytest.fixture(autouse=True)
def office_hours(monkeypatch):
    """Every test runs at 10:00 customer time on a working day, inside the contact hours, so
    the contact rules only refuse what a test makes them refuse."""
    from datetime import datetime, timedelta, timezone

    from app.decisions import contact_rules

    taipei = timezone(timedelta(hours=8))
    fixed = datetime.now(taipei).replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(contact_rules, "now", lambda: fixed.astimezone(timezone.utc))
    return fixed


@pytest.fixture
def generator() -> FakeGenerator:
    return FakeGenerator()


@pytest.fixture
def client(settings, generator):
    """Real API and pipeline; only the embedding model and the LLM are replaced (no network)."""
    app = create_app(settings, embedder=HashingEmbedder(), generator=generator)
    with TestClient(app, headers=AUTH) as c:
        yield c


def upload(client, name: str, data: bytes, content_type: str = "application/octet-stream"):
    return client.post("/api/admin/documents", files={"file": (name, data, content_type)})


@pytest.fixture
def indexed(client):
    """The three sample policies, uploaded and indexed. Returns {filename: document}."""
    docs = {}
    for path in sorted(SAMPLES.glob("*_policy.*")):
        response = upload(client, path.name, path.read_bytes())
        assert response.status_code == 202, response.text
        doc = client.get(f"/api/admin/documents/{response.json()['id']}").json()
        assert doc["status"] == "indexed", doc
        docs[path.name] = doc
    return docs
