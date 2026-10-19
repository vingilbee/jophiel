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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ChatMessage, Conversation
from app.services import chat as chat_service
from app.services import desktop_preview, dialogue_memory
from app.services.game_event_kinds import QUIET, URGENT_GAP_SECONDS, kind_of, rules

log = logging.getLogger(__name__)

# Which events she may speak about, in which mode, is in game_event_kinds. Stuck and
# struggling are said in any mode with no gap: she may need to ask the user for what's
# missing (seen live: "stuck" said 8 s after a reply was dropped).
# Too serious to wait out the gap: she died, or is down to this many hearts (of 10).
# Each is said once per moment (the TTL), so a long fight at low health is one warning.
LOW_HEARTS = 3.0
SERIOUS_TTL = {"died": 30, "low_health": 90}
_HEARTS = re.compile(r"(\d+(?:\.\d+)?) of 10 hearts")
_POINTS = re.compile(r"health (\d+(?:\.\d+)?)/20")


def channel(user_id: uuid.UUID | str) -> str:
    return f"agent:say:{user_id}"


def _last_key(user_id: uuid.UUID) -> str:
    return f"agent:last_say:{user_id}"


def _serious_key(user_id: uuid.UUID, which: str) -> str:
    return f"agent:serious:{user_id}:{which}"


def serious(reasons: list[str]) -> str | None:
    """"died" or "low_health" when a danger reason is that serious, else None."""
    for reason in reasons:
        if kind_of(reason) != "danger":
            continue
        text = reason.split(":", 1)[1].strip()
        if text == "died" or text.startswith("died "):
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
    kinds = [rules(r) for r in reasons]
    if any(kind.always for kind in kinds):
        return True, ""
    if serious_fresh and commentary_mode != QUIET:
        return True, ""
    # She paused on her own: the user should know, whatever the mode or the gap.
    stopped = any(r.startswith("paused:") and r != "paused:user_pause" for r in reasons)
    if commentary_mode == QUIET:
        if stopped:
            return True, ""
        return False, "the user asked for quiet; only say something when you need them"
    if not any(commentary_mode in kind.said for kind in kinds):
        return False, "this isn't worth interrupting the user for; keep it to yourself"
    gap = get_settings().agent_say_min_gap_seconds * (1 if commentary_mode == "occasional" else 0.33)
    if any(kind.urgent and commentary_mode in kind.said for kind in kinds):
        gap = min(gap, URGENT_GAP_SECONDS)
    if seconds_since_last is not None and seconds_since_last < gap and not stopped:
        return False, f"you spoke {int(seconds_since_last)} s ago; don't chatter"
    return True, ""


# Her mind's own lines (a quick thought's, or a hard thought's background turn) wait this long after
# the user's last message: her reply answers it. Live, nearly every message got two answers, a quick
# thought's and the reply, and now and then they disagreed. A reaction to danger still speaks.
MIND_HUSH_SECONDS = 20.0
_MIND_PREFIXES = ("thought:", "light:", "quiet:", "plan:")


def from_mind(reasons: list[str]) -> bool:
    return any(r.startswith(_MIND_PREFIXES) for r in reasons)


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


def deliver(db: Session, *, user_id: uuid.UUID, text: str, reasons: list[str], commentary_mode: str) -> str:
    """Say ``text`` to the user now, if restraint allows. Returns what happened,
    for the model."""
    text = " ".join(text.split())[:400]
    if not text:
        return "Nothing to say."
    if from_mind(reasons) and _user_spoke_within(db, user_id, MIND_HUSH_SECONDS):
        return "Not said: the user just spoke, and your reply to them answers it; keep this to yourself."
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
    return any(rules(r).interrupts for r in reasons)
