import json

from app import manage
from app.portfolio import Portfolio

from .conftest import SAMPLES
from .test_portfolio_api import ROWS, write_csv


def load(client, tmp_path, rows=ROWS):
    Portfolio(client.app.state.kb.db).import_data(write_csv(tmp_path / "data.csv", rows))


def test_guidance_asks_the_policies_about_the_customer(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    response = client.post("/api/admin/customers/1/guidance")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["months_late"], body["days_overdue_estimate"], body["latest_bill"]) == (
        2,
        60,
        4000,
    )
    assert [s["key"] for s in body["sections"]] == ["actions", "escalation", "hardship", "contact"]
    actions = body["sections"][0]
    assert "2 months late, about 60 days overdue" in actions["question"]
    assert actions["answer_mode"] == "generated" and actions["sources"]
    # Each section is a full RAG answer, made from the customer's facts, never invented rules.
    # Sections with no matching passage never reach the model.
    asked = [call["question"] for call in generator.calls]
    assert asked == [s["question"] for s in body["sections"] if s["sources"]]
    assert "NT$4,000" in body["sections"][1]["question"]


def test_no_guidance_is_needed_when_nothing_is_overdue(client, indexed, generator, tmp_path):
    load(client, tmp_path)
    body = client.post("/api/admin/customers/2/guidance").json()  # paid in full every month
    assert body["months_late"] == 0 and body["sections"] == []
    assert generator.calls == []


def test_guidance_for_a_missing_customer(client, tmp_path):
    load(client, tmp_path)
    missing = client.post("/api/admin/customers/999/guidance")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_add_documents_and_evaluate_rag_commands(client, tmp_path, capsys):
    kb = client.app.state.kb
    files = [str(p) for p in sorted(SAMPLES.glob("*_policy.*"))]
    assert manage.main(["add-documents", *files], kb=kb) == 0
    assert manage.main(["add-documents", files[0]], kb=kb) == 0  # a duplicate is skipped
    bad = tmp_path / "notes.rtf"
    bad.write_bytes(b"{\\rtf1 notes}")
    assert manage.main(["add-documents", str(bad)], kb=kb) == 1
    out = capsys.readouterr().out
    assert out.count("indexed") == len(files) and "already uploaded" in out and "notes.rtf" in out

    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps(
            [
                {
                    "question": "When must an account be escalated to a human?",
                    "document": "escalation_policy.pdf",
                    "answer_contains": ["scripted"],
                },
                {"question": "Sourdough recipe?", "document": None},
            ]
        )
    )
    assert manage.main(["evaluate-rag", "--questions", str(questions)], kb=kb) == 0
    out = capsys.readouterr().out
    assert "Retrieval: 1/2" in out or "Retrieval: 2/2" in out
    assert "Answers:" in out and "fake-llm" in out


def test_the_shipped_question_set_is_valid():
    from app.evaluation import DEFAULT_QUESTIONS, load_questions

    items = load_questions(DEFAULT_QUESTIONS)
    documents = {p.name for p in SAMPLES.glob("*_policy.*")}
    assert len(items) >= 15
    assert {i["document"] for i in items if i["document"]} == documents
    assert any(i["document"] is None for i in items)
