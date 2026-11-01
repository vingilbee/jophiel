"""Pydantic request/response schemas for users & auth."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    """Public view of a user — never includes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: EmailStr
    created_at: datetime

    @computed_field
    @property
    def is_owner(self) -> bool:
        """Whether the app shows her insides (mind, learning) to this user; the routes enforce it."""
        from app.core.config import get_settings
        return get_settings().is_owner(self.email)
