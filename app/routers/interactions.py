"""
Logs a click/bookmark/dismiss/order_intent, then recomputes the user's
preference weight for that place's category - per CLAUDE(CARA-BACKEND).md:
"Live, per-user taste weights. Updated on every interaction (this is the
'feels real-time' layer - NOT model retraining)." See app/services/
preferences.py for why this is a full recompute rather than an incremental
EMA bump.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.interaction import Interaction
from app.models.place import Place
from app.models.user import User
from app.schemas.interaction import InteractionCreate, InteractionRead
from app.services.preferences import recompute_preference

router = APIRouter(tags=["interactions"])


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
    db.flush()  # so the new row is included in recompute_preference's replay

    recompute_preference(db, payload.user_id, place.category)

    db.commit()
    db.refresh(interaction)
    return interaction
