"""
Bootstraps a labeled training set for the ranking model.

There is no real `interactions` history yet (no users), so this generates
many simulated (context, candidate place) pairs and assigns each a synthetic
relevance label using the hand-tuned heuristic in features.py — distance,
budget fit, a simulated per-"user" category preference, rating, crowd, and
emotion/weather affinity bonuses. XGBoost then learns to reproduce/generalize
that combination rule (see train.py) rather than memorizing the exact
formula. This is a cold-start bootstrap, not a substitute for training on
real `interactions` data once the app has real usage.

Run: python -m ml.synthesize_data
Output: ml/artifacts/training_data.csv
"""

import numpy as np
import pandas as pd

from ml.features import (
    CATEGORIES,
    DAY_TYPES,
    EMOTIONS,
    EMOTION_CATEGORY_BONUS,
    TIME_SLOTS,
    WEATHER_CONDITIONS,
    budget_fit,
    crowd_score,
    haversine_km,
    weather_bonus,
)

PLACES_PATH = "places_merged.csv"
OUTPUT_PATH = "ml/artifacts/training_data.csv"

NUM_SYNTHETIC_USERS = 250
NUM_CONTEXTS = 3000

# Nagpur city center-ish; contexts are sampled around here rather than
# uniformly over the places' own lat/lon range, since that range includes
# ~50km-out day-trip attractions (Ramtek Fort, Nagzira) that aren't
# representative of where a user is actually standing.
CITY_CENTER_LAT, CITY_CENTER_LON = 21.146, 79.088

RNG = np.random.default_rng(42)


def load_places() -> pd.DataFrame:
    df = pd.read_csv(PLACES_PATH)
    df["is_indoor"] = df["is_indoor"].astype(bool)
    # A handful of places are missing a rating (not yet confirmed by Google
    # or the draft dataset) - fall back to the dataset mean rather than
    # letting NaN propagate through the relevance formula below.
    df["approx_rating"] = df["approx_rating"].fillna(df["approx_rating"].mean())
    return df


def sample_user_profiles(n: int) -> np.ndarray:
    """Dirichlet(alpha=0.5) per synthetic user -> concentrated, varied
    category preference vectors (some categories near 0, one or two dominant)
    rather than everyone liking everything equally."""
    return RNG.dirichlet(alpha=[0.5] * len(CATEGORIES), size=n)


def sample_contexts(n: int, num_users: int) -> pd.DataFrame:
    user_idx = RNG.integers(0, num_users, size=n)
    lat = RNG.normal(CITY_CENTER_LAT, 0.045, size=n).clip(20.95, 21.35)
    lon = RNG.normal(CITY_CENTER_LON, 0.045, size=n).clip(78.95, 79.25)
    time_slot = RNG.choice(TIME_SLOTS, size=n)
    day_type = RNG.choice(DAY_TYPES, size=n, p=[5 / 7, 2 / 7])
    weather = RNG.choice(WEATHER_CONDITIONS, size=n, p=[0.45, 0.25, 0.2, 0.1])
    temp_c = RNG.normal(30, 6, size=n).clip(12, 45)
    budget = RNG.uniform(100, 1200, size=n)
    # neutral, tired, stressed, happy, excited, hungry, bored, want_to_relax, want_to_exercise
    # Needs-based labels (hungry/bored/want_to_relax/want_to_exercise)
    # deliberately oversampled relative to a "natural" usage distribution -
    # 2026-07-16 testing showed their category bonus was too thin a training
    # signal (6-7% each) to compete against always-present continuous
    # features like proximity/rating, so want_to_exercise's gym bonus never
    # actually surfaced gym in live results despite being the strongest
    # bonus in the table. This isn't meant to mirror real query frequency,
    # just to give the model enough examples per label to learn each
    # association robustly.
    emotion = RNG.choice(EMOTIONS, size=n, p=[0.20, 0.08, 0.07, 0.07, 0.06, 0.14, 0.13, 0.13, 0.12])

    return pd.DataFrame(
        {
            "user_idx": user_idx,
            "user_lat": lat,
            "user_lon": lon,
            "time_slot": time_slot,
            "day_type": day_type,
            "weather_condition": weather,
            "temp_celsius": temp_c,
            "budget": budget,
            "emotion": emotion,
        }
    )


