"""She looks at the phone again after a turn of hers that used its screen (phase 9 follow-up, 2026-11-25).

A reply turn that acted on the user's phone screen ends when she answers, but what's on the screen keeps going (a
battle, a timer, a page loading). Her background mind can't see the screen, so it can't tell that something is
still under way. Instead, the end of such a turn schedules one wake of her mind ``LOOK_SECONDS`` later (a durable
``coordinator_work`` wake, origin ``phone_look:<turn>``), and that wake has her hands on the screen: she reads it
herself and decides to carry on, say something, or stay silent. Code decides when she looks, never whether anything
is left: that's hers.

The exception to "reply turns only" (frontend-phone/PLAN_PHASE_9.md rule 7, the owner's decision 2026-11-25) is
kept narrow:

- Only after a turn whose hands acted (not one that only read), that wasn't taken over (their touch, Stop, or their
  next words), in the conversation that turn was in.
- Only about what the user's own message asked (``asked``): the window is ``WINDOW_SECONDS`` from that message, and
  any newer message of theirs in the conversation drops the look (it no longer answers anything: their new turn
  holds the phone). A look that acted schedules the next one, the same message and the same window, so a battle can
  be followed through; a look that didn't act ends the chain.
- Never on a wake someone else's words caused (``third_party``): those never reach a reply turn's phone either.
- The phone's own refusals still stand (locked, screen off, her own app), and the newest turn wins on both sides,
  so the user's words or touch stop her at once.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db.models import ChatMessage, CoordinatorWork
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

ORIGIN = "phone_look"
LOOK_SECONDS = 10.0  # after the turn ends: long enough to read her answer, short enough to catch a battle
WINDOW_SECONDS = 600.0  # from the user's message: how long her looks can follow what it asked
ASKED_CHARS = 400


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _asked(db, message_id: uuid.UUID) -> ChatMessage | None:
    message = db.get(ChatMessage, message_id)
    return message if message is not None and message.role == "user" else None


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _still_theirs(db, conversation_id: uuid.UUID, message: ChatMessage, now: datetime, *, ahead: float = 0.0) -> bool:
    """The window from their message is still open ``ahead`` seconds from now, and they haven't written since."""
    if message.conversation_id != conversation_id:
        return False
    if (now + timedelta(seconds=ahead) - _aware(message.created_at)).total_seconds() > WINDOW_SECONDS:
        return False
    if message.conversation_sequence is None:
        return False
    newer = db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.role == "user",
        ChatMessage.conversation_sequence > message.conversation_sequence).limit(1))
    return newer is None


def is_look(inp: dict | None) -> bool:
    return str((inp or {}).get("origin") or "").startswith(f"{ORIGIN}:")


def schedule(*, user_id: uuid.UUID, conversation_id: uuid.UUID, turn: str, asked: uuid.UUID,
             zone: str | None = None, now: datetime | None = None) -> uuid.UUID | None:
    """A turn whose hands acted on the phone just ended: wake her to look again in ``LOOK_SECONDS``, if their
    message's window is still open then and they haven't written since. One look at a time per user: a pending one
    is replaced. Returns the wake's id, or None when no look is due."""
    from app.services import coordinator

    now = now or _now()
    with SessionLocal() as db:
        message = _asked(db, asked)
        if message is None or not _still_theirs(db, conversation_id, message, now, ahead=LOOK_SECONDS):
            return None
        db.execute(update(CoordinatorWork).where(
            CoordinatorWork.user_id == user_id, CoordinatorWork.kind == "wake",
            CoordinatorWork.status.in_(coordinator.ACTIVE),
            CoordinatorWork.input["origin"].astext.startswith(f"{ORIGIN}:"),
        ).values(status="cancelled", error_code="replaced_by_newer_look",
                 cancellation_generation=CoordinatorWork.cancellation_generation + 1))
        work = coordinator.schedule_wake(
            db, user_id=user_id, conversation_id=conversation_id, zone=zone, asked_by="mind",
            about="Look at their phone again: is what they asked finished?",
            due_at=now + timedelta(seconds=LOOK_SECONDS), origin=f"{ORIGIN}:{turn}", origin_message=asked)
        work.input = {**work.input, "look": {"asked": str(asked), "turn": turn, "ended_at": now.isoformat(),
                                             "text": " ".join((message.content or "").split())[:ASKED_CHARS]}}
        db.commit()
        log.info("phone_look: scheduled %s after turn %s", work.id, turn)
        return work.id


def asked_of(inp: dict | None) -> uuid.UUID | None:
    """The user's message a look wake follows (None: not a look)."""
    if not is_look(inp):
        return None
    try:
        return uuid.UUID(str(((inp or {}).get("look") or {}).get("asked")))
    except (TypeError, ValueError):
        return None


def valid(db, work: CoordinatorWork, *, now: datetime | None = None) -> bool:
    """Right before she's woken (and when deciding whether the wake gets the screen): still a look about the
    user's own message, in its window, with nothing newer from them, and nothing of someone else's in it."""
    inp = dict(work.input or {})
    asked = asked_of(inp)
    if asked is None or inp.get("third_party") or inp.get("phone_follow_up"):
        return False
    message = _asked(db, asked)
    return message is not None and _still_theirs(db, work.conversation_id, message, now or _now())


def render(info: dict) -> str:
    """The wake as she reads it. The screen isn't here: she reads it herself."""
    inp = info.get("input") or {}
    look = inp.get("look") or {}
    try:
        ago = max(0, int((_now() - datetime.fromisoformat(look["ended_at"])).total_seconds()))
        when = f"{ago} s ago"
    except (KeyError, TypeError, ValueError):
        when = "a moment ago"
    lines = [f"[wake:{str(info.get('work_id') or '')[:8]}] Your turn using their phone's screen ended {when}; you're "
             "looking again (code woke you for this, nobody asked).",
             f"What they asked: \"{look.get('text') or '(not kept)'}\"",
             "Your answer is the last thing you said above. You can use their screen in this turn, for what they "
             "asked only: read_phone_screen to see where it's got to. If what they asked isn't finished, carry on. "
             "If there's something worth telling them, tell them. If it's done or there's nothing to do, "
             "stay_silent. Their touch or their next words take the phone back at once."]
    return "\n".join(lines)
