"""Jophiel speaking first in a game: the user's commentary setting, and the receipt of when she last spoke.

Whether to speak is her mind's choice. What stays here is the user's own rule for a game session (its commentary
mode: which kinds of events may be said in which mode, ``allowed``) and a fact her mind reads when it decides:
when she last spoke on her own (``mark_said``). Delivery itself is ``services/autonomous_speech.py``.
"""
from __future__ import annotations

import logging
import time
import uuid

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, Conversation
from app.services import desktop_preview
from app.services.game_event_kinds import QUIET, rules

log = logging.getLogger(__name__)

# Which events she may speak about, in which mode, is in game_event_kinds (the user's commentary setting).


def channel(user_id: uuid.UUID | str) -> str:
    return f"agent:say:{user_id}"


def _last_key(user_id: uuid.UUID) -> str:
    return f"agent:last_say:{user_id}"


def allowed(reasons: list[str], commentary_mode: str) -> tuple[bool, str]:
    """The user's commentary setting applied to an event: quiet means only when she needs them (or stopped on her
    own); otherwise the event's kind must be one that mode lets her speak about. No pace rule: how often she
    speaks is her own judgment."""
    kinds = [rules(r) for r in reasons]
    if any(kind.always for kind in kinds):
        return True, ""
    # She paused on her own: the user should know, whatever the mode.
    if any(r.startswith("paused:") and r != "paused:user_pause" for r in reasons):
        return True, ""
    if commentary_mode == QUIET:
        return False, "the user asked for quiet; only say something when you need them"
    if not any(commentary_mode in kind.said for kind in kinds):
        return False, "the user's commentary setting keeps this kind of thing unsaid"
    return True, ""


def _user_spoke_within(db: Session, user_id: uuid.UUID, seconds: float) -> bool:
    # created_at is now() stored without a zone, so compare with the database's own local clock.
    since = func.localtimestamp() - func.make_interval(0, 0, 0, 0, 0, 0, seconds)
    return db.scalar(
        select(ChatMessage.id).join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(Conversation.user_id == user_id, ChatMessage.role == "user", ChatMessage.created_at >= since)
        .limit(1)) is not None


def user_spoke_since(db: Session, user_id: uuid.UUID, at: float) -> bool:
    """Whether the user sent a message after ``at`` (Unix seconds)."""
    return _user_spoke_within(db, user_id, max(0.0, time.time() - at))


def mark_said(user_id: uuid.UUID, reasons: list[str]) -> None:
    """After a line was actually delivered: when she last spoke on her own (a fact her mind reads)."""
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.set(_last_key(user_id), str(time.time()), ex=24 * 3600)
    except redis.RedisError:
        log.warning("agent say: delivery receipt unavailable for user %s", user_id)


def interrupts(reasons: list[str]) -> bool:
    """Whether this message should cut off a reply she's in the middle of."""
    return any(rules(r).interrupts for r in reasons)
