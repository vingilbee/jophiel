"""Adapters over existing owners for the coordinator's maintenance work. Durable work is the
notification/recovery source.

Memory maintenance retains its existing rule engine. Observation preparation has no production
transformer: its input contract and scheduler are implemented, and work settles as blocked.
The contracts are here (pure dataclasses): ``AdapterPolicy``, ``PreparationInput``, ``PreparationResult``.
"""
from __future__ import annotations

import uuid
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import select, func, exists
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.models import (CoordinatorWork, MemoryManagerState, Observation, ObservationProcessing,
                           ChatMessage)
from app.db.session import SessionLocal



@dataclass(frozen=True)
class AdapterPolicy:
    component: str
    event_types: tuple[str, ...]
    input_versions: tuple[str, ...]
    scope: str
    concurrency_key: str
    timeout_seconds: int
    max_attempts: int
    result_type: str
    cadence_seconds: float | None = None


POLICIES = {
    "mind": AdapterPolicy("mind", ("thinking_requested",), ("evidence", "conversation"),
                          "conversation/activity", "mind:user", 180, 3, "private_thought"),
    "recall": AdapterPolicy("recall", ("recall_requested",), ("evidence",),
                            "conversation", "recall:user", 180, 3, "memory_result"),
    "wake": AdapterPolicy("wake", ("reconsideration_due",), (), "conversation", "wake:user", 180, 3, "wake"),
    "memory": AdapterPolicy("memory", ("source_report", "maintenance_due"), ("due", "receipt"),
                            "user", "memory:user", 180, 3, "maintenance_receipt", 1),
    "observation": AdapterPolicy("observation", ("exchange_settled", "experience", "delivery_changed"),
                                 ("source", "delivery"), "conversation/activity", "observation:user",
                                 180, 3, "learning_preparation"),
}


@dataclass(frozen=True)
class PreparationInput:
    event_id: str
    user_id: str
    conversation_id: str | None
    activity_id: str | None
    source_refs: tuple[str, ...]
    input_versions: dict[str, str]
    delivery: dict  # recorded/partial/cancelled and heard extent, never assumed delivered
    correlation_id: str
    created_at: str
    schema_version: int = 1


@dataclass(frozen=True)
class PreparationResult:
    output_refs: tuple[str, ...] = ()
    processing_version: str = "unavailable"
    coverage: tuple[str, ...] = ()
    unavailable: str | None = None
    schema_version: int = 1


class PreparationAdapter(Protocol):
    def __call__(self, evidence: PreparationInput) -> PreparationResult: ...


PREPARATION_CONSUMER = "preparation"
ELIGIBLE = ("reply_recorded", "reply_interrupted", "delivery_status", "skill_outcome", "notice")


def unavailable_preparation(evidence: PreparationInput) -> PreparationResult:
    return PreparationResult(unavailable="conversation/experience learning-preparation transformer not implemented")


# Test/implementation seams, deliberately without model or teacher fallbacks.
prepare_observation = unavailable_preparation


def enabled(kind: str) -> bool:
    s = get_settings()
    # Everything always runs except observation preparation, which has no production transformer yet.
    return bool({"mind": True,
                 "recall": True,
                 "wake": True,
                 "memory": True,
                 "observation": getattr(s, "observation_preparation_enabled", False)}.get(kind, False))


