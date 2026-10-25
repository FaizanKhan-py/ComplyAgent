"""The message generator, through the real API, policies and database; only the LLM is scripted.

Customer 1 is 2 months late with a latest bill of NT$4,000; customer 2 is not overdue.
"""

from __future__ import annotations

import pytest

from app.messages import MESSAGE_PROMPT, MessageDraft, problems

from .test_guidance import load
from .test_portfolio_api import ROWS, row

URL = "/api/admin/customers/{}/message"
VALUES = {
    "amount_due": "4,000",
    "months_overdue": "2 months",
    "payment_link": "https://pay.complyagent.example/CA-000001",
    "support_phone": "0800-000-123",
    "reference": "CA-000001",
}
EMAIL = (
    "Dear customer,\n\nYour payment of NT${amount_due} is {months_overdue} overdue. "
    "Please call {support_phone} or pay at {payment_link}.\n\nReference: {reference}\n"
    "ComplyAgent Finance, Collections Team"
)


def decision(action="send_reminder", channel="sms") -> dict:
    return {
        "action": action,
        "channel": channel,
        "priority": "medium",
        "reasons": [{"text": "Two months late.", "fact_ids": ["F1"], "source_ids": [1]}],
        "needs_human_review": False,
    }


def draft(body=EMAIL, subject="ComplyAgent Finance: about your account", source_ids=(1,)) -> dict:
    return {"subject": subject, "body": body, "source_ids": list(source_ids)}


