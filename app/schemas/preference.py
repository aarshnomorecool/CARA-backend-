from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.place import PlaceCategory


class PreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    category: PlaceCategory
    weight: float
    last_updated: datetime
