from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.interaction import Interaction, InteractionAction
from app.models.place import Place
from app.models.preference import Preference
from app.models.user import User
from app.schemas.place import PlaceRead
from app.schemas.preference import PreferenceRead
from app.schemas.user import LocationUpdate, UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: int, db: Session = Depends(get_db)) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/{user_id}/preferences", response_model=list[PreferenceRead])
def get_preferences(user_id: int, db: Session = Depends(get_db)) -> list[Preference]:
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return list(db.execute(select(Preference).where(Preference.user_id == user_id)).scalars())


@router.get("/{user_id}/saved-places", response_model=list[PlaceRead])
def get_saved_places(user_id: int, db: Session = Depends(get_db)) -> list[Place]:
    """Places the user has bookmarked, most recently bookmarked first.

    There's no "unbookmark" interaction type (see PlaceDetailsScreen.kt's
    bookmark button - it only ever logs "bookmark", never a removal), so
    this is everything ever bookmarked, deduped by place. Acceptable V1
    simplification - revisit if a real "remove from saved" feature is
    wanted later.
    """
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    last_bookmarked = (
        select(Interaction.place_id, func.max(Interaction.timestamp).label("last_bookmarked"))
        .where(Interaction.user_id == user_id, Interaction.action == InteractionAction.bookmark)
        .group_by(Interaction.place_id)
        .subquery()
    )
    stmt = (
        select(Place)
        .join(last_bookmarked, Place.place_id == last_bookmarked.c.place_id)
        .order_by(last_bookmarked.c.last_bookmarked.desc())
    )
    return list(db.execute(stmt).scalars())


@router.delete("/{user_id}/saved-places/{place_id}", status_code=204)
def delete_saved_place(user_id: int, place_id: int, db: Session = Depends(get_db)) -> Response:
    """Removes a bookmark by deleting the underlying bookmark interaction(s).

    Idempotent - unbookmarking something that was never bookmarked still
    returns 204, since the caller's desired end state ("not in Saved") is
    already true.
    """
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    db.execute(
        delete(Interaction).where(
            Interaction.user_id == user_id,
            Interaction.place_id == place_id,
            Interaction.action == InteractionAction.bookmark,
        )
    )
    db.commit()
    return Response(status_code=204)


@router.put("/{user_id}/home-location", status_code=204)
def update_home_location(user_id: int, payload: LocationUpdate, db: Session = Depends(get_db)) -> Response:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.home_lat = payload.lat
    user.home_lon = payload.lon
    db.commit()
    return Response(status_code=204)


@router.put("/{user_id}/college-location", status_code=204)
def update_college_location(user_id: int, payload: LocationUpdate, db: Session = Depends(get_db)) -> Response:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.college_lat = payload.lat
    user.college_lon = payload.lon
    db.commit()
    return Response(status_code=204)
