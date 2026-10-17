"""The customer portfolio: the UCI "Default of Credit Card Clients" data (30,000 customers with
six months of payment history) and the default-risk predictions made for them.

    python -m app.manage import-customers     # download the data and store it
    python -m app.manage train-model          # train the model and score every customer

The data and the model follow notebooks/Data_pipeline_and_Modeling.ipynb.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx

from .db import Database, now_iso

DATA_URL = "https://archive.ics.uci.edu/static/public/350/data.csv"

# The UCI file names its columns X1..X23 and Y; some copies use the descriptive names instead.
_PAY = ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
COLUMNS = (
    ["LIMIT_BAL", "SEX", "EDUCATION", "MARRIAGE", "AGE"]
    + _PAY
    + [f"BILL_AMT{m}" for m in range(1, 7)]
    + [f"PAY_AMT{m}" for m in range(1, 7)]
)
TARGET = "default payment next month"
_ALIASES = {f"X{i}": name for i, name in enumerate(COLUMNS, start=1)} | {"Y": TARGET}

# month_no 1 is the most recent month of history.
MONTHS = {1: "Sep 2005", 2: "Aug 2005", 3: "Jul 2005", 4: "Jun 2005", 5: "May 2005", 6: "Apr 2005"}
SEX = {1: "Male", 2: "Female"}
EDUCATION = {1: "Graduate school", 2: "University", 3: "High school", 4: "Other or unknown"}
MARRIAGE = {1: "Married", 2: "Single", 3: "Other"}

# The same levels as v_customer_risk_report in the team's MySQL schema.
RISK_LEVELS: list[tuple[str, float]] = [("high", 0.7), ("medium", 0.4), ("low", 0.0)]
_RISK_SQL = "CASE WHEN p.default_probability >= 0.7 THEN 'high' " + (
    "WHEN p.default_probability >= 0.4 THEN 'medium' "
    "WHEN p.default_probability IS NOT NULL THEN 'low' END"
)
SORT_COLUMNS = {
    "id": "c.customer_id",
    "probability": "p.default_probability",
    "limit_bal": "c.limit_bal",
    "latest_status": "h.repayment_status",
}

# One row per customer with the three engineered features, using the notebook's formulas.
FEATURES_SQL = """
    SELECT c.customer_id, c.limit_bal, c.age, c.education_code, c.sex, c.default_next_month,
           COALESCE(SUM(h.repayment_status = -1) * 1.0
                    / NULLIF(SUM(h.repayment_status NOT IN (-2, 0)), 0), 1.0)
               AS on_time_payment_rate,
           AVG(MAX(h.repayment_status, 0) * 1.0) AS avg_payment_delay,
           AVG(h.bill_amt * 1.0 / c.limit_bal) AS avg_credit_utilization
    FROM customers c JOIN monthly_history h ON h.customer_id = c.customer_id
"""

_CUSTOMER_ROWS = f"""
    SELECT c.customer_id, c.limit_bal, c.age, c.sex, c.education, c.marriage,
           c.default_next_month AS actual_default, c.data_split,
           h.repayment_status AS latest_status, h.bill_amt AS latest_bill,
           p.default_probability, p.predicted_default, {_RISK_SQL} AS risk_level
    FROM customers c
    LEFT JOIN monthly_history h ON h.customer_id = c.customer_id AND h.month_no = 1
    LEFT JOIN predictions p ON p.customer_id = c.customer_id
