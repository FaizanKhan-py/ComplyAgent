"""LangChain AGENTS: the AI Assistant, a tool-calling agent built with create_agent.

Staff ask in plain words ("How long can a hardship plan last?", "Which high-risk customers are
overdue?", "What does the policy say about customer 4?"). The model decides which tools to call,
reads their results and answers. The tools only read: the assistant advises and never sends an
email or changes a record. Administrators get every tool; other users get search_policies only,
so they never see customer data.

    search_policies          the policy index (indexes.PolicyRetriever)
    customer_overview        one customer's account facts and risk score
    payment_history          one customer's last six months
    list_overdue_customers   overdue customers, riskiest first
    portfolio_summary        the whole portfolio in numbers
    recommend_next_action    the Decision Agent's checked recommendation for one customer

Every policy passage the assistant reads is numbered across the whole answer and returned as a
source, so the answer's [n] citations can be checked and shown next to it. Customer tools never
return sex, education, marital status, age or the email address, so the model cannot use or
reveal them. The conversation is remembered per user and conversation id (memory.AgentMemory),
so follow-up questions work.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from .indexes import PolicyRetriever, format_documents
from .memory import AgentMemory
from .prompts import AGENT_PROMPT

MAX_LISTED = 25  # customers per list_overdue_customers call


class _Sources:
    """The policy passages read while answering one message, numbered 1, 2, ... once each."""

    def __init__(self) -> None:
        self.documents: list = []
        self._numbers: dict[tuple, int] = {}

    def number(self, doc) -> int:
        key = (doc.metadata["document_id"], doc.metadata["chunk_index"])
        if key not in self._numbers:
            self.documents.append(doc)
            self._numbers[key] = len(self.documents)
        return self._numbers[key]


# Set for each message in ask(); the tools run in copies of that context and share the object.
_current_sources: ContextVar[_Sources | None] = ContextVar("sources", default=None)


def build_tools(kb: Any, portfolio: Any, generator: Any, customer_tools: bool = True) -> list:
    """The assistant's tools, bound to this app's knowledge base, portfolio and Decision Agent.
    Without `customer_tools` it can only search the policies."""
    from ..decisions.decision import DecisionUnavailable, case_facts, recommend
    from ..decisions.guidance import describe

    retriever = PolicyRetriever(knowledge_base=kb, k=4)

    def _customer(customer_id: int) -> dict | None:
        return portfolio.get_customer(int(customer_id))

    @tool
    def search_policies(query: str) -> str:
        """Search the company's indexed collections policies. Use it for any question about
        rules, stages, tone, contact hours, hardship plans or escalation. Returns numbered
        passages with their document names; cite them by number, e.g. [2]."""
        documents = retriever.invoke(query)
        sources = _current_sources.get() or _Sources()
        return format_documents(documents, [sources.number(d) for d in documents])

    @tool
    def customer_overview(customer_id: int) -> str:
        """Account facts for one customer: how late, the latest bill and payment, the credit
        limit used, and the risk model's chance of default. Returns 'not found' for an unknown
        customer number."""
        customer = _customer(customer_id)
        if customer is None:
            return f"Customer {customer_id} was not found."
        facts = describe(customer)
        lines = [f"Customer {customer_id}:"] + [f"- {f['text']}" for f in case_facts(customer)]
        lines.append(f"- Credit limit: NT${customer['limit_bal']:,}.")
        if facts["risk_level"] is None:
            lines.append("- The risk model has not scored this customer.")
        return "\n".join(lines)

    @tool
    def payment_history(customer_id: int) -> str:
        """The last six months for one customer, most recent first: repayment status (months
        late, or paid in full), bill and amount paid."""
        customer = _customer(customer_id)
        if customer is None:
            return f"Customer {customer_id} was not found."
        named = {-2: "no card use", -1: "paid in full", 0: "revolving credit (minimum paid)"}

        def status(code: int) -> str:
            return named.get(code, f"{code} months late")

        rows = [
            f"- {h['month']}: {status(h['repayment_status'])}, "
            f"bill NT${h['bill_amt']:,}, paid NT${h['pay_amt']:,}"
            for h in customer["history"]
        ]
        return "\n".join([f"Customer {customer_id}, most recent month first:", *rows])

    @tool
    def list_overdue_customers(risk_level: str = "", limit: int = 10) -> str:
        """Customers who are late in the latest month, riskiest first. risk_level may be
        'high', 'medium', 'low' or empty for all. Returns customer numbers, months late, latest
        bill and chance of default."""
        risk = risk_level.strip().lower() or None
        if risk not in (None, "high", "medium", "low"):
            return "risk_level must be high, medium, low or empty."
        page = portfolio.list_customers(
            risk=risk,
            payment="overdue",
            sort="probability",
            descending=True,
            page_size=max(1, min(int(limit), MAX_LISTED)),
        )
        if not page["customers"]:
            return "No customers match."
        rows = [
            f"- Customer {c['customer_id']}: {c['latest_status']} months late, latest bill "
            f"NT${(c['latest_bill'] or 0):,}, "
            + (
                f"{c['default_probability']:.0%} chance of default ({c['risk_level']} risk)"
                if c["default_probability"] is not None
                else "not scored"
            )
            for c in page["customers"]
        ]
        return "\n".join([f"{page['total']} customers match; the first {len(rows)}:", *rows])

    @tool
    def portfolio_summary() -> str:
        """The whole portfolio: number of customers, total credit, late rate in the latest
        month, and customers and actual default rate per risk level."""
        s = portfolio.summary()
        if not s["loaded"]:
            return "No customer data is loaded."
        lines = [
            f"{s['customers']:,} customers, total credit limit NT${s['total_credit_limit']:,}.",
        ]
        if s["latest_late_rate"] is not None:
            lines.append(f"{s['latest_late_rate']:.1%} were late in the latest month.")
        for group in s["by_risk"]:
            rate = group["default_rate"]
            lines.append(
                f"{group['risk_level']} risk: {group['customers']:,} customers"
                + (f", {rate:.1%} actually defaulted" if rate is not None else "")
            )
        return "\n".join(lines)

    @tool
    def recommend_next_action(customer_id: int) -> str:
        """The Decision Agent's recommended next collections action for one customer, with its
        reasons. The recommendation is checked against the evidence before it is returned."""
        try:
            result = recommend(kb, portfolio, generator, int(customer_id))
        except DecisionUnavailable as exc:
            return f"No recommendation: {exc.message}"
        if result is None:
            return f"Customer {customer_id} was not found."
        rec = result["recommendation"]
        reasons = "\n".join(f"- {r.text}" for r in rec.reasons)
        return (
            f"Action: {rec.action.value}, channel: {rec.channel.value}, priority: "
            f"{rec.priority.value}, needs human review: {rec.needs_human_review}.\n{reasons}"
        )

    if not customer_tools:
        return [search_policies]
    return [
        search_policies,
        customer_overview,
        payment_history,
        list_overdue_customers,
        portfolio_summary,
        recommend_next_action,
    ]


class AssistantAgent:
    """The AI Assistant: one agent for a set of tools, with memory per user and conversation."""

    def __init__(
        self,
        chat_model: Any,
        model_name: str,
        kb: Any,
        portfolio: Any,
        generator: Any,
        customer_tools: bool = True,
    ):
        from langchain.agents import create_agent

        self.model_name = model_name
        self.memory = AgentMemory()
        self._agent = create_agent(
            chat_model,
            tools=build_tools(kb, portfolio, generator, customer_tools),
            system_prompt=AGENT_PROMPT,
            checkpointer=self.memory.checkpointer,
        )

    def ask(self, message: str, owner: str, conversation_id: str | None = None) -> dict:
        """The answer, the policy passages it read (as sources) and the tool calls it made.
        Model errors propagate, for models.classify_error."""
        if conversation_id and not self.memory.knows(owner, conversation_id):
            conversation_id = None  # forgotten, or not this user's: start a new one
        conversation_id, config = self.memory.thread(owner, conversation_id)
        before = len(self._history(config))
        sources = _Sources()
        token = _current_sources.set(sources)
        try:
            result = self._agent.invoke(
                {"messages": [{"role": "user", "content": message}]}, config=config
            )
        finally:
            _current_sources.reset(token)
        new = result["messages"][before:]
        steps = [
            {"tool": call["name"], "input": json.dumps(call["args"], ensure_ascii=False)}
            for m in new
            if isinstance(m, AIMessage)
            for call in m.tool_calls
        ]
        answer = next(
            (m.text for m in reversed(new) if isinstance(m, AIMessage) and not m.tool_calls),
            "",
        )
        # Keep only citations of passages read for this answer; drop any other [n].
        valid = set(range(1, len(sources.documents) + 1))
        answer = re.sub(
            r"\s*\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in valid else "", answer
        ).strip()
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
        return {
            "answer": answer,
            "answer_mode": "generated",
            "insufficient_evidence": False,
            "sources": [
                {
                    "rank": number,
                    "document_id": doc.metadata["document_id"],
                    "filename": doc.metadata["filename"],
                    "file_type": doc.metadata["file_type"],
                    "page_number": doc.metadata["page_number"],
                    "chunk_index": doc.metadata["chunk_index"],
                    "score": doc.metadata["score"],
                    "text": doc.page_content,
                    "cited": number in cited,
                }
                for number, doc in enumerate(sources.documents, start=1)
            ],
            "model": self.model_name,
            "warning": None,
            "conversation_id": conversation_id,
            "steps": steps,
        }

    def _history(self, config: dict) -> list:
        state = self._agent.get_state(config)
        return list(state.values.get("messages", [])) if state and state.values else []
