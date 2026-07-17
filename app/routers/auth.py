"""
Registration and login. No session tokens/JWT - the rest of this API's
contract (see CLAUDE(CARA-BACKEND).md's "API Endpoints") already identifies
the caller by a plain user_id query/path param on every other endpoint
(e.g. GET /recommendations?user_id=...), so login just verifies credentials
and hands back the user record (including user_id) for the client to hold
onto locally. Keeping this simple matches the project's stated scope.
"""

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@router.post("/register", response_model=UserRead, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=_hash_password(payload.password),
        budget_default=payload.budget_default,
        home_lat=payload.home_lat,
        home_lon=payload.home_lon,
        college_lat=payload.college_lat,
        college_lon=payload.college_lon,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=UserRead)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> User:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or user.password_hash is None or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return user
