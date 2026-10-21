"""Source adapters for observation (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md §3, §6): turn authoritative
records into reports, and recover any that were missed.

Conversation (pulled from the mind loop; the chat path is untouched):
- messages, by each conversation's ``conversation_sequence``. That position is allocated under the
  conversation's row lock and commits in order with no gaps (``CHAT_MESSAGE_SEQUENCE_DDL``), so a
  per-conversation position in ``observation_sources`` can't skip a late commit;
- interruptions (``chat_messages.heard_chars``, which only moves earlier) and settled WhatsApp
  deliveries (``message_deliveries``), by an anti-join over committed rows within ``lookback``. A
  delivery has no settle time, so its window is when it was created (it settles within minutes).

Minecraft (``minecraft_pack``):
- skill outcomes and notices are captured in the same transaction that writes the game experience
  (``game_learning.record_experience``), with the context of the session's goal at that moment. If
  capture fails, the experience is written alone and the session's source state remembers where to
  recover from;
- availability: the pack environment going offline or coming back.

Capture starts when the adapter first runs (``observation_adapters.started_at``) or when an observation
reset restarted it: nothing older is imported.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, and_, cast, exists, func, literal, select
from sqlalchemy.orm import Session

from app.ai.observe import registry
from app.db.models import (
    ChatMessage,
    Conversation,
    DesktopEnvironment,
    DesktopSession,
    GameExperience,
    MemoryContext,
    MessageDelivery,
    Observation,
    ObservationSource,
    WorkingItem,
)
from app.services.observation import Report, adapter, as_utc, lookback, note_source, record, source_row

log = logging.getLogger(__name__)

CONVERSATION = "conversation"
PACK = "minecraft_pack"
CAPTURE_MARK = "*"  # a user's "capture (re)started at" row in observation_sources
GAME_KINDS = ("skill_outcome", "notice")
CONVERSATIONS_PER_TICK = 50


def _naive(moment: datetime) -> datetime:
    """For comparing with message timestamps, which are stored without a zone (UTC)."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _since(db: Session, user_id: uuid.UUID, started: datetime) -> datetime:
    mark = db.get(ObservationSource, (user_id, CONVERSATION, CAPTURE_MARK))
    restarted = (mark.position or {}).get("since") if mark is not None else None
    if restarted:
        return max(as_utc(started), datetime.fromisoformat(restarted))
    return as_utc(started)


# --------------------------------------------------------------------------- #
# The pull (every tick)
# --------------------------------------------------------------------------- #


def pull(db: Session, now: datetime, deadline: float) -> int:
    """New messages and availability changes. Commits per conversation. Returns how many were recorded."""
    captured = _pull_conversations(db, deadline)
    captured += _check_availability(db, now)
    return captured


def _message_report(message: ChatMessage, user_id: uuid.UUID) -> Report | None:
    common = {"message_id": str(message.id), "sequence": message.conversation_sequence,
              "turn_id": str(message.turn_id) if message.turn_id else None,
              "channel": message.source_channel or "web", "chars": len(message.content or "")}
    if message.role == "user":
        kind = "user_message"
        payload = {**common, "attachments": len(message.image_ids or []) + len(message.asset_ids or [])}
    elif message.role == "assistant":
        kind = "reply_recorded"
        payload = {**common, "status": message.status, "proactive": message.turn_id is None}
    else:
        return None
    return Report(user_id=user_id, source=CONVERSATION, source_instance=str(message.conversation_id),
                  source_event_key=f"message:{message.conversation_sequence}", kind=kind, payload=payload,
                  occurred_at=message.created_at, message_id=message.id)


