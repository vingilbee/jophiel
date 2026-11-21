"""Her messages reaching the phone when the user hasn't seen them anywhere (frontend-phone/PLAN_PHASE_0-3.md,
phase 2).

Where an assistant message is finally saved, ``schedule_for_message`` adds one outbox row per signed-in phone
(in the caller's transaction, so a message and its push are saved together). The push worker
(``workers/push.py`` -> ``run_once``) takes a row once it's due (``push_delay_seconds`` later), and then sends it
only if, *at that moment*, the user isn't at any of their apps (``services/presence.py``) and she didn't say it
aloud; otherwise the row is dropped as not needed. A backend that was down sends late, not never.

When the user comes back to an app, ``schedule_clear`` tells each phone that got notifications to take them
down. Every push is data-only; the phone words the notification. Her text is in the payload only when the
device's "Show message text" is on.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ChatMessage, Conversation, PhoneDevice, PushDelivery, UserChatState
from app.db.session import SessionLocal
from app.services import auth as auth_service
from app.services import fcm, presence

logger = logging.getLogger(__name__)

# Longest text carried in a push when the device shows message text.
TEXT_LIMIT = 200
# A message waits at most this long for a live voice session to say it aloud before it's pushed anyway.
VOICE_WAIT_SECONDS = 60
# Retries back off along this list, then give up.
BACKOFF_SECONDS = (5, 15, 60, 300, 900, 1800, 3600)
# A claimed row is someone's for this long; a worker that died lets it go after.
LEASE_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _primary(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
    """Only her messages in the user's primary (omnichannel) conversation reach the phone."""
    state = db.get(UserChatState, user_id)
    if state is not None:
        return state.primary_conversation_id == conversation_id
    latest = db.scalar(select(Conversation.id).where(Conversation.user_id == user_id)
                       .order_by(Conversation.updated_at.desc()).limit(1))
    return latest is None or latest == conversation_id


def schedule_for_message(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID,
                         message_id: uuid.UUID) -> int:
    """Queue a push of this message for each of the user's phones with notifications on. No commit: it's
    saved with the message. Returns how many were queued. Never raises (a push must not fail her reply)."""
    try:
        with db.begin_nested():
            if not _primary(db, user_id, conversation_id):
                return 0
            devices = list(db.scalars(select(PhoneDevice.id).where(
                PhoneDevice.user_id == user_id, PhoneDevice.notifications_enabled.is_(True))))
            due = _now() + timedelta(seconds=get_settings().push_delay_seconds)
            for device_id in devices:
                db.add(PushDelivery(kind="message", user_id=user_id, device_id=device_id,
                                    assistant_message_id=message_id, conversation_id=conversation_id,
                                    next_attempt_at=due, queued_at=_now()))
            db.flush()
            return len(devices)
    except Exception:
        logger.exception("push: could not queue message %s", message_id)
        return 0


def schedule_for_message_now(*, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID) -> None:
    """The same, in its own transaction (callers that already committed the message)."""
    with SessionLocal() as db:
        if schedule_for_message(db, user_id=user_id, conversation_id=conversation_id, message_id=message_id):
            db.commit()


def schedule_clear(db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None = None) -> int:
    """The user is back at an app: each phone that got notifications since the last clear takes them down."""
    devices = list(db.scalars(select(PhoneDevice).where(PhoneDevice.user_id == user_id,
                                                        PhoneDevice.needs_clear.is_(True))
                              .with_for_update(skip_locked=True)))
    for device in devices:
        device.needs_clear = False
        db.add(PushDelivery(kind="clear", user_id=user_id, device_id=device.id,
                            conversation_id=conversation_id, next_attempt_at=_now()))
    db.commit()
    return len(devices)


def mark_spoken(db: Session, message_id: uuid.UUID | str) -> None:
    """She said this message aloud on a live voice session: it isn't pushed."""
    try:
        message = db.get(ChatMessage, uuid.UUID(str(message_id)))
    except ValueError:
        return
    if message is not None and message.spoken_at is None:
        message.spoken_at = _now()
        db.commit()


