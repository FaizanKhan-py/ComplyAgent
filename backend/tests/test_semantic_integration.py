"""Real embedding model, no mocks. Opt-in because it downloads the model on first run:

pytest -m integration
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import BACKEND_DIR, Settings
from app.main import create_app
from app.rag.embeddings import FastEmbedEmbedder

from .conftest import AUTH, SAMPLES, TOKEN, upload

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def embedder():
    return FastEmbedEmbedder("BAAI/bge-small-en-v1.5", BACKEND_DIR / "storage" / "models")


@pytest.fixture
def client(tmp_path, embedder):
    settings = Settings(storage_dir=tmp_path, admin_api_token=TOKEN)
    app = create_app(settings, embedder=embedder, generator=None)
    with TestClient(app, headers=AUTH) as c:
        for path in sorted(SAMPLES.glob("*_policy.*")):
            assert upload(c, path.name, path.read_bytes()).status_code == 202
        yield c


@pytest.mark.parametrize(
    ("question", "filename", "page"),
    [
        # The first question shares almost no keywords with the policy text.
        ("Is it OK to phone someone at 10 at night?", "customer_contact_policy.md", None),
        (
            "Can an automated agent approve a hardship plan?",
            "hardship_assistance_policy.txt",
            None,
        ),
        ("When must an account be handed to a human reviewer?", "escalation_policy.pdf", 2),
    ],
)
def test_semantic_retrieval_finds_the_right_policy(client, question, filename, page):
    result = client.post("/api/admin/rag/query", json={"question": question}).json()
    assert result["sources"], result
    assert result["sources"][0]["filename"] == filename
    assert result["sources"][0]["page_number"] == page


@pytest.mark.parametrize(
    "question",
    [
        "How do I bake sourdough bread?",
        "What is the capital of France?",
        "What is the dress code for office staff?",
    ],
)
def test_unrelated_questions_retrieve_nothing(client, question):
    result = client.post("/api/admin/rag/query", json={"question": question}).json()
    assert result["sources"] == [] and result["answer_mode"] == "no_evidence"
