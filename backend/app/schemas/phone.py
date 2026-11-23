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


class MediaState(BaseModel):
    """What's playing on the phone (its active media session)."""

    model_config = ConfigDict(extra="forbid")

    app: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=200)
    artist: str | None = Field(default=None, max_length=200)
    state: Literal["playing", "paused"]


class NavigationState(BaseModel):
    """A Maps trip under way, read off Google Maps' navigation notification."""

    model_config = ConfigDict(extra="forbid")

    next_step: str | None = Field(default=None, max_length=200)
    distance_text: str | None = Field(default=None, max_length=40)
    eta_text: str | None = Field(default=None, max_length=80)


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
    # Phase 8. ``media``: now playing (null: nothing). ``navigation``: the next Maps step (null: no trip).
    media: MediaState | None = None
    navigation: NavigationState | None = None
    # The phone's Messages switch (replies and drafts): false = her replies to other people are paused.
    messages_on: bool | None = None
    observed_at: datetime | None = None

    def fields(self) -> dict:
        """The reported fields only (what the phone actually sent), without ``observed_at``."""
        return self.model_dump(exclude_unset=True, exclude={"observed_at"})


class PhoneStateOut(BaseModel):
    changed: list[str]


class PhoneActionData(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    lat: float = Field(ge=-90, le=90, strict=True, repr=False)
    lng: float = Field(ge=-180, le=180, strict=True, repr=False)
    accuracy_m: float | None = Field(default=None, ge=0, repr=False)
    fix_at: datetime | None = Field(default=None, repr=False)


class PhoneActionOpened(BaseModel):
    """``open_app``: the label of the app that opened."""

    model_config = ConfigDict(extra="forbid")

    opened: str = Field(min_length=1, max_length=80)


class PhoneActionDraft(BaseModel):
    """``whatsapp_draft``: whether the draft went to a contact's chat or to WhatsApp's chat picker."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["contact", "picker"]


class PhoneActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["done", "notified", "refused", "expired", "failed"]
    reason: str | None = Field(default=None, max_length=80, repr=False)
    data: PhoneActionData | PhoneActionOpened | PhoneActionDraft | None = Field(default=None, repr=False)


class PhoneActionResultFrame(PhoneActionResult):
    type: Literal["phone_action_result"]
    action_id: uuid.UUID


class PhonePlaceOut(BaseModel):
    name: str
    address: str
    lat: float
    lng: float


# --------------------------------------------------------------------------- #
# Notifications on the phone (phase 8): services/phone_notifications.py
# --------------------------------------------------------------------------- #


class NotificationMessage(BaseModel):
    """One message of a chat notification, with who sent it. ``sender_id``: an opaque per-install id (HMAC of
    the contact's lookup key, ``identity: contact``, or of name + app + conversation, ``identity: name``); the phone
    number never leaves the phone. ``event_id``: the same message in two snapshots has one id."""

    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(default=None, max_length=128)
    sender: str | None = Field(default=None, max_length=120)
    sender_id: str | None = Field(default=None, max_length=128)
    identity: Literal["contact", "name"] | None = None
    in_contacts: bool | None = None
    text: str = Field(default="", max_length=4000)
    at: datetime | None = None
    # The ``event_id`` of the message this one replies to, when the app shows it (affinity B2: kept as a fact for
    # her reading of who was addressed, never inferred).
    reply_to: str | None = Field(default=None, max_length=128)


class PostedNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=256)
    revision: int = Field(default=0, ge=0)
    app: str | None = Field(default=None, max_length=80)
    package: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=4000)
    conversation: str | None = Field(default=None, max_length=300)
    conversation_id: str | None = Field(default=None, max_length=128)
    conversation_identity: Literal["shortcut", "title"] | None = None
    is_group: bool = False
    messages: list[NotificationMessage] = Field(default_factory=list, max_length=5)
    can_reply: bool = False
    posted_at: datetime | None = None


class RemovedNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=40)


class PhoneNotificationsReport(BaseModel):
    """What changed among the phone's notifications: ``posted`` (new or updated, coalesced per key on the phone),
    ``removed`` (gone from the phone), ``all_removed`` (the Notifications sense was switched off, or the phone
    lost notification access: nothing it reported before is still known). Socket frame or
    ``POST /api/phone/notifications``, same body."""

    model_config = ConfigDict(extra="forbid")

    posted: list[PostedNotification] = Field(default_factory=list, max_length=50)
    removed: list[RemovedNotification] = Field(default_factory=list, max_length=200)
    all_removed: bool = False


class PhoneNotificationsFrame(PhoneNotificationsReport):
    type: Literal["phone_notifications"]


class PhoneNotificationsOut(BaseModel):
    posted: int
    removed: int