def _pull_conversations(db: Session, deadline: float) -> int:
    from app.core.config import get_settings

    started = adapter(db, CONVERSATION).started_at
    db.commit()
    source = ObservationSource
    position = func.coalesce(source.position["sequence"].as_integer(), -1)
    behind = db.execute(
        select(Conversation.id, Conversation.user_id)
        .outerjoin(source, and_(source.user_id == Conversation.user_id, source.source == CONVERSATION,
                                source.source_instance == cast(Conversation.id, String)))
        .where(Conversation.last_message_sequence > position)
        .order_by(Conversation.updated_at).limit(CONVERSATIONS_PER_TICK)).all()
    batch = get_settings().observation_batch
    captured = 0
    for conversation_id, user_id in behind:
        if time.monotonic() > deadline:
            break
        try:
            captured += _pull_one(db, conversation_id, user_id, started, batch)
            db.commit()
        except Exception:  # noqa: BLE001 - one conversation never stops the others
            log.exception("observation: pulling conversation %s failed", conversation_id)
            db.rollback()
    return captured


def _pull_one(db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID, started: datetime, batch: int) -> int:
    state = source_row(db, user_id, CONVERSATION, str(conversation_id), lock=True, skip_locked=True)
    if state is None:
        return 0  # another worker has it
    at = (state.position or {}).get("sequence")
    if at is None:  # first sight: only what's new since capture (re)started
        at = db.scalar(select(func.coalesce(func.max(ChatMessage.conversation_sequence), 0)).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at < _naive(_since(db, user_id, started))))
    messages = list(db.scalars(select(ChatMessage).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.conversation_sequence > at,
    ).order_by(ChatMessage.conversation_sequence).limit(batch)))
    captured = 0
    for message in messages:
        report = _message_report(message, user_id)
        if report is not None:
            try:
                if record(db, report) is not None:
                    captured += 1
            except registry.ObservationInvalid as exc:
                # The position stays before it: capture isn't known to have worked.
                state.last_error = str(exc)[:500]
                break
        at = message.conversation_sequence
    state.position = {**(state.position or {}), "sequence": at}
    return captured


def _check_availability(db: Session, now: datetime) -> int:
    """The pack environment's online state, as a source's availability: a change is one observation."""
    captured = 0
    # populate_existing: a long-lived session would otherwise read its cached status (expire_on_commit=False).
    for env in db.scalars(select(DesktopEnvironment).where(
            DesktopEnvironment.hosting_kind == "game_pack", DesktopEnvironment.revoked_at.is_(None),
    ).execution_options(populate_existing=True)).all():
        instance = f"environment:{env.id}"
        try:
            state = source_row(db, env.owner_user_id, PACK, instance, lock=True, skip_locked=True)
            if state is None:
                continue
            current = "online" if env.status == "online" else "offline"
            if state.availability != current:
                changes = int((state.position or {}).get("transitions", 0)) + 1
                live = db.scalar(select(DesktopSession.id).where(
                    DesktopSession.environment_id == env.id).order_by(DesktopSession.started_at.desc()).limit(1))
                if record(db, Report(
                        user_id=env.owner_user_id, source=PACK, source_instance=instance,
                        source_event_key=f"availability:{changes}", kind="source_availability",
                        payload={"availability": current, "previous": state.availability}, occurred_at=now,
                        session_id=live)) is not None:
                    captured += 1
                state.availability, state.last_change_at = current, now
                state.position = {**(state.position or {}), "transitions": changes}
            state.next_check_at = now
            db.commit()
        except Exception:  # noqa: BLE001
            log.exception("observation: availability check for %s failed", env.id)
            db.rollback()
    return captured


# --------------------------------------------------------------------------- #
# Recovery (every OBSERVATION_SWEEP_SECONDS)
# --------------------------------------------------------------------------- #


def sweep(db: Session, now: datetime) -> int:
    """Interruptions, deliveries and missed game experiences not yet observed. Commits."""
    recovered = 0
    for step in (_sweep_heard, _sweep_deliveries, _sweep_missed_experiences):
        try:
            recovered += step(db, now)
            db.commit()
        except Exception:  # noqa: BLE001
            log.exception("observation: %s failed", step.__name__)
            db.rollback()
    return recovered


