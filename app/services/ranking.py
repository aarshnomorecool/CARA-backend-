"""
Loads the XGBoost model trained by ml/train.py once at import time (per
CLAUDE(CARA-BACKEND).md's "Loaded at FastAPI startup"), and scores candidate
feature rows against it using the exact column order the model was trained
on (ml/artifacts/feature_columns.json) - any column the caller didn't set
(e.g. a category/time_slot/emotion/weather dummy that isn't "on" for this
row) defaults to 0.0, matching one-hot encoding.
"""

import json
from pathlib import Path

import pandas as pd
import xgboost as xgb

_ARTIFACTS_DIR = Path(__file__).resolve().parent.parent.parent / "ml" / "artifacts"
MODEL_PATH = _ARTIFACTS_DIR / "cara_xgb_model.json"
FEATURE_COLUMNS_PATH = _ARTIFACTS_DIR / "feature_columns.json"

with open(FEATURE_COLUMNS_PATH) as f:
    FEATURE_COLUMNS: list[str] = json.load(f)

model = xgb.XGBRegressor()
model.load_model(MODEL_PATH)


def to_feature_matrix(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
    return df[FEATURE_COLUMNS].astype(float)


def score_candidates(rows: list[dict]) -> list[float]:
    if not rows:
        return []
    return [float(s) for s in model.predict(to_feature_matrix(rows))]