def payload(row: PushDelivery, device: PhoneDevice, message: ChatMessage | None) -> dict[str, str]:
    """The data-only payload. ``text`` only when the device shows message text."""
    if row.kind == "clear":
        data = {"type": "clear"}
        if row.conversation_id:
            data["conversation_id"] = str(row.conversation_id)
        return data
    data = {"type": "message", "conversation_id": str(row.conversation_id), "message_id": str(row.assistant_message_id)}
    if device.show_text and message is not None:
        text = " ".join(message.content.split())
        data["text"] = text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1].rstrip() + "…"
    return data


def _claim(db: Session) -> PushDelivery | None:
    now = _now()
    row = db.scalar(
        select(PushDelivery)
        .where(PushDelivery.status.in_(("pending", "sending")), PushDelivery.next_attempt_at <= now)
        .order_by(PushDelivery.next_attempt_at, PushDelivery.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        db.rollback()
        return None
    row.status = "sending"
    row.claim_token = secrets.token_urlsafe(24)
    row.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return row


def _finish(db: Session, row: PushDelivery, status: str, error: str | None = None) -> None:
    row.status = status
    row.last_error = error
    row.claim_token = None
    row.finished_at = _now()
    db.commit()


def _later(db: Session, row: PushDelivery, seconds: float, error: str | None = None, *, count: bool = True) -> None:
    if count:
        row.attempts += 1
    if row.attempts > len(BACKOFF_SECONDS):
        _finish(db, row, "failed", error)
        return
    row.status = "pending"
    row.last_error = error
    row.claim_token = None
    row.next_attempt_at = _now() + timedelta(seconds=seconds)
    db.commit()


def _not_needed(db: Session, row: PushDelivery, message: ChatMessage | None) -> str | None:
    """Why this message push isn't needed any more, or None to send it."""
    if message is None:
        return "message gone"
    if message.spoken_at is not None:
        return "spoken aloud"
    if presence.present(row.user_id):
        return "user present"
    return None


def run_once(*, client=None) -> bool:
    """Take one due push and settle it. True when there was one."""
    with SessionLocal() as db:
        row = _claim(db)
        if row is None:
            return False
        device = db.get(PhoneDevice, row.device_id)
        if device is None:
            _finish(db, row, "dropped", "device gone")
            return True
        if not auth_service.session_alive(device.session_hash):
            # The device dies with its session (and its pushes with it).
            db.delete(device)
            db.commit()
            return True
        message = db.get(ChatMessage, row.assistant_message_id) if row.assistant_message_id else None
        if row.kind == "message":
            if not device.notifications_enabled:
                _finish(db, row, "dropped", "notifications off")
                return True
            reason = _not_needed(db, row, message)
            if reason is not None:
                _finish(db, row, "dropped", reason)
                return True
            age = (_now() - _aware(row.queued_at)).total_seconds()
            if presence.voice_live(row.user_id) and age < VOICE_WAIT_SECONDS:
                # A live voice session may still say it aloud: look again shortly.
                _later(db, row, get_settings().push_delay_seconds, count=False)
                return True
        if not fcm.configured():
            _finish(db, row, "dropped", "push not configured")
            return True
        result = fcm.send(device.fcm_token, payload(row, device, message), client=client)
        if result.outcome == "sent":
            if row.kind == "message":
                device.needs_clear = True
            _finish(db, row, "sent")
        elif result.outcome == "unregistered":
            logger.info("push: FCM token for device %s is dead; forgetting the device", device.id)
            db.delete(device)
            db.commit()
        elif result.outcome == "retry":
            wait = result.retry_after or BACKOFF_SECONDS[min(row.attempts, len(BACKOFF_SECONDS) - 1)]
            _later(db, row, wait, result.detail)
        else:
            _finish(db, row, "failed", result.detail)
        return True


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