def _observed(user_id, instance, key) -> exists:
    return exists().where(Observation.user_id == user_id, Observation.source == CONVERSATION,
                          Observation.source_instance == instance, Observation.source_event_key == key)


def _sweep_heard(db: Session, now: datetime) -> int:
    started = adapter(db, CONVERSATION).started_at
    instance = cast(Conversation.id, String)
    key = literal("heard:") + cast(ChatMessage.id, String) + literal(":") + cast(ChatMessage.heard_chars, String)
    rows = db.execute(
        select(ChatMessage, Conversation.user_id)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(ChatMessage.heard_chars.is_not(None), ChatMessage.role == "assistant",
               ChatMessage.created_at >= _naive(lookback(now)),
               ~_observed(Conversation.user_id, instance, key))
        .order_by(ChatMessage.created_at).limit(500)).all()
    recovered = 0
    for message, user_id in rows:
        if as_utc(message.created_at) < _since(db, user_id, started):
            continue
        if record(db, Report(
                user_id=user_id, source=CONVERSATION, source_instance=str(message.conversation_id),
                source_event_key=f"heard:{message.id}:{message.heard_chars}", kind="reply_interrupted",
                payload={"message_id": str(message.id), "turn_id": str(message.turn_id) if message.turn_id else None,
                         "heard_chars": message.heard_chars, "total_chars": len(message.content or "")},
                occurred_at=message.created_at,  # cut off while it was being spoken
                message_id=message.id)) is not None:
            recovered += 1
    return recovered


def _sweep_deliveries(db: Session, now: datetime) -> int:
    started = adapter(db, CONVERSATION).started_at
    instance = cast(Conversation.id, String)
    key = literal("delivery:") + cast(MessageDelivery.id, String) + literal(":") + MessageDelivery.status
    rows = db.execute(
        select(MessageDelivery, ChatMessage.conversation_id, Conversation.user_id)
        .join(ChatMessage, ChatMessage.id == MessageDelivery.assistant_message_id)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(MessageDelivery.status.in_(("delivered", "read", "failed")),
               MessageDelivery.created_at >= _naive(lookback(now)),
               ~_observed(Conversation.user_id, instance, key))
        .order_by(MessageDelivery.created_at).limit(500)).all()
    recovered = 0
    for delivery, conversation_id, user_id in rows:
        if as_utc(delivery.created_at) < _since(db, user_id, started):
            continue
        if record(db, Report(
                user_id=user_id, source=CONVERSATION, source_instance=str(conversation_id),
                source_event_key=f"delivery:{delivery.id}:{delivery.status}", kind="delivery_status",
                payload={"delivery_id": str(delivery.id), "message_id": str(delivery.assistant_message_id),
                         "channel": delivery.channel, "part_index": delivery.part_index, "status": delivery.status},
                occurred_at=None, delivery_id=delivery.id,  # when it settled isn't recorded
                message_id=delivery.assistant_message_id)) is not None:
            recovered += 1
    return recovered


def _sweep_missed_experiences(db: Session, now: datetime) -> int:
    recovered = 0
    for state in db.scalars(select(ObservationSource).where(
            ObservationSource.source == PACK, ObservationSource.position.has_key("missed_from"),
    ).with_for_update(skip_locked=True)).all():
        session = db.get(DesktopSession, uuid.UUID(state.source_instance))
        if session is not None:
            for experience in db.scalars(select(GameExperience).where(
                    GameExperience.session_id == session.id, GameExperience.id >= int(state.position["missed_from"]),
                    GameExperience.kind.in_(GAME_KINDS), GameExperience.occurred_at >= lookback(now),
                    ~exists().where(Observation.experience_id == GameExperience.id),
            ).order_by(GameExperience.id)):
                # Its context at the time isn't known now: the memory consumer binds it from the record.
                if record(db, experience_report(session, experience, context_id=None)) is not None:
                    recovered += 1
        state.position = {k: v for k, v in state.position.items() if k != "missed_from"}
        state.last_decision = {"at": now.isoformat(), "decision": "recovered", "count": recovered}
    return recovered


