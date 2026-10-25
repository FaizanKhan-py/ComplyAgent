"""Predicts the target column ("default payment next month") for an uploaded dataset, with the
model that notebooks/Data_pipeline_and_Modeling.ipynb saves to storage/models.

The upload (CSV, .xls or .xlsx) has the UCI "Default of Credit Card Clients" columns (X1..X23 or
LIMIT_BAL, SEX, ...).
Every row gets the notebook's engineered features, then the saved XGBoost model scores it. The
result is the same rows as CSV with the target column filled in, plus the probability and
risk level.

Loading the model needs scikit-learn, xgboost and joblib (requirements.txt), ideally the same
versions as the environment that ran the notebook.
"""

from __future__ import annotations

import csv
import io
import threading
from pathlib import Path

import numpy as np

from .portfolio import _ALIASES, _PAY, COLUMNS, TARGET, risk_level
from .tables import TableError, read_table

MODEL_FILE = "default_risk_model.joblib"
PROBABILITY_COLUMN = "default_probability"
RISK_COLUMN = "risk_level"
ACTUAL_COLUMN = "actual default payment next month"
_BILL = [f"BILL_AMT{m}" for m in range(1, 7)]
# The model inputs _features() builds, named as in the notebook's training table.
BUILT_FEATURES = {
    "LIMIT_BAL",
    "AGE",
    "on_time_payment_rate",
    "avg_payment_delay",
    "avg_credit_utilization",
    "EDUCATION_cleaned",
    "sex_male",
}


