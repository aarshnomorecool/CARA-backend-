from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.interaction import InteractionAction


class InteractionCreate(BaseModel):
    user_id: int
    place_id: int
    action: InteractionAction
    context_snapshot: dict | None = None


class InteractionRead(InteractionCreate):
    model_config = ConfigDict(from_attributes=True)

    interaction_id: int
    timestamp: datetime
