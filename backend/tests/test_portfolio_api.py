import csv
import random

import pytest
from fastapi.testclient import TestClient

from app import manage
from app.portfolio import ImportFailed, Portfolio

HEADER = ["ID"] + [f"X{i}" for i in range(1, 24)] + ["Y"]


def row(cid, limit=50000, sex=2, education=2, marriage=1, age=30, pay=(0,) * 6, bill=(1000,) * 6,
        paid=(500,) * 6, default=0):  # fmt: skip
    return [cid, limit, sex, education, marriage, age, *pay, *bill, *paid, default]


ROWS = [
    row(1, limit=20000, pay=(2, 2, -1, -1, -2, -2), bill=(4000, 2000, 0, 0, 0, 0), default=1),
    row(2, limit=100000, sex=1, education=1, marriage=2, age=45, pay=(-1,) * 6),
    row(3, education=6, marriage=0, pay=(0, 0, 0, 0, 0, 0)),
    row(4, education=3, pay=(3, 2, 1, 0, 0, 0), default=1),
]


def write_csv(path, rows=ROWS, header=HEADER):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def many_rows(count=400):
    """Enough customers to train on: people who pay late default far more often."""
    rng = random.Random(7)
    rows = []
    for cid in range(1, count + 1):
        late = rng.random() < 0.3
        pay = tuple(rng.choice((1, 2, 3)) if late else rng.choice((-1, 0)) for _ in range(6))
        rows.append(
            row(
                cid,
                limit=rng.choice((20000, 50000, 200000)),
                sex=rng.choice((1, 2)),
                education=rng.choice((1, 2, 3)),
                age=rng.randint(22, 60),
                pay=pay,
                bill=tuple(rng.randint(0, 20000) for _ in range(6)),
                default=int(rng.random() < (0.8 if late else 0.08)),
            )
        )
    return rows


@pytest.fixture
def loaded(client, tmp_path):
    Portfolio(client.app.state.kb.db).import_data(write_csv(tmp_path / "data.csv"))
    return client


def ids(response):
    return [c["customer_id"] for c in response.json()["customers"]]


def test_an_empty_portfolio_is_reported_as_not_loaded(client):
    summary = client.get("/api/admin/portfolio/summary").json()
    assert summary["loaded"] is False and summary["customers"] == 0
    assert summary["model"] is None and summary["latest_late_rate"] is None
    assert summary["by_risk"] == summary["by_month"] == summary["by_education"] == []
    assert client.get("/api/admin/customers").json() == {
        "customers": [], "total": 0, "page": 1, "page_size": 25,
    }  # fmt: skip


def test_summary_before_any_model_is_trained(loaded):
    s = loaded.get("/api/admin/portfolio/summary").json()
    assert s["loaded"] is True and s["imported_at"] and s["customers"] == 4
    assert s["total_credit_limit"] == 220000 and s["actual_defaults"] == 2
    assert s["latest_billed"] == 7000 and s["latest_late_rate"] == 0.5
    assert s["model"] is None and s["by_risk"] == []
    assert [m["month"] for m in s["by_month"]] == [
        "Apr 2005", "May 2005", "Jun 2005", "Jul 2005", "Aug 2005", "Sep 2005",
    ]  # fmt: skip
    assert [m["late_rate"] for m in s["by_month"]] == [0, 0, 0, 0.25, 0.5, 0.5]
    assert {g["education"]: (g["customers"], g["default_rate"]) for g in s["by_education"]} == {
        "Graduate school": (1, 0.0),
        "University": (1, 1.0),
        "High school": (1, 1.0),
        "Other or unknown": (1, 0.0),
    }


def test_customers_are_found_filtered_sorted_and_paged(loaded):
    assert ids(loaded.get("/api/admin/customers")) == [1, 2, 3, 4]
    assert ids(loaded.get("/api/admin/customers?search=3")) == [3]
    assert ids(loaded.get("/api/admin/customers?search=%233")) == [3]  # "#3"
    assert ids(loaded.get("/api/admin/customers?search=bob")) == []
    assert ids(loaded.get("/api/admin/customers?actual=defaulted")) == [1, 4]
    assert ids(loaded.get("/api/admin/customers?actual=paid")) == [2, 3]
    assert ids(loaded.get("/api/admin/customers?risk=high")) == []  # nothing is scored yet
    assert ids(loaded.get("/api/admin/customers?sort=latest_status&order=desc")) == [4, 1, 3, 2]
    # Late in the latest month: 4 is 3 months late, 1 is 2; 2 paid in full, 3 paid the minimum.
    overdue = loaded.get("/api/admin/customers?payment=overdue").json()
    assert [c["customer_id"] for c in overdue["customers"]] == [1, 4] and overdue["total"] == 2
    assert ids(loaded.get("/api/admin/customers?payment=current")) == [2, 3]
    assert ids(loaded.get("/api/admin/customers?payment=overdue&actual=paid")) == []
    paged = loaded.get("/api/admin/customers?sort=limit_bal&order=desc&page_size=1&page=2")
    assert ids(paged) == [3] and paged.json()["total"] == 4

    for bad in (
        "sort=password_hash",
        "risk=huge",
        "actual=x",
        "payment=x",
        "page=0",
        "page_size=500",
    ):
        response = loaded.get(f"/api/admin/customers?{bad}")
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "invalid_request"


