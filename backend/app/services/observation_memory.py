"""The memory consumer of observations (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md §7, §8, §10).

For each due receipt (``observation_processing``, consumer ``memory``), in one short transaction with a
row lock (``SKIP LOCKED``) and a savepoint per observation:

1. **Bind** a context if the observation has none. Conversation reports take the context in focus when
   her reply was written (her reply is where a context switch gets recognized), or, with no reply after
   OBSERVATION_BIND_GRACE_SECONDS, when the message was sent. Other sources take the one context of that
   source alive when it happened. Never the focus at processing time; never General just to move on.
2. **Decide** with the code rule from the registry: promote a short memory event, or skip with a reason.
3. **Apply**: the event (deduplicated per observation, or per repeat of the same failure) and its
   evidence link commit with the receipt, so a crash leaves both undone and a retry never duplicates.

A report that can't be bound waits (``pending``, shown in inspection) and is skipped after
OBSERVATION_CONTEXT_WAIT_HOURS; one that keeps failing retries with backoff, then ``failed``. Neither
blocks the others. Nothing here calls a model, speaks or acts.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.observe import registry
from app.core.config import get_settings
from app.db.models import (
    ChatMessage,
    MemoryContext,
    MemoryEvent,
    MemoryEvidence,
    Observation,
    ObservationBinding,
    ObservationProcessing,
)
from app.services import memory_manager
from app.services.observation import as_utc

log = logging.getLogger(__name__)
CONSUMER = "memory"
DEFER = timedelta(seconds=30)
MAX_BACKOFF = timedelta(hours=1)
PENDING_SHOWN = 3


def process(db: Session, now: datetime, deadline: float, *, user_id: uuid.UUID | None = None) -> dict:
    """Process what's due, within the deadline. Commits. Returns counts per outcome."""
    settings = get_settings()
    rows = db.execute(
        select(ObservationProcessing, Observation)
        .join(Observation, Observation.id == ObservationProcessing.observation_id)
        .where(ObservationProcessing.consumer == CONSUMER,
               True if user_id is None else ObservationProcessing.user_id == user_id,
               ObservationProcessing.status.in_(("pending", "retry")),
               ObservationProcessing.next_attempt_at <= now)
        .order_by(ObservationProcessing.next_attempt_at, ObservationProcessing.observation_id)
        .limit(settings.observation_batch)
        .with_for_update(of=ObservationProcessing, skip_locked=True)).all()
    counts: Counter = Counter()
    for receipt, observation in rows:
        if time.monotonic() > deadline:
            break
        try:
            with db.begin_nested():
                outcome, reason = _apply(db, observation, now)
        except (registry.ObservationInvalid, ValidationError) as exc:
            outcome, reason = "invalid", f"doesn't fit its kind: {str(exc)[:150]}"
        except Exception as exc:  # noqa: BLE001 - one report never stops the others
            log.exception("observation: memory processing of %s failed", observation.id)
            outcome, reason = "error", type(exc).__name__
        _settle(receipt, observation, outcome, reason, now)
        counts[outcome] += 1
    db.commit()
    return dict(counts)


def _settle(receipt: ObservationProcessing, observation: Observation, outcome: str, reason: str,
            now: datetime) -> None:
    settings = get_settings()
    receipt.reason = reason[:200]
    if outcome == "defer":
        waited = now - observation.received_at
        if waited > timedelta(hours=settings.observation_context_wait_hours):
            outcome, receipt.reason = "skipped", f"no context found after {round(waited.total_seconds() / 3600)} h"
        else:
            receipt.next_attempt_at = now + DEFER
            return
    if outcome in ("applied", "skipped"):
        receipt.status, receipt.processed_at = outcome, now
        receipt.rule_version = registry.RULE_VERSION
        receipt.attempts += 1
        return
    receipt.attempts += 1
    if outcome == "invalid" or receipt.attempts >= settings.observation_max_attempts:
        receipt.status, receipt.processed_at = "failed", now
    else:
        receipt.status = "retry"
        receipt.next_attempt_at = now + min(timedelta(seconds=10 * 2 ** receipt.attempts), MAX_BACKOFF)


def _apply(db: Session, observation: Observation, now: datetime) -> tuple[str, str]:
    decision = registry.decide(observation.source, observation.kind, observation.schema_version,
                               observation.payload, observation.source_instance)
    entry = registry.spec(observation.source, observation.kind, observation.schema_version)
    binding = db.get(ObservationBinding, observation.id)
    context_id = observation.context_id or (binding.context_id if binding is not None else None)
    if context_id is None:
        context_id = bind(db, observation, now)
    # A conversation report is always bound (so a message records which context it was about).
    needs = observation.source == "conversation" or (isinstance(decision, registry.Promote) and entry.needs_context)
    if context_id is None and needs:
        return "defer", "waiting to know which context it belongs to"
    if isinstance(decision, registry.Skip):
        return "skipped", decision.reason
    key = (f"observation-coalesce:{decision.coalesce_key}" if decision.coalesce_key
           else f"observation:{observation.id}:{registry.RULE_VERSION}")
    source = memory_manager.Source("observation", observation.id)
    # What happened in a game session belongs to the effort its goal is: on that outcome's timeline.
    goal = memory_manager.open_goal_of_session(db, observation.session_id)
    event = memory_manager.record_event(
        db, observation.user_id, kind=decision.kind,
        text=decision.text + registry.age_note(observation.occurred_at, observation.received_at, now),
        epistemic=decision.epistemic, context_id=context_id,
        item_id=goal.id if goal is not None and goal.context_id == context_id else None,
        occurred_at=observation.occurred_at or observation.received_at, dedupe_key=key[:500])
    if event is not None:
        memory_manager.link(db, observation.user_id, role="origin", source=source, event_id=event.id)
        from app.services import system_signals
        system_signals.emit(observation.user_id, "observation", "memory", "event", event.id)
        return "applied", f"memory event {event.id}"
    existing = db.scalar(select(MemoryEvent.id).where(MemoryEvent.user_id == observation.user_id,
                                                      MemoryEvent.dedupe_key == key[:500]))
    if existing is None:
        return "skipped", "already in memory"
    already = db.scalar(select(MemoryEvidence.id).where(MemoryEvidence.event_id == existing,
                                                        MemoryEvidence.observation_id == observation.id))
    if decision.coalesce_key is None or already is not None:
        return "applied", f"already in memory as event {existing}"  # this very report, retried
    # The same thing again: one memory, one more piece of evidence.
    memory_manager.link(db, observation.user_id, role="support", source=source, event_id=existing)
    return "applied", f"repeat of memory event {existing}"


