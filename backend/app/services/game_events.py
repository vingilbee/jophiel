"""One background turn for a desktop game event (plan §8.2, stage D2b).

The source half of the event mechanism for ``source="desktop"``: given a job,
build Columbina's context (her prompt, recent conversation, the game summary,
why she woke), run her once with the background game tools, and let her act
and maybe ``say``. Her written reply is discarded; only ``say`` reaches the
user. Waits for a user turn already in flight, so it never talks over a reply.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from sqlalchemy import select

from app.ai.context import AgentRuntimeContext, GameEvent
from app.core.config import get_settings
from app.db.models import ChatJob, DesktopSession, User
from app.db.session import SessionLocal
from app.services import chat as chat_service
from app.services import desktop_game

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 10
# How long to wait for a user turn already in flight before going ahead.
USER_TURN_WAIT_SECONDS = 30


def describe_reason(reason: str) -> str:
    kind, _, detail = reason.partition(":")
    if kind == "goal_done":
        return "You finished every step of your goal."
    if kind == "plan_low":
        return "You're on the last step of your plan."
    if kind == "stuck":
        return "You got stuck and paused yourself."
    if kind == "needs_human":
        return "You hit something outside the game and paused: you need the user."
    if kind == "paused":
        return f"Your game paused: {desktop_game.REASONS.get(detail, detail)}."
    if kind in ("danger", "notable"):
        return f"{'Danger' if kind == 'danger' else 'Something notable'} (read off the screen): {detail.strip()}"
    return reason


def _user_turn_in_flight(user_id: uuid.UUID) -> bool:
    with SessionLocal() as db:
        return db.scalar(
            select(ChatJob.id).where(ChatJob.user_id == user_id, ChatJob.status.in_(("pending", "processing"))).limit(1)
        ) is not None


def _prepare(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str]):
    with SessionLocal() as db:
        session = db.get(DesktopSession, session_id)
        if session is None or session.owner_user_id != user_id:
            return None
        # A session the user ended is over: nothing to act on or announce.
        if session.state == "ended" and session.reason_code == "user_end":
            return None
        user = db.get(User, user_id)
        conversations = chat_service.list_conversations(db, user_id=user_id)
        history = (chat_service.get_history(db, conversation=conversations[0])[-HISTORY_MESSAGES:]
                   if conversations else [])
        game = desktop_game.describe(db, user_id)
        if game is None:
            return None
        event = GameEvent(
            reasons=tuple(reasons), commentary_mode=session.commentary_mode,
            keep_playing=session.autonomy == "continue",
            described=tuple(describe_reason(r) for r in reasons),
        )
        return user.name if user else None, history, game, event


async def run(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str]) -> str | None:
    """Run one background turn. Returns her (unshown) reply, for logs and tests."""
    from app.ai import prompts
    from app.ai.chat import complete_chat

    started = time.monotonic()
    while await asyncio.to_thread(_user_turn_in_flight, user_id):
        if time.monotonic() - started > USER_TURN_WAIT_SECONDS:
            break
        await asyncio.sleep(1)
    prepared = await asyncio.to_thread(_prepare, user_id, session_id, reasons)
    if prepared is None:
        return None
    user_name, history, game, event = prepared
    context = AgentRuntimeContext(
        user_id=user_id, user_name=user_name, game=game.summary, game_activity=game.activity,
        game_event=event, model_override=get_settings().chat_model,
    )
    messages = [*history, {"role": "user", "content": prompts.GAME_EVENT_TRIGGER}]
    reply = await complete_chat(messages=messages, context=context)
    log.info("game event turn for session %s (%s): %s", session_id, ", ".join(reasons), reply[:200])
    return reply
