import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InteractionAction(str, enum.Enum):
    click = "click"
    bookmark = "bookmark"
    dismiss = "dismiss"
    order_intent = "order_intent"


class Interaction(Base):
    __tablename__ = "interactions"

    interaction_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.place_id"), nullable=False, index=True)
    action: Mapped[InteractionAction] = mapped_column(
        Enum(InteractionAction, name="interaction_action"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # snapshot of context at interaction time: lat, lon, time_slot, weather, emotion
    context_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
