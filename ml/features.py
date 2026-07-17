"""
Shared feature engineering for the recommendation model.

This module is deliberately framework-agnostic (no DB/FastAPI imports) so the
exact same functions can be reused by the live `/recommendations` endpoint
later — the crowd table and budget_fit formula defined here ARE the model's
contract with the real request flow, not just training-time scaffolding.
"""

import numpy as np

CATEGORIES = [
    "restaurant",
    "cafe",
    "park",
    "mall",
    "library",
    "gym",
    "hospital",
    "tourist_attraction",
]

TIME_SLOTS = ["morning", "afternoon", "evening", "night"]
DAY_TYPES = ["weekday", "weekend"]
# Covers both moods (tired/stressed/happy/excited) and situational needs/wants
# (hungry/bored/want_to_relax/want_to_exercise) - real users type both ("I'm
# hungry", "want to relax"), not just feelings, and each label needs a
# EMOTION_CATEGORY_BONUS entry below to actually influence ranking.
EMOTIONS = [
    "neutral",
    "tired",
    "stressed",
    "happy",
    "excited",
    "hungry",
    "bored",
    "want_to_relax",
    "want_to_exercise",
]
WEATHER_CONDITIONS = ["clear", "cloudy", "rain", "extreme_heat"]


def haversine_km(lat1, lon1, lat2, lon2):
    """Works on scalars or numpy arrays (uses numpy ufuncs throughout)."""
    r = 6371.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


# Hand-tuned base crowd level (0=empty, 1=packed) per category x time_slot,
# per CLAUDE_BACKEND.md's Crowd Prediction section (gyms peak morning/evening,
# cafes peak afternoon, restaurants peak lunch/dinner, malls peak weekend
# afternoon). These are starting values, not measured data — refine later by
# blending in self-reported crowding from `interactions`, per that doc.
_BASE_CROWD = {
    "restaurant": {"morning": 0.2, "afternoon": 0.7, "evening": 0.5, "night": 0.8},
    "cafe": {"morning": 0.3, "afternoon": 0.8, "evening": 0.5, "night": 0.2},
    "park": {"morning": 0.6, "afternoon": 0.3, "evening": 0.7, "night": 0.1},
    "mall": {"morning": 0.2, "afternoon": 0.5, "evening": 0.7, "night": 0.3},
    "library": {"morning": 0.4, "afternoon": 0.6, "evening": 0.5, "night": 0.1},
    "gym": {"morning": 0.8, "afternoon": 0.3, "evening": 0.8, "night": 0.2},
    "hospital": {"morning": 0.6, "afternoon": 0.5, "evening": 0.4, "night": 0.2},
    "tourist_attraction": {"morning": 0.4, "afternoon": 0.5, "evening": 0.6, "night": 0.2},
}

# Additive weekend bump — leisure categories get busier, routine/utility
# categories don't.
_WEEKEND_BOOST = {
    "restaurant": 0.15,
    "cafe": 0.1,
    "park": 0.2,
    "mall": 0.25,
    "library": -0.05,
    "gym": -0.1,
    "hospital": 0.0,
    "tourist_attraction": 0.25,
}


def crowd_score(category: str, day_type: str, time_slot: str) -> float:
    base = _BASE_CROWD[category][time_slot]
    if day_type == "weekend":
        base += _WEEKEND_BOOST[category]
    return max(0.0, min(1.0, base))


def budget_fit(avg_price_inr: float | None, budget: float | None) -> float:
    """1.0 = comfortably affordable, decaying linearly to 0 at 2x budget."""
    if avg_price_inr is None or budget is None or budget <= 0:
        return 0.5  # no signal either way
    if avg_price_inr <= budget:
        return 1.0
    overshoot = (avg_price_inr - budget) / budget
    return max(0.0, 1.0 - overshoot)


# Emotion -> category affinity bonus, added to the synthetic relevance label.
# e.g. a tired user gets a bump toward parks/cafes/libraries over gyms/malls.
EMOTION_CATEGORY_BONUS = {
    "tired": {"cafe": 0.25, "park": 0.2, "library": 0.1},
    "stressed": {"park": 0.25, "cafe": 0.2},
    "happy": {"mall": 0.2, "restaurant": 0.15, "tourist_attraction": 0.15},
    "excited": {"tourist_attraction": 0.25, "mall": 0.15, "restaurant": 0.1},
    # Needs-based labels use bigger bonuses than the mood labels above -
    # 2026-07-16 testing showed the original magnitudes (matching the mood
    # labels') weren't enough to consistently beat proximity/rating once
    # combined with these labels' thinner training signal (see the
    # oversampling note in synthesize_data.py's sample_contexts). gym
    # specifically failed to surface at all for want_to_exercise, hence the
    # especially large bump there.
    "hungry": {"restaurant": 0.4, "cafe": 0.2},
    "bored": {"mall": 0.3, "tourist_attraction": 0.25, "park": 0.2},
    "want_to_relax": {"park": 0.3, "cafe": 0.2, "library": 0.2},
    "want_to_exercise": {"gym": 0.45, "park": 0.2},
    "neutral": {},
}

# Human-readable phrasing per label, shared between explain.py's SHAP-derived
# reason tags and recommendations.py's deterministic post-hoc emotion boost
# (see that module's "why not just let the model learn it" note) - both need
# to describe the same emotion/need the same way. No "neutral" entry:
# neutral is never a reason worth stating.
EMOTION_PHRASES = {
    "tired": "good pick when you're tired",
    "stressed": "good pick when you're stressed",
    "happy": "good pick when you're happy",
    "excited": "good pick when you're excited",
    "hungry": "good pick when you're hungry",
    "bored": "good pick when you're bored",
    "want_to_relax": "good pick when you want to relax",
    "want_to_exercise": "good pick when you want to exercise",
}

# Weather -> indoor/outdoor bonus, added to the synthetic relevance label.
def weather_bonus(weather_condition: str, is_indoor: bool) -> float:
    if weather_condition in ("rain", "extreme_heat"):
        return 0.15 if is_indoor else -0.15
    if weather_condition == "clear":
        return 0.0 if is_indoor else 0.1
    return 0.0
