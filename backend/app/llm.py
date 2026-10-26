"""The language model, as the rest of the backend sees it: one object, LangChainGenerator.

    generate(question, sources, history)  the policy assistant's answer, with [n] citations
    structured(system, user, schema)      a reply that must match a Pydantic schema (the
                                          Decision Agent and the message writer use it)
    health()                              for the admin health page
    chat_model                            the LangChain model itself, for the Collections Agent

Inside, it is built from app/langchain: the model (models.py), the prompt templates
(prompts.py), the LCEL chains (chains.py) and the chat memory (memory.py). Every failure becomes
an LLMError with a message that is safe to show an administrator.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel

from .config import Settings
from .langchain import chains, memory
from .langchain.models import PROVIDER_NAMES, build_chat_model, classify_error

logger = logging.getLogger("complyagent.llm")

_Schema = TypeVar("_Schema", bound=BaseModel)


class LLMError(Exception):
    """Answer generation failed. `message` is safe to show to an administrator."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class SourcePassage:
    number: int
    filename: str
    page_number: int | None
    text: str


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    insufficient_evidence: bool
    cited_source_ids: list[int]


class AnswerGenerator(Protocol):
    provider: str
    model: str

    def generate(
        self, question: str, sources: list[SourcePassage], history: list[dict[str, str]]
    ) -> GeneratedAnswer: ...
    def structured(self, system: str, user: str, schema: type[_Schema]) -> _Schema: ...
    def health(self) -> tuple[str, str]: ...


def build_user_message(question: str, sources: list[SourcePassage]) -> str:
    """Passages are escaped so document text cannot close its own <source> tag."""
    blocks = []
    for s in sources:
        page = f' page="{s.page_number}"' if s.page_number is not None else ""
        name = html.escape(s.filename, quote=True)
        blocks.append(
            f'<source id="{s.number}" document="{name}"{page}>\n'
            f"{html.escape(s.text, quote=False)}\n</source>"
        )
    return "<sources>\n" + "\n".join(blocks) + f"\n</sources>\n\nQuestion: {question}"


class LangChainGenerator:
    def __init__(self, provider: str, model: str, chat_model, health_check=None) -> None:
        self.provider = provider
        self.model = model
        self.chat_model = chat_model
        self._health_check = health_check  # () -> (status, detail); spends no tokens
        self._answer_chain = chains.policy_answer_chain(chat_model, provider)
        self._last_error: str | None = None
        self._last_success: str | None = None

    def generate(
        self, question: str, sources: list[SourcePassage], history: list[dict[str, str]]
    ) -> GeneratedAnswer:
        parsed = self._run(
            self._answer_chain,
            {
                "history": memory.chat_history(history),
                "request": build_user_message(question, sources),
            },
            chains.PolicyAnswer,
        )
        return GeneratedAnswer(
            parsed.answer.strip(), parsed.insufficient_evidence, parsed.cited_source_ids
        )

    def structured(self, system: str, user: str, schema: type[_Schema]) -> _Schema:
        """One request whose reply must match `schema`."""
        chain = chains.structured_chain(self.chat_model, self.provider, system, schema)
        return self._run(chain, {"request": user}, schema)

    def _run(self, chain, inputs: dict, schema: type[_Schema]) -> _Schema:
        try:
            parsed = chains.run(chain, inputs, schema)
        except chains.IncompleteAnswer as exc:
            raise self._fail(
                "llm_bad_output", "The model returned an incomplete answer.", True
            ) from exc
        except Exception as exc:
            raise self._fail(*classify_error(exc)) from exc
        self._last_error = None
        self._last_success = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return parsed

    def _fail(self, code: str, message: str, retryable: bool = False) -> LLMError:
        self._last_error = message
        logger.warning("Answer generation failed: %s", code)
        return LLMError(code, message, retryable)

    def health(self) -> tuple[str, str]:
        label = f"{self.model} ({PROVIDER_NAMES.get(self.provider, self.provider)} via LangChain)"
        if self._health_check:
            status, detail = self._health_check()
            if status != "ok":
                return status, detail
        if self._last_error:
            return "error", f"{label}: last request failed. {self._last_error}"
        if self._last_success:
            return "ok", f"{label}: last request succeeded at {self._last_success}"
        return "ok", f"{label} is configured"


def build_generator(
    settings: Settings, http_client: httpx.Client | None = None
) -> AnswerGenerator | None:
    """None means retrieval-only mode: passages are returned without a generated answer."""
    chat = build_chat_model(settings, http_client)
    if chat is None:
        return None
    return LangChainGenerator(chat.provider, chat.name, chat.model, chat.health_check)
