"""The customer portfolio: the UCI "Default of Credit Card Clients" data (30,000 customers with
six months of payment history) and the default-risk predictions made for them.

    python -m app.manage import-customers     # download the data and store it
    python -m app.manage score-customers      # score everyone with the notebook's model
    python -m app.manage import-emails FILE   # add the EMAIL column of a copy to the customers

The data and the model follow notebooks/Data_pipeline_and_Modeling.ipynb.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from email_validator import EmailNotValidError, validate_email

from ..core.db import INTEGRITY_ERRORS, Database, now_iso
from .tables import TableError, read_table

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
           COALESCE(SUM(CASE WHEN h.repayment_status = -1 THEN 1 ELSE 0 END) * 1.0
                    / NULLIF(SUM(CASE WHEN h.repayment_status NOT IN (-2, 0) THEN 1 ELSE 0 END),
                             0), 1.0)
               AS on_time_payment_rate,
           AVG(CASE WHEN h.repayment_status > 0 THEN h.repayment_status ELSE 0 END * 1.0)
               AS avg_payment_delay,
           AVG(h.bill_amt * 1.0 / c.limit_bal) AS avg_credit_utilization
    FROM customers c JOIN monthly_history h ON h.customer_id = c.customer_id
"""

_CUSTOMER_ROWS = f"""
    SELECT c.customer_id, c.limit_bal, c.age, c.sex, c.education, c.marriage,
           c.default_next_month AS actual_default, c.data_split, c.email,
           h.repayment_status AS latest_status, h.bill_amt AS latest_bill,
           p.default_probability, p.predicted_default, {_RISK_SQL} AS risk_level
    FROM customers c
    LEFT JOIN monthly_history h ON h.customer_id = c.customer_id AND h.month_no = 1
    LEFT JOIN predictions p ON p.customer_id = c.customer_id
"""


