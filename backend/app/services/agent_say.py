"""Columbina speaking first from a background turn (plan §8.4).

``deliver`` is behind her ``say`` tool. It decides *whether now* (restraint:
the session's commentary mode, a minimum gap between unprompted messages, at
most one per turn), saves the message in the user's latest conversation (so a
reply has context), and publishes it for every open socket of that user,
which shows it as a nudge and speaks it if voice is live and idle.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

import redis
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services import chat as chat_service
from app.services import desktop_preview, dialogue_memory

log = logging.getLogger(__name__)

# Always worth saying, whatever the mode or the gap.
URGENT = ("needs_human",)
# Worth saying in "occasional" mode (subject to the gap); "quiet" says only urgent ones
# and the session stopping on its own.
OCCASIONAL = ("goal_done", "stuck", "danger", "paused", "struggling", "warning")
# She's blocked and may need to ask the user (what's missing: an item, a move, a place): the
# gap never holds these back, in any mode. Seen live: "stuck" said 8 s after a reply was dropped.
BLOCKED = ("stuck", "struggling")
# Too serious to wait out the gap: she died, or is down to this many hearts (of 10).
# Each is said once per moment (the TTL), so a long fight at low health is one warning.
LOW_HEARTS = 3.0
SERIOUS_TTL = {"died": 30, "low_health": 90}
# Urgent enough to cut off what she's saying (it's out of date now): danger in the
# game (a mob on her, a hit, low health, a death) and needing the user.
INTERRUPTS = ("danger", "needs_human")
_HEARTS = re.compile(r"(\d+(?:\.\d+)?) of 10 hearts")
_POINTS = re.compile(r"health (\d+(?:\.\d+)?)/20")


def channel(user_id: uuid.UUID | str) -> str:
    return f"agent:say:{user_id}"


def _last_key(user_id: uuid.UUID) -> str:
    return f"agent:last_say:{user_id}"


def _serious_key(user_id: uuid.UUID, which: str) -> str:
    return f"agent:serious:{user_id}:{which}"


def _kind(reason: str) -> str:
    return reason.split(":", 1)[0]


def serious(reasons: list[str]) -> str | None:
    """"died" or "low_health" when a danger reason is that serious, else None."""
    for reason in reasons:
        if _kind(reason) != "danger":
            continue
        text = reason.split(":", 1)[1].strip()
        if text == "died":
            return "died"
        hearts = _HEARTS.search(text)
        points = _POINTS.search(text)
        value = float(hearts.group(1)) if hearts else float(points.group(1)) / 2 if points else None
        if value is not None and value <= LOW_HEARTS:
            return "low_health"
    return None


def allowed(reasons: list[str], commentary_mode: str, seconds_since_last: float | None,
            *, serious_fresh: bool = False) -> tuple[bool, str]:
    """``serious_fresh``: a serious moment (see ``serious``) not yet said: it skips the gap."""
    kinds = {_kind(r) for r in reasons}
    if kinds & set(URGENT):
        return True, ""
    if serious_fresh and commentary_mode != "quiet":
        return True, ""
    if kinds & set(BLOCKED):
        return True, ""
    stopped = any(r.startswith("paused:") and r != "paused:user_pause" for r in reasons)
    if commentary_mode == "quiet":
        if stopped:
            return True, ""
        return False, "the user asked for quiet; only say something when you need them"
    if commentary_mode == "occasional" and not (kinds & set(OCCASIONAL)):
        return False, "this isn't worth interrupting the user for; keep it to yourself"
    gap = get_settings().agent_say_min_gap_seconds * (1 if commentary_mode == "occasional" else 0.33)
    if seconds_since_last is not None and seconds_since_last < gap and not stopped:
        return False, f"you spoke {int(seconds_since_last)} s ago; don't chatter"
    return True, ""


def deliver(db: Session, *, user_id: uuid.UUID, text: str, reasons: list[str], commentary_mode: str) -> str:
    """Say ``text`` to the user now, if restraint allows. Returns what happened,
    for the model."""
    text = " ".join(text.split())[:400]
    if not text:
        return "Nothing to say."
    client = desktop_preview.redis_client()
    last = None
    if client is not None:
        try:
            raw = client.get(_last_key(user_id))
            last = time.time() - float(raw) if raw else None
        except (redis.RedisError, ValueError):
            last = None
    which = serious(reasons)
    fresh = False
    if which and client is not None:
        try:
            fresh = not client.exists(_serious_key(user_id, which))
        except redis.RedisError:
            fresh = False
    ok, why = allowed(reasons, commentary_mode, last, serious_fresh=fresh)
    if not ok:
        return f"Not said: {why}."
    conversations = chat_service.list_conversations(db, user_id=user_id)
    if conversations:
        conversation = conversations[0]
    else:
        conversation = chat_service.get_or_create_conversation(db, user_id=user_id, conversation_id=None)
    message = chat_service.add_message(
        db, conversation=conversation, role="assistant", content=text, commit=False,
    )
    # Like any delivered assistant message (see the nudge path).
    scheduled = dialogue_memory.schedule_update(
        db, user_id=user_id, conversation_id=conversation.id, trigger_message_id=message.id,
    )
    db.commit()
    if scheduled:
        dialogue_memory.wake_worker()
    if client is not None:
        try:
            client.set(_last_key(user_id), str(time.time()), ex=24 * 3600)
            if which:
                client.set(_serious_key(user_id, which), "1", ex=SERIOUS_TTL[which])
            client.publish(channel(user_id), json.dumps({
                "conversation_id": str(conversation.id), "message_id": str(message.id),
                "text": text, "reason": "game", "interrupt": interrupts(reasons),
            }))
        except redis.RedisError:
            log.warning("agent say: saved but could not publish for user %s", user_id)
    return "Said it. The user will see it (and hear it if voice is on)."



def interrupts(reasons: list[str]) -> bool:
    """Whether this message should cut off a reply she's in the middle of."""
    return any(_kind(r) in INTERRUPTS for r in reasons)
