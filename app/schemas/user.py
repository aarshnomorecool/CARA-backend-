from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    name: str
    email: EmailStr
    budget_default: float | None = None
    home_lat: float | None = None
    home_lon: float | None = None
    college_lat: float | None = None
    college_lon: float | None = None


class UserCreate(UserBase):
    # bcrypt silently truncates beyond 72 bytes - reject rather than let
    # that happen unnoticed.
    password: str = Field(min_length=8, max_length=72)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    created_at: datetime


class LocationUpdate(BaseModel):
    lat: float
    lon: float