@pytest.mark.parametrize("channel", ["sms", "email", "phone"])
def test_an_email_is_written_whatever_channel_the_decision_suggests(
    client, indexed, generator, tmp_path, channel
):
    load(client, tmp_path)
    generator.structured_replies = [decision(channel=channel), draft()]
    response = client.post(URL.format(1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft" and body["reason"] is None
    assert body["subject"] == "ComplyAgent Finance: about your account"
    assert body["body"] == (
        "Dear customer,\n\nYour payment of NT$4,000 is 2 months overdue. Please call "
        "0800-000-123 or pay at https://pay.complyagent.example/CA-000001.\n\n"
        "Reference: CA-000001\nComplyAgent Finance, Collections Team"
    )
    assert any("of 150 words" in check for check in body["checks"])
    assert body["sources"] and body["corrected"] == []

    decided, written = generator.structured_calls
    assert written["system"] == MESSAGE_PROMPT and written["schema"] is MessageDraft
    assert "The email must remind the customer" in written["user"]
    assert "Two months late." in written["user"]  # the Decision Agent's reasons
    assert "messaging_policy.docx" in written["user"]  # the messaging policy is retrieved


def test_the_email_carries_out_the_chosen_action(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    generator.structured_replies = [decision("schedule_call", "phone"), draft()]
    client.post(URL.format(1))
    assert "ask the customer to call {support_phone}" in generator.structured_calls[1]["user"]


def test_a_draft_that_breaks_the_rules_is_sent_back_once(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    bad = draft(EMAIL.replace("NT${amount_due}", "NT$4,000"), subject="Your debt is overdue!")
    generator.structured_replies = [decision(), bad, draft(subject="Your account")]
    body = client.post(URL.format(1)).json()
    assert body["status"] == "draft" and body["subject"] == "Your account"
    assert "NT$4,000" in body["body"] and "0800-000-123" in body["body"]
    assert any("never as a number" in c for c in body["corrected"])
    assert any('"debt"' in c for c in body["corrected"])
    assert "Your previous draft broke these rules" in generator.structured_calls[2]["user"]


def test_a_draft_that_stays_wrong_is_never_shown(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    shouting = draft("PAY NOW. " + EMAIL)
    generator.structured_replies = [decision(), shouting, shouting, shouting]
    response = client.post(URL.format(1))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_bad_output"


def test_no_email_is_written_when_the_decision_is_no_contact(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    generator.structured_replies = [decision("no_contact", "none")]
    body = client.post(URL.format(1)).json()
    assert body["status"] == "not_written" and body["body"] is None
    assert body["decision"]["action"] == "no_contact"
    assert len(generator.structured_calls) == 1  # the writer is never asked


def test_an_escalated_customer_gets_an_email_saying_an_officer_will_call(
    client, indexed, generator, tmp_path
):
    load(client, tmp_path)
    generator.structured_replies = [decision("escalate_to_human", "phone"), draft()]
    body = client.post(URL.format(1)).json()
    assert body["status"] == "draft"
    assert "passed to a collections officer" in generator.structured_calls[1]["user"]


def score(client, customer_id: int, probability: float) -> None:
    """Gives a customer a risk prediction without training the model."""
    with client.app.state.kb.db.connect() as conn:
        run = conn.execute(
            "INSERT INTO model_runs (model_name, hyperparameters, features, metrics, trained_at) "
            "VALUES ('test', '{}', '[]', '{}', '2026-01-01')"
        ).lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO predictions VALUES (?, ?, ?, ?)",
            (customer_id, run, probability, int(probability >= 0.5)),
        )


@pytest.mark.parametrize(("probability", "level"), [(0.2, "low"), (0.5, "medium"), (0.9, "high")])
def test_the_tone_follows_the_risk_level(client, indexed, generator, tmp_path, probability, level):
    load(client, tmp_path)
    score(client, 1, probability)
    # MG-04 sets a deadline for medium (7 days) and high (3 days) risk, and none for low.
    deadline = {"low": None, "medium": "within 7 days", "high": "within 3 days"}[level]
    generator.structured_replies = [decision(), draft()]
    if deadline:  # the first draft leaves the deadline out, so it is sent back for it
        generator.structured_replies.append(draft(EMAIL + f" Please pay or call {deadline}."))
    body = client.post(URL.format(1)).json()
    assert body["status"] == "draft" and body["risk_level"] == level
    written = generator.structured_calls[1]["user"]
    assert f"Risk level: {level}. Write in the policy's tone for a {level}-risk customer" in written
    assert "that rule is in source" in written
    assert f"Collections message tone for a {level}-risk customer" in written  # from MG-04
    assert "chance of default" not in written  # the score itself is never given to the writer
    if deadline:
        assert f'Ask the customer to pay or get in touch "{deadline}"' in written  # told upfront
        assert any(f'act "{deadline}"' in c for c in body["corrected"])
        assert deadline in body["body"] and any(deadline in c for c in body["checks"])
    else:
        assert body["corrected"] == []


def test_an_unscored_customer_gets_the_stage_tone(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    generator.structured_replies = [decision(), draft()]
    body = client.post(URL.format(1)).json()
    assert body["risk_level"] is None
    assert "Risk level: not scored" in generator.structured_calls[1]["user"]


def test_customers_with_nothing_to_collect_get_no_email(client, indexed, generator, tmp_path):
    load(client, tmp_path, ROWS + [row(9, pay=(2,) * 6, bill=(0,) * 6)])
    current = client.post(URL.format(2)).json()  # paid in full every month
    assert current["status"] == "not_written" and "not overdue" in current["reason"]
    zero = client.post(URL.format(9)).json()  # late, but the latest bill is NT$0
    assert zero["status"] == "not_written" and "nothing owed" in zero["reason"]
    assert generator.structured_calls == []
    assert client.post(URL.format(999)).status_code == 404


def check(body: str = EMAIL, subject: str = "Your account", source_ids=(1,)) -> list[str]:
    draft = MessageDraft(subject=subject, body=body, source_ids=list(source_ids))
    return problems(draft, VALUES, {1})


def test_the_guardrails():
    assert check() == []

    def has(found: list[str], text: str) -> bool:
        return any(text in f for f in found)

    assert has(check(EMAIL.replace("ComplyAgent Finance", "We")), "name the organisation")
    assert has(check(EMAIL.replace("{payment_link}", "the link")), "{payment_link}")
    assert has(check(EMAIL + " {customer_name}"), "unknown placeholders: customer_name")
    assert has(check(EMAIL + " Visit https://evil.example"), "only as {payment_link}")
    assert has(check(EMAIL + " Pay NT$9,999 today."), "never as a number")
    assert has(check(EMAIL + " word" * 150), "the limit is 150")
    assert has(check(EMAIL + " Or we take legal action."), "legal action")
    assert has(check(EMAIL + " Our AI wrote this."), "remove these words: ai")
    assert has(check(EMAIL + " Your risk score is high."), "risk score")
    assert has(check(EMAIL + " As a high-risk customer, pay now."), "high-risk")
    assert has(check(EMAIL + " Your risk level is low."), "risk level")
    assert has(check(EMAIL + " Pay now to avoid further action."), "further action")
    assert has(check(EMAIL + " There will be consequences."), "consequences")
    assert has(check(EMAIL + " Pay to avoid further escalation."), "further escalation")
    # The month count may be written out, but only as the true value.
    assert check(EMAIL.replace("{months_overdue}", "2 months")) == []
    assert has(
        check(EMAIL.replace("{months_overdue}", "3 months")), "the account is 2 months overdue"
    )
    assert has(check(EMAIL + " PAY NOW."), "no words in capitals: NOW, PAY")
    assert has(check(EMAIL + " Now! Today!"), "one exclamation mark")
    assert has(check(subject=""), "needs a subject")
    assert has(check(subject="NT${amount_due} due"), "must not contain the amount")
    assert has(check(subject="Your debt"), '"debt"')
    assert has(check(source_ids=(7,)), "source_ids")
    draft = MessageDraft(subject="Your account", body=EMAIL, source_ids=[1])
    assert has(problems(draft, VALUES, {1}, "within 7 days"), 'act "within 7 days"')
    assert problems(draft.model_copy(update={"body": EMAIL + " Please pay within 7 days."}),
                    VALUES, {1}, "within 7 days") == []  # fmt: skip


def test_the_risk_rule_and_its_deadline_are_read_from_the_policy():
    from app.messages import risk_rule

    passages = [
        {"number": 1, "text": "Contact hours are 8 to 9."},
        {"number": 2, "text": "Low risk | Collections message tone for a low-risk customer: polite."
         "\n\nMedium risk | Collections message tone for a medium-risk customer: asks the "
         "customer to pay or get in touch within 7 days."},
    ]  # fmt: skip
    assert risk_rule(passages, "medium") == (2, "within 7 days")
    assert risk_rule(passages, "low") == (2, None)  # the low rule sets no deadline
    assert risk_rule(passages, "high") == (None, None)
    assert risk_rule(passages, None) == (None, None)
