"""LangChain CHAINS: the LCEL pipelines the app runs, all of the form

    prompt template  |  chat model with structured output (a Pydantic schema)

    policy_answer_chain   the policy assistant: memory + <source> passages -> PolicyAnswer
    structured_chain      one system prompt + one request -> any schema; the Case Analyst,
                          the Decision Agent, the message writer and the Red Team use it
    conversation_chain    the same, after an email thread (memory.email_thread): the
                          Communication Agent replying to a customer

`include_raw=True` makes a reply that does not fit the schema come back as `parsing_error`
instead of raising, so run() reports it like any other incomplete answer.
"""

from __future__ import annotations

from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from .prompts import conversation_template, policy_assistant_template, single_request_template

Schema = TypeVar("Schema", bound=BaseModel)


class PolicyAnswer(BaseModel):
    answer: str = Field(description="The answer, with [n] citations to the source passages.")
    insufficient_evidence: bool = Field(
        description="True when the passages do not contain enough information to answer."
    )
    cited_source_ids: list[int] = Field(description="Numbers of the passages relied on.")


class IncompleteAnswer(Exception):
    """The model's reply did not match the schema (cut off, or not valid JSON)."""


def _structured(model: BaseChatModel, provider: str, schema: type[BaseModel]) -> Runnable:
    # Both providers constrain the reply to the JSON schema (Azure strictly). Claude's default,
    # forced tool calling, does not work on current Claude models.
    options = {
        "azure": {"method": "json_schema", "strict": True},
        "anthropic": {"method": "json_schema"},
    }.get(provider, {})
    return model.with_structured_output(schema, include_raw=True, **options)


def policy_answer_chain(model: BaseChatModel, provider: str) -> Runnable:
    """Inputs: history (chat messages, from memory.py) and request (the passages and question)."""
    return policy_assistant_template() | _structured(model, provider, PolicyAnswer)


def structured_chain(
    model: BaseChatModel, provider: str, system_prompt: str, schema: type[BaseModel]
) -> Runnable:
    """Input: request (the facts, emails and passages the prompt describes)."""
    return single_request_template(system_prompt) | _structured(model, provider, schema)


def conversation_chain(
    model: BaseChatModel, provider: str, system_prompt: str, schema: type[BaseModel]
) -> Runnable:
    """Inputs: history (the email thread as chat messages) and request."""
    return conversation_template(system_prompt) | _structured(model, provider, schema)


def run(chain: Runnable, inputs: dict, schema: type[Schema]) -> Schema:
    """Invoke a structured chain and return the parsed object. Raises IncompleteAnswer when the
    reply does not match the schema; provider errors propagate for models.classify_error."""
    result = chain.invoke(inputs)
    result = result if isinstance(result, dict) else {}
    parsed = result.get("parsed")
    if result.get("parsing_error") or not isinstance(parsed, schema):
        raise IncompleteAnswer(str(result.get("parsing_error") or "no parsed reply"))
    return parsed
