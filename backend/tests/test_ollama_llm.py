"""The Ollama answer generator, against a scripted HTTP transport (no Ollama server needed)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.llm import (
    SYSTEM_PROMPT,
    LLMError,
    OllamaAnswerGenerator,
    SourcePassage,
    build_generator,
)

SOURCES = [SourcePassage(1, "hardship_assistance_policy.txt", None, "Up to 50 percent.")]
REPLY = {
    "answer": " Up to 50 percent [1]. ",
    "insufficient_evidence": False,
    "cited_source_ids": [1],
}


def make(handler) -> OllamaAnswerGenerator:
    return OllamaAnswerGenerator(
        "llama3.2", "http://ollama.test", 5.0, transport=httpx.MockTransport(handler)
    )


def chat_reply(content: str, done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}, "done_reason": done_reason})


def test_generate_sends_the_prompt_and_schema_and_parses_the_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return chat_reply(json.dumps(REPLY))

    history = [
        {"role": "assistant", "content": "orphan reply"},
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    generator = make(handler)
    result = generator.generate("How much?", SOURCES, history)

    assert result.answer == "Up to 50 percent [1]."
    assert result.insufficient_evidence is False and result.cited_source_ids == [1]
    body = seen["body"]
    assert seen["path"] == "/api/chat" and body["model"] == "llama3.2" and body["stream"] is False
    assert set(body["format"]["properties"]) == set(REPLY)
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert '<source id="1"' in body["messages"][-1]["content"]
    assert generator._last_success is not None


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(404, json={"error": "model not found"}), "llm_model_not_found", False),
        (httpx.Response(500, text="boom"), "llm_unavailable", True),
        (httpx.Response(400, text="bad"), "llm_request_rejected", False),
        (chat_reply("not json"), "llm_bad_output", True),
        (chat_reply(json.dumps({"answer": "x"})), "llm_bad_output", True),
        (chat_reply(json.dumps(REPLY), done_reason="length"), "llm_bad_output", True),
    ],
)
def test_generate_maps_failures_to_llm_errors(response, code, retryable):
    generator = make(lambda request: response)
    with pytest.raises(LLMError) as raised:
        generator.generate("How much?", SOURCES, [])
    assert raised.value.code == code and raised.value.retryable is retryable
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


def test_health_reports_whether_the_model_is_pulled():
    def tags(*names):
        return lambda request: httpx.Response(200, json={"models": [{"name": n} for n in names]})

    assert make(tags("llama3.2:latest")).health() == ("ok", "llama3.2 (Ollama) is available")
    status, detail = make(tags("mistral:latest")).health()
    assert status == "error" and "not pulled" in detail

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    status, detail = make(down).health()
    assert status == "error" and "not reachable" in detail


def test_build_generator_selects_ollama(tmp_path):
    settings = Settings(
        storage_dir=tmp_path,
        llm_provider="ollama",
        ollama_model="llama3.2:3b",
        ollama_base_url="http://ollama.test",
    )
    generator = build_generator(settings)
    assert isinstance(generator, OllamaAnswerGenerator) and generator.model == "llama3.2:3b"
