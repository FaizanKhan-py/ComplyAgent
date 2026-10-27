"""Do not contact, and the officers' inbox, through the real API and database; only the LLM is
scripted. Customer 1 is 2 months late with a latest bill of NT$4,000."""

from __future__ import annotations

from .test_conversation import analysis, draft, give_email, reply, review, start
from .test_guidance import load
from .test_messages import decision
from .test_orchestration import plan, send_first_email

FLAG = "/api/admin/customers/{}/do-not-contact"


def test_a_stop_request_in_an_email_blocks_every_later_email(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    generator.structured_replies = [
        {**reply("stop_contact"), "intent_summary": "The customer asked us to stop emailing."}
    ]
    after = client.post(
        f"/api/admin/conversations/{body['id']}/inbound",
        json={"body": "Take me off your list, I want no more of these."},
    ).json()
    assert after["status"] == "escalated"
    assert after["follow_ups"][0]["status"] == "cancelled"

    flag = client.get("/api/admin/customers/1").json()["do_not_contact"]
    assert flag["reason"] == "The customer asked us to stop emailing."
    assert flag["set_by"] == "Communication Agent"

    # Closing the conversation does not lift it: no new conversation can start.
    client.post(f"/api/admin/conversations/{body['id']}/close", json={})
    generator.structured_calls.clear()
    blocked = client.post("/api/admin/customers/1/conversation")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "do_not_contact"
    assert generator.structured_calls == []  # no agent ran


def test_an_officer_records_and_lifts_a_stop_request(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    give_email(client)
    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    message_id = body["messages"][0]["id"]

    flagged = client.post(FLAG.format(1), json={"reason": "Asked by phone to stop."}).json()
    assert flagged["do_not_contact"]["reason"] == "Asked by phone to stop."
    conversation = client.get("/api/admin/customers/1/conversation").json()
    assert conversation["messages"][0]["status"] == "superseded"  # the waiting draft is withdrawn

    approve = client.post(f"/api/admin/messages/{message_id}/approve", json={})
    assert approve.status_code == 409
    redraft = client.post(f"/api/admin/conversations/{body['id']}/redraft", json={})
    assert redraft.json()["error"]["code"] == "do_not_contact"

    assert client.post(f"{FLAG.format(1)}/lift", json={}).status_code == 422  # a reason is needed
    lifted = client.post(f"{FLAG.format(1)}/lift", json={"note": "The customer called back."})
    assert lifted.json()["do_not_contact"] is None
    again = client.post(f"{FLAG.format(1)}/lift", json={"note": "Again."})
    assert again.status_code == 409

    events = [
        e["event"]
        for e in client.get("/api/admin/audit", params={"event": "customer"}).json()["events"]
    ]
    assert events == ["customer.contact_allowed", "customer.do_not_contact"]


def test_a_flagged_customer_who_writes_in_goes_to_a_person(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    client.post(FLAG.format(1), json={"reason": "Asked by phone to stop."})
    generator.structured_calls.clear()
    after = client.post(
        f"/api/admin/conversations/{body['id']}/inbound", json={"body": "Why did you stop?"}
    ).json()
    assert after["status"] == "escalated"
    assert "asked not to be contacted" in after["status_reason"]
    assert generator.structured_calls == []  # the agent never drafts to them


def test_the_flag_survives_a_reimport(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    client.post(FLAG.format(1), json={"reason": "Asked to stop."})
    from app.customers.portfolio import Portfolio

    from .test_portfolio_api import ROWS, write_csv

    Portfolio(client.app.state.kb.db).import_data(write_csv(tmp_path / "again.csv", ROWS), True)
    assert client.get("/api/admin/customers/1").json()["do_not_contact"]["reason"] == (
        "Asked to stop."
    )


# --- the inbox ------------------------------------------------------------------------------


def test_the_inbox_collects_what_waits_for_an_officer(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    assert client.get("/api/admin/inbox").json()["total"] == 0

    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    inbox = client.get("/api/admin/inbox").json()
    assert inbox["total"] == 1
    (waiting,) = inbox["drafts"]
    assert waiting["customer_id"] == 1 and waiting["status"] == "pending_approval"

    client.post(f"/api/admin/messages/{waiting['id']}/reject", json={"note": "No."})
    assert client.get("/api/admin/inbox").json()["total"] == 0

    generator.structured_replies = [reply("dispute")]
    client.post(f"/api/admin/conversations/{body['id']}/inbound", json={"body": "Not mine."})
    inbox = client.get("/api/admin/inbox").json()
    assert [e["id"] for e in inbox["escalated"]] == [body["id"]]


def test_a_call_task_stays_in_the_inbox_until_something_happens(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan(action="schedule_call"))
    client.post(f"/api/admin/follow-ups/{body['follow_ups'][0]['id']}/run")
    assert len(client.get("/api/admin/inbox").json()["calls"]) == 1

    generator.structured_replies = [reply(), review()]
    client.post(f"/api/admin/conversations/{body['id']}/inbound", json={"body": "Hello"})
    inbox = client.get("/api/admin/inbox").json()
    assert inbox["calls"] == [] and len(inbox["drafts"]) == 1


# --- found in the live run against Azure ----------------------------------------------------


def test_a_plain_stop_request_needs_no_model(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    generator.structured_calls.clear()
    after = client.post(
        f"/api/admin/conversations/{body['id']}/inbound", json={"body": "Please stop emailing me."}
    ).json()
    assert generator.structured_calls == []
    assert after["status"] == "escalated"
    assert after["messages"][-1]["intent"] == "stop_contact"
    flag = client.get("/api/admin/customers/1").json()["do_not_contact"]
    assert "Please stop emailing me." in flag["reason"]


def test_a_stop_request_to_a_person_is_still_caught(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = send_first_email(client, generator, plan())
    generator.structured_replies = [reply("dispute")]
    client.post(f"/api/admin/conversations/{body['id']}/inbound", json={"body": "Not mine."})
    # Escalated: nothing is drafted, but the email is still read.
    generator.structured_calls.clear()
    generator.structured_replies = [
        {"intent": "stop_contact", "intent_summary": "Wants no more messages from us."}
    ]
    after = client.post(
        f"/api/admin/conversations/{body['id']}/inbound",
        json={"body": "I'm done with your messages, take my address off your list."},
    ).json()
    assert len(generator.structured_calls) == 1  # one classification, no draft
    assert after["messages"][-1]["intent"] == "stop_contact"
    assert client.get("/api/admin/customers/1").json()["do_not_contact"]["reason"] == (
        "Wants no more messages from us."
    )


def test_the_red_team_is_told_what_the_policy_requires():
    from app.decisions.decision import Recommendation
    from app.decisions.red_team import build_request

    rec = Recommendation.model_validate(decision())
    request = build_request(
        "Subject", "Body", rec, [], [], required=['ask the customer to pay "within 3 days"']
    )
    assert '<required_by_policy>\n- ask the customer to pay "within 3 days"' in request


def test_a_red_team_blocker_on_words_the_policy_requires_is_only_a_warning(generator):
    from app.decisions import red_team
    from app.decisions.decision import Recommendation

    rec = Recommendation.model_validate(decision())
    body = "Your account needs immediate attention. Please pay within 3 days."
    generator.structured_replies = [
        {
            "findings": [
                {
                    "target": "message",
                    "severity": "blocker",
                    "issue": "Too urgent.",
                    "quote": "needs immediate attention",
                    "source_ids": [],
                    "fix": "Soften it.",
                },
                {
                    "target": "message",
                    "severity": "blocker",
                    "issue": "Too short.",
                    "quote": "Please pay",
                    "source_ids": [],
                    "fix": "Say more.",
                },
            ]
        }
    ]
    verdict = red_team.review(
        generator, "Subject", body, rec, [], [], protected=["immediate attention", "within 3 days"]
    )
    severities = [f.severity for f in verdict["findings"]]
    assert severities == ["warning", "blocker"] and not verdict["passed"]
    assert "the policy requires these words" in verdict["findings"][0].issue


def test_the_guardrails_require_the_words_of_the_risk_rule():
    from app.decisions.messages import MessageDraft, problems, required_phrases

    rule = (
        "Collections message tone for a high-risk customer: it says the account needs immediate "
        "attention, offers to discuss a payment plan, asks the customer to pay within 3 days."
    )
    phrases = required_phrases(rule, "within 3 days")
    assert phrases == ["immediate attention", "payment plan"]
    assert required_phrases(rule, None) == []  # an escalated account: the rule does not apply
    found = problems(
        MessageDraft(subject="s", body="Pay within 3 days.", source_ids=[]),
        {"months_overdue": "2 months"},
        set(),
        phrases=phrases,
        required=(),
    )
    assert any('"immediate attention"' in f for f in found)
    assert any('"payment plan"' in f for f in found)


def test_the_month_count_is_not_written_twice():
    from app.decisions.messages import MessageDraft, problems

    values = {"months_overdue": "1 month"}
    twice = MessageDraft(subject="s", body="It is {months_overdue} month(s) late.", source_ids=[])
    once = MessageDraft(subject="s", body="It is {months_overdue} overdue.", source_ids=[])
    assert any("write it alone" in f for f in problems(twice, values, set(), required=()))
    assert not any("write it alone" in f for f in problems(once, values, set(), required=()))
