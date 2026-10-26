"""The risk model files that notebooks/Data_pipeline_and_Modeling.ipynb saves, and loading them.

    backend/models/risk_model/
        model.json        the tuned XGBoost model, in XGBoost's portable format
        metadata.json     feature names (in model order), threshold, parameters, scores, versions
        data_split.csv    which customers the notebook trained, validated and tested on

The files are part of the repository, so a fresh clone can score customers straight away.
Re-running the notebook replaces them; the backend notices and loads the new model.

The model is loaded with xgboost.Booster, so the backend needs neither scikit-learn nor pickle.
"""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODEL = "model.json"
METADATA = "metadata.json"
SPLITS = "data_split.csv"

# The model inputs the notebook builds, and how each one is computed from a stored customer
# (portfolio.FEATURES_SQL gives the three engineered features with the notebook's formulas).
FROM_RECORD = {
    "LIMIT_BAL": lambda r: r["limit_bal"],
    "AGE": lambda r: r["age"],
    "on_time_payment_rate": lambda r: r["on_time_payment_rate"],
    "avg_payment_delay": lambda r: r["avg_payment_delay"],
    "avg_credit_utilization": lambda r: r["avg_credit_utilization"],
    "EDUCATION_cleaned": lambda r: r["education_code"],  # 5 and 6 already merged into 4
    "sex_male": lambda r: 1.0 if r["sex"] == "Male" else 0.0,
}


class ModelMissing(Exception):
    """No usable model files. The message says what to do."""


@dataclass(frozen=True)
class RiskModel:
    booster: object  # xgboost.Booster
    metadata: dict
    splits: dict[int, str]  # customer id -> train | validation | test

    @property
    def features(self) -> list[str]:
        return self.metadata["features"]

    @property
    def threshold(self) -> float:
        return float(self.metadata["threshold"])

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        """The chance of default for each row (columns in `features` order)."""
        import xgboost

        data = xgboost.DMatrix(np.asarray(matrix, dtype=float), feature_names=self.features)
        return self.booster.predict(data)

    def record_vector(self, row) -> list[float]:
        """One stored customer as model inputs."""
        return [float(FROM_RECORD[name](row)) for name in self.features]


class ModelFiles:
    """Loads the model once and again whenever the notebook saves new files."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        self._loaded: tuple[tuple, RiskModel] | None = None

    def load(self) -> RiskModel:
        paths = [self.folder / MODEL, self.folder / METADATA]
        if not all(p.is_file() for p in paths):
            raise ModelMissing(
                f"No model files in {self.folder}. Run notebooks/Data_pipeline_and_Modeling.ipynb "
                "to the end; its last cell saves model.json and metadata.json there."
            )
        stamp = tuple(p.stat().st_mtime for p in paths)
        with self._lock:
            if self._loaded is None or self._loaded[0] != stamp:
                self._loaded = (stamp, self._read())
            return self._loaded[1]

    def _read(self) -> RiskModel:
        try:
            import xgboost
        except ImportError as exc:
            raise ModelMissing("Scoring needs xgboost: pip install -r requirements.txt") from exc
        try:
            metadata = json.loads((self.folder / METADATA).read_text(encoding="utf-8"))
            booster = xgboost.Booster()
            booster.load_model(self.folder / MODEL)
        except Exception as exc:  # unreadable or corrupt files
            raise ModelMissing(f"The model files in {self.folder} cannot be read ({exc}).") from exc
        unknown = [f for f in metadata.get("features", []) if f not in FROM_RECORD]
        if unknown:
            raise ModelMissing(
                f"The saved model uses features this server cannot build: {unknown}."
            )
        splits: dict[int, str] = {}
        split_file = self.folder / SPLITS
        if split_file.is_file():
            with split_file.open(newline="", encoding="utf-8") as f:
                splits = {int(r["ID"]): r["split"] for r in csv.DictReader(f)}
        return RiskModel(booster, metadata, splits)
