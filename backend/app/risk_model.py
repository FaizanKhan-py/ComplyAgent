"""Scores the customers in the database with the model the notebook saved (see model_files).

    python -m app.manage score-customers     score everyone, after the notebook has been run

The notebook is the one place the model is trained, so the database, the dataset prediction
page and the notebook always use the same model. Scoring stores one model_runs row (the model's
name, parameters, features and scores), each customer's prediction, and which part of the
notebook's split the customer was in.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from .db import Database
from .model_files import ModelFiles, ModelMissing
from .portfolio import FEATURES_SQL

logger = logging.getLogger(__name__)


class ScoringFailed(Exception):
    pass


def _model(models_dir: Path):
    try:
        return ModelFiles(models_dir).load()
    except ModelMissing as exc:
        raise ScoringFailed(str(exc)) from exc


def score_all(db: Database, models_dir: Path) -> dict:
    """Score every customer with the saved model, replacing earlier predictions."""
    model = _model(models_dir)
    with db.connect() as conn:
        rows = conn.execute(f"{FEATURES_SQL} GROUP BY c.customer_id ORDER BY c.customer_id")
        rows = rows.fetchall()
    if not rows:
        raise ScoringFailed(
            "There are no customers to score. Load them first with: "
            "python -m app.manage import-customers"
        )
    ids = [r["customer_id"] for r in rows]
    probability = model.predict(np.array([model.record_vector(r) for r in rows]))
    meta = model.metadata
    with db.connect() as conn:
        conn.execute("DELETE FROM model_runs")  # the predictions of the old run cascade
        run_id = conn.execute(
            "INSERT INTO model_runs (model_name, hyperparameters, features, metrics, trained_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                meta["model_name"],
                json.dumps(meta.get("best_params", {})),
                json.dumps(model.features),
                json.dumps(meta["metrics"]),
                meta["trained_at"],
            ),
        ).lastrowid
        conn.executemany(
            "UPDATE customers SET data_split = ? WHERE customer_id = ?",
            [(model.splits.get(i), i) for i in ids],
        )
        conn.executemany(
            "INSERT INTO predictions (customer_id, run_id, default_probability, "
            "predicted_default) VALUES (?, ?, ?, ?)",
            [
                (i, run_id, round(float(p), 5), int(p >= model.threshold))
                for i, p in zip(ids, probability, strict=True)
            ],
        )
    return {
        "customers": len(ids),
        "model_name": meta["model_name"],
        "trained_at": meta["trained_at"],
        **meta["metrics"],
    }


def score_customer(db: Database, models_dir: Path, customer_id: int) -> bool:
    """Score one customer (e.g. one added by hand) with the saved model.

    Returns False, leaving the customer unscored, when the customers have not been scored yet
    (no model run to attach the prediction to) or the model files cannot be used.
    """
    with db.connect() as conn:
        run = conn.execute("SELECT run_id FROM model_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        row = conn.execute(
            f"{FEATURES_SQL} WHERE c.customer_id = ? GROUP BY c.customer_id", (customer_id,)
        ).fetchone()
    if not run or not row:
        return False
    try:
        model = ModelFiles(models_dir).load()
        probability = float(model.predict(np.array([model.record_vector(row)]))[0])
    except Exception:  # missing package or unreadable files: keep the customer, unscored
        logger.exception("Could not score customer %s", customer_id)
        return False
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO predictions (customer_id, run_id, default_probability, "
            "predicted_default) VALUES (?, ?, ?, ?)",
            (
                customer_id,
                run["run_id"],
                round(probability, 5),
                int(probability >= model.threshold),
            ),
        )
    return True
