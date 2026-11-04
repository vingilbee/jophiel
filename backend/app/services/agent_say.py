"""Restraint for Jophiel speaking first (plan §8.4): *whether now*.

The rules every unprompted line passes: the session's commentary mode, a minimum gap between unprompted
messages, a hush after the user's last message for her mind's own lines, and serious moments said once.
Delivery itself is ``services/autonomous_speech.py`` (the activity's own conversation, one chat job per
conversation behind any reply, the follow-up announcement on ``channel``); this module holds the rules and
the receipts they read.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ChatMessage, Conversation
from app.services import desktop_preview
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


def mark_said(user_id: uuid.UUID, reasons: list[str]) -> None:
    """After a line was actually delivered: the receipts the gap and serious-moment rules read."""
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.set(_last_key(user_id), str(time.time()), ex=24 * 3600)
        which = serious(reasons)
        if which:
            client.set(_serious_key(user_id, which), "1", ex=SERIOUS_TTL[which])
    except redis.RedisError:
        log.warning("agent say: delivery receipt unavailable for user %s", user_id)


def interrupts(reasons: list[str]) -> bool:
    """Whether this message should cut off a reply she's in the middle of."""
    return any(rules(r).interrupts for r in reasons)
