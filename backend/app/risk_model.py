"""Trains the default-risk model from notebooks/Data_pipeline_and_Modeling.ipynb and scores
every customer with it.

The notebook compares four models and tunes XGBoost with a grid search; this module trains that
final model directly, with the hyperparameters the grid search chose. The same seven features,
the same 70/15/15 stratified split and the same class weighting are used.

It needs scikit-learn and xgboost (requirements.txt). The customer pages do not: they only read
the predictions this module saves.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .db import Database, now_iso
from .portfolio import FEATURES_SQL

MODEL_NAME = "XGBoost (tuned)"
MODEL_FILE = "default_risk_xgboost.json"
# In the order the notebook's training table has them.
FEATURES = [
    "limit_bal",
    "age",
    "on_time_payment_rate",
    "avg_payment_delay",
    "avg_credit_utilization",
    "education_code",
    "sex_male",
]
# Best parameters found by the notebook's GridSearchCV (optimised for recall).
HYPERPARAMETERS = {
    "n_estimators": 200,
    "max_depth": 3,
    "learning_rate": 0.1,
    "subsample": 0.8,
    "colsample_bytree": 1.0,
    "min_child_weight": 5,
}
THRESHOLD = 0.5
SEED = 42


class TrainingFailed(Exception):
    pass


def load_features(db: Database) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(customer ids, feature matrix, default flags) for every customer."""
    with db.connect() as conn:
        rows = conn.execute(f"{FEATURES_SQL} GROUP BY c.customer_id ORDER BY c.customer_id")
        rows = rows.fetchall()
    ids = np.array([r["customer_id"] for r in rows])
    matrix = np.array(
        [
            [
                float(r["sex"] == "Male") if name == "sex_male" else float(r[name])
                for name in FEATURES
            ]
            for r in rows
        ]
    ).reshape(len(rows), len(FEATURES))
    return ids, matrix, np.array([r["default_next_month"] for r in rows])


def _scores(y_true: np.ndarray, probability: np.ndarray) -> dict:
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    predicted = (probability >= THRESHOLD).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, predicted, labels=[0, 1]).ravel()
    return {
        "customers": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, predicted)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
    }


def train(db: Database, models_dir: Path) -> dict:
    """Train, evaluate on the validation and test customers, then score everyone."""
    try:
        from sklearn.model_selection import train_test_split
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise TrainingFailed(
            "Training needs scikit-learn and xgboost. Install them with: "
            "pip install -r requirements-ml.txt"
        ) from exc

    ids, features, target = load_features(db)
    if len(ids) < 50 or min(np.bincount(target, minlength=2)) < 10:
        raise TrainingFailed(
            "There is not enough customer data to train on. "
            "Load it first with: python -m app.manage import-customers"
        )

    # 85% / 15% and then 70% / 15% of the whole, exactly as the notebook does.
    index = np.arange(len(ids))
    rest, test = train_test_split(index, test_size=0.15, random_state=SEED, stratify=target)
    fit, val = train_test_split(rest, test_size=0.17647, random_state=SEED, stratify=target[rest])
    model = XGBClassifier(
        **HYPERPARAMETERS,
        scale_pos_weight=float((target[fit] == 0).sum() / (target[fit] == 1).sum()),
        random_state=SEED,
        n_jobs=-1,
    )
    model.fit(features[fit], target[fit])
    probability = model.predict_proba(features)[:, 1]
    metrics = {
        "threshold": THRESHOLD,
        "validation": _scores(target[val], probability[val]),
        "test": _scores(target[test], probability[test]),
    }

    models_dir.mkdir(parents=True, exist_ok=True)
    model.save_model(models_dir / MODEL_FILE)
    split = np.full(len(ids), "train", dtype=object)
    split[val], split[test] = "validation", "test"
    with db.connect() as conn:
        conn.execute("DELETE FROM model_runs")  # the predictions of the old run cascade
        run_id = conn.execute(
            "INSERT INTO model_runs (model_name, hyperparameters, features, metrics, trained_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                MODEL_NAME,
                json.dumps(HYPERPARAMETERS),
                json.dumps(FEATURES),
                json.dumps(metrics),
                now_iso(),
            ),
        ).lastrowid
        conn.executemany(
            "UPDATE customers SET data_split = ? WHERE customer_id = ?",
            zip(split.tolist(), ids.tolist(), strict=True),
        )
        conn.executemany(
            "INSERT INTO predictions (customer_id, run_id, default_probability, "
            "predicted_default) VALUES (?, ?, ?, ?)",
            [
                (int(i), run_id, round(float(p), 5), int(p >= THRESHOLD))
                for i, p in zip(ids, probability, strict=True)
            ],
        )
    return {"customers": int(len(ids)), **metrics}
