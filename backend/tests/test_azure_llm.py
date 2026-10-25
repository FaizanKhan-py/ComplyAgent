"""The Azure answer generator, against a scripted HTTP transport (no network or key needed)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.decision import Recommendation
from app.llm import (
    AzureAnswerGenerator,
    LLMError,
    SourcePassage,
    _strict_schema,
    build_generator,
)

SOURCES = [SourcePassage(1, "hardship_assistance_policy.txt", None, "Up to 50 percent.")]
DECISION = {
    "action": "offer_payment_plan",
    "channel": "sms",
    "priority": "high",
    "reasons": [{"text": "Two months late.", "fact_ids": ["F1"], "source_ids": [1]}],
    "needs_human_review": False,
}


def make(handler) -> AzureAnswerGenerator:
    return AzureAnswerGenerator(
        "gpt-4.1-mini", "https://azure.test/", "secret", 5.0, transport=httpx.MockTransport(handler)
    )


def completion(content: dict | str, finish_reason: str = "stop") -> httpx.Response:
    if isinstance(content, dict):
        content = json.dumps(content)
    choice = {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
    return httpx.Response(200, json={"choices": [choice]})


def test_structured_sends_key_and_strict_schema_and_parses_the_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["body"] = json.loads(request.content)
        return completion(DECISION)

    result = make(handler).structured("system text", "user text", Recommendation)
    request, body = seen["request"], seen["body"]
    assert request.url.path == "/openai/deployments/gpt-4.1-mini/chat/completions"
    assert request.url.params["api-version"] == "2024-10-21"
    assert request.headers["api-key"] == "secret"
    assert body["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]
    schema = body["response_format"]["json_schema"]
    assert (schema["name"], schema["strict"]) == ("Recommendation", True)
    assert isinstance(result, Recommendation) and result.action.value == "offer_payment_plan"


def test_generate_answers_the_policy_assistant():
    reply = {"answer": "Up to half [1].", "insufficient_evidence": False, "cited_source_ids": [1]}
    answer = make(lambda request: completion(reply)).generate("How much?", SOURCES, [])
    assert (answer.answer, answer.cited_source_ids) == ("Up to half [1].", [1])


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(401, json={"error": {"code": "401"}}), "llm_auth", False),
        (
            httpx.Response(404, json={"error": {"code": "DeploymentNotFound"}}),
            "llm_model_not_found",
            False,
        ),
        (httpx.Response(429, json={"error": {"code": "429"}}), "llm_rate_limited", True),
        (httpx.Response(400, json={"error": {"code": "content_filter"}}), "llm_refused", False),
        (
            httpx.Response(400, json={"error": {"code": "BadRequest"}}),
            "llm_request_rejected",
            False,
        ),
        (httpx.Response(503), "llm_unavailable", True),
        (completion(DECISION, finish_reason="length"), "llm_bad_output", True),
        (completion("not json"), "llm_bad_output", True),
    ],
)
def test_failures_map_to_clear_errors(response, code, retryable):
    generator = make(lambda request: response)
    with pytest.raises(LLMError) as info:
        generator.structured("s", "u", Recommendation)
    assert (info.value.code, info.value.retryable) == (code, retryable)


def test_unreachable_azure_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError) as info:
        make(handler).structured("s", "u", Recommendation)
    assert (info.value.code, info.value.retryable) == ("llm_unreachable", True)


def test_health_checks_the_deployment_without_generating():
    def deployments(*names: str):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET" and request.url.path == "/openai/deployments"
            return httpx.Response(200, json={"data": [{"id": n} for n in names]})

        return handler

    assert make(deployments("gpt-4.1-mini")).health() == ("ok", "gpt-4.1-mini (Azure) is deployed")
    status, detail = make(deployments("other")).health()
    assert status == "error" and "gpt-4.1-mini" in detail
    assert make(lambda request: httpx.Response(401)).health()[0] == "error"


def test_strict_schema_forbids_extra_keys_in_every_object():
    schema = _strict_schema(Recommendation)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Reason"]["additionalProperties"] is False


def test_build_generator_picks_azure(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_AI_API_KEY", "secret")
    settings = Settings(
        storage_dir=tmp_path,
        llm_provider="azure",
        azure_endpoint="https://azure.test/",
        azure_deployment="gpt-4.1-mini",
    )
    generator = build_generator(settings)
    assert isinstance(generator, AzureAnswerGenerator) and generator.model == "gpt-4.1-mini"
