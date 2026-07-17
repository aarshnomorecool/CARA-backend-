"""
Real SHAP-based explainability, replacing the V1 rule-based version in
app/services/reason.py. Per CLAUDE(CARA-BACKEND).md's Explainability
Contract: built from the top 2-3 SHAP-attributed features per place, never
returns raw SHAP values to the client - only the rendered string/tags.

Per the "Core Request Flow"'s step 6 ("SHAP extracts top contributing
features per top-N results"), this is meant to run only on the already-
ranked top-N results, not every candidate - see app/routers/recommendations.py.

shap.TreeExplainer is exact (not an approximation) for tree ensembles and
fast enough to run per-request at this scale (top ~20 rows).
"""

import shap

from app.services.ranking import FEATURE_COLUMNS, model, to_feature_matrix
from ml.features import EMOTION_PHRASES

TOP_K_TAGS = 3

_explainer = shap.TreeExplainer(model)


def _distance_phrase(row: dict) -> str:
    km = row["distance_km"]
    return f"{round(km * 1000)}m away" if km < 1 else f"{km:.1f}km away"


def _feature_phrase(feature_name: str, row: dict) -> str | None:
    """Returns a human phrase for a positively-contributing feature, or
    None if this feature isn't interesting/legible enough to surface.

    For one-hot dummy columns (category_/time_slot_/emotion_/weather_), a
    positive SHAP value does NOT mean that dummy is actually 1 for this row
    - SHAP measures contribution relative to a baseline, so a feature being
    "off" can still push the prediction up. Only phrase a dummy if it's
    genuinely active for this row, or the reason would claim something
    false (e.g. "good in extreme heat" for a candidate scored under clear
    weather).
    """
    if feature_name.startswith(("category_", "time_slot_", "emotion_", "weather_")) and row.get(feature_name) != 1.0:
        return None
    if feature_name == "distance_km":
        return _distance_phrase(row)
    if feature_name == "budget_fit":
        return "fits your budget" if row["budget_fit"] >= 0.7 else None
    if feature_name == "preference_weight":
        # only worth surfacing if it's meaningfully above the cold-start default
        if row["preference_weight"] > 1 / 8:
            return f"matches your {row['category'].replace('_', ' ')} preference"
        return None
    if feature_name == "crowd_score":
        if row["crowd_score"] <= 0.4:
            return "currently low crowd"
        if row["crowd_score"] >= 0.7:
            return "currently busy"
        return None
    if feature_name == "rating":
        return "highly rated" if row.get("rating", 0) >= 4.2 else None
    if feature_name == "is_indoor":
        if row.get("weather_condition") in ("rain", "extreme_heat"):
            return "indoor - good for this weather" if row["is_indoor"] >= 0.5 else None
        return None
    if feature_name == "temp_celsius":
        return None  # rarely a legible standalone reason on its own
    if feature_name.startswith("category_"):
        return None  # the category itself isn't an interesting "reason"
    if feature_name.startswith("time_slot_"):
        return f"good {feature_name.removeprefix('time_slot_')} spot"
    if feature_name.startswith("emotion_"):
        return EMOTION_PHRASES.get(feature_name.removeprefix("emotion_"))
    if feature_name.startswith("weather_"):
        condition = feature_name.removeprefix("weather_").replace("_", " ")
        return f"good choice in {condition} weather" if condition != "clear" else None
    return None


def explain_top_results(feature_rows: list[dict]) -> list[tuple[str, list[str]]]:
    """feature_rows: one dict per result, containing both the model's
    feature columns AND the extra plain-string context (category,
    weather_condition) _feature_phrase needs for wording - extra keys are
    harmless, to_feature_matrix only selects FEATURE_COLUMNS for SHAP."""
    if not feature_rows:
        return []

    X = to_feature_matrix(feature_rows)
    shap_values = _explainer.shap_values(X)

    explained = []
    for i, row in enumerate(feature_rows):
        contributions = [
            (feature_name, value) for feature_name, value in zip(FEATURE_COLUMNS, shap_values[i]) if value > 0
        ]
        contributions.sort(key=lambda t: t[1], reverse=True)

        tags: list[str] = []
        for feature_name, _ in contributions:
            phrase = _feature_phrase(feature_name, row)
            if phrase and phrase not in tags:
                tags.append(phrase)
            if len(tags) >= TOP_K_TAGS:
                break

        if not tags:
            tags = [_distance_phrase(row)]

        explained.append(("Recommended because: " + " · ".join(tags), tags))

    return explained
