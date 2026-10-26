"""LangChain AGENTS: the Collections Agent, a tool-calling agent built with create_agent.

An officer asks in plain words ("Which high-risk customers are overdue?", "What does the policy
say about customer 4?"). The model decides which tools to call, reads their results and answers.
The tools only read: the agent advises and never sends an email or changes a record.

    search_policies          the policy index (indexes.PolicyRetriever)
    customer_overview        one customer's account facts and risk score
    payment_history          one customer's last six months
    list_overdue_customers   overdue customers, riskiest first
    portfolio_summary        the whole portfolio in numbers
    recommend_next_action    the Decision Agent's checked recommendation for one customer

Customer tools never return sex, education, marital status, age or the email address, so the
model cannot use or reveal them. The conversation is remembered per conversation id
(memory.AgentMemory), so follow-up questions work.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from .indexes import PolicyRetriever, format_documents
from .memory import AgentMemory
from .prompts import AGENT_PROMPT

MAX_LISTED = 25  # customers per list_overdue_customers call


def build_tools(kb: Any, portfolio: Any, generator: Any) -> list:
    """The agent's tools, bound to this app's knowledge base, portfolio and Decision Agent."""
    from ..decisions.decision import DecisionUnavailable, case_facts, recommend
    from ..decisions.guidance import describe

    retriever = PolicyRetriever(knowledge_base=kb, k=4)

    def _customer(customer_id: int) -> dict | None:
        return portfolio.get_customer(int(customer_id))

    @tool
    def search_policies(query: str) -> str:
        """Search the company's indexed collections policies. Use it for any question about
        rules, stages, tone, contact hours, hardship plans or escalation. Returns numbered
        passages with their document names."""
        return format_documents(retriever.invoke(query))

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

    return [
        search_policies,
        customer_overview,
        payment_history,
        list_overdue_customers,
        portfolio_summary,
        recommend_next_action,
    ]


class CollectionsAgent:
    """One agent per app, with memory per conversation."""

    def __init__(self, chat_model: Any, model_name: str, kb: Any, portfolio: Any, generator: Any):
        from langchain.agents import create_agent

        self.model_name = model_name
        self.memory = AgentMemory()
        self._agent = create_agent(
            chat_model,
            tools=build_tools(kb, portfolio, generator),
            system_prompt=AGENT_PROMPT,
            checkpointer=self.memory.checkpointer,
        )

    def ask(self, message: str, conversation_id: str | None = None) -> dict:
        """The answer and the tool calls the agent made for it. Model errors propagate, for
        models.classify_error."""
        if conversation_id and not self.memory.knows(conversation_id):
            conversation_id = None  # forgotten, or never this server's: start a new one
        conversation_id, config = self.memory.thread(conversation_id)
        before = len(self._history(config))
        result = self._agent.invoke(
            {"messages": [{"role": "user", "content": message}]}, config=config
        )
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
        return {
            "answer": answer.strip(),
            "conversation_id": conversation_id,
            "steps": steps,
            "model": self.model_name,
        }

    def _history(self, config: dict) -> list:
        state = self._agent.get_state(config)
        return list(state.values.get("messages", [])) if state and state.values else []
