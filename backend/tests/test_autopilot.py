"""The autopilot: the queue, and one customer's whole journey with no button pressed. Only the
LLM is scripted; the agents, policies, database and API are real."""

from __future__ import annotations

from dataclasses import replace

from app.autopilot.runner import Autopilot
from app.autopilot.store import Store
from app.customers.portfolio import Portfolio
from app.integrations.mail import RecordOnly

from .test_conversation import give_email
from .test_guidance import load
from .test_messages import analysis, decision, draft, review


def drain(pilot: Autopilot) -> None:
    while task := pilot.store.claim("test"):
        pilot.run_task(task)


def test_a_customers_tasks_run_in_order_and_failures_are_retried(client):
    store = Store(client.app.state.kb.db)
    first = store.enqueue("analyse", 1)
    store.enqueue("decide", 1)
    other = store.enqueue("analyse", 2)
    assert store.enqueue("analyse", 1) is None  # already waiting

    assert store.claim("w1")["id"] == first
    assert store.claim("w2")["id"] == other  # customer 1's next task waits for the first
    assert store.claim("w3") is None

    assert store.fail(first, "model timed out") is True  # retried later, not lost
    task = store.task(first)
    assert (task["status"], task["attempts"], task["error"]) == ("pending", 1, "model timed out")


def test_an_overdue_customer_goes_through_every_agent_by_itself(
    client, indexed, generator, settings, tmp_path
):
    load(client, tmp_path)
    give_email(client)
    kb = client.app.state.kb
    pilot = Autopilot(replace(settings, autopilot_max_cases=1), kb, Portfolio(kb.db), RecordOnly())
    generator.structured_replies = [analysis(), decision(channel="email"), draft(), review()]

    pilot.tick()  # detects the overdue customer
    drain(pilot)  # Analysis -> Decision -> Conversation (written, checked, sent)

    ctx = pilot.store.context(1)
    assert ctx["stage"] == "awaiting_reply"
    assert ctx["analysis"]["prediction_check"]["verdict"] == "no_prediction"
    assert ctx["decision"]["plan"] == {
        "tone": "medium", "channel": "email", "hardship_offer": False, "escalate": False,
        "next": "email",
    }  # fmt: skip
    conv = client.get("/api/admin/customers/1/conversation").json()
    assert conv["created_by"] == "Autopilot"
    assert [m["status"] for m in conv["messages"]] == ["sent"]

    # The customer disputes the debt: the Decision Agent reuses the saved analysis (no new
    # Case Analyst call) and hands the case to a person.
    generator.structured_replies = [{"intent": "dispute", "intent_summary": "Says it is wrong."}]
    client.post(f"/api/admin/conversations/{conv['id']}/inbound",
                json={"subject": "Re", "body": "I never owed this."})  # fmt: skip
    calls = len(generator.structured_calls)
    pilot.tick()
    drain(pilot)
    assert len(generator.structured_calls) == calls
    ctx = pilot.store.context(1)
    assert ctx["stage"] == "escalated"
    assert "dispute" in ctx["escalation"]["reason"]

    live = client.get("/api/admin/autopilot/overview").json()
    assert [(c["customer_id"], c["stage"]) for c in live["customers"]] == [(1, "escalated")]
    timeline = client.get("/api/admin/autopilot/customers/1").json()["timeline"]
    events = {e["event"] for e in timeline}
    assert {"autopilot.detected", "agent.case_analysis", "agent.decision", "draft.sent",
            "autopilot.escalated"} <= events  # fmt: skip
