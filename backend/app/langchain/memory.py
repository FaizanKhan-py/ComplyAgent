"""LangChain MEMORY: what the model remembers of a conversation.

chat_history(...)   the policy assistant: the browser sends the conversation so far; it is
                    turned into LangChain messages and trimmed to the last few turns
                    (a window memory), so follow-up questions keep their context.
AgentMemory         the Collections Agent: LangGraph's checkpointer keeps each conversation
                    on the server, keyed by its conversation id (the agent's thread_id).
                    Only the most recent conversations are kept, so memory stays bounded.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, trim_messages

MAX_HISTORY_MESSAGES = 6  # the policy assistant sees the last three questions and answers
MAX_AGENT_CONVERSATIONS = 200


def chat_history(history: list[dict[str, str]]) -> list[BaseMessage]:
    """[{"role": "user"|"assistant", "content": ...}] as messages: the last turns, starting with
    a question, so the model never sees an answer without the question it answered."""
    messages: list[BaseMessage] = [
        HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"])
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return trim_messages(
        messages,
        max_tokens=MAX_HISTORY_MESSAGES,
        token_counter=len,  # count messages, not tokens
        strategy="last",
        start_on="human",
    )


class AgentMemory:
    """The agent's conversations. `checkpointer` goes to create_agent; `thread(id)` gives the
    config for one conversation and forgets the oldest ones beyond the limit."""

    def __init__(self, limit: int = MAX_AGENT_CONVERSATIONS) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        self.checkpointer = InMemorySaver()
        self._limit = limit
        self._recent: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def thread(self, conversation_id: str | None) -> tuple[str, dict]:
        """(conversation id, invoke config). A new id when none is given."""
        conversation_id = conversation_id or uuid.uuid4().hex
        with self._lock:
            self._recent[conversation_id] = None
            self._recent.move_to_end(conversation_id)
            while len(self._recent) > self._limit:
                oldest, _ = self._recent.popitem(last=False)
                self.checkpointer.delete_thread(oldest)
        return conversation_id, {"configurable": {"thread_id": conversation_id}}

    def knows(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._recent
