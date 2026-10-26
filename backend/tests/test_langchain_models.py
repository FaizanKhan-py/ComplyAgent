"""LangChain MODELS and CHAINS against a scripted HTTP transport: the real AzureChatOpenAI client,
the real prompt templates and chains, no network and no key."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.decisions.decision import Recommendation
from app.langchain.llm import LangChainGenerator, LLMError, SourcePassage, build_generator
from app.main import create_app
from app.rag.embeddings import HashingEmbedder

from .conftest import AUTH, TOKEN

SOURCES = [SourcePassage(1, "hardship_assistance_policy.txt", None, "Up to 50 percent.")]
DECISION = {
    "action": "offer_payment_plan",
    "channel": "sms",
    "priority": "high",
    "reasons": [{"text": "Two months late.", "fact_ids": ["F1"], "source_ids": [1]}],
    "needs_human_review": False,
}


@pytest.fixture
def azure_env(monkeypatch):
    monkeypatch.setenv("AZURE_AI_API_KEY", "secret")


def make(handler, tmp_path) -> LangChainGenerator:
    settings = Settings(
        storage_dir=tmp_path,
        llm_provider="azure",
        azure_endpoint="https://azure.test/",
        azure_deployment="gpt-4.1-mini",
    )
    generator = build_generator(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    generator.chat_model.max_retries = 0  # failures surface at once, without back-off
    return generator


def completion(content: dict | str, finish_reason: str = "stop") -> httpx.Response:
    if isinstance(content, dict):
        content = json.dumps(content)
    choice = {
        "index": 0,
        "message": {"role": "assistant", "content": content},
        "finish_reason": finish_reason,
    }
    body = {
        "id": "1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [choice],
    }
    return httpx.Response(200, json=body)


def test_structured_chain_sends_the_prompt_and_a_strict_schema(azure_env, tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"], seen["body"] = request, json.loads(request.content)
        return completion(DECISION)

    # A literal {placeholder} in a system prompt is sent as written, never treated as a variable.
    result = make(handler, tmp_path).structured(
        "Write NT${amount_due}.", "user text", Recommendation
    )
    request, body = seen["request"], seen["body"]
    assert request.url.path == "/openai/deployments/gpt-4.1-mini/chat/completions"
    assert request.url.params["api-version"] == "2024-10-21"
    assert request.headers["api-key"] == "secret"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("system", "Write NT${amount_due}."),
        ("user", "user text"),
    ]
    schema = body["response_format"]["json_schema"]
    assert (schema["name"], schema["strict"]) == ("Recommendation", True)
    assert isinstance(result, Recommendation) and result.action.value == "offer_payment_plan"


def test_the_policy_assistant_chain_sends_its_memory_then_the_question(azure_env, tmp_path):
    seen = {}
    reply = {"answer": "Up to half [1].", "insufficient_evidence": False, "cited_source_ids": [1]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return completion(reply)

    history = [
        {"role": "assistant", "content": "an answer without its question is dropped"},
        {"role": "user", "content": "How long is a plan?"},
        {"role": "assistant", "content": "Up to 6 months [1]."},
    ]
    answer = make(handler, tmp_path).generate("And how much?", SOURCES, history)
    assert (answer.answer, answer.cited_source_ids) == ("Up to half [1].", [1])
    messages = seen["body"]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["content"].startswith("You are ComplyAgent's policy assistant.")
    assert messages[1]["content"] == "How long is a plan?"
    assert '<source id="1"' in messages[3]["content"] and "And how much?" in messages[3]["content"]


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": "x"}})


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(401, json={"error": {"code": "401"}}), "llm_auth", False),
        (error(404, "DeploymentNotFound"), "llm_model_not_found", False),
        (httpx.Response(429, json={"error": {"code": "429"}}), "llm_rate_limited", True),
        (error(400, "content_filter"), "llm_refused", False),
        (error(400, "BadRequest"), "llm_request_rejected", False),
        (httpx.Response(503, json={"error": {"message": "down"}}), "llm_unavailable", True),
        (completion(DECISION, finish_reason="length"), "llm_bad_output", True),
        (completion("not json"), "llm_bad_output", True),
    ],
)  # fmt: skip
def test_failures_map_to_clear_errors(azure_env, tmp_path, response, code, retryable):
    with pytest.raises(LLMError) as info:
        make(lambda request: response, tmp_path).structured("s", "u", Recommendation)
    assert (info.value.code, info.value.retryable) == (code, retryable)


def test_unreachable_azure_is_retryable(azure_env, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError) as info:
        make(handler, tmp_path).structured("s", "u", Recommendation)
    assert (info.value.code, info.value.retryable) == ("llm_unreachable", True)


def test_health_checks_the_deployment_without_generating(azure_env, tmp_path):
    def deployments(*names: str):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET" and request.url.path == "/openai/deployments"
            return httpx.Response(200, json={"data": [{"id": n} for n in names]})

        return handler

    status, detail = make(deployments("gpt-4.1-mini"), tmp_path).health()
    assert status == "ok" and "gpt-4.1-mini (Azure OpenAI via LangChain)" in detail
    status, detail = make(deployments("other"), tmp_path).health()
    assert status == "error" and "gpt-4.1-mini" in detail
    assert make(lambda request: httpx.Response(401), tmp_path).health()[0] == "error"


def test_the_provider_is_picked_from_the_settings(monkeypatch, tmp_path):
    for name in ("AZURE_AI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    azure = {"azure_endpoint": "https://azure.test/", "azure_deployment": "gpt-4.1-mini"}
    assert build_generator(Settings(storage_dir=tmp_path, **azure)) is None  # auto, no key

    monkeypatch.setenv("AZURE_AI_API_KEY", "secret")
    generator = build_generator(Settings(storage_dir=tmp_path, **azure))
    assert (generator.provider, generator.model) == ("azure", "gpt-4.1-mini")
    assert build_generator(Settings(storage_dir=tmp_path, llm_provider="none", **azure)) is None

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    claude = build_generator(Settings(storage_dir=tmp_path, llm_provider="anthropic"))
    assert (claude.provider, claude.model) == ("anthropic", "claude-opus-5-5")


def test_bad_provider_settings_are_refused(monkeypatch, tmp_path):
    monkeypatch.delenv("AZURE_AI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AZURE_AI_ENDPOINT"):
        build_generator(Settings(storage_dir=tmp_path, llm_provider="azure"))
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        build_generator(Settings(storage_dir=tmp_path, llm_provider="ollama"))
    with pytest.raises(ValueError, match="https://"):
        Settings(storage_dir=tmp_path, azure_endpoint="http://azure.test")


def test_an_outage_degrades_the_api_to_retrieved_passages(azure_env, tmp_path):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    settings = Settings(storage_dir=tmp_path / "storage", admin_api_token=TOKEN)
    app = create_app(settings, embedder=HashingEmbedder(), generator=make(down, tmp_path))
    with TestClient(app, headers=AUTH) as client:
        policy = b"A hardship plan may reduce the monthly installment by up to 50 percent."
        assert (
            client.post(
                "/api/admin/documents", files={"file": ("hardship.txt", policy)}
            ).status_code
            == 202
        )
        config = client.get("/api/admin/config").json()
        assert (config["llm_provider"], config["llm_model"]) == ("azure", "gpt-4.1-mini")
        result = client.post(
            "/api/admin/rag/query", json={"question": "How much can a hardship plan reduce?"}
        ).json()
        assert result["answer_mode"] == "retrieval_only" and result["sources"]
        assert "Could not reach the model provider" in result["warning"]
        health = client.get("/api/admin/health").json()
        assert health["components"]["llm"]["status"] == "error" and health["status"] == "degraded"
