"""Customer email addresses: imported from the dataset, shown by the API, never given to an LLM."""

from __future__ import annotations

import csv
import sqlite3

import pytest

from app import manage
from app.core.db import Database
from app.customers.portfolio import ImportFailed, Portfolio

from .test_guidance import load
from .test_portfolio_api import ROWS, entry, write_csv

EMAILS = {1: "Zainab.Khan1@Example.net", 2: "nadia.chaudhry2@example.org"}


def write_xlsx(path, header, rows):
    """A spreadsheet like the UCI download: a row of X1..X23 codes, then the column names."""
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([""] + [f"X{i}" for i in range(1, 24)] + ["Y", ""])
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def with_emails(rows=ROWS):
    names = ["ID", "LIMIT_BAL", "SEX", "EDUCATION", "MARRIAGE", "AGE"]
    names += ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
    names += [f"BILL_AMT{m}" for m in range(1, 7)] + [f"PAY_AMT{m}" for m in range(1, 7)]
    names += ["default payment next month", "EMAIL"]
    return names, [[*r, EMAILS.get(r[0], "")] for r in rows]


def test_a_spreadsheet_with_an_email_column_is_imported(client, tmp_path):
    header, rows = with_emails()
    path = write_xlsx(tmp_path / "data.xlsx", header, rows)
    assert Portfolio(client.app.state.kb.db).import_data(path) == len(ROWS)
    one = client.get("/api/admin/customers/1").json()
    assert one["email"] == "zainab.khan1@example.net"  # validated and lower-cased
    listed = client.get("/api/admin/customers").json()["customers"]
    assert {c["customer_id"]: c["email"] for c in listed} == {
        1: "zainab.khan1@example.net",
        2: "nadia.chaudhry2@example.org",
        3: None,
        4: None,
    }


def test_the_original_file_without_emails_still_imports(client, tmp_path):
    load(client, tmp_path)  # the X1..X23 CSV, no EMAIL column
    assert client.get("/api/admin/customers/1").json()["email"] is None


def test_emails_are_added_to_loaded_customers_without_touching_anything_else(
    client, tmp_path, capsys
):
    load(client, tmp_path)
    db = client.app.state.kb.db
    before = client.get("/api/admin/customers/1").json()
    path = tmp_path / "emails.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ID", "EMAIL"])
        writer.writerows([[1, EMAILS[1]], [2, EMAILS[2]], [999, "nobody@example.com"]])

    assert manage.main(["import-emails", str(path)], db=db) == 0
    out = capsys.readouterr().out
    assert "Added email addresses to 2 customers." in out
    assert "Skipped 1 rows whose ID is not a loaded customer." in out
    assert "2 customers still have no email address." in out
    after = client.get("/api/admin/customers/1").json()
    assert after["email"] == "zainab.khan1@example.net"
    assert {k: v for k, v in after.items() if k != "email"} == {
        k: v for k, v in before.items() if k != "email"
    }


def test_bad_email_files_are_refused_whole(client, tmp_path):
    load(client, tmp_path)
    portfolio = Portfolio(client.app.state.kb.db)
    bad = write_csv(tmp_path / "bad.csv", [[1, "not-an-email"]], header=["ID", "EMAIL"])
    with pytest.raises(ImportFailed, match="Row 2: EMAIL 'not-an-email' is not a valid"):
        portfolio.import_emails(bad)
    no_column = write_csv(tmp_path / "none.csv", [[1]], header=["ID"])
    with pytest.raises(ImportFailed, match="missing the column"):
        portfolio.import_emails(no_column)
    with pytest.raises(ImportFailed, match="No file"):
        portfolio.import_emails(tmp_path / "missing.csv")
    assert client.get("/api/admin/customers/1").json()["email"] is None


def test_customers_added_by_hand_can_have_an_email(client, tmp_path):
    load(client, tmp_path)
    created = client.post("/api/admin/customers", json=entry(email=" Ada@Example.COM "))
    assert created.status_code == 201, created.text
    assert created.json()["customer"]["email"] == "ada@example.com"
    without = client.post("/api/admin/customers", json=entry())
    assert without.json()["customer"]["email"] is None
    bad = client.post("/api/admin/customers", json=entry(email="nope"))
    assert bad.status_code == 422


def test_an_old_database_gets_the_email_column(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, limit_bal INTEGER NOT NULL, "
            "sex TEXT NOT NULL, education_code INTEGER NOT NULL, education TEXT NOT NULL, "
            "marriage TEXT NOT NULL, age INTEGER NOT NULL, default_next_month INTEGER NOT NULL, "
            "data_split TEXT, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO customers VALUES (7, 1000, 'Male', 2, 'University', 'Single', 30, 0, "
            "NULL, '2026-01-01')"
        )
    Database(path)  # opening it upgrades it
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT customer_id, email FROM customers").fetchall() == [(7, None)]


def test_the_email_address_is_never_given_to_the_language_model(
    client, indexed, generator, tmp_path
):
    header, rows = with_emails()
    Portfolio(client.app.state.kb.db).import_data(write_xlsx(tmp_path / "d.xlsx", header, rows))
    generator.structured_replies = [
        {
            "action": "send_reminder",
            "channel": "email",
            "priority": "medium",
            "reasons": [{"text": "Late.", "fact_ids": ["F1"], "source_ids": []}],
            "needs_human_review": False,
        },
        {
            "subject": "Your account",
            "body": "Dear customer, NT${amount_due} is {months_overdue} overdue. Pay at "
            "{payment_link} or call {support_phone}. Ref {reference}. ComplyAgent Finance",
            "source_ids": [1],
        },
    ]
    assert client.post("/api/admin/customers/1/message").json()["status"] == "draft"
    client.post("/api/admin/customers/1/guidance")
    sent = [c["system"] + c["user"] for c in generator.structured_calls]
    sent += [c["question"] + str(c["sources"]) for c in generator.calls]
    assert sent and not any("zainab" in text.lower() for text in sent)


def test_the_header_row_is_still_checked(tmp_path, client):
    path = write_csv(tmp_path / "x.csv", [[1, 2]], header=["A", "B"])
    with pytest.raises(ImportFailed, match="missing the column"):
        Portfolio(client.app.state.kb.db).import_data(path)
