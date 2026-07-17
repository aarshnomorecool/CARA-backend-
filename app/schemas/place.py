from pydantic import BaseModel, ConfigDict

from app.models.place import PlaceCategory


class PlaceBase(BaseModel):
    name: str
    category: PlaceCategory
    sub_category: str | None = None
    area: str | None = None
    latitude: float
    longitude: float
    approx_rating: float | None = None
    price_range: str | None = None
    avg_price_inr: float | None = None
    is_indoor: bool | None = None
    popular_time_slot: str | None = None
    eco_friendly: bool = False
    sustainability_score: int | None = None
    source: str | None = None
    coordinates_estimated: bool = False
    needs_verification: bool = False


class PlaceCreate(PlaceBase):
    pass


class PlaceRead(PlaceBase):
    model_config = ConfigDict(from_attributes=True)

    place_id: int