def admit(db, *, kind, key, user_id, payload, versions, conversation_id=None, activity_id=None,
          request_id=None):
    """Serialized bounded admission, stable identity across retries/restarts. Caller commits."""
    from app.services import coordinator
    if not db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtext(f"admission:{kind}")))):
        return None
    existing = db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.idempotency_key == key))
    if existing:
        return existing
    count = db.scalar(select(func.count()).select_from(CoordinatorWork).where(
        CoordinatorWork.kind == kind, CoordinatorWork.status.in_((*coordinator.ACTIVE, "blocked"))))
    if count >= getattr(get_settings(), "coordinator_max_pending", 1000):
        return None  # source/receipt remains unconsumed for the next pass
    work_id = uuid.uuid4()
    return db.execute(insert(CoordinatorWork).values(
        id=work_id, request_id=request_id or work_id, user_id=user_id, conversation_id=conversation_id,
        activity_id=activity_id, kind=kind, status="queued", input=payload, input_versions=versions,
        dependency_ids=[], idempotency_key=key,
        deadline_at=coordinator._now() + timedelta(seconds=getattr(
            get_settings(), "mind_work_deadline_seconds", 180)),
    ).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(CoordinatorWork.id)).scalar()


def schedule_due() -> None:
    """Bounded database-only eligibility. No high-water mark can lose a late source commit."""
    from app.services import coordinator
    now = coordinator._now()
    with SessionLocal() as db:
        if enabled("memory"):
            users = list(db.scalars(select(MemoryManagerState.user_id).where(
                MemoryManagerState.next_check_at <= now).order_by(MemoryManagerState.next_check_at).limit(50)))
            users += list(db.scalars(select(ObservationProcessing.user_id).where(
                ObservationProcessing.consumer == "memory", ObservationProcessing.status.in_(("pending", "retry")),
                ObservationProcessing.next_attempt_at <= now).distinct().limit(50)))
            for user in dict.fromkeys(users):
                if db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.user_id == user,
                        CoordinatorWork.kind == "memory", CoordinatorWork.status.in_(coordinator.ACTIVE)).limit(1)):
                    continue
                state = db.get(MemoryManagerState, user)
                receipt = db.scalar(select(ObservationProcessing).where(
                    ObservationProcessing.user_id == user, ObservationProcessing.consumer == "memory",
                    ObservationProcessing.status.in_(("pending", "retry")),
                    ObservationProcessing.next_attempt_at <= now).order_by(
                    ObservationProcessing.next_attempt_at, ObservationProcessing.observation_id).limit(1))
                due = str(state.next_check_at) if state else "none"
                version = f"{receipt.observation_id}:{receipt.next_attempt_at}" if receipt else "none"
                # A failed maintenance pass is retried by the work policy, not rescheduled every tick.
                key = str(uuid.uuid5(user, f"maintenance:{due}:{version}"))
                admit(db, kind="memory", key=f"memory:{key}", user_id=user, payload={"schema_version": 1},
                      versions={"due": due, "receipt": version})
        if enabled("observation"):
            rows = db.scalars(select(Observation).join(ObservationProcessing,
                ObservationProcessing.observation_id == Observation.id).where(
                ObservationProcessing.consumer == PREPARATION_CONSUMER,
                ObservationProcessing.status == "pending",
                ~exists().where(CoordinatorWork.idempotency_key ==
                                func.concat("observation:v1:", Observation.id)))
                .order_by(Observation.received_at, Observation.id).limit(50))
            for source in rows:
                message = db.get(ChatMessage, source.message_id) if source.message_id else None
                conversation_id = message.conversation_id if message else None
                refs = [f"observation:{source.id}"]
                delivery = {"status": source.payload.get("status", "not_applicable"),
                            "source_kind": source.kind, "source_payload": source.payload}
                if message:
                    refs.append(f"message:{message.id}")
                    refs += [f"message:{m}" for m in db.scalars(select(ChatMessage.id).where(
                        ChatMessage.conversation_id == conversation_id, ChatMessage.turn_id == message.turn_id,
                        ChatMessage.role == "user"))] if message.turn_id else []
                    delivery.update(recorded_status=message.status, heard_chars=message.heard_chars,
                                    channel=message.source_channel or "web")
                if source.experience_id:
                    refs.append(f"experience:{source.experience_id}")
                if source.delivery_id:
                    refs.append(f"delivery:{source.delivery_id}")
                versions = {"source": f"{source.id}@{source.schema_version}",
                            "delivery": f"{message.status}:{message.heard_chars}" if message else "none"}
                evidence = PreparationInput(str(source.id), str(source.user_id),
                    str(conversation_id) if conversation_id else None, str(source.session_id) if source.session_id else None,
                    tuple(refs), versions, delivery, str(source.id), source.received_at.isoformat())
                admit(db, kind="observation", key=f"observation:v1:{source.id}", user_id=source.user_id,
                      conversation_id=conversation_id, activity_id=source.session_id,
                      payload=asdict(evidence), versions=versions)
        db.commit()


