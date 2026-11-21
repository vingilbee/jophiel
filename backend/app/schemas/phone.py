"""Pydantic schemas for the phone app's device (push notifications)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceRegister(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=4096)
    device_name: str | None = Field(default=None, max_length=120)


class DeviceUpdate(BaseModel):
    notifications_enabled: bool | None = None
    # On: the push carries her words (trimmed to ~200 chars), which then pass through Google.
    show_text: bool | None = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_name: str | None
    notifications_enabled: bool
    show_text: bool
    last_seen_at: datetime
