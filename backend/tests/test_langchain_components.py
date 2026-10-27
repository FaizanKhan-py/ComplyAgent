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

URL = "/api/assistant/chat"


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
    from app.decisions import decision
    from app.orchestration import graph

    assert decision.DECISION_PROMPT is prompts.DECISION_PROMPT
    assert graph.MESSAGE_PROMPT is prompts.MESSAGE_PROMPT  # the writer runs in the graph
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


def test_agent_memory_forgets_the_oldest_and_keeps_owners_apart():
    memory = AgentMemory(limit=2)
    first, config = memory.thread("ada", None)
    assert config == {"configurable": {"thread_id": f"ada:{first}"}}
    memory.thread("ada", "b")
    memory.thread("ada", "c")
    assert not memory.knows("ada", first) and memory.knows("ada", "b")
    assert not memory.knows("bob", "b")  # the same id is a different conversation for bob


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


# --- agents: the AI Assistant ------------------------------------------------------------------


def test_the_assistant_cites_numbered_sources_and_remembers_the_conversation(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    model = ScriptedToolModel(
        replies=[
            call("customer_overview", {"customer_id": 1}),
            call("search_policies", {"query": "hardship plan"}, "2"),
            AIMessage("Customer 1 is 2 months late; a hardship plan is possible [1] [9]."),
            AIMessage("Yes, the same customer."),
        ]
    )
    generator.chat_model = model

    first = client.post(URL, json={"message": "What can we offer customer 1?"})
    assert first.status_code == 200, first.text
    body = first.json()
    # [9] was never retrieved, so it is removed; [1] stays and its source is marked cited.
    assert body["answer"] == "Customer 1 is 2 months late; a hardship plan is possible [1]."
    assert body["answer_mode"] == "generated" and body["model"] == "fake-llm"
    assert [s["tool"] for s in body["steps"]] == ["customer_overview", "search_policies"]
    assert body["steps"][0]["input"] == '{"customer_id": 1}'
    sources = body["sources"]
    assert [s["rank"] for s in sources] == [1, 2, 3, 4]
    assert sources[0]["cited"] is True and not any(s["cited"] for s in sources[1:])
    assert sources[0]["filename"] == "hardship_assistance_policy.txt"
    assert {"text", "score", "file_type", "chunk_index", "page_number"} <= set(sources[0])
    assert model.seen[0] == [
        "search_policies",
        "customer_overview",
        "payment_history",
        "list_overdue_customers",
        "portfolio_summary",
        "recommend_next_action",
    ]
    # What the tools gave the model: facts and numbered policy text, no protected data.
    tool_results = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert "The account is 2 months late" in tool_results[0]
    assert tool_results[1].startswith('<source id="1" document="hardship_assistance_policy.txt"')
    shown = " ".join(tool_results).lower()
    assert not any(word in shown for word in ("female", "university", "married", "@"))

    second = client.post(
        URL, json={"message": "Same one?", "conversation_id": body["conversation_id"]}
    )
    reply = second.json()
    assert reply["answer"] == "Yes, the same customer." and reply["steps"] == []
    assert reply["sources"] == []  # nothing was searched for this message
    remembered = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)]
    assert remembered == ["What can we offer customer 1?", "Same one?"]


def test_sources_are_numbered_once_across_several_searches(client, indexed, generator):
    model = ScriptedToolModel(
        replies=[
            call("search_policies", {"query": "hardship plan"}),
            call("search_policies", {"query": "hardship plan"}, "2"),  # the same passages again
            AIMessage("See [1]."),
        ]
    )
    generator.chat_model = model
    body = client.post(URL, json={"message": "Hardship?"}).json()
    assert [s["rank"] for s in body["sources"]] == [1, 2, 3, 4]
    second_search = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)][1]
    assert second_search.startswith('<source id="1"')  # reused numbers, no duplicates


def test_the_tools_report_unknown_customers_and_overdue_lists(client, generator, tmp_path):
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


def test_the_assistant_can_ask_the_decision_agent(client, indexed, generator, tmp_path):
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


def test_an_ordinary_user_gets_policy_search_only_and_their_own_memory(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.rag.embeddings import HashingEmbedder

    from .helpers import FakeGenerator
    from .test_auth_api import ADA, sign_in

    generator = FakeGenerator()
    model = ScriptedToolModel(replies=[AIMessage("Only policies."), AIMessage("New chat.")])
    generator.chat_model = model
    app = create_app(settings, embedder=HashingEmbedder(), generator=generator)
    with TestClient(app) as anon:
        assert anon.post("/api/auth/register", json=ADA).status_code == 201
        assert anon.post(URL, json={"message": "Hi"}).status_code == 401  # signed out
    user = sign_in(app, ADA["email"])
    first = user.post(URL, json={"message": "Show customer 1"}).json()
    assert first["answer"] == "Only policies."
    assert model.seen[0] == ["search_policies"]  # no customer tools for this user
    # An id from someone else's conversation (or a made-up one) starts a fresh one.
    other = user.post(URL, json={"message": "Again", "conversation_id": "someone-elses"}).json()
    assert other["conversation_id"] != "someone-elses"
    asked = [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)]
    assert asked == ["Again"]


def test_without_a_model_or_when_it_fails_the_assistant_shows_the_passages(
    client, indexed, generator, settings
):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.rag.embeddings import HashingEmbedder

    class Down(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise TimeoutError("slow")

    generator.chat_model = Down(replies=[])
    body = client.post(URL, json={"message": "How much can a hardship plan reduce?"}).json()
    assert body["answer_mode"] == "retrieval_only" and body["sources"]
    assert body["warning"].startswith("The answer model is not available right now")
    assert "timed out" in body["warning"]

    without = create_app(settings, embedder=HashingEmbedder(), generator=None)
    with TestClient(without, headers=client.headers) as c:
        reply = c.post(URL, json={"message": "How much can a hardship plan reduce?"}).json()
        assert reply["answer_mode"] == "retrieval_only" and reply["warning"] is None
        assert reply["sources"] and reply["conversation_id"]
    assert client.post(URL, json={"message": "   "}).status_code == 422