_INSERT_CUSTOMER = (
    "INSERT INTO customers (customer_id, limit_bal, sex, education_code, education, "
    "marriage, age, default_next_month, created_at, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def clean_email(raw: str | None) -> str | None:
    """A valid, lower-cased address, or None when there is none. Raises ValueError if invalid."""
    if raw is None or not str(raw).strip():
        return None
    try:
        return validate_email(str(raw).strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValueError(f"'{raw}' is not a valid email address") from exc


class ImportFailed(Exception):
    """The data cannot be loaded; the message says which row and why."""


class CustomerExists(Exception):
    def __init__(self, customer_id: int) -> None:
        super().__init__(f"Customer {customer_id} already exists.")
        self.customer_id = customer_id


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
        """Load the dataset (CSV, .xls or .xlsx) from `path`, or download it from UCI. All rows or
        none. An EMAIL column, when the file has one, gives each customer's address."""
        if path is None:
            try:
                response = httpx.get(DATA_URL, timeout=120, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ImportFailed(
                    f"Could not download the dataset from {DATA_URL} ({exc}). "
                    "Download it yourself and pass the file with --csv."
                ) from exc
            data = response.content
        elif path.is_file():
            data = path.read_bytes()
        else:
            raise ImportFailed(f"No file at {path}.")

        rows = _read_rows(data, required=[*COLUMNS, TARGET])
        customers, history = [], []
        for line, row in rows:
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
            conn.executemany(_INSERT_CUSTOMER, customers)
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
        try:
            email = clean_email(row.get("EMAIL"))
        except ValueError as exc:
            raise ImportFailed(f"Row {line}: EMAIL {exc}.") from None
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
                email,
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

    def import_emails(self, path: Path) -> dict:
        """Fill in each existing customer's address from a copy of the dataset with ID and EMAIL
        columns. Nothing else changes, so the risk predictions stay. All rows or none."""
        if not path.is_file():
            raise ImportFailed(f"No file at {path}.")
        emails: dict[int, str] = {}
        for line, row in _read_rows(path.read_bytes(), required=["ID", "EMAIL"]):
            customer_id = _int(row, "ID", line)
            try:
                email = clean_email(row.get("EMAIL"))
            except ValueError as exc:
                raise ImportFailed(f"Row {line}: EMAIL {exc}.") from None
            if email:
                emails[customer_id] = email
        if not emails:
            raise ImportFailed("The file has no email addresses.")
        with self.db.connect() as conn:
            known = {r[0] for r in conn.execute("SELECT customer_id FROM customers")}
            conn.executemany(
                "UPDATE customers SET email = ? WHERE customer_id = ?",
                [(email, cid) for cid, email in emails.items() if cid in known],
            )
        updated = sum(1 for cid in emails if cid in known)
        return {
            "updated": updated,
            "unknown_ids": sorted(cid for cid in emails if cid not in known),
            "without_email": len(known) - updated,
        }

    def add_customer(self, record: dict) -> int:
        """Store one customer entered by hand, with six months of history (most recent first).

        The record has the same fields as an imported row, already validated by the API schema.
        Without a customer number, the next free one is used. Returns the customer number.
        """
        with self.db.connect() as conn:
            customer_id = (
                record.get("customer_id")
                or conn.execute(
                    "SELECT COALESCE(MAX(customer_id), 0) + 1 FROM customers"
                ).fetchone()[0]
            )
            code = record["education_code"]
            try:
                conn.execute(
                    _INSERT_CUSTOMER,
                    (
                        customer_id,
                        record["limit_bal"],
                        record["sex"],
                        code,
                        EDUCATION.get(code, EDUCATION[4]),
                        record["marriage"],
                        record["age"],
                        int(record["default_next_month"]),
                        now_iso(),
                        record.get("email"),
                    ),
                )
            except INTEGRITY_ERRORS:
                raise CustomerExists(customer_id) from None
            conn.executemany(
                "INSERT INTO monthly_history VALUES (?, ?, ?, ?, ?)",
                [
                    (customer_id, month, m["repayment_status"], m["bill_amt"], m["pay_amt"])
                    for month, m in enumerate(record["history"], start=1)
                ],
            )
        return customer_id

    # --- dashboard -------------------------------------------------------------------------

    def model(self) -> dict | None:
        """The model the customers were scored with, or None before `score-customers` ran."""
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
                "SELECT month_no, COUNT(*) AS customers,"
                " AVG(CASE WHEN repayment_status >= 1 THEN 1.0 ELSE 0 END) AS late_rate, "
                "SUM(bill_amt) AS billed, SUM(pay_amt) AS paid FROM monthly_history "
                "GROUP BY month_no ORDER BY month_no DESC"
            ).fetchall()
            by_risk = {
                r["risk_level"]: r
                for r in conn.execute(
                    f"SELECT {_RISK_SQL} AS risk_level, COUNT(*) AS customers, "
                    "AVG(c.default_next_month * 1.0) AS default_rate FROM predictions p "
                    "JOIN customers c ON c.customer_id = p.customer_id GROUP BY 1"
                )
            }
            by_education = conn.execute(
                "SELECT education, COUNT(*) AS customers,"
                " AVG(default_next_month * 1.0) AS default_rate "
                "FROM customers GROUP BY education"
                " ORDER BY MIN(CASE WHEN education_code = 0 THEN 1 ELSE 0 END), "
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
        payment: str | None = None,
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
        if payment:
            # Overdue: late in the latest month (repayment status 1 = one month late, and so on).
            where.append(
                "h.repayment_status > 0" if payment == "overdue" else "h.repayment_status <= 0"
            )
        condition = f" WHERE {' AND '.join(where)}" if where else ""
        column = SORT_COLUMNS[sort]
        # Customers without a prediction sort last either way.
        order = f"{column} IS NULL, {column} {'DESC' if descending else 'ASC'}, c.customer_id"
        with self.db.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM customers c "
                "LEFT JOIN monthly_history h ON h.customer_id = c.customer_id AND h.month_no = 1 "
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
        # The ledger (ledger.py): what was paid after the data ends, and an open promise.
        with self.db.connect() as conn:
            paid = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total, MAX(paid_on) AS latest FROM payments"
                " WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            promise = conn.execute(
                "SELECT amount, due_on FROM promises WHERE customer_id = ? AND status = 'open'"
                " ORDER BY id DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
        customer["recorded_paid"] = int(paid["total"])
        customer["last_payment_on"] = paid["latest"]
        customer["open_promise"] = dict(promise) if promise else None
        return customer

    @staticmethod
    def _customer(row) -> dict:
        customer = dict(row)
        customer["actual_default"] = bool(customer["actual_default"])
        if customer["predicted_default"] is not None:
            customer["predicted_default"] = bool(customer["predicted_default"])
        return customer


def _read_rows(data: bytes, required: list[str]) -> list[tuple[int, dict]]:
    """The data rows of a CSV, .xls or .xlsx file as {column: text}, with the line number each came
    from. Column names X1..X23 and Y are read as LIMIT_BAL ... and the default flag."""
    try:
        header, body = read_table(data)
    except TableError as exc:
        raise ImportFailed(str(exc)) from None
    names = [_ALIASES.get(n, n) for n in header]
    missing = [c for c in required if c not in names]
    if missing:
        raise ImportFailed(f"The file is missing the column(s): {', '.join(missing)}.")
    # Line 1 is the header, so the first data row is line 2.
    return [(line, dict(zip(names, row, strict=False))) for line, row in enumerate(body, start=2)]
