"""
Implements the "Core Request Flow" from CLAUDE(CARA-BACKEND).md: resolve
context, query candidate places, build feature vectors, score with the
trained model, and return ranked results with an explanation string per
place.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.place import Place
from app.models.preference import Preference
from app.models.user import User
from app.schemas.recommendation import RecommendationContext, RecommendationItem, RecommendationsResponse
from app.services.emotion import detect_emotion
from app.services.explain import explain_top_results
from app.services.ranking import score_candidates
from app.services.weather import get_weather
from ml.features import (
    CATEGORIES,
    EMOTION_CATEGORY_BONUS,
    EMOTION_PHRASES,
    budget_fit as compute_budget_fit,
    crowd_score as compute_crowd_score,
    haversine_km,
)

router = APIRouter(tags=["recommendations"])

RADIUS_KM = 10.0  # matches the proximity assumption baked into ml/synthesize_data.py's training labels
GEOFENCE_RADIUS_KM = 0.1  # 100m, per CLAUDE(CARA-BACKEND).md's semantic location check
DEFAULT_PREFERENCE_WEIGHT = 1 / len(CATEGORIES)  # cold-start users have no preference rows yet
DEFAULT_RATING = 3.5
TOP_N = 20


def _time_slot(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _semantic_location(
    lat: float, lon: float,
    home_lat: float | None, home_lon: float | None,
    college_lat: float | None, college_lon: float | None,
) -> str:
    if home_lat is not None and home_lon is not None:
        if haversine_km(lat, lon, home_lat, home_lon) <= GEOFENCE_RADIUS_KM:
            return "HOME"
    if college_lat is not None and college_lon is not None:
        if haversine_km(lat, lon, college_lat, college_lon) <= GEOFENCE_RADIUS_KM:
            return "COLLEGE"
    return "OUTSIDE"


@router.get("/recommendations", response_model=RecommendationsResponse)
def get_recommendations(
    user_id: int,
    lat: float,
    lon: float,
    budget: float | None = Query(default=None),
    text_input: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RecommendationsResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now()
    time_slot = _time_slot(now)
    day_type = "weekend" if now.weekday() >= 5 else "weekday"

    weather = get_weather(lat, lon)
    emotion = detect_emotion(text_input)
    location_context = _semantic_location(lat, lon, user.home_lat, user.home_lon, user.college_lat, user.college_lon)
    effective_budget = budget if budget is not None else user.budget_default

    preference_weights = {
        p.category.value: p.weight
        for p in db.execute(select(Preference).where(Preference.user_id == user_id)).scalars()
    }

    candidates: list[tuple[Place, float]] = []
    for place in db.execute(select(Place)).scalars():
        distance_km = haversine_km(lat, lon, place.latitude, place.longitude)
        if distance_km <= RADIUS_KM:
            candidates.append((place, distance_km))

    if not candidates:
        return RecommendationsResponse(
            context=RecommendationContext(
                time_slot=time_slot,
                weather_condition=weather["weather_condition"],
                temp_celsius=weather["temp_celsius"],
                emotion=emotion,
                location_context=location_context,
            ),
            results=[],
        )

    feature_rows = []
    for place, distance_km in candidates:
        category = place.category.value
        feature_rows.append(
            {
                "distance_km": distance_km,
                "crowd_score": compute_crowd_score(category, day_type, time_slot),
                "budget_fit": compute_budget_fit(place.avg_price_inr, effective_budget),
                "preference_weight": preference_weights.get(category, DEFAULT_PREFERENCE_WEIGHT),
                "rating": place.approx_rating if place.approx_rating is not None else DEFAULT_RATING,
                "temp_celsius": weather["temp_celsius"],
                "is_indoor": float(place.is_indoor) if place.is_indoor is not None else 0.5,
                f"category_{category}": 1.0,
                f"time_slot_{time_slot}": 1.0,
                f"emotion_{emotion}": 1.0,
                f"weather_{weather['weather_condition']}": 1.0,
                # extra context for app/services/explain.py's phrasing -
                # ignored by scoring, which only selects FEATURE_COLUMNS
                "category": category,
                "weather_condition": weather["weather_condition"],
            }
        )

    scores = score_candidates(feature_rows)

    # Deterministic post-hoc boost, applied on top of the model's raw score.
    # Root cause (see project memory, 2026-07-16): the trained model's own
    # emotion signal is unreliable for rarer/needs-based labels - a strong,
    # numerous category like restaurant can drown out a correctly-intended
    # but statistically thinner interaction signal, no matter how it's
    # weighted in the synthetic training formula. This guarantees the
    # intended category shift instead of hoping the model learned it.
    # EMOTION_BOOST_SCALE converts EMOTION_CATEGORY_BONUS's 0-1 magnitudes
    # (same scale used when building synthetic relevance labels) into points
    # on the model's 0-100 score scale - large enough to reliably outweigh
    # the score gaps observed between categories (a few points, up to ~10).
    EMOTION_BOOST_SCALE = 100
    emotion_bonus_map = EMOTION_CATEGORY_BONUS.get(emotion, {})
    if emotion_bonus_map:
        scores = [
            score + emotion_bonus_map.get(row["category"], 0.0) * EMOTION_BOOST_SCALE
            for score, row in zip(scores, feature_rows)
        ]

    scored = sorted(zip(scores, candidates, feature_rows), key=lambda t: t[0], reverse=True)
    top = scored[:TOP_N]

    # SHAP only runs on the already-ranked top-N, per CLAUDE.md's Core
    # Request Flow step 6 - explaining every discarded candidate would be
    # wasted work.
    top_feature_rows = [row for _, _, row in top]
    explanations = explain_top_results(top_feature_rows)

    # SHAP explains the model's raw prediction, not the boost above - a
    # boosted candidate might otherwise get a reason that never mentions why
    # it's actually here. Force the emotion phrase to the front whenever the
    # boost applied, so the explanation stays honest about the real reason.
    final_explanations = []
    for row, (reason, reason_tags) in zip(top_feature_rows, explanations):
        if emotion_bonus_map.get(row["category"], 0.0) > 0:
            phrase = EMOTION_PHRASES.get(emotion)
            if phrase and phrase not in reason_tags:
                reason_tags = [phrase] + reason_tags
                reason = "Recommended because: " + " · ".join(reason_tags)
        final_explanations.append((reason, reason_tags))

    results = [
        RecommendationItem(
            place_id=place.place_id,
            name=place.name,
            category=place.category,
            sub_category=place.sub_category,
            area=place.area,
            latitude=place.latitude,
            longitude=place.longitude,
            approx_rating=place.approx_rating,
            price_range=place.price_range,
            avg_price_inr=place.avg_price_inr,
            eco_friendly=place.eco_friendly,
            reason=reason,
            reason_tags=reason_tags,
            crowd_score=row["crowd_score"],
            budget_fit=row["budget_fit"],
            preference_weight=row["preference_weight"],
        )
        for (_, (place, _distance_km), row), (reason, reason_tags) in zip(top, final_explanations)
    ]

    return RecommendationsResponse(
        context=RecommendationContext(
            time_slot=time_slot,
            weather_condition=weather["weather_condition"],
            temp_celsius=weather["temp_celsius"],
            emotion=emotion,
            location_context=location_context,
        ),
        results=results,
    )
