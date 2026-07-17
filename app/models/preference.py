from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.place import PlaceCategory


class Preference(Base):
    __tablename__ = "preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    category: Mapped[PlaceCategory] = mapped_column(Enum(PlaceCategory, name="place_category"), primary_key=True)

    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
