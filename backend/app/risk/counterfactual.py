"""The Counterfactual Simulator: "what if this customer's record were different?"

The notebook's model is run on a changed copy of one customer; nothing in the database changes.

    scenarios   ready-made what-ifs about payment behaviour: pays this month's bill in full,
                one month less late, catches up completely, misses next month, a higher limit
    fairness    the same customer with only a personal attribute changed (age, and sex or
                education if the model still uses them). Collections decisions never use them,
                so this shows how much the model's prediction depends on who the customer is (a
                large change is a fairness finding to report). The notebook no longer trains on
                sex or education; a model that does is still checked.
    custom      the officer's own changes: the latest month's status, bill and payment, and the
                credit limit; optionally the Decision Agent then decides for the changed record

The three engineered features are computed exactly as portfolio.FEATURES_SQL computes them.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np

from ..customers.portfolio import EDUCATION, risk_level
from .model_files import ModelFiles

EDUCATION_CODES = {name: code for code, name in EDUCATION.items()}


def features(customer: dict) -> dict:
    """The engineered features of a (possibly changed) customer, as FEATURES_SQL computes them."""
    history = customer["history"]
    statuses = [h["repayment_status"] for h in history]
    counted = [s for s in statuses if s not in (-2, 0)]
    on_time = sum(1 for s in statuses if s == -1) / len(counted) if counted else 1.0
    limit = customer["limit_bal"]
    return {
        "on_time_payment_rate": on_time,
        "avg_payment_delay": sum(max(s, 0) for s in statuses) / len(statuses),
        "avg_credit_utilization": sum(h["bill_amt"] / limit for h in history) / len(history),
    }


def _model_row(customer: dict) -> dict:
    return {
        "limit_bal": customer["limit_bal"],
        "age": customer["age"],
        "education_code": EDUCATION_CODES.get(customer["education"], 4),
        "sex": customer["sex"],
        **features(customer),
    }


def _changed(customer: dict, **months_and_limit) -> dict:
    """A deep copy with the latest month (month_no 1) and the limit changed."""
    c = copy.deepcopy(customer)
    latest = c["history"][0]
    for key in ("repayment_status", "bill_amt", "pay_amt"):
        if months_and_limit.get(key) is not None:
            latest[key] = months_and_limit[key]
    if months_and_limit.get("limit_bal") is not None:
        c["limit_bal"] = months_and_limit["limit_bal"]
    c["latest_status"] = latest["repayment_status"]
    c["latest_bill"] = latest["bill_amt"]
    return c


def scenarios(customer: dict) -> list[dict]:
    """The ready-made what-ifs that apply to this customer: (key, label, kind, changed record)."""
    latest = customer["history"][0]
    status = latest["repayment_status"]
    found = []

    def add(key: str, label: str, kind: str, changed: dict) -> None:
        found.append({"key": key, "label": label, "kind": kind, "customer": changed})

    if status > 0:
        add(
            "pays_in_full",
            "Pays this month's bill in full",
            "behaviour",
            _changed(customer, repayment_status=-1, pay_amt=max(latest["bill_amt"], 0)),
        )
        add(
            "one_month_less_late",
            "One month less late",
            "behaviour",
            _changed(customer, repayment_status=status - 1),
        )
        caught_up = copy.deepcopy(customer)
        for h in caught_up["history"]:
            h["repayment_status"] = min(h["repayment_status"], 0)
        caught_up["latest_status"] = caught_up["history"][0]["repayment_status"]
        add("catches_up", "Catches up on every month", "behaviour", caught_up)
    # Next month comes in late: the history moves on a month, the oldest month drops out.
    later = copy.deepcopy(customer)
    newest = {**later["history"][0], "repayment_status": max(status, 0) + 1, "pay_amt": 0}
    later["history"] = [newest, *later["history"][:-1]]
    later["latest_status"] = newest["repayment_status"]
    add("misses_next_month", "Misses next month's payment too", "behaviour", later)
    add("limit_up", "Credit limit 20% higher", "behaviour",
        _changed(customer, limit_bal=round(customer["limit_bal"] * 1.2)))  # fmt: skip

    other_sex = "Female" if customer["sex"] == "Male" else "Male"
    add("sex", f"Same record, sex {other_sex.lower()}", "fairness", {**customer, "sex": other_sex})
    for code, name in EDUCATION.items():
        if name != customer["education"]:
            add(f"education_{code}", f"Same record, education: {name.lower()}", "fairness",
                {**customer, "education": name})  # fmt: skip
    for years in (-10, 10):
        age = customer["age"] + years
        if 18 <= age <= 100:
            older = "older" if years > 0 else "younger"
            label = f"Same record, {abs(years)} years {older}"
            add(f"age_{years:+d}", label, "fairness", {**customer, "age": age})
    return found


# The personal attributes a model may use, by its input name, and the scenarios that test them.
PERSONAL_INPUTS = {"AGE": ("age", "age_"), "sex_male": ("sex", "sex"),
                   "EDUCATION_cleaned": ("education", "education_")}  # fmt: skip


def _score(model, customers: list[dict]) -> list[float]:
    rows = [model.record_vector(_model_row(c)) for c in customers]
    return [float(p) for p in model.predict(np.array(rows))]


def _result(model, probability: float) -> dict:
    return {
        "probability": round(probability, 4),
        "risk_level": risk_level(probability),
        "predicted_default": probability >= model.threshold,
    }


def simulate(models_dir: Path, customer: dict, custom: dict | None = None) -> dict:
    """The model's prediction for the record, every ready-made what-if, and the custom change.
    Raises ModelMissing when no model files can be used."""
    model = ModelFiles(models_dir).load()
    # Only test the personal attributes this model actually uses.
    unused = [prefix for name, (_, prefix) in PERSONAL_INPUTS.items() if name not in model.features]
    found = [
        s for s in scenarios(customer)
        if s["kind"] != "fairness" or not any(s["key"].startswith(p) for p in unused)
    ]  # fmt: skip
    changed = _changed(customer, **custom) if custom else None
    records = [customer, *(s["customer"] for s in found), *([changed] if changed else [])]
    probabilities = _score(model, records)
    base = probabilities[0]

    def entry(probability: float) -> dict:
        return {**_result(model, probability), "change": round(probability - base, 4)}

    result = {
        "customer_id": customer["customer_id"],
        "model_name": model.metadata.get("model_name"),
        "threshold": model.threshold,
        "base": _result(model, base),
        "personal_inputs": [
            label for name, (label, _) in PERSONAL_INPUTS.items() if name in model.features
        ],
        "scenarios": [
            {"key": s["key"], "label": s["label"], "kind": s["kind"], **entry(p)}
            for s, p in zip(found, probabilities[1 : len(found) + 1], strict=True)
        ],
        "custom": None,
    }
    if changed:
        result["custom"] = {
            **entry(probabilities[-1]),
            "changes": {k: v for k, v in custom.items() if v is not None},
            "features": {k: round(v, 4) for k, v in features(changed).items()},
            "record": changed,
        }
    return result
