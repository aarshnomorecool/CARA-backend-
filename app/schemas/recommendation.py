from pydantic import BaseModel

from app.models.place import PlaceCategory


class RecommendationContext(BaseModel):
    time_slot: str
    weather_condition: str
    temp_celsius: float
    emotion: str
    location_context: str  # HOME | COLLEGE | OUTSIDE


class RecommendationItem(BaseModel):
    place_id: int
    name: str
    category: PlaceCategory
    sub_category: str | None = None
    area: str | None = None
    latitude: float
    longitude: float
    approx_rating: float | None = None
    price_range: str | None = None
    avg_price_inr: float | None = None
    eco_friendly: bool = False
    # Rendered explanation string - never raw SHAP values (see
    # CLAUDE(CARA-BACKEND).md's Explainability Contract).
    reason: str
    reason_tags: list[str] = []
    # Already computed server-side per candidate for scoring/explanation -
    # exposed so the Android Home screen's curated rows (2026-07-16 layout)
    # can re-sort the same already-fetched list client-side (low crowd,
    # matches taste, fits budget) without extra API calls.
    crowd_score: float
    budget_fit: float
    preference_weight: float


class RecommendationsResponse(BaseModel):
    context: RecommendationContext
    results: list[RecommendationItem]
