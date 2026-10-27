"""The collections conversation: Case Analyst, Decision Agent, writer, Red Team, Communication
Agent and the officer's approval, through the real API, policies and database; only the LLM is
scripted.

Customer 1 is 2 months late with a latest bill of NT$4,000; customer 2 is not overdue.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.decisions import compliance_loop
from app.decisions.case_analyst import analyst_facts
from app.langchain.prompts import (
    CASE_ANALYST_PROMPT,
    CONVERSATION_PROMPT,
    DECISION_PROMPT,
    MESSAGE_PROMPT,
    RED_TEAM_PROMPT,
)

from .test_guidance import load
from .test_messages import EMAIL, decision, draft

START = "/api/admin/customers/{}/conversation"


def analysis(fact_ids=("F1", "F7")) -> dict:
    return {
        "summary": "Two months late and paying only part of each bill.",
        "situation": "partial_payer",
        "urgency": "medium",
        "patterns": [{"text": "Pays part of each bill.", "fact_ids": list(fact_ids)}],
        "care_flags": [],
        "open_questions": ["Can the customer pay the full amount?"],
    }


def review(*findings) -> dict:
    return {"findings": list(findings)}


def finding(quote="Please call", target="message", severity="blocker") -> dict:
    return {
        "target": target,
        "severity": severity,
        "issue": "Too abrupt for this stage.",
        "quote": quote,
        "source_ids": [1, 999],
        "fix": "Open with a polite reminder.",
    }


def reply(intent="question", needs_human=False, human_reason="") -> dict:
    return {
        "intent": intent,
        "intent_summary": "The customer asked how to pay.",
        "needs_human": needs_human,
        "human_reason": human_reason,
        "subject": "Re: your account",
        "body": "Dear customer,\n\nYou can pay at {payment_link}. Reference: {reference}\n"
        "ComplyAgent Finance",
        "source_ids": [1],
    }


def start(client, generator, *replies):
    generator.structured_replies = list(replies)
    return client.post(START.format(1))


def first_draft(body: dict) -> dict:
    return [m for m in body["messages"] if m["direction"] == "outbound"][0]


def give_email(client, customer_id=1):
    with client.app.state.kb.db.connect() as conn:
        conn.execute(
            "UPDATE customers SET email = 'customer@example.com' WHERE customer_id = ?",
            (customer_id,),
        )


def sent_conversation(client, generator) -> dict:
    give_email(client)
    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    message_id = first_draft(body)["id"]
    client.post(f"/api/admin/messages/{message_id}/approve", json={})
    return client.post(f"/api/admin/messages/{message_id}/send").json()


# --- Case Analyst ---------------------------------------------------------------------------


def test_the_case_analyst_adds_what_the_payments_show(client, tmp_path):
    load(client, tmp_path)
    customer = client.app.state.kb.db
    from app.customers.portfolio import Portfolio

    facts = {f["id"]: f["text"] for f in analyst_facts(Portfolio(customer).get_customer(1))}
    assert facts["F7"] == "In 2 of the 2 months with a bill, the customer paid part of it."
    assert facts["F8"] == "In 0 of the 2 months with a bill, nothing was paid."
    assert facts["F9"] == "The bill rose by 100% since last month."


def test_a_case_file_citing_unknown_facts_is_sent_back_then_refused(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    response = start(client, generator, analysis(("F99",)), analysis(("F99",)))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_bad_output"
    assert "F99" in generator.structured_calls[1]["user"]


# --- the first email: every agent, then approval ---------------------------------------------


def test_starting_runs_every_agent_and_waits_for_an_officer(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    response = start(client, generator, analysis(), decision(), draft(), review())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "open" and body["created_by"] == "API token"
    assert body["case_file"]["analysis"]["situation"] == "partial_payer"
    assert body["decision"]["action"] == "send_reminder"
    message = first_draft(body)
    assert message["status"] == "pending_approval"
    assert "NT$4,000" in message["body"]
    assert [r["checked_by"] for r in message["review"]["rounds"]] == ["red_team"]
    assert message["review"]["checks"]

    systems = [c["system"] for c in generator.structured_calls]
    assert systems == [CASE_ANALYST_PROMPT, DECISION_PROMPT, MESSAGE_PROMPT, RED_TEAM_PROMPT]
    assert "<case_analysis>" in generator.structured_calls[1]["user"]  # the Decision Agent reads it
    assert "NT$4,000" in generator.structured_calls[3]["user"]  # the Red Team sees the real email

    assert client.get(START.format(1)).json()["id"] == body["id"]
    again = client.post(START.format(1))
    assert again.status_code == 409 and again.json()["error"]["code"] == "conversation_open"


def test_nothing_overdue_means_no_conversation_and_no_model(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    response = client.post(START.format(2))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_send"
    assert generator.structured_calls == []
    assert client.get(START.format(2)).json() is None


# --- the adversarial compliance loop ---------------------------------------------------------


def test_a_red_team_blocker_is_rewritten(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = start(
        client, generator, analysis(), decision(), draft(), review(finding()), draft(), review()
    ).json()
    rounds = first_draft(body)["review"]["rounds"]
    assert [r["passed"] for r in rounds] == [False, True]
    assert rounds[0]["findings"][0]["source_ids"] == [1]  # an unknown passage is not kept
    rewrite = generator.structured_calls[4]
    assert rewrite["system"] == MESSAGE_PROMPT
    assert "A compliance reviewer found these problems" in rewrite["user"]
    assert (
        '"Please call": Too abrupt for this stage. Fix: Open with a polite reminder.'
        in (rewrite["user"])
    )


def test_a_finding_about_words_not_in_the_email_is_dropped(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = start(
        client, generator, analysis(), decision(), draft(), review(finding("we will sue you"))
    ).json()
    message = first_draft(body)
    assert message["status"] == "pending_approval"
    assert message["review"]["rounds"][0]["dropped"] == 1


def test_guardrail_breaks_are_fixed_before_the_red_team_reads_it(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    shouting = draft("PAY NOW. " + EMAIL)
    body = start(client, generator, analysis(), decision(), shouting, draft(), review()).json()
    rounds = first_draft(body)["review"]["rounds"]
    assert [(r["checked_by"], r["passed"]) for r in rounds] == [
        ("guardrails", False),
        ("red_team", True),
    ]
    assert any("capitals" in issue for issue in rounds[0]["issues"])


def test_a_challenged_action_is_decided_again(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    objection = finding("send_reminder", target="decision")
    body = start(
        client, generator, analysis(), decision(), draft(), review(objection),
        decision("schedule_call", "phone"), draft(), review(),
    ).json()  # fmt: skip
    assert body["decision"]["action"] == "schedule_call"
    rounds = first_draft(body)["review"]["rounds"]
    assert rounds[0]["decision_revised"] == {"from": "send_reminder", "to": "schedule_call"}
    redecided = generator.structured_calls[4]
    assert redecided["system"] == DECISION_PROMPT
    assert "A compliance reviewer challenged the previous choice" in redecided["user"]
    assert "ask the customer to call" in generator.structured_calls[5]["user"]


def test_a_draft_that_never_passes_is_blocked_and_cannot_be_approved(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    replies = [analysis(), decision()]
    for _ in range(compliance_loop.MAX_ROUNDS):
        replies += [draft(), review(finding())]
    body = start(client, generator, *replies).json()
    message = first_draft(body)
    assert message["status"] == "blocked"
    assert len(message["review"]["rounds"]) == compliance_loop.MAX_ROUNDS
    response = client.post(f"/api/admin/messages/{message['id']}/approve", json={})
    assert response.status_code == 409


# --- human approval ---------------------------------------------------------------------------


def test_an_officer_approves_then_sends(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    message_id = first_draft(body)["id"]
    send = f"/api/admin/messages/{message_id}/send"
    assert client.post(send).status_code == 409  # not approved yet

    approved = client.post(
        f"/api/admin/messages/{message_id}/approve", json={"note": "Looks right."}
    ).json()
    message = first_draft(approved)
    assert message["status"] == "approved" and message["review_note"] == "Looks right."
    assert message["reviewed_at"] and message["edited"] is False

    response = client.post(send)
    assert response.status_code == 409  # no email address on record
    assert response.json()["error"]["code"] == "no_email_address"
    give_email(client)
    message = first_draft(client.post(send).json())
    assert message["status"] == "sent" and message["sent_at"]


def test_an_edit_is_checked_again(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    message = first_draft(body)
    url = f"/api/admin/messages/{message['id']}/approve"

    threat = client.post(url, json={"body": message["body"] + "\nOr we go to court."})
    assert threat.status_code == 422
    assert "court" in threat.json()["error"]["message"]

    edited_body = message["body"].replace("Dear customer,", "Dear customer, thank you.")
    result = first_draft(client.post(url, json={"body": edited_body}).json())
    assert result["status"] == "approved" and result["edited"] is True
    assert result["body"] == edited_body


def test_a_rejected_draft_is_rewritten_with_the_officers_feedback(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    body = start(client, generator, analysis(), decision(), draft(), review()).json()
    message_id = first_draft(body)["id"]
    url = f"/api/admin/messages/{message_id}/reject"
    assert client.post(url, json={}).status_code == 422  # a reason is required
    rejected = client.post(url, json={"note": "Too formal."}).json()
    assert first_draft(rejected)["status"] == "rejected"

    generator.structured_replies = [analysis(), decision(), draft(), review()]
    redrafted = client.post(
        f"/api/admin/conversations/{body['id']}/redraft", json={"feedback": "Be warmer."}
    ).json()
    statuses = [m["status"] for m in redrafted["messages"]]
    assert statuses == ["rejected", "pending_approval"]
    assert "asked for these changes:\nBe warmer." in generator.structured_calls[-2]["user"]


# --- the Communication Agent: replies ------------------------------------------------------


def test_the_customer_reply_is_answered_with_the_whole_thread(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    conversation = sent_conversation(client, generator)
    generator.structured_calls.clear()
    generator.structured_replies = [reply(), review()]
    response = client.post(
        f"/api/admin/conversations/{conversation['id']}/inbound",
        json={"subject": "Re: about your account", "body": "How can I pay?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    sent, received, answer = body["messages"]
    assert received["direction"] == "inbound" and received["intent"] == "question"
    assert received["intent_summary"] == "The customer asked how to pay."
    assert answer["status"] == "pending_approval"
    assert "https://pay.complyagent.example/CA-000001" in answer["body"]

    written, reviewed = generator.structured_calls
    assert written["system"] == CONVERSATION_PROMPT
    assert "How can I pay?" in written["user"]
    history = written["history"]
    assert len(history) == 1 and isinstance(history[0], AIMessage)  # the email that was sent
    assert sent["body"] in history[0].content
    assert reviewed["system"] == RED_TEAM_PROMPT and "How can I pay?" in reviewed["user"]


def test_a_dispute_goes_to_a_person_and_no_reply_is_written(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    conversation = sent_conversation(client, generator)
    generator.structured_calls.clear()
    generator.structured_replies = [reply("dispute")]
    body = client.post(
        f"/api/admin/conversations/{conversation['id']}/inbound",
        json={"body": "This is not my debt."},
    ).json()
    assert body["status"] == "escalated"
    assert "dispute" in body["status_reason"]
    assert [m["direction"] for m in body["messages"]] == ["outbound", "inbound"]
    assert len(generator.structured_calls) == 1  # no Red Team review: nothing was drafted

    generator.structured_replies = [{"intent": "question", "intent_summary": "Asks for news."}]
    later = client.post(
        f"/api/admin/conversations/{conversation['id']}/inbound", json={"body": "Hello?"}
    ).json()
    assert later["messages"][-1]["body"] == "Hello?"  # stored for the person; no new draft
    assert later["messages"][-1]["intent"] == "question"  # still read, in case it says stop
    assert len(generator.structured_calls) == 2
    assert later["messages"][-1]["direction"] == "inbound"


def test_the_model_can_hand_over_too(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    conversation = sent_conversation(client, generator)
    generator.structured_replies = [
        reply("hardship", needs_human=True, human_reason="The customer reports a serious illness.")
    ]
    body = client.post(
        f"/api/admin/conversations/{conversation['id']}/inbound",
        json={"body": "I am in hospital and cannot pay."},
    ).json()
    assert body["status"] == "escalated"
    assert body["status_reason"] == "The customer reports a serious illness."


def test_a_closed_conversation_takes_no_more_emails(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    conversation = sent_conversation(client, generator)
    url = f"/api/admin/conversations/{conversation['id']}"
    closed = client.post(f"{url}/close", json={"note": "Paid in full."}).json()
    assert closed["status"] == "closed" and "Paid in full." in closed["status_reason"]
    response = client.post(f"{url}/inbound", json={"body": "Thanks"})
    assert response.status_code == 409
    # A closed conversation lets a new one start.
    generator.structured_replies = [analysis(), decision(), draft(), review()]
    assert client.post(START.format(1)).status_code == 200