def build_training_frame() -> pd.DataFrame:
    places = load_places()
    profiles = sample_user_profiles(NUM_SYNTHETIC_USERS)
    contexts = sample_contexts(NUM_CONTEXTS, NUM_SYNTHETIC_USERS)

    # Cross join contexts x places
    contexts["_key"] = 1
    places["_key"] = 1
    rows = contexts.merge(places, on="_key").drop(columns="_key")

    rows["distance_km"] = haversine_km(
        rows["user_lat"].to_numpy(),
        rows["user_lon"].to_numpy(),
        rows["latitude"].to_numpy(),
        rows["longitude"].to_numpy(),
    )
    rows["crowd_score"] = rows.apply(
        lambda r: crowd_score(r["category"], r["day_type"], r["time_slot"]), axis=1
    )
    rows["budget_fit"] = rows.apply(
        lambda r: budget_fit(r["avg_price_inr"], r["budget"]), axis=1
    )
    rows["preference_weight"] = rows.apply(
        lambda r: profiles[r["user_idx"]][CATEGORIES.index(r["category"])], axis=1
    )
    rows["rating"] = rows["approx_rating"]

    emotion_bonus = rows.apply(
        lambda r: EMOTION_CATEGORY_BONUS[r["emotion"]].get(r["category"], 0.0), axis=1
    )
    weather_bonus_val = rows.apply(
        lambda r: weather_bonus(r["weather_condition"], r["is_indoor"]), axis=1
    )

    proximity = (1 - (rows["distance_km"] / 10.0).clip(upper=1.0)).clip(lower=0.0)
    # crowd_score's weight was 0.10 until 2026-07-16, when a live diagnostic
    # (comparing measured model output against this formula's own math for
    # specific candidates) showed the trained model had learned a
    # category-level bias meaningfully stronger than 0.10 alone would
    # produce. Root cause: crowd_score is a deterministic lookup keyed only
    # on category x day_type x time_slot (see features.py's _BASE_CROWD) -
    # every row of the same category at the same time_slot gets the exact
    # same value, so it doubles as a near-perfect proxy for "which category
    # is this", which XGBoost's tree splits can exploit far more efficiently
    # than the one-off, per-context emotion/preference signals, especially
    # now that the emotion taxonomy has grown from 5 to 9 labels (each one
    # individually rarer in the training distribution). Weight moved to
    # preference_weight instead - a per-request personalization signal we
    # actually want to dominate over a fixed per-category/time-of-day prior.
    base_score = (
        0.30 * proximity
        + 0.20 * rows["budget_fit"]
        + 0.31 * rows["preference_weight"]
        + 0.15 * (rows["rating"] / 5.0)
        + 0.04 * (1 - rows["crowd_score"])
    )
    noise = RNG.normal(0, 0.03, size=len(rows))
    relevance = (base_score + emotion_bonus + weather_bonus_val + noise).clip(0, 1) * 100
    rows["relevance"] = relevance

    return rows


def main() -> None:
    rows = build_training_frame()
    keep_cols = [
        "place_id",
        "name",
        "category",
        "is_indoor",
        "time_slot",
        "emotion",
        "weather_condition",
        "temp_celsius",
        "distance_km",
        "crowd_score",
        "budget_fit",
        "preference_weight",
        "rating",
        "relevance",
    ]
    out = rows[keep_cols]
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(out)} rows to {OUTPUT_PATH}")
    print(out["relevance"].describe())


if __name__ == "__main__":
    main()
