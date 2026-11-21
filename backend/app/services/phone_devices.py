"""Signed-in phones and where their notifications go (frontend-phone/PLAN_PHASE_0-3.md, phase 2).

One device row per phone session (bearer token). It is created or refreshed with the phone's FCM token after
sign-in (``PUT /api/phone/device``), carries the phone's notification switches, and is deleted with the session
(sign-out) or when FCM says the token is dead. Route handlers stay thin and call into here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import PhoneDevice
from app.services import auth as auth_service


def for_session(db: Session, token: str) -> PhoneDevice | None:
    return db.scalar(select(PhoneDevice).where(PhoneDevice.session_hash == auth_service.session_key_hash(token)))


def register(db: Session, *, user_id: uuid.UUID, token: str, fcm_token: str,
             device_name: str | None) -> PhoneDevice:
    """Create or refresh this session's device. An FCM token belongs to one phone: if an older session of
    that phone still holds it (signed in again without signing out), that old row goes."""
    session_hash = auth_service.session_key_hash(token)
    db.execute(delete(PhoneDevice).where(PhoneDevice.fcm_token == fcm_token,
                                         PhoneDevice.session_hash != session_hash))
    device = db.scalar(select(PhoneDevice).where(PhoneDevice.session_hash == session_hash))
    now = datetime.now(timezone.utc)
    if device is None:
        device = PhoneDevice(user_id=user_id, session_hash=session_hash, fcm_token=fcm_token,
                             device_name=device_name, last_seen_at=now)
        db.add(device)
    else:
        device.fcm_token = fcm_token
        device.last_seen_at = now
        if device_name:
            device.device_name = device_name
    db.commit()
    db.refresh(device)
    return device


def update(db: Session, device: PhoneDevice, *, notifications_enabled: bool | None = None,
           show_text: bool | None = None) -> PhoneDevice:
    if notifications_enabled is not None:
        device.notifications_enabled = notifications_enabled
    if show_text is not None:
        device.show_text = show_text
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(device)
    return device


def forget_session(db: Session, token: str) -> None:
    """Sign-out: this session's device goes (and with it its pending pushes)."""
    db.execute(delete(PhoneDevice).where(PhoneDevice.session_hash == auth_service.session_key_hash(token)))
    db.commit()


def forget(db: Session, device_id: uuid.UUID) -> None:
    db.execute(delete(PhoneDevice).where(PhoneDevice.id == device_id))
    db.commit()
