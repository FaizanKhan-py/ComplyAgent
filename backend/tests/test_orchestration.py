"""The LangGraph graph, the audit trail, the Follow-up Agent and the Counterfactual Simulator,
through the real API and database; only the LLM is scripted.

Customer 1 is 2 months late with a latest bill of NT$4,000; customer 2 is not overdue.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from app.decisions import follow_up
from app.decisions.conversation import Conversations
from app.langchain.prompts import FOLLOW_UP_PLAN_PROMPT, FOLLOW_UP_PROMPT

from .helpers import save_model_files
from .test_conversation import analysis, draft, give_email, review, start
from .test_guidance import load
from .test_messages import decision


def plan(wait_days=3, action="send_reminder") -> dict:
    return {
        "wait_days": wait_days,
        "action": action,
        "reason": "The contact policy allows a reminder after three days.",
        "source_ids": [1, 99],
    }


def send_first_email(client, generator, *plan_reply) -> dict:
    give_email(client)
    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    message_id = body["messages"][0]["id"]
    client.post(f"/api/admin/messages/{message_id}/approve", json={})
    generator.structured_replies = list(plan_reply)
    return client.post(f"/api/admin/messages/{message_id}/send").json()


def events(client, **params) -> list[str]:
    return [e["event"] for e in client.get("/api/admin/audit", params=params).json()["events"]]


# --- the graph ------------------------------------------------------------------------------


def test_the_graph_is_drawn_by_langgraph(client):
    mermaid = client.get("/api/admin/agents/graph").json()["mermaid"]
    for node in ("case_analyst", "decision_agent", "writer", "guardrails", "red_team", "blocked"):
        assert node in mermaid


# --- the audit trail ------------------------------------------------------------------------


def test_every_agent_step_and_officer_action_is_audited(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    send_first_email(client, generator, plan())
    assert events(client, customer_id=1)[::-1] == [
        "conversation.started",
        "agent.case_analysis",
        "agent.decision",
        "agent.draft",
        "agent.red_team",
        "draft.created",
        "draft.approved",
        "draft.sent",
        "follow_up.scheduled",
    ]
    trail = client.get("/api/admin/audit", params={"event": "agent"}).json()["events"]
    assert {e["actor"] for e in trail} == {
        "Case Analyst Agent",
        "Decision Agent",
        "Message writer",
        "Compliance Red Team",
    }
    decided = next(e for e in trail if e["event"] == "agent.decision")
    assert decided["detail"]["recommendation"]["action"] == "send_reminder"
    assert decided["conversation_id"] and decided["customer_id"] == 1
    assert client.get("/api/admin/audit", params={"customer_id": 2}).json()["events"] == []


def test_the_audit_trail_cannot_be_changed(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    start(client, generator, analysis(), decision(), draft(), review())
    db = client.app.state.kb.db
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("UPDATE audit_log SET actor = 'someone else'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with db.connect() as conn:
            conn.execute("DELETE FROM audit_log")


def test_the_audit_trail_pages_back(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    start(client, generator, analysis(), decision(), draft(), review())
    first = client.get("/api/admin/audit", params={"limit": 2}).json()
    assert len(first["events"]) == 2 and first["next_before"]
    older = client.get("/api/admin/audit", params={"limit": 2, "before": first["next_before"]})
    assert all(e["id"] < first["next_before"] for e in older.json()["events"])


# --- the Follow-up Agent --------------------------------------------------------------------


def test_sending_schedules_a_follow_up_from_the_policies(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    (scheduled,) = body["follow_ups"]
    assert scheduled["status"] == "scheduled" and scheduled["action"] == "send_reminder"
    due = datetime.fromisoformat(scheduled["due_at"])
    # Three days, moved forward into the contact hours (08:00-21:00 customer time) if needed.
    assert (
        timedelta(days=2, hours=23)
        < due - datetime.now(timezone.utc)
        <= timedelta(days=3, hours=12)
    )
    assert 8 <= due.astimezone(timezone(timedelta(hours=8))).hour < 21
    asked = generator.structured_calls[-1]
    assert asked["system"] == FOLLOW_UP_PLAN_PROMPT
    assert "Emails sent so far: 1." in asked["user"]
    assert (
        client.get("/api/admin/follow-ups", params={"status": "scheduled"}).json()[0]["id"]
        == (scheduled["id"])
    )


def test_a_wait_outside_the_limits_is_kept_inside_them(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    (scheduled,) = send_first_email(client, generator, plan(wait_days=90))["follow_ups"]
    due = datetime.fromisoformat(scheduled["due_at"])
    assert due - datetime.now(timezone.utc) <= timedelta(days=follow_up.MAX_WAIT_DAYS, hours=12)


def test_planning_never_stops_an_email_from_being_sent(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator)  # no plan scripted: the model call fails
    assert body["messages"][0]["status"] == "sent"
    (scheduled,) = body["follow_ups"]
    assert "default" in scheduled["reason"]
    audit = client.get("/api/admin/audit", params={"event": "follow_up"}).json()["events"][0]
    assert audit["detail"]["planned_by"] == "default"


def test_a_due_reminder_is_drafted_and_waits_for_approval(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    follow_up_id = body["follow_ups"][0]["id"]
    generator.structured_calls.clear()
    generator.structured_replies = [draft(), review()]
    store = Conversations(client.app.state.kb.db)
    kb = client.app.state.kb
    from app.customers.portfolio import Portfolio

    later = datetime.now(timezone.utc) + timedelta(days=4)
    result = follow_up.run_due(kb, Portfolio(kb.db), generator, store, now=later)
    assert result == {"due": 1, "done": [follow_up_id], "failed": []}

    conversation = client.get("/api/admin/customers/1/conversation").json()
    assert [m["status"] for m in conversation["messages"]] == ["sent", "pending_approval"]
    assert conversation["follow_ups"][0]["status"] == "done"
    assert "waits for an officer" in conversation["follow_ups"][0]["outcome"]
    written = generator.structured_calls[0]
    assert written["system"] == FOLLOW_UP_PROMPT
    assert isinstance(written["history"][0], AIMessage)  # it reads the email that was sent
    assert "has not replied" in written["user"]


def test_nothing_is_due_before_its_time(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    send_first_email(client, generator, plan())
    assert client.post("/api/admin/follow-ups/run-due").json() == {
        "due": 0,
        "done": [],
        "failed": [],
    }


def test_a_reply_cancels_the_follow_up(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    generator.structured_replies = [
        {
            "intent": "dispute",
            "intent_summary": "Disputes it.",
            "needs_human": True,
            "human_reason": "The customer disputes the debt.",
            "subject": "Re",
            "body": "x",
            "source_ids": [],
            "promised_date": "",
            "promised_amount": 0,
        }
    ]
    after = client.post(
        f"/api/admin/conversations/{body['id']}/inbound", json={"body": "Not mine."}
    ).json()
    assert after["follow_ups"][0]["status"] == "cancelled"
    assert after["follow_ups"][0]["outcome"] == "The customer replied."


def test_an_escalation_follow_up_hands_the_conversation_over(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan(action="escalate_to_human"))
    follow_up_id = body["follow_ups"][0]["id"]
    after = client.post(f"/api/admin/follow-ups/{follow_up_id}/run").json()
    assert after["status"] == "escalated"
    assert after["follow_ups"][0]["status"] == "done"
    again = client.post(f"/api/admin/follow-ups/{follow_up_id}/run")
    assert again.status_code == 409


def test_a_call_follow_up_becomes_a_task_for_the_officer(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan(action="schedule_call"))
    after = client.post(f"/api/admin/follow-ups/{body['follow_ups'][0]['id']}/run").json()
    assert after["status"] == "open"
    assert after["follow_ups"][0]["outcome"].startswith("An officer should call the customer")


# --- the Counterfactual Simulator -----------------------------------------------------------


@pytest.fixture
def model(settings):
    save_model_files(settings.risk_model_dir)


def test_the_ready_made_what_ifs_and_the_fairness_check(client, model, generator, tmp_path):
    load(client, tmp_path)
    response = client.get("/api/admin/customers/1/counterfactuals")
    assert response.status_code == 200, response.text
    body = response.json()
    keys = {s["key"]: s for s in body["scenarios"]}
    assert {"pays_in_full", "one_month_less_late", "catches_up", "misses_next_month"} <= set(keys)
    # The test model learnt that late payers default: catching up lowers the risk.
    assert keys["catches_up"]["probability"] < body["base"]["probability"]
    assert keys["catches_up"]["change"] < 0
    fairness = [s for s in body["scenarios"] if s["kind"] == "fairness"]
    assert {s["key"] for s in fairness} >= {"sex"}
    assert generator.structured_calls == []  # no LLM for the ready-made what-ifs


def test_a_custom_what_if_and_the_decision_for_it(client, indexed, model, generator, tmp_path):
    load(client, tmp_path)
    generator.structured_replies = [decision("offer_payment_plan", "email")]
    response = client.post(
        "/api/admin/customers/1/counterfactuals",
        json={"repayment_status": 4, "pay_amt": 0, "decide": True},
    )
    assert response.status_code == 200, response.text
    custom = response.json()["custom"]
    assert custom["changes"] == {"repayment_status": 4, "pay_amt": 0}
    assert custom["recommendation"]["action"] == "offer_payment_plan"
    assert custom["features"]["avg_payment_delay"] > 0
    asked = generator.structured_calls[0]["user"]
    assert "4 months late" in asked  # the Decision Agent saw the changed record
    trail = client.get("/api/admin/audit", params={"event": "simulation"}).json()["events"]
    assert trail[0]["customer_id"] == 1
    # The real record is unchanged.
    assert client.get("/api/admin/customers/1").json()["latest_status"] == 2


def test_the_simulator_says_when_there_is_no_model(client, tmp_path):
    load(client, tmp_path)
    response = client.get("/api/admin/customers/1/counterfactuals")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_missing"


def test_the_audit_trail_exports_as_csv(client, indexed, generator, tmp_path):
    import csv
    import io

    load(client, tmp_path)
    start(client, generator, analysis(), decision(), draft(), review())
    with client.app.state.kb.db.connect() as conn:  # a summary that starts like a formula
        conn.execute(
            "INSERT INTO audit_log (created_at, actor, event, customer_id, summary)"
            " VALUES ('2026-10-27T10:00:00+00:00', 'x', 'test.formula', 1, '=HYPERLINK(1)')"
        )
    response = client.get("/api/admin/audit/export", params={"customer_id": 1})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="audit-trail-customer-1.csv"' in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["event"] == "conversation.started"  # oldest first
    assert {r["actor"] for r in rows} >= {"Case Analyst Agent", "Decision Agent"}
    assert next(r for r in rows if r["event"] == "test.formula")["summary"] == "'=HYPERLINK(1)"
    # The export is itself recorded.
    exported = client.get("/api/admin/audit", params={"event": "audit"}).json()["events"][0]
    assert exported["detail"]["filters"] == {"customer_id": "1"}

    later = client.get("/api/admin/audit/export", params={"since": "2999-01-01"}).text
    assert later.strip().splitlines() == [
        "id,created_at,actor,event,customer_id,conversation_id,message_id,summary,detail"
    ]


def events_of(text: str) -> list[tuple[str, dict]]:
    """Server-sent events as (event, data)."""
    import json

    found = []
    for block in text.strip().split("\n\n"):
        kind = next(line[7:] for line in block.splitlines() if line.startswith("event: "))
        data = next(line[6:] for line in block.splitlines() if line.startswith("data: "))
        found.append((kind, json.loads(data)))
    return found


def test_each_agent_step_streams_as_it_finishes(client, indexed, generator, tmp_path):
    from .test_conversation import finding

    load(client, tmp_path)
    generator.structured_replies = [
        analysis(), decision(), draft("PAY NOW. " + draft()["body"]), draft(),
        review(finding()), draft(), review(),
    ]  # fmt: skip
    response = client.post("/api/admin/customers/1/conversation/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = events_of(response.text)
    steps = [(d["node"], d["round"]) for kind, d in events if kind == "step"]
    assert steps[:3] == [("case_analyst", None), ("decision_agent", None), ("writer", 1)]
    assert ("guardrails", 1) in steps and ("red_team", 2) in steps and steps[-1] == ("red_team", 3)
    summaries = [d["summary"] for kind, d in events if kind == "step"]
    assert any("broke 1 fixed rule" in s for s in summaries)
    assert "The draft passed the fixed rules." in summaries
    kind, done = events[-1]
    assert kind == "done" and done["messages"][0]["status"] == "pending_approval"


def test_a_streamed_failure_ends_with_an_error_event(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    start(client, generator, analysis(), decision(), draft(), review())
    events = events_of(client.post("/api/admin/customers/1/conversation/stream").text)
    assert events == [
        (
            "error",
            {
                "status": 409,
                "code": "conversation_open",
                "message": "This customer already has an open conversation.",
            },
        )
    ]


def test_the_collections_dashboard_counts_the_work(client, indexed, generator, tmp_path):
    from datetime import date

    from .test_conversation import finding

    load(client, tmp_path)
    empty = client.get("/api/admin/collections/summary").json()
    assert empty["work"]["pending_approval"] == 0 and empty["promises"]["kept_rate"] is None
    assert len(empty["activity"]["sent_by_day"]) == 14

    # One conversation: a first email (rewritten once by the Red Team) sent, a promise kept,
    # then closed.
    give_email(client)
    body = start(
        client, generator, analysis(), decision(), draft(), review(finding()), draft(), review()
    ).json()
    message_id = body["messages"][0]["id"]
    client.post(f"/api/admin/messages/{message_id}/approve", json={})
    generator.structured_replies = [plan()]
    client.post(f"/api/admin/messages/{message_id}/send")
    today = date.today().isoformat()
    client.post("/api/admin/customers/1/promises", json={"due_on": today, "amount": 1000})
    client.post("/api/admin/customers/1/payments", json={"amount": 1000, "paid_on": today})
    client.post(f"/api/admin/conversations/{body['id']}/close", json={"note": "Paid."})

    s = client.get("/api/admin/collections/summary").json()
    assert s["conversations"]["closed"] == 1
    assert s["conversations"]["emails_per_resolution"] == 1.0
    assert (s["promises"]["kept"], s["promises"]["kept_rate"]) == (1, 1.0)
    assert s["promises"]["collected"] == 1000
    assert s["compliance"]["first_round_pass_rate"] == 0.0  # it passed on the second round
    assert s["compliance"]["average_rounds"] == 2.0
    assert s["activity"]["emails_sent"] == 1
    assert s["activity"]["sent_by_day"][-1]["emails"] == 1
