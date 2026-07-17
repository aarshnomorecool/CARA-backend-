"""
Logs a click/bookmark/dismiss/order_intent, then updates the user's
preference weight for that place's category via an exponential moving
average - per CLAUDE(CARA-BACKEND).md: "Live, per-user taste weights.
Updated on every interaction (this is the 'feels real-time' layer - NOT
model retraining)."
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.interaction import Interaction, InteractionAction
from app.models.place import Place
from app.models.preference import Preference
from app.models.user import User
from app.schemas.interaction import InteractionCreate, InteractionRead
from ml.features import CATEGORIES

router = APIRouter(tags=["interactions"])

# How strongly each action type pulls the EMA toward itself. bookmark/
# order_intent are strong explicit signals; a click is a weak positive
# signal; dismiss pulls the weight down.
ACTION_SIGNAL = {
    InteractionAction.click: 0.4,
    InteractionAction.bookmark: 0.9,
    InteractionAction.dismiss: 0.05,
    InteractionAction.order_intent: 1.0,
}
EMA_ALPHA = 0.3
DEFAULT_PREFERENCE_WEIGHT = 1 / len(CATEGORIES)  # matches recommendations.py's cold-start default


@router.post("/interactions", response_model=InteractionRead, status_code=201)
def create_interaction(payload: InteractionCreate, db: Session = Depends(get_db)) -> Interaction:
    if db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    place = db.get(Place, payload.place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")

    interaction = Interaction(
        user_id=payload.user_id,
        place_id=payload.place_id,
        action=payload.action,
        context_snapshot=payload.context_snapshot,
    )
    db.add(interaction)

    pref = db.get(Preference, (payload.user_id, place.category))
    if pref is None:
        pref = Preference(user_id=payload.user_id, category=place.category, weight=DEFAULT_PREFERENCE_WEIGHT)
        db.add(pref)

    signal = ACTION_SIGNAL[payload.action]
    pref.weight = max(0.0, min(1.0, (1 - EMA_ALPHA) * pref.weight + EMA_ALPHA * signal))

    db.commit()
    db.refresh(interaction)
    return interaction
