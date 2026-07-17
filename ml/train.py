"""
Trains the XGBoost ranking model on the synthetic dataset from
synthesize_data.py, evaluates it on a held-out split, and saves the model +
its exact feature column order for later reuse by the live endpoint.

Run: python -m ml.train   (after python -m ml.synthesize_data)
Output: ml/artifacts/cara_xgb_model.json, ml/artifacts/feature_columns.json
"""

import json

import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

from ml.features import CATEGORIES, EMOTIONS, TIME_SLOTS, WEATHER_CONDITIONS

TRAINING_DATA_PATH = "ml/artifacts/training_data.csv"
MODEL_PATH = "ml/artifacts/cara_xgb_model.json"
FEATURE_COLUMNS_PATH = "ml/artifacts/feature_columns.json"

NUMERIC_FEATURES = [
    "distance_km",
    "crowd_score",
    "budget_fit",
    "preference_weight",
    "rating",
    "temp_celsius",
    "is_indoor",
]


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["is_indoor"] = df["is_indoor"].astype(int)

    category_dummies = pd.get_dummies(
        pd.Categorical(df["category"], categories=CATEGORIES), prefix="category"
    )
    time_slot_dummies = pd.get_dummies(
        pd.Categorical(df["time_slot"], categories=TIME_SLOTS), prefix="time_slot"
    )
    emotion_dummies = pd.get_dummies(
        pd.Categorical(df["emotion"], categories=EMOTIONS), prefix="emotion"
    )
    weather_dummies = pd.get_dummies(
        pd.Categorical(df["weather_condition"], categories=WEATHER_CONDITIONS), prefix="weather"
    )

    return pd.concat(
        [df[NUMERIC_FEATURES], category_dummies, time_slot_dummies, emotion_dummies, weather_dummies],
        axis=1,
    ).astype(float)


def main() -> None:
    df = pd.read_csv(TRAINING_DATA_PATH)
    X = build_feature_matrix(df)
    y = df["relevance"]

    feature_columns = list(X.columns)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = xgb.XGBRegressor(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        random_state=42,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    rmse = mean_squared_error(y_test, preds) ** 0.5
    mae = mean_absolute_error(y_test, preds)
    r2 = r2_score(y_test, preds)

    print(f"Test RMSE: {rmse:.3f}  (relevance is on a 0-100 scale)")
    print(f"Test MAE:  {mae:.3f}")
    print(f"Test R^2:  {r2:.4f}")

    importances = sorted(
        zip(feature_columns, model.feature_importances_), key=lambda kv: kv[1], reverse=True
    )
    print("\nTop 10 feature importances:")
    for name, score in importances[:10]:
        print(f"  {name:<20s} {score:.4f}")

    model.get_booster().save_model(MODEL_PATH)
    with open(FEATURE_COLUMNS_PATH, "w") as f:
        json.dump(feature_columns, f, indent=2)

    print(f"\nSaved model to {MODEL_PATH}")
    print(f"Saved feature column order to {FEATURE_COLUMNS_PATH}")


if __name__ == "__main__":
    main()
