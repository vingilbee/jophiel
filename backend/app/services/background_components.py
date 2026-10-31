"""Step 7 adapters over existing owners. Durable work is the notification/recovery source.

Memory maintenance retains its existing rule engine. Observation preparation has no production
transformer: its input contract and scheduler are implemented, and work settles as blocked.
"""
from __future__ import annotations

import uuid
import time
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import select, func, exists
from sqlalchemy.dialects.postgresql import insert

from app.ai.state.background import CognitionEvent, PreparationInput, PreparationResult, SinkReceipt
from app.core.config import get_settings
from app.db.models import (CoordinatorWork, MemoryManagerState, Observation, ObservationProcessing,
                           ChatMessage)
from app.db.session import SessionLocal

PREPARATION_CONSUMER = "preparation"
ELIGIBLE = ("reply_recorded", "reply_interrupted", "delivery_status", "skill_outcome", "notice")


def unavailable_preparation(evidence: PreparationInput) -> PreparationResult:
    return PreparationResult(unavailable="conversation/experience learning-preparation transformer not implemented")


def unavailable_cognition(event: CognitionEvent) -> SinkReceipt:
    return SinkReceipt("blocked", "background result cognition unavailable; relevance check required before replay")


def student_cognition(event: CognitionEvent) -> SinkReceipt:
    """A finished thought already went to Student 2 in its follow-up phase (``coordinator._conclude``); other
    events have no reader yet."""
    if event.kind == "private_thought":
        return SinkReceipt("consumed", "decided by Student 2 in the follow-up phase")
    return unavailable_cognition(event)


# Test/implementation seams, deliberately without model or teacher fallbacks.
prepare_observation = unavailable_preparation
cognition_sink = student_cognition


def enabled(kind: str) -> bool:
    s = get_settings()
    # Everything always runs except observation preparation, which has no production transformer yet.
    return bool({"mind": True,
                 "recall": True,
                 "memory": True,
                 "observation": getattr(s, "observation_preparation_enabled", False),
                 "cognition": True}.get(kind, False))


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
            get_settings(), "shared_state_work_deadline_seconds", 180)),
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
    if work.kind == "cognition":
        values = dict(work.input)
        values["source_refs"] = tuple(values["source_refs"])
        receipt = cognition_sink(CognitionEvent(**values))
        if receipt.status not in ("consumed", "blocked"):
            raise ValueError("invalid cognition receipt; no speech actions supported")
        return asdict(receipt), "blocked" if receipt.status == "blocked" else "completed", None
    raise ValueError(f"unknown background component: {work.kind}")


def accepted(db, work) -> None:
    """Same transaction as accepted output. Historical work survives new conversational input."""
    from app.services import coordinator
    if work.kind == "observation":
        receipt = db.get(ObservationProcessing, (int(work.input["event_id"]), PREPARATION_CONSUMER))
        if receipt:
            receipt.status = "applied" if work.status == "completed" else "failed"
            receipt.processed_at = coordinator._now()
            receipt.reason = work.error_code
            receipt.rule_version = (work.result or {}).get("processing_version", "unavailable")[:32]
    # Maintenance receipts are not cognitive events. Mind's speech decision keeps its step 4/5 boundary.
    if work.kind not in ("mind", "observation") or work.status != "completed":
        return
    event_id = uuid.uuid5(work.id, "cognition")
    event = CognitionEvent(str(event_id), str(work.user_id),
        str(work.conversation_id) if work.conversation_id else None,
        str(work.activity_id) if work.activity_id else None,
        "private_thought" if work.kind == "mind" else "learning_preparation",
        (f"work:{work.id}",), {**work.input_versions, **(work.result or {}).get("evidence_versions", {})},
        str(work.id), str(work.request_id),
        coordinator._now().isoformat())
    enqueue_cognition(db, event, event_id, f"cognition:{work.id}", work.request_id)


def enqueue_cognition(db, event, identity, key, request_id):
    """Keep every source handoff, but never overfill its runnable queue. Overflow is explicit/blocked."""
    from app.services import coordinator
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext("admission:cognition"))))
    pending = db.scalar(select(func.count()).select_from(CoordinatorWork).where(
        CoordinatorWork.kind == "cognition", CoordinatorWork.status.in_(coordinator.ACTIVE)))
    full = pending >= getattr(get_settings(), "coordinator_max_pending", 1000)
    db.execute(insert(CoordinatorWork).values(
        id=identity, request_id=request_id, user_id=uuid.UUID(event.user_id),
        conversation_id=uuid.UUID(event.conversation_id) if event.conversation_id else None,
        activity_id=uuid.UUID(event.activity_id) if event.activity_id else None, kind="cognition",
        input=asdict(event), input_versions=event.input_versions, dependency_ids=[],
        status="blocked" if full else "queued", idempotency_key=key,
        result=asdict(SinkReceipt("blocked", "cognition queue capacity; relevance review required")) if full else None,
        error_code="handoff_capacity" if full else None,
        deadline_at=coordinator._now() + timedelta(days=1),
    ).on_conflict_do_nothing(index_elements=["idempotency_key"]))


