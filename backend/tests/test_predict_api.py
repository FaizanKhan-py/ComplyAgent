import csv
import io

import pytest

from .helpers import NOTEBOOK_FEATURES as FEATURES
from .helpers import save_model_files
from .test_portfolio_api import HEADER, ROWS, many_rows


def as_csv(rows, header=HEADER) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode()


@pytest.fixture
def saved_model(settings):
    """The notebook's model files, with a small model in which late payers default."""
    save_model_files(settings.risk_model_dir)


def predict(client, data: bytes, name="customers.csv"):
    return client.post("/api/admin/predict/dataset", files={"file": (name, data, "text/csv")})


def test_without_a_saved_model_it_says_how_to_make_one(client):
    info = client.get("/api/admin/predict/model").json()
    assert info["available"] is False and "notebook" in info["message"].lower()
    response = predict(client, as_csv(ROWS))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_unavailable"


def test_scores_every_row_and_fills_in_the_target(client, saved_model):
    assert client.get("/api/admin/predict/model").json()["features"] == FEATURES
    response = predict(client, as_csv(ROWS))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"] == len(ROWS)
    assert [p["id"] for p in body["predictions"]] == [1, 2, 3, 4]
    # Rows 1 and 4 pay months late; rows 2 and 3 do not.
    assert [p["predicted_default"] for p in body["predictions"]] == [1, 0, 0, 1]
    assert body["predicted_defaults"] == 2
    assert sum(body["by_risk"].values()) == len(ROWS)
    # The file had real outcomes (Y), so they are kept and compared.
    assert body["scores"]["accuracy"] == 1.0
    out = list(csv.DictReader(io.StringIO(body["csv"])))
    assert [r["Y"] for r in out] == ["1", "0", "0", "1"]
    assert [r["actual default payment next month"] for r in out] == ["1", "0", "0", "1"]
    assert all(0 <= float(r["default_probability"]) <= 1 for r in out)


def test_a_file_without_the_target_column_gets_one(client, saved_model):
    rows = [r[:-1] for r in many_rows(50)]
    body = predict(client, as_csv(rows, HEADER[:-1])).json()
    assert body["rows"] == 50 and body["scores"] is None
    out = list(csv.DictReader(io.StringIO(body["csv"])))
    assert all(r["default payment next month"] in ("0", "1") for r in out)


def test_descriptive_names_and_a_second_header_row_are_accepted(client, saved_model):
    names = ["ID", "LIMIT_BAL", "SEX", "EDUCATION", "MARRIAGE", "AGE"]
    names += ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
    names += [f"BILL_AMT{m}" for m in range(1, 7)] + [f"PAY_AMT{m}" for m in range(1, 7)]
    names += ["default payment next month"]
    assert predict(client, as_csv(ROWS, names)).json()["rows"] == 4
    # The UCI spreadsheet saved as CSV: X1..X23 and then the real names.
    assert predict(client, as_csv([names, *ROWS])).json()["rows"] == 4


@pytest.mark.parametrize(
    ("data", "name", "status", "message"),
    [
        (as_csv(ROWS), "customers.pdf", 415, ".csv"),
        (b"", "customers.csv", 422, "empty"),
        (as_csv([]), "customers.csv", 422, "no data rows"),
        (as_csv([r[:-1] for r in ROWS], HEADER[:-2] + ["Y"]), "c.csv", 422, "PAY_AMT6"),
        (as_csv([[1, "abc", *ROWS[0][2:]]]), "customers.csv", 422, "LIMIT_BAL is not a number"),
        (as_csv([[1, 0, *ROWS[0][2:]]]), "customers.csv", 422, "greater than 0"),
    ],
)
def test_bad_files_are_refused_with_a_reason(client, saved_model, data, name, status, message):
    response = predict(client, data, name)
    assert response.status_code == status, response.text
    assert message in response.json()["error"]["message"]


def test_only_administrators_can_score(client, saved_model):
    client.headers.pop("Authorization")
    assert predict(client, as_csv(ROWS)).status_code == 401


def as_xlsx(rows, header=HEADER) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    for row in [header, *rows]:
        book.active.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_excel_files_are_read_from_their_contents(client, saved_model):
    body = predict(client, as_xlsx(ROWS), "customers.xlsx").json()
    assert [p["predicted_default"] for p in body["predictions"]] == [1, 0, 0, 1]
    # A spreadsheet whose name says .csv (e.g. renamed with extensions hidden) still reads.
    assert predict(client, as_xlsx(ROWS), "customers.csv").json()["rows"] == 4
    assert predict(client, as_xlsx(ROWS), "customers.xls").json()["rows"] == 4