# --------------------------------------------------------------------------- #
# Binding a context after the fact (spec §7)
# --------------------------------------------------------------------------- #


def _conversation_moment(db: Session, observation: Observation, now: datetime) -> tuple[datetime | None, str]:
    """When the context of a conversation report is decided: her reply, else (after the grace) the message."""
    payload = observation.payload
    if observation.kind == "user_message":
        reply = None
        if payload.get("turn_id"):
            reply = db.scalar(select(ChatMessage).where(
                ChatMessage.turn_id == uuid.UUID(payload["turn_id"]), ChatMessage.role == "assistant",
                ChatMessage.conversation_id == uuid.UUID(observation.source_instance)))
        if reply is not None:
            return as_utc(reply.created_at), "focus_at_reply"
        sent = observation.occurred_at or observation.received_at
        if now - sent >= timedelta(seconds=get_settings().observation_bind_grace_seconds):
            return sent, "focus_at_message"
        return None, ""
    message = db.get(ChatMessage, observation.message_id) if observation.message_id else None
    if message is not None:
        return as_utc(message.created_at), "focus_at_reply"
    return observation.occurred_at or observation.received_at, "focus_at_message"


def bind(db: Session, observation: Observation, now: datetime) -> uuid.UUID | None:
    """Bind the observation's context if it can be decided now; returns it, or None to wait."""
    if observation.source == "conversation":
        at, method = _conversation_moment(db, observation, now)
        if at is None:
            return None
        context_id = memory_manager.focus_at(db, observation.user_id, at).id
    elif observation.source == "desktop":
        # Her desktop work happens inside a conversation: it belongs to what was in focus when she did it.
        at = observation.occurred_at or observation.received_at
        context_id, method = memory_manager.focus_at(db, observation.user_id, at).id, "focus_when_it_happened"
    else:
        at = observation.occurred_at or observation.received_at
        alive = list(db.scalars(select(MemoryContext.id).where(
            MemoryContext.user_id == observation.user_id, MemoryContext.source == observation.source,
            MemoryContext.created_at <= at,
            (MemoryContext.closed_at.is_(None)) | (MemoryContext.closed_at > at)).limit(2)))
        if len(alive) != 1:
            return None
        context_id, method = alive[0], "source_context_at_time"
    db.add(ObservationBinding(observation_id=observation.id, user_id=observation.user_id,
                              context_id=context_id, method=method))
    db.flush()
    return context_id


# --------------------------------------------------------------------------- #
# Live freshness: what was just reported, before it's promoted (spec §10)
# --------------------------------------------------------------------------- #


def pending_lines(db: Session, user_id: uuid.UUID, context_id: uuid.UUID, now: datetime) -> list[tuple[datetime, str]]:
    """Notable reports for this context that memory hasn't taken in yet (processing lags, or the mind loop
    is off), worded as their memory event would be, newest last. A repeat of something already in memory
    isn't shown again; conversation history isn't repeated (its kinds are never promoted)."""
    rows = db.execute(
        select(Observation, ObservationBinding.context_id)
        .join(ObservationProcessing, ObservationProcessing.observation_id == Observation.id)
        .outerjoin(ObservationBinding, ObservationBinding.observation_id == Observation.id)
        .where(Observation.user_id == user_id, ObservationProcessing.consumer == CONSUMER,
               ObservationProcessing.status.in_(("pending", "retry")),
               Observation.salience.in_(("notable", "critical")))
        .order_by(Observation.received_at.desc(), Observation.id.desc()).limit(20)).all()
    shown: list[tuple[datetime, str]] = []
    for observation, bound in rows:
        if (observation.context_id or bound) != context_id:
            continue
        try:
            decision = registry.decide(observation.source, observation.kind, observation.schema_version,
                                       observation.payload, observation.source_instance)
        except (registry.ObservationInvalid, ValidationError):
            continue
        if not isinstance(decision, registry.Promote):
            continue
        if decision.coalesce_key and db.scalar(select(MemoryEvent.id).where(
                MemoryEvent.user_id == user_id,
                MemoryEvent.dedupe_key == f"observation-coalesce:{decision.coalesce_key}"[:500])):
            continue
        shown.append((observation.occurred_at or observation.received_at, decision.text))
        if len(shown) >= PENDING_SHOWN:
            break
    return shown[::-1]
