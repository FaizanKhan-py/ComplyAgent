from __future__ import annotations

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


@pytest.fixture
def settings(tmp_path) -> Settings:
    # A model folder of its own, so tests never use the real notebook model in backend/models.
    return Settings(
        storage_dir=tmp_path / "storage",
        risk_model_dir=tmp_path / "risk_model",
        admin_api_token=TOKEN,
        max_upload_mb=1,
    )


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
