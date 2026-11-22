"""Pydantic schemas for the phone app: its device (push notifications) and what it reports about itself."""
import uuid
from datetime import datetime
from typing import Literal

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


class PhoneStateReport(BaseModel):
    """What the phone says about itself (services/phone_state.py). Partial: only the fields sent are reported, an
    explicit null means "no longer known" (motion unknown, location off; a confirmed departure is ``place: "away"``). Descriptive only, like every sense: it shapes
    what she knows, never what she's allowed to do. ``observed_at``: when the phone saw it (a batch sent late from
    the background), clamped by the server."""

    model_config = ConfigDict(extra="forbid")

    screen: Literal["on", "off"] | None = None
    locked: bool | None = None
    battery: int | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None
    headphones: bool | None = None
    network: Literal["wifi", "cellular", "none"] | None = None
    wifi_name: str | None = Field(default=None, max_length=64)
    dnd: bool | None = None
    ringer: Literal["normal", "vibrate", "silent"] | None = None
    motion: Literal["still", "walking", "running", "cycling", "vehicle"] | None = None
    # A named place the user set on the phone ("home", "work"), never coordinates. "away": the phone confirmed
    # they're at none of their named places (left home); null: where they are isn't known (location off).
    place: str | None = Field(default=None, max_length=60)
    observed_at: datetime | None = None

    def fields(self) -> dict:
        """The reported fields only (what the phone actually sent), without ``observed_at``."""
        return self.model_dump(exclude_unset=True, exclude={"observed_at"})


class PhoneStateOut(BaseModel):
    changed: list[str]
