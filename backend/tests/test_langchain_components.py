"""LangChain PROMPTS, MEMORY, INDEXES and AGENTS, through the real app.

The agent runs on a scripted chat model: it returns tool calls and then an answer, exactly as a
real model would, so the real create_agent loop, the real tools and the real memory are tested.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.langchain import prompts
from app.langchain.indexes import PolicyRetriever, format_documents
from app.langchain.memory import AgentMemory, chat_history

from .test_guidance import load

URL = "/api/admin/agent/chat"


class ScriptedToolModel(BaseChatModel):
    """Replies with the next scripted message and records every conversation it was shown."""

    replies: list[Any]
    seen: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.seen.append([t.name for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def call(name: str, args: dict, call_id: str = "1") -> AIMessage:
    return AIMessage(
        "", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


# --- prompts ----------------------------------------------------------------------------------


def test_every_prompt_lives_in_one_module_and_keeps_its_placeholders():
    from app.decisions import decision, messages

    assert decision.DECISION_PROMPT is prompts.DECISION_PROMPT
    assert messages.MESSAGE_PROMPT is prompts.MESSAGE_PROMPT
    # {amount_due} is text the model must write, not a template variable.
    template = prompts.single_request_template(prompts.MESSAGE_PROMPT)
    assert template.input_variables == ["request"]
    rendered = template.invoke({"request": "Write it."}).to_messages()
    assert "{amount_due}" in rendered[0].content and rendered[1].content == "Write it."


# --- memory -----------------------------------------------------------------------------------


def test_chat_history_keeps_the_last_turns_and_starts_with_a_question():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(9)
    ]
    messages = chat_history(history)
    assert len(messages) == 5 and isinstance(messages[0], HumanMessage)
    assert [m.content for m in messages] == ["m4", "m5", "m6", "m7", "m8"]
    assert chat_history([{"role": "assistant", "content": "orphan"}]) == []


def test_agent_memory_forgets_the_oldest_conversations():
    memory = AgentMemory(limit=2)
    first, config = memory.thread(None)
    assert config == {"configurable": {"thread_id": first}}
    memory.thread("b")
    memory.thread("c")
    assert not memory.knows(first) and memory.knows("b") and memory.knows("c")


# --- indexes ----------------------------------------------------------------------------------


def test_the_retriever_returns_policy_passages_as_documents(client, indexed):
    retriever = PolicyRetriever(knowledge_base=client.app.state.kb, k=2)
    documents = retriever.invoke("How much can a hardship plan reduce the monthly installment?")
    assert len(documents) == 2
    assert documents[0].metadata["filename"] == "hardship_assistance_policy.txt"
    assert {"score", "page_number", "document_id", "chunk_index"} <= set(documents[0].metadata)
    text = format_documents(documents)
    assert text.startswith('<source id="1" document="hardship_assistance_policy.txt"')
    assert format_documents([]) == "No indexed policy passage matches this query."


# --- agents -----------------------------------------------------------------------------------


def test_the_agent_calls_tools_answers_and_remembers_the_conversation(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    model = ScriptedToolModel(
        replies=[
            call("customer_overview", {"customer_id": 1}),
            call("search_policies", {"query": "hardship plan"}, "2"),
            AIMessage("Customer 1 is 2 months late; the hardship policy allows a plan."),
            AIMessage("Yes, the same customer."),
        ]
    )
    generator.chat_model = model

    first = client.post(URL, json={"message": "What can we offer customer 1?"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["answer"] == "Customer 1 is 2 months late; the hardship policy allows a plan."
    assert [s["tool"] for s in body["steps"]] == ["customer_overview", "search_policies"]
    assert body["steps"][0]["input"] == '{"customer_id": 1}' and body["model"] == "fake-llm"
    assert model.seen[0] == [
        "search_policies",
        "customer_overview",
        "payment_history",
        "list_overdue_customers",
        "portfolio_summary",
        "recommend_next_action",
    ]
    # What the tools gave the model: facts and policy text, never protected data or the address.
    tool_results = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert "The account is 2 months late" in tool_results[0]
    assert "hardship_assistance_policy.txt" in tool_results[1]
    shown = " ".join(tool_results).lower()
    assert not any(word in shown for word in ("female", "university", "married", "@"))

    second = client.post(
        URL, json={"message": "Same one?", "conversation_id": body["conversation_id"]}
    )
    assert second.json()["answer"] == "Yes, the same customer." and second.json()["steps"] == []
    # Memory: the second turn was shown the whole first conversation.
    remembered = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)]
    assert remembered == ["What can we offer customer 1?", "Same one?"]


def test_the_agent_tools_report_unknown_customers_and_overdue_lists(client, generator, tmp_path):
    load(client, tmp_path)
    model = ScriptedToolModel(
        replies=[
            call("customer_overview", {"customer_id": 999}),
            call("list_overdue_customers", {"risk_level": "", "limit": 5}, "2"),
            call("portfolio_summary", {}, "3"),
            AIMessage("Done."),
        ]
    )
    generator.chat_model = model
    assert client.post(URL, json={"message": "Overview please"}).status_code == 200
    results = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert results[0] == "Customer 999 was not found."
    assert results[1].startswith("2 customers match") and "Customer 4" in results[1]
    assert results[2].startswith("4 customers")


def test_the_agent_can_ask_the_decision_agent(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    generator.structured_replies = [
        {
            "action": "offer_payment_plan",
            "channel": "phone",
            "priority": "high",
            "reasons": [{"text": "Two months late.", "fact_ids": ["F1"], "source_ids": [1]}],
            "needs_human_review": True,
        }
    ]
    model = ScriptedToolModel(
        replies=[call("recommend_next_action", {"customer_id": 1}), AIMessage("Offer a plan.")]
    )
    generator.chat_model = model
    assert (
        client.post(URL, json={"message": "What next for 1?"}).json()["answer"] == "Offer a plan."
    )
    result = next(m.content for m in model.seen[-1] if isinstance(m, ToolMessage))
    assert result.startswith("Action: offer_payment_plan, channel: phone, priority: high")


def test_the_agent_needs_a_model_and_an_administrator(client, settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.rag.embeddings import HashingEmbedder

    without = create_app(settings, embedder=HashingEmbedder(), generator=None)
    with TestClient(without, headers=client.headers) as c:
        response = c.post(URL, json={"message": "Hi"})
        assert (
            response.status_code == 503 and response.json()["error"]["code"] == "llm_not_configured"
        )
    assert (
        client.post(URL, json={"message": "Hi"}, headers={"Authorization": ""}).status_code == 401
    )
    assert client.post(URL, json={"message": ""}).status_code == 422
