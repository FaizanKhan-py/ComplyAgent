"""The Ollama answer generator, against a scripted HTTP transport (no Ollama server needed)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.embeddings import HashingEmbedder
from app.llm import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    SYSTEM_PROMPT,
    LLMError,
    OllamaAnswerGenerator,
    SourcePassage,
    build_generator,
)
from app.main import create_app

from .conftest import AUTH, TOKEN

SOURCES = [
    SourcePassage(1, "hardship_assistance_policy.txt", None, "Up to 50 percent."),
    SourcePassage(2, "hardship_assistance_policy.txt", None, "For six months."),
]
REPLY = {
    "statements": [
        {"text": " A plan may cut the installment by up to 50 percent. ", "source_ids": [1]},
        {"text": "It lasts six months [7].", "source_ids": [2, 1, 2]},
        {"text": "  ", "source_ids": [2]},
        {"text": "Ask a supervisor for anything else.", "source_ids": []},
    ],
    "insufficient_evidence": False,
}


def make(handler, **kwargs) -> OllamaAnswerGenerator:
    return OllamaAnswerGenerator(
        "llama3.2", "http://ollama.test", 5.0, transport=httpx.MockTransport(handler), **kwargs
    )


def chat_reply(content: str | dict, done_reason: str = "stop") -> httpx.Response:
    if isinstance(content, dict):
        content = json.dumps(content)
    return httpx.Response(200, json={"message": {"content": content}, "done_reason": done_reason})


def tags(*models: dict) -> httpx.Response:
    return httpx.Response(200, json={"models": list(models)})


def test_generate_sends_the_prompt_and_schema_and_builds_a_cited_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return chat_reply(REPLY)

    history = [
        {"role": "assistant", "content": "orphan reply"},
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    generator = make(handler)
    result = generator.generate("How much?", SOURCES, history)

    # Markers come from each statement's own sources; ones the model typed itself are removed.
    assert result.answer == (
        "A plan may cut the installment by up to 50 percent. [1] "
        "It lasts six months. [1][2] Ask a supervisor for anything else."
    )
    assert result.insufficient_evidence is False and result.cited_source_ids == [1, 2]

    body = seen["body"]
    assert seen["path"] == "/api/chat" and body["model"] == "llama3.2" and body["stream"] is False
    assert set(body["format"]["properties"]) == {"statements", "insufficient_evidence"}
    assert body["options"] == {"temperature": 0}
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert '<source id="1"' in body["messages"][-1]["content"]
    assert generator._last_success is not None


def test_num_ctx_is_sent_only_when_configured():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["options"] = json.loads(request.content)["options"]
        return chat_reply(REPLY)

    make(handler, num_ctx=8192).generate("How much?", SOURCES, [])
    assert seen["options"] == {"temperature": 0, "num_ctx": 8192}


def test_insufficient_evidence_cites_nothing_and_always_has_text():
    empty = {"statements": [], "insufficient_evidence": True}
    result = make(lambda request: chat_reply(empty)).generate("How much?", SOURCES, [])
    assert result.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert result.insufficient_evidence is True and result.cited_source_ids == []

    uncited = {
        "statements": [{"text": "The policies do not state a penalty.", "source_ids": []}],
        "insufficient_evidence": True,
    }
    result = make(lambda request: chat_reply(uncited)).generate("How much?", SOURCES, [])
    assert result.answer == "The policies do not state a penalty."
    assert result.insufficient_evidence is True and result.cited_source_ids == []


def test_a_partial_answer_that_cites_sources_is_not_reported_as_insufficient():
    partial = {
        "statements": [{"text": "Interest accrues at the contract rate.", "source_ids": [1]}],
        "insufficient_evidence": True,
    }
    result = make(lambda request: chat_reply(partial)).generate("What rate?", SOURCES, [])
    assert result.answer == "Interest accrues at the contract rate. [1]"
    assert result.insufficient_evidence is False and result.cited_source_ids == [1]


@pytest.mark.parametrize(
    ("response", "code", "retryable", "message_part"),
    [
        (
            httpx.Response(404, json={"error": "model 'llama3.2' not found"}),
            "llm_model_not_found",
            False,
            "ollama pull llama3.2",
        ),
        (httpx.Response(500, text="boom"), "llm_unavailable", True, "HTTP 500"),
        (
            httpx.Response(400, json={"error": '"nomic-embed-text" does not support chat'}),
            "llm_request_rejected",
            False,
            "does not support chat",
        ),
        (chat_reply("not json"), "llm_bad_output", True, "incomplete"),
        (chat_reply({"answer": "old shape"}), "llm_bad_output", True, "incomplete"),
        (
            chat_reply({"statements": [], "insufficient_evidence": False}),
            "llm_bad_output",
            True,
            "incomplete",
        ),
        (chat_reply(REPLY, done_reason="length"), "llm_bad_output", True, "incomplete"),
    ],
)
def test_generate_maps_failures_to_llm_errors(response, code, retryable, message_part):
    generator = make(lambda request: response)
    with pytest.raises(LLMError) as raised:
        generator.generate("How much?", SOURCES, [])
    assert raised.value.code == code and raised.value.retryable is retryable
    assert message_part in raised.value.message
    assert generator._last_error == raised.value.message


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ConnectError("refused"), "llm_unreachable"),
        (httpx.ReadTimeout("slow"), "llm_timeout"),
    ],
)
def test_generate_maps_connection_problems(error, code):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(LLMError) as raised:
        make(handler).generate("How much?", SOURCES, [])
    assert raised.value.code == code and raised.value.retryable is True


def test_health_reports_the_real_state_of_ollama():
    chat_model = {"name": "llama3.2:latest", "capabilities": ["completion", "tools"]}
    assert make(lambda r: tags(chat_model)).health() == ("ok", "llama3.2 (Ollama) is available")
    # Older Ollama versions do not list capabilities.
    assert make(lambda r: tags({"name": "llama3.2:latest"})).health()[0] == "ok"

    status, detail = make(lambda r: tags({"name": "mistral:latest"})).health()
    assert status == "error" and "ollama pull llama3.2" in detail

    embedding_only = {"name": "llama3.2:latest", "capabilities": ["embedding"]}
    status, detail = make(lambda r: tags(embedding_only)).health()
    assert status == "error" and "not a chat model" in detail

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    status, detail = make(down).health()
    assert status == "error" and "not reachable at http://ollama.test" in detail


def test_health_shows_the_last_failure_then_recovers():
    replies = [httpx.Response(500, text="boom"), chat_reply(REPLY)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return tags({"name": "llama3.2:latest"})
        return replies.pop(0)

    generator = make(handler)
    with pytest.raises(LLMError):
        generator.generate("How much?", SOURCES, [])
    status, detail = generator.health()
    assert status == "error" and "last request failed" in detail
    generator.generate("How much?", SOURCES, [])
    assert generator.health()[0] == "ok"


def test_build_generator_selects_ollama(tmp_path):
    settings = Settings(
        storage_dir=tmp_path,
        llm_provider="ollama",
        ollama_model="llama3.2:3b",
        ollama_base_url="http://ollama.test",
        ollama_num_ctx=8192,
    )
    generator = build_generator(settings)
    assert isinstance(generator, OllamaAnswerGenerator) and generator.model == "llama3.2:3b"
    assert generator._options["num_ctx"] == 8192


def test_ollama_settings_are_read_from_the_environment_and_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "Ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "")
    settings = Settings.from_env()
    assert (settings.llm_provider, settings.ollama_base_url) == ("ollama", "http://gpu-box:11434")
    assert settings.ollama_model == "mistral" and settings.ollama_num_ctx is None

    with pytest.raises(ValueError, match="OLLAMA_BASE_URL"):
        Settings(storage_dir=tmp_path, ollama_base_url="localhost:11434")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        build_generator(Settings(storage_dir=tmp_path, llm_provider="olama"))


def test_ollama_outage_degrades_the_api_to_retrieved_passages(tmp_path):
    """Through the real API: an unreachable Ollama never breaks a query or the health page."""

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    settings = Settings(storage_dir=tmp_path / "storage", admin_api_token=TOKEN)
    app = create_app(settings, embedder=HashingEmbedder(), generator=make(down))
    with TestClient(app, headers=AUTH) as client:
        policy = b"A hardship plan may reduce the monthly installment by up to 50 percent."
        upload = client.post("/api/admin/documents", files={"file": ("hardship.txt", policy)})
        assert upload.status_code == 202

        config = client.get("/api/admin/config").json()
        assert (config["llm_provider"], config["llm_model"]) == ("ollama", "llama3.2")

        result = client.post(
            "/api/admin/rag/query", json={"question": "How much can a hardship plan reduce?"}
        ).json()
        assert result["answer_mode"] == "retrieval_only" and result["sources"]
        assert "Could not reach Ollama at http://ollama.test" in result["warning"]

        health = client.get("/api/admin/health").json()
        assert health["components"]["llm"]["status"] == "error"
        assert health["status"] == "degraded"  # retrieval still works, so not "error"