def test_customer_detail_has_the_history_and_the_notebook_features(loaded):
    c = loaded.get("/api/admin/customers/1").json()
    assert (c["limit_bal"], c["sex"], c["education"], c["marriage"]) == (
        20000, "Female", "University", "Married",
    )  # fmt: skip
    assert c["actual_default"] is True and c["latest_status"] == 2 and c["latest_bill"] == 4000
    assert c["default_probability"] is None and c["risk_level"] is None
    # 2 on-time months out of 4 that count (-2 and 0 do not); delays 2, 2 over six months.
    assert c["on_time_payment_rate"] == 0.5
    assert c["avg_payment_delay"] == pytest.approx(4 / 6)
    assert c["avg_credit_utilization"] == pytest.approx((0.2 + 0.1) / 6)
    assert [h["month"] for h in c["history"]][0::5] == ["Sep 2005", "Apr 2005"]
    assert c["history"][0] == {
        "month_no": 1, "month": "Sep 2005", "repayment_status": 2, "bill_amt": 4000, "pay_amt": 500,
    }  # fmt: skip

    no_history_that_counts = loaded.get("/api/admin/customers/3").json()
    assert no_history_that_counts["on_time_payment_rate"] == 1.0  # as the notebook fills it
    assert no_history_that_counts["education"] == "Other or unknown"
    assert no_history_that_counts["marriage"] == "Other"

    missing = loaded.get("/api/admin/customers/999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert loaded.get("/api/admin/customers/abc").status_code == 422


def test_portfolio_is_closed_to_ordinary_users(loaded):
    account = {"email": "ada@example.com", "password": "S3cure-pass-phrase"}
    loaded.post(
        "/api/auth/register",
        json={"full_name": "Ada Lovelace", **account},
        headers={"Authorization": ""},
    )
    with TestClient(loaded.app) as member:
        login = member.post("/api/auth/login", json=account)
        assert login.status_code == 200, login.text
        for path in (
            "/api/admin/portfolio/summary",
            "/api/admin/customers",
            "/api/admin/customers/1",
        ):
            response = member.get(path)
            assert response.status_code == 403, path
            assert response.json()["error"]["code"] == "forbidden"


def test_import_accepts_named_columns_and_refuses_to_double_load(loaded, tmp_path):
    portfolio = Portfolio(loaded.app.state.kb.db)
    with pytest.raises(ImportFailed, match="already loaded"):
        portfolio.import_data(tmp_path / "data.csv")
    named = ["LIMIT_BAL", "SEX", "EDUCATION", "MARRIAGE", "AGE"]
    named += ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
    named += [f"BILL_AMT{m}" for m in range(1, 7)] + [f"PAY_AMT{m}" for m in range(1, 7)]
    named += ["default payment next month"]
    path = write_csv(tmp_path / "named.csv", [r[1:] for r in ROWS[:2]], named)  # no ID column
    assert portfolio.import_data(path, replace=True) == 2
    assert ids(loaded.get("/api/admin/customers")) == [1, 2]


def test_a_bad_file_imports_nothing(client, tmp_path):
    portfolio = Portfolio(client.app.state.kb.db)
    cases = {
        "Row 2: LIMIT_BAL is not a number": [row(1, limit="lots")],
        "Row 2: LIMIT_BAL must be greater than 0": [row(1, limit=0)],
        "Row 3: the default flag must be 0 or 1": [row(1), row(2, default=2)],
        "Row 2: SEX must be 1 or 2": [row(1, sex=3)],
        "Row 2: PAY_2 must be between -2 and 9": [row(1, pay=(0, 12, 0, 0, 0, 0))],
        "no data rows": [],
    }
    for message, rows in cases.items():
        with pytest.raises(ImportFailed, match=message):
            portfolio.import_data(write_csv(tmp_path / "bad.csv", rows))
    with pytest.raises(ImportFailed, match="No file at"):
        portfolio.import_data(tmp_path / "missing.csv")
    with pytest.raises(ImportFailed, match="missing the column"):
        portfolio.import_data(write_csv(tmp_path / "short.csv", [[1, 2]], ["ID", "X1"]))
    assert client.get("/api/admin/portfolio/summary").json()["loaded"] is False


def test_training_scores_every_customer_and_reports_the_model(client, tmp_path, capsys):
    db = client.app.state.kb.db
    path = write_csv(tmp_path / "many.csv", many_rows())
    with pytest.raises(SystemExit, match="not enough customer data"):
        manage.main(["train-model"], db=db, models_dir=tmp_path / "models")
    assert manage.main(["import-customers", "--csv", str(path)], db=db) == 0
    assert manage.main(["train-model"], db=db, models_dir=tmp_path / "models") == 0
    out = capsys.readouterr().out
    assert "Imported 400 customers" in out and "scored 400 customers" in out
    assert (tmp_path / "models" / "default_risk_xgboost.json").is_file()

    s = client.get("/api/admin/portfolio/summary").json()
    model = s["model"]
    assert model["model_name"] == "XGBoost (tuned)" and len(model["features"]) == 7
    assert model["metrics"]["test"]["customers"] == 60  # 15% held out
    assert model["metrics"]["test"]["roc_auc"] > 0.75  # late payers are easy to tell apart
    assert [g["risk_level"] for g in s["by_risk"]] == ["low", "medium", "high"]
    assert sum(g["customers"] for g in s["by_risk"]) == 400
    low, _, high = s["by_risk"]
    assert high["default_rate"] > low["default_rate"]

    riskiest = client.get("/api/admin/customers?sort=probability&order=desc&page_size=5").json()
    first = riskiest["customers"][0]
    assert first["risk_level"] == "high" and first["predicted_default"] is True
    assert first["default_probability"] >= riskiest["customers"][-1]["default_probability"]
    assert first["data_split"] in ("train", "validation", "test")
    high_total = client.get("/api/admin/customers?risk=high").json()["total"]
    assert high_total == high["customers"]

    # Loading new data removes the predictions made for the old customers.
    assert manage.main(["import-customers", "--csv", str(path), "--replace"], db=db) == 0
    assert client.get("/api/admin/portfolio/summary").json()["model"] is None


def entry(**overrides):
    """A customer as the "Add customer" form sends it: most recent month first."""
    body = {
        "limit_bal": 80000, "sex": "Female", "education_code": 2, "marriage": "Single", "age": 34,
        "default_next_month": False,
        "history": [
            {"repayment_status": 2, "bill_amt": 40000, "pay_amt": 0},
            {"repayment_status": 1, "bill_amt": 38000, "pay_amt": 1000},
            *[{"repayment_status": -1, "bill_amt": 20000, "pay_amt": 20000}] * 4,
        ],
    }  # fmt: skip
    return {**body, **overrides}


def test_a_customer_entered_by_hand_is_stored_with_its_history(loaded):
    created = loaded.post("/api/admin/customers", json=entry())
    assert created.status_code == 201, created.text
    body = created.json()
    customer = body["customer"]
    assert customer["customer_id"] == 5  # the next free number after the four imported
    assert body["scored"] is False and customer["default_probability"] is None  # no model yet
    assert customer["education"] == "University" and customer["latest_status"] == 2
    assert [h["month"] for h in customer["history"]][:2] == ["Sep 2005", "Aug 2005"]
    assert customer["on_time_payment_rate"] == pytest.approx(4 / 6)
    assert customer["avg_payment_delay"] == pytest.approx(0.5)
    assert loaded.get("/api/admin/portfolio/summary").json()["customers"] == 5

    chosen = loaded.post(
        "/api/admin/customers", json=entry(customer_id=900, default_next_month=True)
    )
    assert chosen.json()["customer"]["customer_id"] == 900
    assert chosen.json()["customer"]["actual_default"] is True


def test_a_taken_number_or_a_bad_record_stores_nothing(loaded):
    taken = loaded.post("/api/admin/customers", json=entry(customer_id=1))
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "customer_exists"
    for bad in (
        entry(limit_bal=0),
        entry(age=12),
        entry(education_code=7),
        entry(history=entry()["history"][:5]),
        entry(history=[{"repayment_status": 12, "bill_amt": 0, "pay_amt": 0}] * 6),
    ):
        response = loaded.post("/api/admin/customers", json=bad)
        assert response.status_code == 422, bad
    assert loaded.get("/api/admin/portfolio/summary").json()["customers"] == 4


def test_ordinary_users_cannot_add_customers(loaded):
    account = {"email": "ada@example.com", "password": "S3cure-pass-phrase"}
    loaded.post(
        "/api/auth/register",
        json={"full_name": "Ada Lovelace", **account},
        headers={"Authorization": ""},
    )
    with TestClient(loaded.app) as member:
        csrf = member.post("/api/auth/login", json=account).json()["csrf_token"]
        response = member.post("/api/admin/customers", json=entry(), headers={"X-CSRF-Token": csrf})
        assert response.status_code == 403


def test_a_customer_added_after_training_is_scored_by_the_model(client, tmp_path):
    db = client.app.state.kb.db
    models_dir = client.app.state.kb.settings.models_dir
    assert (
        manage.main(
            ["import-customers", "--csv", str(write_csv(tmp_path / "m.csv", many_rows()))], db=db
        )
        == 0
    )
    assert manage.main(["train-model"], db=db, models_dir=models_dir) == 0

    late = client.post("/api/admin/customers", json=entry(history=[
        {"repayment_status": 3, "bill_amt": 70000, "pay_amt": 0}] * 6)).json()  # fmt: skip
    prompt = client.post("/api/admin/customers", json=entry(history=[
        {"repayment_status": -1, "bill_amt": 5000, "pay_amt": 5000}] * 6)).json()  # fmt: skip
    assert late["scored"] is True and prompt["scored"] is True
    assert late["customer"]["risk_level"] == "high"
    assert late["customer"]["default_probability"] > prompt["customer"]["default_probability"]
