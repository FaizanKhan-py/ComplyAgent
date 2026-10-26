"""LangChain MODELS: builds the chat model, and turns any provider error into one clear error.

    LLM_PROVIDER=azure      AzureChatOpenAI (Azure OpenAI / Azure AI Foundry)   AZURE_AI_*
    LLM_PROVIDER=anthropic  ChatAnthropic (Claude)                               ANTHROPIC_API_KEY
    LLM_PROVIDER=auto       Azure when its settings are complete, else Claude when its key is set,
                            else no model (the API answers with retrieved passages only)
    LLM_PROVIDER=none       no model

Both are LangChain BaseChatModel objects, so the chains and the agent work the same with either.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx
from langchain_core.language_models import BaseChatModel

from ..config import Settings

logger = logging.getLogger("complyagent.llm")

PROVIDER_NAMES = {"azure": "Azure OpenAI", "anthropic": "Claude"}


@dataclass(frozen=True)
class ChatModel:
    provider: str  # "azure" | "anthropic"
    name: str  # the deployment or model name, shown in the UI
    model: BaseChatModel
    # Azure's deployments list: checks the key and the deployment name without spending tokens.
    health_check: object | None = None


def build_chat_model(
    settings: Settings, http_client: httpx.Client | None = None
) -> ChatModel | None:
    """The configured chat model, or None for retrieval-only mode.

    `http_client` lets tests answer the provider's HTTP calls without a network.
    """
    provider = settings.llm_provider
    azure_key = os.getenv("AZURE_AI_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if provider == "auto":
        if azure_key and settings.azure_endpoint and settings.azure_deployment:
            provider = "azure"
        elif anthropic_key:
            provider = "anthropic"
        else:
            return None
    if provider == "none":
        return None

    if provider == "azure":
        missing = [
            name
            for name, value in (
                ("AZURE_AI_ENDPOINT", settings.azure_endpoint),
                ("AZURE_AI_DEPLOYMENT", settings.azure_deployment),
                ("AZURE_AI_API_KEY", azure_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"LLM_PROVIDER=azure needs {', '.join(missing)}")
        from langchain_openai import AzureChatOpenAI

        client = http_client or httpx.Client(timeout=settings.llm_timeout_seconds)
        model = AzureChatOpenAI(
            azure_endpoint=settings.azure_endpoint,
            azure_deployment=settings.azure_deployment,
            api_key=azure_key,
            api_version=settings.azure_api_version,
            temperature=0,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
            http_client=client,
        )
        check = _azure_health(settings.azure_endpoint, azure_key, settings.azure_deployment, client)
        return ChatModel("azure", settings.azure_deployment, model, check)

    if provider == "anthropic":
        if not anthropic_key:
            raise ValueError("LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY")
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=anthropic_key,
            max_tokens=4000,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )
        return ChatModel("anthropic", settings.anthropic_model, model)

    raise ValueError(f"Unknown LLM_PROVIDER '{provider}' (use auto, azure, anthropic or none)")


def classify_error(exc: Exception) -> tuple[str, str, bool]:
    """(code, message, retryable) for an exception raised while calling a chat model.

    LangChain's provider packages raise subclasses of the same langchain_core errors
    (ModelTimeoutError, ModelRateLimitError, ...), so one mapping covers every provider.
    """
    from langchain_core import exceptions as lc

    if isinstance(exc, (lc.ModelTimeoutError, TimeoutError, httpx.TimeoutException)):
        return "llm_timeout", "The model provider timed out.", True
    if isinstance(exc, (lc.ModelConnectionError, httpx.TransportError)):
        return "llm_unreachable", "Could not reach the model provider.", True
    if isinstance(exc, (lc.ModelAuthenticationError, lc.ModelPermissionDeniedError)):
        return "llm_auth", "The model provider's API key or endpoint is wrong.", False
    if isinstance(exc, lc.ModelNotFoundError):
        return "llm_model_not_found", "The configured model or deployment does not exist.", False
    if isinstance(exc, lc.ModelRateLimitError):
        return "llm_rate_limited", "The model provider's rate limit was hit. Try again.", True
    if isinstance(exc, lc.ContextOverflowError):
        return "llm_request_rejected", "The request is too long for the model.", False
    if isinstance(exc, lc.OutputParserException) or type(exc).__name__ in (
        "LengthFinishReasonError",
        "ValidationError",
    ):
        return "llm_bad_output", "The model returned an incomplete answer.", True
    if type(exc).__name__ in ("ContentFilterFinishReasonError", "OpenAIRefusalError"):
        return "llm_refused", "The model or its content filter declined this request.", False
    status = getattr(exc, "status_code", None)
    if status == 400 and "content_filter" in str(exc):
        return "llm_refused", "The provider's content filter blocked this request.", False
    if isinstance(status, int):
        retryable = status >= 500
        return (
            "llm_unavailable" if retryable else "llm_request_rejected",
            f"The model provider returned an error (HTTP {status}).",
            retryable,
        )
    logger.exception("Unexpected model error", exc_info=exc)
    return "llm_unavailable", "The model call failed unexpectedly.", True


def _azure_health(endpoint: str, api_key: str, deployment: str, client: httpx.Client):
    def check() -> tuple[str, str]:
        try:
            response = client.get(
                endpoint.rstrip("/") + "/openai/deployments",
                params={"api-version": "2022-12-01"},
                headers={"api-key": api_key},
                timeout=5.0,
            )
            if response.status_code in (401, 403):
                return "error", "The Azure API key or endpoint is wrong."
            if response.status_code == 404:
                return "ok", ""  # newer Foundry resources do not list deployments
            response.raise_for_status()
            deployments = {d["id"] for d in response.json()["data"]}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return "error", "Azure is not reachable."
        if deployment not in deployments:
            return "error", f"Azure has no deployment named '{deployment}'."
        return "ok", ""

    return check
