import enum

from sqlalchemy import Boolean, CheckConstraint, Enum, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlaceCategory(str, enum.Enum):
    restaurant = "restaurant"
    cafe = "cafe"
    park = "park"
    mall = "mall"
    library = "library"
    gym = "gym"
    hospital = "hospital"
    tourist_attraction = "tourist_attraction"


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (
        CheckConstraint(
            "sustainability_score IS NULL OR sustainability_score BETWEEN 1 AND 5",
            name="ck_places_sustainability_score_range",
        ),
    )

    place_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[PlaceCategory] = mapped_column(Enum(PlaceCategory, name="place_category"), nullable=False)
    sub_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    area: Mapped[str | None] = mapped_column(String(255), nullable=True)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    approx_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_range: Mapped[str | None] = mapped_column(String(20), nullable=True)
    avg_price_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_indoor: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    popular_time_slot: Mapped[str | None] = mapped_column(String(50), nullable=True)

    eco_friendly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sustainability_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    coordinates_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_verification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Google's Places API ToS prohibits caching/storing photo *content* -
    # only place_id is exempt for indefinite storage. So we store metadata
    # only and fetch the actual image live at display time via
    # GET /places/{id}/photo (see app/routers/places.py).
    google_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_attribution: Mapped[str | None] = mapped_column(String(500), nullable=True)