class PredictionFailed(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _features(values: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The notebook's feature engineering, column for column."""
    pay = np.column_stack([values[c] for c in _PAY])
    bill = np.column_stack([values[c] for c in _BILL])
    limit = values["LIMIT_BAL"]
    counted = ((pay != -2) & (pay != 0)).sum(axis=1)
    on_time = (pay == -1).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        # 0 / 0 is NaN in pandas, which the notebook fills with 1.
        on_time_rate = np.where(counted > 0, on_time / np.maximum(counted, 1), 1.0)
    education = values["EDUCATION"]
    return {
        "LIMIT_BAL": limit,
        "AGE": values["AGE"],
        "on_time_payment_rate": on_time_rate,
        "avg_payment_delay": pay.clip(min=0).mean(axis=1),
        "avg_credit_utilization": (bill / limit[:, None]).mean(axis=1),
        "EDUCATION_cleaned": np.where(np.isin(education, (5, 6)), 4, education),
        "sex_male": (values["SEX"] == 1).astype(float),
    }


SUPPORTED_EXTENSIONS = (".csv", ".xls", ".xlsx")


class Predictor:
    def __init__(self, models_dir: Path) -> None:
        self.path = models_dir / MODEL_FILE
        self._lock = threading.Lock()
        self._bundle: dict | None = None
        self._loaded_mtime: float | None = None

    def _load(self) -> dict:
        """The saved bundle, reloaded when the notebook writes a new file."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            raise PredictionFailed(
                "No trained model found. Run notebooks/Data_pipeline_and_Modeling.ipynb to the "
                f"end; its last cell saves the model to storage/models/{MODEL_FILE}.",
                503,
            ) from None
        with self._lock:
            if self._bundle is None or self._loaded_mtime != mtime:
                try:
                    import joblib

                    bundle = joblib.load(self.path)
                except ImportError as exc:
                    raise PredictionFailed(
                        "Scoring needs scikit-learn, xgboost and joblib. Install them with: "
                        "pip install -r requirements.txt",
                        503,
                    ) from exc
                unknown = [f for f in bundle["features"] if f not in BUILT_FEATURES]
                if unknown:
                    raise PredictionFailed(
                        f"The saved model uses features this server cannot build: {unknown}.", 503
                    )
                self._bundle, self._loaded_mtime = bundle, mtime
            return self._bundle

    def info(self) -> dict:
        try:
            bundle = self._load()
        except PredictionFailed as exc:
            return {"available": False, "message": str(exc)}
        return {
            "available": True,
            "message": None,
            "model_name": bundle["model_name"],
            "features": bundle["features"],
            "threshold": bundle["threshold"],
            "test_metrics": bundle.get("test_metrics"),
        }

    def predict_file(self, filename: str, data: bytes) -> dict:
        bundle = self._load()
        try:
            header, body = read_table(data)
        except TableError as exc:
            raise PredictionFailed(str(exc), exc.status_code) from None
        names = [_ALIASES.get(n, n) for n in header]
        missing = [c for c in COLUMNS if c not in names]
        if missing:
            raise PredictionFailed(f"The file is missing the column(s): {', '.join(missing)}.")
        position = {name: names.index(name) for name in [*COLUMNS, TARGET, "ID"] if name in names}

        values = {}
        for column in COLUMNS:
            index = position[column]
            parsed = np.empty(len(body))
            for line, row in enumerate(body):
                raw = row[index].strip() if index < len(row) else ""
                try:
                    parsed[line] = float(raw)
                except ValueError:
                    raise PredictionFailed(
                        f"Data row {line + 1}: {column} is not a number ({raw!r})."
                    ) from None
            values[column] = parsed
        if (values["LIMIT_BAL"] <= 0).any():
            line = int(np.argmax(values["LIMIT_BAL"] <= 0)) + 1
            raise PredictionFailed(f"Data row {line}: LIMIT_BAL must be greater than 0.")

        built = _features(values)
        matrix = np.column_stack([built[f] for f in bundle["features"]])
        probability = bundle["model"].predict_proba(matrix)[:, 1]
        predicted = (probability >= bundle["threshold"]).astype(int)
        actual = self._actual(body, position.get(TARGET))
        return {
            "filename": filename,
            "model_name": bundle["model_name"],
            "threshold": bundle["threshold"],
            "rows": len(body),
            "predicted_defaults": int(predicted.sum()),
            "by_risk": {
                level: sum(risk_level(p) == level for p in probability)
                for level in ("high", "medium", "low")
            },
            "scores": self._scores(actual, predicted) if actual is not None else None,
            "predictions": [
                {
                    "row": line + 1,
                    "id": self._id(body[line], position.get("ID")),
                    "limit_bal": int(values["LIMIT_BAL"][line]),
                    "age": int(values["AGE"][line]),
                    "default_probability": round(float(p), 5),
                    "predicted_default": int(predicted[line]),
                    "risk_level": risk_level(float(p)),
                    "actual_default": None if actual is None else int(actual[line]),
                }
                for line, p in enumerate(probability)
            ],
            "csv": self._output_csv(header, body, position.get(TARGET), probability, predicted),
        }

    @staticmethod
    def _actual(body: list[list[str]], index: int | None) -> np.ndarray | None:
        """The real target values, when the upload has them all as 0 or 1."""
        if index is None:
            return None
        raw = [row[index].strip() if index < len(row) else "" for row in body]
        if not all(v in ("0", "1", "0.0", "1.0") for v in raw):
            return None
        return np.array([int(float(v)) for v in raw])

    @staticmethod
    def _scores(actual: np.ndarray, predicted: np.ndarray) -> dict:
        tp = int(((predicted == 1) & (actual == 1)).sum())
        fp = int(((predicted == 1) & (actual == 0)).sum())
        fn = int(((predicted == 0) & (actual == 1)).sum())
        tn = int(((predicted == 0) & (actual == 0)).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        return {
            "accuracy": (tp + tn) / len(actual),
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "tp": tp,
        }

    @staticmethod
    def _id(row: list[str], index: int | None) -> int | None:
        if index is None or index >= len(row):
            return None
        try:
            return int(float(row[index]))
        except ValueError:
            return None

    @staticmethod
    def _output_csv(header, body, target_index, probability, predicted) -> str:
        """The uploaded rows with the target column predicted. Real values, if any, are kept."""
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        columns = list(header)
        if target_index is None:
            columns.append(TARGET)
        else:
            columns.append(ACTUAL_COLUMN)
        writer.writerow([*columns, PROBABILITY_COLUMN, RISK_COLUMN])
        for row, p, flag in zip(body, probability, predicted, strict=True):
            cells = list(row) + [""] * (len(header) - len(row))
            if target_index is None:
                cells.append(str(int(flag)))
            else:
                cells.append(cells[target_index])
                cells[target_index] = str(int(flag))
            writer.writerow([*cells, f"{p:.5f}", risk_level(float(p))])
        return out.getvalue()