def execute(work):
    if work.kind == "memory":
        from app.services import memory_manager, observation_memory, coordinator
        with SessionLocal() as db:
            counts = observation_memory.process(db, coordinator._now(),
                time.monotonic() + get_settings().observation_tick_budget_seconds, user_id=work.user_id)
            checked = memory_manager._tick_user(db, work.user_id, coordinator._now())
            db.commit()
        return {"counts": counts, "checked": checked, "schema_version": 1}, "completed", None
    if work.kind == "observation":
        values = dict(work.input)
        values["source_refs"] = tuple(values["source_refs"])
        with SessionLocal() as db:
            if not source_current(db, work):
                return {"unavailable": "delivery changed"}, "superseded", "delivery_changed"
        result = prepare_observation(PreparationInput(**values))
        if len(result.output_refs) > 32 or len(result.coverage) > 32:
            raise ValueError("preparation outputs must be bounded references")
        return asdict(result), "blocked" if result.unavailable else "completed", (
            "preparation_unavailable" if result.unavailable else None)
    raise ValueError(f"unknown background component: {work.kind}")


def accepted(db, work) -> None:
    """Same transaction as accepted output: an observation preparation settles its processing receipt."""
    from app.services import coordinator
    if work.kind == "observation":
        receipt = db.get(ObservationProcessing, (int(work.input["event_id"]), PREPARATION_CONSUMER))
        if receipt:
            receipt.status = "applied" if work.status == "completed" else "failed"
            receipt.processed_at = coordinator._now()
            receipt.reason = work.error_code
            receipt.rule_version = (work.result or {}).get("processing_version", "unavailable")[:32]


def source_current(db, work):
    source = db.get(Observation, int(work.input["event_id"]))
    if source is None or source.user_id != work.user_id:
        return False
    if source.message_id:
        message = db.get(ChatMessage, source.message_id)
        return message is not None and work.input_versions["delivery"] == f"{message.status}:{message.heard_chars}"
    return True


def memory_changed(db, user_id, correction_id, event_id, refs):
    """Publish a correction's version change in its owner's transaction, without interpreting it."""
    from app.services import coordinator, memory_manager, background_wake, recall
    db.flush()
    versions = {}
    for ref in refs[:32]:
        result = recall.read(db, user_id, ref)
        if result.records:
            versions[ref] = coordinator.evidence_version(result.records[0])
    current = memory_manager.state(db, user_id, lock=True)
    current.revision += 1
    current.last_event_id = event_id
    current.last_decision = {"decision": "invalidated", "source": f"correction:{correction_id}",
                             "changed_refs": list(versions)}
    background_wake.mark(db)


def prune_receipts():
    """Bound routine maintenance history. Required source evidence and blocked handoffs are retained."""
    from sqlalchemy import delete
    from app.services import coordinator
    with SessionLocal() as db:
        ids = list(db.scalars(select(CoordinatorWork.id).where(CoordinatorWork.kind == "memory",
            CoordinatorWork.status.in_(coordinator.TERMINAL), CoordinatorWork.updated_at <
            coordinator._now() - timedelta(days=getattr(get_settings(), "coordinator_receipt_retention_days", 30)))
            .order_by(CoordinatorWork.updated_at).limit(100)))
        if ids:
            db.execute(delete(CoordinatorWork).where(CoordinatorWork.id.in_(ids)))
        db.commit()