# --------------------------------------------------------------------------- #
# Game experiences (inline, from game_learning.record_experience)
# --------------------------------------------------------------------------- #


def _bounded(params, limit: int = 1024) -> dict:
    import json

    if not isinstance(params, dict):
        return {}
    return params if len(json.dumps(params, default=str)) <= limit else {"omitted": "too large"}


def experience_report(session: DesktopSession, experience: GameExperience,
                      context_id: uuid.UUID | None) -> Report | None:
    """The report for a game experience, or None for a kind or game observation doesn't mirror."""
    if experience.kind not in GAME_KINDS or (session.profile_id, experience.kind, 1) not in registry.REGISTRY:
        return None
    p = experience.payload or {}
    if experience.kind == "skill_outcome":
        seconds = p.get("seconds")
        payload = {
            "experience_id": experience.id, "skill": str(p.get("skill") or "unknown")[:80],
            "status": str(p.get("status") or "unknown")[:16],
            "reason": registry.quote(str(p["reason"]), 300) if p.get("reason") else None,
            "seconds": float(seconds) if isinstance(seconds, (int, float)) else None,
            "step_id": str(p["step_id"])[:80] if p.get("step_id") else None, "params": _bounded(p.get("params")),
        }
    else:
        notice = str(p.get("kind") or "unknown")[:48]
        dropped = (p.get("data") or {}).get("dropped") if notice == "died" else None
        payload = {
            "experience_id": experience.id, "notice": notice,
            "urgency": str(p["urgency"])[:16] if p.get("urgency") else None,
            "text": registry.quote(p.get("text"), 300),
            "dropped": {str(k)[:60]: int(v) for k, v in list(dropped.items())[:20]
                        if isinstance(v, (int, float))} if isinstance(dropped, dict) else {},
        }
    return Report(user_id=session.owner_user_id, source=session.profile_id, source_instance=str(session.id),
                  source_event_key=f"experience:{experience.id}", kind=experience.kind, payload=payload,
                  occurred_at=experience.occurred_at, context_id=context_id, experience_id=experience.id,
                  session_id=session.id)


def game_context_now(db: Session, session: DesktopSession) -> uuid.UUID | None:
    """The context a game outcome belongs to as it happens: the goal the session plays for, else the
    source's one open context. Never created here, and never the focus."""
    if session.goal_item_id is not None:
        goal = db.get(WorkingItem, session.goal_item_id)
        if goal is not None:
            return goal.context_id
    open_ones = list(db.scalars(select(MemoryContext.id).where(
        MemoryContext.user_id == session.owner_user_id, MemoryContext.source == session.profile_id,
        MemoryContext.status != "closed").limit(2)))
    return open_ones[0] if len(open_ones) == 1 else None


def experience_recorded(db: Session, session: DesktopSession, experience: GameExperience) -> None:
    """Capture a just-flushed experience in its own transaction (the caller commits both together)."""
    report = experience_report(session, experience, game_context_now(db, session))
    if report is not None:
        record(db, report)


def mark_missed(db: Session, session: DesktopSession, experience_id: int) -> None:
    """Capture failed for this experience: the sweep recovers it (and anything after it) later."""
    state = source_row(db, session.owner_user_id, session.profile_id, str(session.id), lock=True)
    known = (state.position or {}).get("missed_from")
    state.position = {**(state.position or {}),
                      "missed_from": experience_id if known is None else min(int(known), experience_id)}
    state.last_error = f"capture failed at experience {experience_id}; it will be recovered"


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #


def restart(db: Session, user_ids: list[uuid.UUID] | None, now: datetime) -> None:
    """Capture for these users starts again from ``now`` (after an observation reset)."""
    if user_ids is None:
        user_ids = list(db.scalars(select(Conversation.user_id).distinct()))
    for user_id in user_ids:
        note_source(db, user_id, CONVERSATION, CAPTURE_MARK, position={"since": now.isoformat()},
                    decision={"decision": "restarted"})