"""


class ImportFailed(Exception):
    """The data cannot be loaded; the message says which row and why."""


def risk_level(probability: float | None) -> str | None:
    if probability is None:
        return None
    return next(level for level, floor in RISK_LEVELS if probability >= floor)


def _int(row: dict, column: str, line: int) -> int:
    raw = (row.get(column) or "").strip()
    try:
        return int(float(raw))
    except ValueError:
        raise ImportFailed(f"Row {line}: {column} is not a number ({raw!r}).") from None


class Portfolio:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- import ----------------------------------------------------------------------------

    def import_data(self, path: Path | None = None, replace: bool = False) -> int:
        """Load the dataset from `path`, or download it from UCI. All rows or none."""
        if path is None:
            try:
                response = httpx.get(DATA_URL, timeout=120, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ImportFailed(
                    f"Could not download the dataset from {DATA_URL} ({exc}). "
                    "Download it yourself and pass the file with --csv."
                ) from exc
            text = response.text
        elif path.is_file():
            text = path.read_text(encoding="utf-8-sig")
        else:
            raise ImportFailed(f"No file at {path}.")

        reader = csv.DictReader(io.StringIO(text))
        names = [_ALIASES.get(n, n) for n in reader.fieldnames or []]
        missing = [c for c in [*COLUMNS, TARGET] if c not in names]
        if missing:
            raise ImportFailed(f"The file is missing the column(s): {', '.join(missing)}.")
        reader.fieldnames = names
        customers, history = [], []
        for line, row in enumerate(reader, start=2):
            self._parse(row, line, len(customers) + 1, customers, history)
        if not customers:
            raise ImportFailed("The file has no data rows.")

        with self.db.connect() as conn:
            if conn.execute("SELECT 1 FROM customers LIMIT 1").fetchone():
                if not replace:
                    raise ImportFailed(
                        "Customer data is already loaded. Pass --replace to delete it "
                        "(and its predictions) and load again."
                    )
                conn.execute("DELETE FROM customers")  # history and predictions cascade
                conn.execute("DELETE FROM model_runs")
            conn.executemany(
                "INSERT INTO customers (customer_id, limit_bal, sex, education_code, education, "
                "marriage, age, default_next_month, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                customers,
            )
            conn.executemany("INSERT INTO monthly_history VALUES (?, ?, ?, ?, ?)", history)
        return len(customers)

    @staticmethod
    def _parse(row: dict, line: int, default_id: int, customers: list, history: list) -> None:
        customer_id = _int(row, "ID", line) if row.get("ID") else default_id
        limit, target = _int(row, "LIMIT_BAL", line), _int(row, TARGET, line)
        if limit <= 0:
            raise ImportFailed(f"Row {line}: LIMIT_BAL must be greater than 0.")
        if target not in (0, 1):
            raise ImportFailed(f"Row {line}: the default flag must be 0 or 1.")
        sex = SEX.get(_int(row, "SEX", line))
        if sex is None:
            raise ImportFailed(f"Row {line}: SEX must be 1 or 2.")
        # The notebook merges education codes 5 and 6 into 4 and feeds that code to the model.
        education_code = {5: 4, 6: 4}.get(code := _int(row, "EDUCATION", line), code)
        customers.append(
            (
                customer_id,
                limit,
                sex,
                education_code,
                EDUCATION.get(education_code, EDUCATION[4]),
                MARRIAGE.get(_int(row, "MARRIAGE", line), MARRIAGE[3]),
                _int(row, "AGE", line),
                target,
                now_iso(),
            )
        )
        for month in range(1, 7):
            status = _int(row, _PAY[month - 1], line)
            if not -2 <= status <= 9:
                raise ImportFailed(f"Row {line}: {_PAY[month - 1]} must be between -2 and 9.")
            history.append(
                (
                    customer_id,
                    month,
                    status,
                    _int(row, f"BILL_AMT{month}", line),
                    _int(row, f"PAY_AMT{month}", line),
                )
            )

    # --- dashboard -------------------------------------------------------------------------

    def model(self) -> dict | None:
        """The latest trained model and its scores, or None before `train-model` has run."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM model_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {
            "model_name": row["model_name"],
            "trained_at": row["trained_at"],
            "features": json.loads(row["features"]),
            "metrics": json.loads(row["metrics"]),
        }

    def summary(self) -> dict:
        with self.db.connect() as conn:
            totals = conn.execute(
                "SELECT COUNT(*) AS n, MAX(created_at) AS imported_at, "
                "COALESCE(SUM(limit_bal), 0) AS limits, "
                "COALESCE(SUM(default_next_month), 0) AS defaults FROM customers"
            ).fetchone()
            by_month = conn.execute(
                "SELECT month_no, COUNT(*) AS customers, AVG(repayment_status >= 1) AS late_rate, "
                "SUM(bill_amt) AS billed, SUM(pay_amt) AS paid FROM monthly_history "
                "GROUP BY month_no ORDER BY month_no DESC"
            ).fetchall()
            by_risk = {
                r["risk_level"]: r
                for r in conn.execute(
                    f"SELECT {_RISK_SQL} AS risk_level, COUNT(*) AS customers, "
                    "AVG(c.default_next_month) AS default_rate FROM predictions p "
                    "JOIN customers c ON c.customer_id = p.customer_id GROUP BY 1"
                )
            }
            by_education = conn.execute(
                "SELECT education, COUNT(*) AS customers, AVG(default_next_month) AS default_rate "
                "FROM customers GROUP BY education ORDER BY MIN(education_code = 0), "
                "MIN(CASE education_code WHEN 0 THEN 4 ELSE education_code END)"
            ).fetchall()
        latest = next((m for m in by_month if m["month_no"] == 1), None)
        return {
            "loaded": totals["n"] > 0,
            "imported_at": totals["imported_at"],
            "customers": totals["n"],
            "total_credit_limit": totals["limits"],
            "latest_billed": latest["billed"] if latest else 0,
            "latest_late_rate": latest["late_rate"] if latest else None,
            "actual_defaults": totals["defaults"],
            "model": self.model(),
            "by_risk": [
                {
                    "risk_level": level,
                    "customers": by_risk[level]["customers"] if level in by_risk else 0,
                    "default_rate": by_risk[level]["default_rate"] if level in by_risk else None,
                }
                for level, _ in reversed(RISK_LEVELS)
            ]
            if by_risk
            else [],
            "by_month": [
                {
                    "month": MONTHS.get(m["month_no"], f"Month {m['month_no']}"),
                    "customers": m["customers"],
                    "late_rate": m["late_rate"],
                    "billed": m["billed"],
                    "paid": m["paid"],
                }
                for m in by_month
            ],
            "by_education": [dict(r) for r in by_education],
        }

    # --- customer records ------------------------------------------------------------------

    def list_customers(
        self,
        search: str = "",
        risk: str | None = None,
        actual: str | None = None,
        sort: str = "id",
        descending: bool = False,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        where, params = [], []
        search = search.strip().lstrip("#")
        if search:
            where.append("c.customer_id = ?")
            params.append(int(search) if search.isdigit() else -1)
        if risk:
            where.append(f"{_RISK_SQL} = ?")
            params.append(risk)
        if actual:
            where.append("c.default_next_month = ?")
            params.append(1 if actual == "defaulted" else 0)
        condition = f" WHERE {' AND '.join(where)}" if where else ""
        column = SORT_COLUMNS[sort]
        # Customers without a prediction sort last either way.
        order = f"{column} IS NULL, {column} {'DESC' if descending else 'ASC'}, c.customer_id"
        with self.db.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM customers c "
                f"LEFT JOIN predictions p ON p.customer_id = c.customer_id{condition}",
                params,
            ).fetchone()["n"]
            rows = conn.execute(
                f"{_CUSTOMER_ROWS}{condition} ORDER BY {order} LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "customers": [self._customer(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_customer(self, customer_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                f"{_CUSTOMER_ROWS} WHERE c.customer_id = ?", (customer_id,)
            ).fetchone()
            if not row:
                return None
            features = conn.execute(
                f"{FEATURES_SQL} WHERE c.customer_id = ? GROUP BY c.customer_id", (customer_id,)
            ).fetchone()
            history = conn.execute(
                "SELECT month_no, repayment_status, bill_amt, pay_amt FROM monthly_history "
                "WHERE customer_id = ? ORDER BY month_no",
                (customer_id,),
            ).fetchall()
        customer = self._customer(row)
        for name in ("on_time_payment_rate", "avg_payment_delay", "avg_credit_utilization"):
            customer[name] = features[name] if features else None
        customer["history"] = [
            {**dict(h), "month": MONTHS.get(h["month_no"], f"Month {h['month_no']}")}
            for h in history
        ]
        return customer

    @staticmethod
    def _customer(row) -> dict:
        customer = dict(row)
        customer["actual_default"] = bool(customer["actual_default"])
        if customer["predicted_default"] is not None:
            customer["predicted_default"] = bool(customer["predicted_default"])
        return customer
