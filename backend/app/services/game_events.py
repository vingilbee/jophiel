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
# News about her plan's progress. Once the plan has changed since (the user gave a new goal),
# it's about a plan that's gone: seen live, three "on the last step" turns queued for her bed
# plan finished after the user said "go to the cave" and set the bed plan back, twice.
PLAN_NEWS = ("plan_low", "goal_done", "paused:plan_complete", "paused:planning_timeout")
# How long to wait for a user turn already in flight before going ahead.
USER_TURN_WAIT_SECONDS = 30


def describe_reason(reason: str) -> str:
    kind, _, detail = reason.partition(":")
    if kind == "thought":
        return ("Something on your mind needs real thought (you noticed it yourself; what changed: "
                f"{detail.strip()}).")
    if kind == "goal_done":
        return "You finished every step of your goal."
    if kind == "plan_low":
        return "You're on the last step of your plan."
    said = f' Your hands said (their words): "{detail.strip()}"' if detail.strip() else ""
    if kind == "stuck":
        return "You got stuck and paused yourself." + said
    if kind == "needs_human":
        return "You paused because you need the user." + said
    if kind == "struggling":
        return ("You keep failing at your current step (you're still playing). Failed tries, counted: "
                f"{detail.strip()}")
    if kind == "paused":
        return f"Your game paused: {desktop_game.REASONS.get(detail, detail)}."
    if kind == "learning":
        return f"You finished trying to teach yourself a move: you {detail.strip()}."
    if kind == "rethink":
        return f"Thinking it over, you concluded the plan may not fit: {detail.strip()}"
    if kind == "warning":
        return f"A heads-up (read off the screen): {detail.strip()}"
    if kind in ("danger", "notable"):
        return f"{'Danger' if kind == 'danger' else 'Something notable'} (read off the screen): {detail.strip()}"
    return reason


def _user_turn_in_flight(user_id: uuid.UUID) -> bool:
    with SessionLocal() as db:
        return db.scalar(
            select(ChatJob.id).where(ChatJob.user_id == user_id, ChatJob.status.in_(("pending", "processing"))).limit(1)
        ) is not None


def current_reasons(reasons: list[str], news_revision: int | None, plan_revision: int) -> list[str]:
    """The reasons still worth a turn: plan news only if it's about the plan she's on now."""
    if news_revision is None or news_revision == plan_revision:
        return reasons
    return [reason for reason in reasons if reason not in PLAN_NEWS]


def _prepare(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str], news_revision: int | None):
    with SessionLocal() as db:
        session = db.get(DesktopSession, session_id)
        if session is None or session.owner_user_id != user_id:
            return None
        reasons = current_reasons(reasons, news_revision, session.plan_revision)
        if not reasons:
            log.info("game event turn for session %s skipped: the plan changed since", session_id)
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
            described=tuple(describe_reason(r) for r in reasons), plan_revision=session.plan_revision,
        )
        return user.name if user else None, history, game, event


async def run(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str],
              plan_revision: int | None = None) -> str | None:
    """Run one background turn. Returns her (unshown) reply, for logs and tests.
    ``plan_revision``: the plan when the newest of these events happened.
    A ``thought:`` reason is a hard thought of her mind: the reply becomes the thought."""
    topic = next((r.partition(":")[2].strip() for r in reasons if r.startswith("thought:")), None)
    try:
        reply = await _run(user_id, session_id, reasons, plan_revision)
    except BaseException:
        if topic is not None:
            await asyncio.to_thread(_thought_done, user_id, session_id, topic, None)
        raise
    if topic is not None:
        await asyncio.to_thread(_thought_done, user_id, session_id, topic, reply)
    return reply


def _thought_done(user_id: uuid.UUID, session_id: uuid.UUID, topic: str, reply: str | None) -> None:
    from app.services import mind

    mind.hard_thought_done(user_id, session_id, topic, reply)


async def _run(user_id: uuid.UUID, session_id: uuid.UUID, reasons: list[str],
               plan_revision: int | None) -> str | None:
    from app.ai import prompts
    from app.ai.chat import complete_chat

    started = time.monotonic()
    while await asyncio.to_thread(_user_turn_in_flight, user_id):
        if time.monotonic() - started > USER_TURN_WAIT_SECONDS:
            break
        await asyncio.sleep(1)
    prepared = await asyncio.to_thread(_prepare, user_id, session_id, reasons, plan_revision)
    if prepared is None:
        return None
    user_name, history, game, event = prepared
    context = AgentRuntimeContext(
        user_id=user_id, user_name=user_name, game=game.summary, game_activity=game.activity, game_vm=game.vm,
        game_planner=game.planner,
        game_event=event, model_override=get_settings().chat_model,
    )
    messages = [*history, {"role": "user", "content": prompts.GAME_EVENT_TRIGGER}]
    reply = await complete_chat(messages=messages, context=context)
    log.info("game event turn for session %s (%s): %s", session_id, ", ".join(reasons), reply[:200])
    return reply
