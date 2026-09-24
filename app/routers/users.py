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
from app.routers.auth import _hash_password, _verify_password
from app.schemas.user import LocationUpdate, PasswordChange, UserRead, UserStats, UserUpdate
from app.services.preferences import recompute_preference

router = APIRouter(prefix="/users", tags=["users"])


def _get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: int, db: Session = Depends(get_db)) -> User:
    return _get_user_or_404(db, user_id)


@router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)) -> User:
    """Edit profile from the Android Profile screen. budget_default feeds
    straight into GET /recommendations whenever the client sends no explicit
    budget, so changing it here changes what Home ranks."""
    user = _get_user_or_404(db, user_id)
    fields = payload.model_fields_set
    if "name" in fields and payload.name is not None:
        user.name = payload.name.strip()
    if "budget_default" in fields:
        user.budget_default = payload.budget_default
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}/password", status_code=204)
def change_password(user_id: int, payload: PasswordChange, db: Session = Depends(get_db)) -> Response:
    user = _get_user_or_404(db, user_id)
    if user.password_hash is None or not _verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    user.password_hash = _hash_password(payload.new_password)
    db.commit()
    return Response(status_code=204)


@router.get("/{user_id}/stats", response_model=UserStats)
def get_user_stats(user_id: int, db: Session = Depends(get_db)) -> UserStats:
    """Headline numbers for the Profile screen - all derived from the
    interactions log, nothing stored separately."""
    user = _get_user_or_404(db, user_id)

    saved_count = db.execute(
        select(func.count(func.distinct(Interaction.place_id))).where(
            Interaction.user_id == user_id, Interaction.action == InteractionAction.bookmark
        )
    ).scalar_one()
    interaction_count = db.execute(
        select(func.count()).select_from(Interaction).where(Interaction.user_id == user_id)
    ).scalar_one()
    places_explored = db.execute(
        select(func.count(func.distinct(Interaction.place_id))).where(Interaction.user_id == user_id)
    ).scalar_one()
    top_category = db.execute(
        select(Preference.category)
        .where(Preference.user_id == user_id)
        .order_by(Preference.weight.desc())
        .limit(1)
    ).scalar_one_or_none()

    return UserStats(
        saved_count=saved_count,
        interaction_count=interaction_count,
        places_explored=places_explored,
        top_category=top_category.value if hasattr(top_category, "value") else top_category,
        member_since=user.created_at,
    )


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)) -> Response:
    """Permanently deletes the account and everything tied to it
    (interactions, learned preferences). Irreversible - the Android client
    gates this behind a typed confirmation dialog."""
    user = _get_user_or_404(db, user_id)
    db.execute(delete(Interaction).where(Interaction.user_id == user_id))
    db.execute(delete(Preference).where(Preference.user_id == user_id))
    db.delete(user)
    db.commit()
    return Response(status_code=204)


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
    """Removes a bookmark by deleting the underlying bookmark interaction(s),
    then recomputes that place's category preference weight so the removed
    bookmark's influence is actually undone rather than permanently baked in
    - see app/services/preferences.py's docstring for the bug this fixes.

    Idempotent - unbookmarking something that was never bookmarked still
    returns 204, since the caller's desired end state ("not in Saved") is
    already true.
    """
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    place = db.get(Place, place_id)

    db.execute(
        delete(Interaction).where(
            Interaction.user_id == user_id,
            Interaction.place_id == place_id,
            Interaction.action == InteractionAction.bookmark,
        )
    )

    if place is not None:
        recompute_preference(db, user_id, place.category)

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