def projections(user_id, conversation_id):
    from app.ai.state.contracts import ComponentView, Projection, SourceRef
    from app.services import coordinator
    output = []
    try:
        with SessionLocal() as db:
            for kind in ("mind", "memory", "observation", "cognition"):
                if not enabled(kind):
                    output.append(ComponentView(Projection.off(kind, "component disabled"), kind,
                        str(user_id), str(conversation_id) if conversation_id else None, "disabled"))
                    continue
                scope = [CoordinatorWork.user_id == user_id, CoordinatorWork.kind.in_(
                    ("memory", "recall") if kind == "memory" else (kind,))]
                if kind == "mind":
                    scope.append(CoordinatorWork.conversation_id == conversation_id)
                pending = list(db.scalars(select(CoordinatorWork).where(*scope,
                    CoordinatorWork.status.in_(coordinator.ACTIVE)).order_by(CoordinatorWork.created_at).limit(10)))
                last = db.scalar(select(CoordinatorWork).where(*scope,
                    CoordinatorWork.status.in_(coordinator.TERMINAL)).order_by(CoordinatorWork.updated_at.desc()).limit(1))
                state = db.get(MemoryManagerState, user_id) if kind == "memory" else None
                status = ("running" if any(w.status == "running" for w in pending) else "queued") if pending else (
                    "blocked" if last and last.status == "blocked" else
                    "failed" if last and last.status in ("failed", "expired") else "idle")
                version = str(state.revision) if state else (last.updated_at.isoformat() if last else None)
                updated = state.updated_at if state else (last.updated_at if last else None)
                reason = ((last.result or {}).get("unavailable") or (last.result or {}).get("reason")) if last and status == "blocked" else None
                stale = any(w.status == "running" and w.lease_expires_at and
                            w.lease_expires_at <= coordinator._now() for w in pending)
                if stale:
                    reason = "lease expired; recovery pending"
                elif last and status == "failed":
                    reason = last.error_code
                output.append(ComponentView(Projection(kind, "stale" if stale else "available", version=version, updated_at=updated,
                    source_refs=(SourceRef("work", str(last.id)),) if last else (), reason=reason),
                    kind, str(user_id), str(conversation_id) if kind == "mind" and conversation_id else None,
                    status, tuple(f"work:{w.id}" for w in pending), f"work:{last.id}" if last else None,
                    last.input_versions if last else {}, state.next_check_at if state else None))
    except Exception:
        # A projection outage cannot derail the reply or misrepresent it as empty/idle.
        return tuple(ComponentView(Projection.missing(k, "component state read failed"), k, str(user_id),
                                   None, "blocked") for k in ("mind", "memory", "observation", "cognition"))
    return tuple(output) + owned_memory_projections(user_id, conversation_id)


def owned_memory_projections(user_id, conversation_id):
    """Expose existing ingestion workers without scheduling duplicate copies of their work."""
    from app.ai.state.contracts import ComponentView, Projection, SourceRef
    from app.db.models import MemoryJob
    result = []
    for name, model, source_attr in (
        ("memory_ingestion", MemoryJob, "assistant_message_id"),
    ):
        scope_id = str(conversation_id) if conversation_id else None
        try:
            with SessionLocal() as db:
                scope = (model.user_id == user_id, model.conversation_id == conversation_id)
                pending = list(db.scalars(select(model).where(*scope, model.status.in_(("pending", "processing")))
                                         .order_by(model.created_at).limit(10)))
                last = db.scalar(select(model).where(*scope, model.status.in_(("completed", "failed", "skipped")))
                                 .order_by(model.processed_at.desc().nulls_last()).limit(1))
                running = any(row.status == "processing" for row in pending)
                status = ("running" if running else "queued") if pending else (
                    "failed" if last and last.status == "failed" else "idle")
                ref = f"{name}:{last.id}" if last else None
                stamp = last.processed_at if last else None
                result.append(ComponentView(Projection(name, "available", str(stamp) if stamp else None, stamp,
                    (SourceRef(name, str(last.id)),) if last else ()), name, str(user_id), scope_id, status,
                    tuple(f"{name}:{row.id}" for row in pending), ref,
                    {"source_message": str(getattr(last, source_attr))} if last else {}))
        except Exception:
            result.append(ComponentView(Projection.missing(name, "owner read failed"), name, str(user_id), scope_id, "blocked"))
    return tuple(result)


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
    now = coordinator._now()
    identity = uuid.uuid5(user_id, f"memory-correction:{correction_id}")
    event = CognitionEvent(str(identity), str(user_id), None, None, "memory_changed",
        (f"correction:{correction_id}", f"event:{event_id}"), versions, str(correction_id),
        str(correction_id), now.isoformat())
    enqueue_cognition(db, event, identity, f"correction:{correction_id}", identity)
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
