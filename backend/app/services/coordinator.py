"""The coordinator's background work and durable wake events for her mind (docs/MIND_ARCHITECTURE.md). Code, not
a model: it runs what the mind asked for and wakes the mind with what came of it; it never decides what she says.
Tables: ``coordinator_work``, ``coordinator_outputs``.

Kinds of work:

- ``mind`` / ``recall``: background thinking the mind started with its ``think_it_over`` tool (``schedule``), with
  a memory lookup it depends on when asked for. Also the carrier of a thought another part of her mind produced
  (``autonomous_speech.submit``: its result is already there).
- ``wake``: a reconsideration the mind scheduled for later (``schedule_wake``): nothing to execute, it just comes
  due (``next_attempt_at``). It is never superseded by a new user message: an intention outlives the turn.
- ``memory`` / ``observation``: maintenance (``background_components``).

Lifecycle:

1. The ``coordinator`` heartbeat (``run_once``) dispatches leased work to bounded executors. Every result is
   checked against the lease and the cancellation generation before it's accepted.
2. ``_conclude``: finished ``mind`` / ``wake`` work becomes one wake of the mind: a chat job
   ``uuid5(request, "follow_up")`` in the work's own conversation, behind any reply in progress (chat jobs run one
   at a time per conversation). Only execution facts are checked here: a result for a conversation that moved on
   stays private (``superseded``); evidence that changed makes it stale. Whether anything is said is the mind's
   choice in that evaluation, never the coordinator's.
3. The chat worker runs the wake (``workers/chat.py``), stores what she said (or that she stayed silent) on the
   output row, and announces it through the durable outbox (``announce_follow_up``).

A new user message supersedes unfinished thinking in its conversation (``supersede_older``): its result can never
become the answer to the new message. Deadlines end work as ``expired``; failures as ``failed``. Both still wake
the mind, so a waiting user isn't left with silence nobody chose.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update, exists, cast, String
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert

from dataclasses import dataclass

from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation, CoordinatorOutput, CoordinatorWork
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ACTIVE = ("queued", "waiting_for_memory", "running")
TERMINAL = ("completed", "failed", "cancelled", "expired", "superseded", "blocked")
LEASE_SECONDS = 120
# Work that wakes the mind when it ends (thinking she started, a thought of hers, a reconsideration come due).
WAKES = ("mind", "wake")
# A scheduled reconsideration is kept this long past its time (the worker was down): then it wakes her as late.
WAKE_GRACE = timedelta(days=30)
WAKE_MAX_AHEAD = timedelta(days=60)


@dataclass(frozen=True)
class PendingWork:
    """What the mind may know about work in progress. ``status`` is real: queued is not thinking."""

    work_id: str
    kind: str  # recall / mind
    status: str  # queued / waiting_for_memory / running / ...
    subject: str  # what it's about, in her own words (never a conclusion)
    unresolved: str  # what waits on it


def follow_up_turn(request_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(request_id, "follow_up")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Before the mouth
# --------------------------------------------------------------------------- #


def supersede_older(user_id: uuid.UUID, conversation_id: uuid.UUID | None, trigger_message_id: uuid.UUID | None) -> int:
    """A new user message: unfinished work for earlier messages in this conversation can't answer it. Its
    generation goes up (a running worker sees that before accepting), its status becomes ``superseded``."""
    if conversation_id is None:
        return 0
    try:
        with SessionLocal() as db:
            rows = list(db.scalars(select(CoordinatorWork).where(
                CoordinatorWork.user_id == user_id, CoordinatorWork.conversation_id == conversation_id,
                CoordinatorWork.status.in_(ACTIVE), CoordinatorWork.kind.in_(("mind", "recall")),
                CoordinatorWork.trigger_message_id.is_distinct_from(trigger_message_id)).with_for_update()))
            for row in rows:
                row.status, row.error_code = "superseded", "new_user_message"
                row.cancellation_generation += 1
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("coordinator: superseding older work failed")
        return 0
    for row in rows:
        _log({"op": "superseded", "work": str(row.id), "kind": row.kind, "request": str(row.request_id)})
    return len(rows)


def schedule(*, user_id: uuid.UUID, conversation_id: uuid.UUID, request_id: uuid.UUID,
             trigger_message_id: uuid.UUID | None, with_recall: bool, work_input: dict[str, Any],
             input_versions: dict[str, str]) -> tuple[PendingWork, ...] | None:
    """Commit thinking the mind started (a mind item, after a recall item when ``with_recall``). Returns what she
    may know about it, or None when it couldn't be committed (then no work exists, and nobody may say it does)."""
    deadline = _now() + timedelta(seconds=get_settings().mind_work_deadline_seconds)
    subject = (work_input.get("think_about") or work_input.get("message") or "")[:200]
    try:
        with SessionLocal() as db:
            # Admission is atomic for the request's recall/mind pair and bounded across schedulers.
            db.execute(select(func.pg_advisory_xact_lock(func.hashtext("coordinator:request-admission"))))
            already = db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.request_id == request_id).limit(1))
            if not already:
                for component in (("recall", "mind") if with_recall else ("mind",)):
                    queued = db.scalar(select(func.count()).select_from(CoordinatorWork).where(
                        CoordinatorWork.kind == component, CoordinatorWork.status.in_(ACTIVE)))
                    if queued >= getattr(get_settings(), "coordinator_max_pending", 1000):
                        return None
            ids: dict[str, uuid.UUID] = {}
            for kind in (("recall", "mind") if with_recall else ("mind",)):
                deps = [str(ids["recall"])] if kind == "mind" and with_recall else []
                key = f"{request_id}:{kind}"
                db.execute(insert(CoordinatorWork).values(
                    id=uuid.uuid4(), request_id=request_id, user_id=user_id, conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id, kind=kind, dependency_ids=deps,
                    status="queued", input={"schema_version": 1, "source_refs": [f"message:{trigger_message_id}"]
                        if trigger_message_id else [], **work_input}, input_versions=input_versions, deadline_at=deadline,
                    idempotency_key=key).on_conflict_do_nothing(index_elements=["idempotency_key"]))
                ids[kind] = db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.idempotency_key == key))
            db.commit()
            rows = list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.id.in_(list(ids.values())))))
    except Exception:  # noqa: BLE001
        logger.exception("coordinator: scheduling work failed")
        return None
    _wake()
    for row in rows:
        _log({"op": "scheduled", "work": str(row.id), "kind": row.kind, "request": str(request_id),
              "deps": row.dependency_ids})
    order = {"recall": 0, "mind": 1}
    return tuple(PendingWork(str(r.id), r.kind, r.status, subject,
                             "what memory holds about it" if r.kind == "recall" else "the considered answer")
                 for r in sorted(rows, key=lambda r: order[r.kind]))


def record_immediate(*, user_id: uuid.UUID, conversation_id: uuid.UUID, request_id: uuid.UUID,
                     spoke: bool, pending: tuple[PendingWork, ...]) -> None:
    """What the evaluation that started the thinking did meanwhile (her own choice: ``respond`` or ``silent``).
    Recorded once per request, before its delivery outcome."""
    _record(request_id=request_id, user_id=user_id, conversation_id=conversation_id, phase="immediate",
            decision="respond" if spoke else "silent", origin="mind", missing=None,
            work_id=uuid.UUID(pending[-1].work_id) if pending else None)


def immediate_outcome(request_id: uuid.UUID, *, status: str, assistant_message_id: uuid.UUID | None) -> None:
    """What the evaluation that started the thinking actually delivered: delivered / partial / cancelled /
    failed / not_sent (silent)."""
    try:
        with SessionLocal() as db:
            row = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.request_id == request_id,
                                                            CoordinatorOutput.phase == "immediate"))
            if row is not None:
                row.status, row.assistant_message_id = status, assistant_message_id
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("coordinator: recording the immediate outcome failed")


# --------------------------------------------------------------------------- #
# The worker
# --------------------------------------------------------------------------- #


def _claim(db, token: str, kinds: tuple[str, ...] = ("recall", "mind", "wake"), *, exclude_ids=()) -> tuple[CoordinatorWork | None, str | None]:
    """Claim one ready job. Per-user/component advisory locking serializes competing schedulers."""
    now = _now()
    busy = aliased(CoordinatorWork)
    dependency = aliased(CoordinatorWork)
    candidates = list(db.scalars(select(CoordinatorWork).where(
        ~exists().where(busy.id != CoordinatorWork.id, busy.user_id == CoordinatorWork.user_id,
                        busy.kind == CoordinatorWork.kind, busy.status == "running", busy.lease_expires_at > now),
        ~exists().where(func.jsonb_exists(CoordinatorWork.dependency_ids, cast(dependency.id, String)),
                        dependency.status.in_(ACTIVE)),
        CoordinatorWork.id.not_in(exclude_ids),
        CoordinatorWork.kind.in_(kinds), CoordinatorWork.next_attempt_at <= now,
        (CoordinatorWork.status.in_(("queued", "waiting_for_memory")))
        | ((CoordinatorWork.status == "running") & (CoordinatorWork.lease_expires_at <= now)))
        .order_by(CoordinatorWork.created_at, CoordinatorWork.id).with_for_update(skip_locked=True).limit(100)))
    for work in candidates:
        if not db.scalar(select(func.pg_try_advisory_xact_lock(func.hashtext(f"work:{work.kind}:{work.user_id}")))):
            continue
        conflict = db.scalar(select(CoordinatorWork.id).where(
            CoordinatorWork.id != work.id, CoordinatorWork.user_id == work.user_id,
            CoordinatorWork.kind == work.kind, CoordinatorWork.status == "running",
            CoordinatorWork.lease_expires_at > now).limit(1))
        if conflict:
            continue
        if work.deadline_at <= now:
            work.status, work.error_code, work.lease_owner = "expired", "deadline", None
            return work, "expired"
        if work.attempt >= getattr(get_settings(), "coordinator_max_attempts", 3):
            work.status, work.error_code, work.lease_owner = "failed", "attempts_exhausted", None
            return work, "failed"
        deps = list(db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.id.in_([uuid.UUID(d) for d in work.dependency_ids])))) if work.dependency_ids else []
        if any(d.status in ACTIVE for d in deps):
            work.status = "waiting_for_memory"
            continue
        work.status, work.attempt = "running", work.attempt + 1
        work.lease_owner, work.lease_expires_at = token, min(work.deadline_at, now + timedelta(seconds=LEASE_SECONDS))
        return work, None
    return None, None


def expire_due() -> int:
    """Deadlines progress even when every executor is busy; late results cannot change them."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(CoordinatorWork.id).where(CoordinatorWork.status.in_(ACTIVE),
            CoordinatorWork.deadline_at <= _now()).with_for_update(skip_locked=True).limit(100)))
        if ids:
            db.execute(update(CoordinatorWork).where(CoordinatorWork.id.in_(ids)).values(
                status="expired", error_code="deadline", lease_owner=None, lease_expires_at=None))
        db.commit()
    return len(ids)


def renew(work_id, token, generation) -> bool:
    now = _now()
    with SessionLocal() as db:
        result = db.execute(update(CoordinatorWork).where(
            CoordinatorWork.id == work_id, CoordinatorWork.status == "running",
            CoordinatorWork.lease_owner == token, CoordinatorWork.cancellation_generation == generation,
            CoordinatorWork.lease_expires_at > now, CoordinatorWork.deadline_at > now).values(
                lease_expires_at=func.least(CoordinatorWork.deadline_at, now + timedelta(seconds=LEASE_SECONDS))))
        db.commit()
        return result.rowcount == 1


def execute_claim(work, token):
    """Slow code runs only on an executor, outside the claim transaction."""
    from app.services import background_components
    try:
        if work.input.get("autonomous") or work.kind == "wake":
            # Nothing to compute: a thought already made, or a reconsideration whose time came.
            result, status, error = work.result, "completed", None
        elif work.kind == "recall":
            result, status, error = _run_recall(work)
        elif work.kind == "mind":
            result, status, error = _run_mind(work)
        else:
            result, status, error = background_components.execute(work)
    except Exception as exc:  # recorded, bounded retry; a crash is recovered by lease expiry
        logger.exception("coordinator: %s work %s failed", work.kind, work.id)
        result, status, error = None, "failed", type(exc).__name__[:64]
    accepted = _accept(work.id, token, work.cancellation_generation, status, result, error)
    _log({"op": "finished", "work": str(work.id), "kind": work.kind, "status": status,
          "accepted": accepted, "error": error})
    _wake()


def execute_once() -> bool:
    """Synchronous single-job harness for scripts/tests. Production uses run_once's bounded scheduler."""
    repaired = recover_follow_up()
    with SessionLocal() as db:
        orphan = db.scalar(select(CoordinatorWork.id).where(
            CoordinatorWork.kind.in_(WAKES), CoordinatorWork.status.in_(("completed", "failed", "expired")),
            ~select(CoordinatorOutput.id).where(CoordinatorOutput.work_id == CoordinatorWork.id,
                CoordinatorOutput.phase == "follow_up").exists()).order_by(CoordinatorWork.created_at).limit(1))
    if orphan:
        _conclude(orphan)
        return True
    token = uuid.uuid4().hex
    with SessionLocal() as db:
        work, ended = _claim(db, token)
        db.commit()
        if not work:
            return repaired
        db.expunge(work)
    if not ended:
        execute_claim(work, token)
    if work.kind in WAKES:
        _conclude(work.id)
    return True


def run_once() -> bool:
    from app.services.coordinator_runtime import runtime
    return runtime().tick()


def evidence_version(record):
    return f"{record.fingerprint}:{record.applicability}:{int(record.corrected)}"


def evidence_current(db, work, result=None, *, lock=False) -> bool:
    """Compare only referenced evidence; unrelated memory edits never invalidate a thought."""
    from app.services import recall
    versions = (result or work.result or {}).get("evidence_versions", work.input.get("evidence_versions", {}))
    if not versions and (result or work.result or {}).get("evidence_refs"):
        return False  # pre-migration evidence has no version proof
    if lock and versions:
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"memory:{work.user_id}"))))
    for ref, fingerprint in versions.items():
        read = recall.read(db, work.user_id, ref)
        if read.status != "matched" or not read.records or evidence_version(read.records[0]) != fingerprint:
            return False
    return True


def _accept(work_id: uuid.UUID, token: str, generation: int, status: str, result: dict | None,
            error: str | None) -> bool:
    """Only the current, unexpired owner can populate the authoritative result."""
    from app.services import background_components
    with SessionLocal() as db:
        row = db.get(CoordinatorWork, work_id, with_for_update=True)
        now = _now()
        if (row is None or row.status != "running" or row.lease_owner != token
                or row.cancellation_generation != generation or row.lease_expires_at <= now
                or row.deadline_at <= now):
            return False
        if row.kind == "observation" and not background_components.source_current(db, row):
            status, error = "superseded", "delivery_changed"
        if row.kind == "mind" and not evidence_current(db, row, result, lock=True):
            status, error = "superseded", "evidence_changed"
        if status == "failed" and row.attempt < getattr(get_settings(), "coordinator_max_attempts", 3):
            row.status, row.error_code = "queued", error
            row.next_attempt_at = now + timedelta(seconds=min(30, 2 ** row.attempt))
        else:
            row.result, row.status, row.error_code = result, status, error
            background_components.accepted(db, row)
        row.lease_owner, row.lease_expires_at = None, None
        db.commit()
        return True


def _trigger(db, work: CoordinatorWork) -> ChatMessage | None:
    return db.get(ChatMessage, work.trigger_message_id) if work.trigger_message_id else None


def _run_recall(work: CoordinatorWork) -> tuple[dict, str, str | None]:
    """Memory's own reply search (the previous answer's references plus a search for the message). Its status
    stays memory's: matched / partial / no_match are completed results; failed is a failed item."""
    from app.services import recall_references

    turn = recall_references.for_reply(work.user_id, work.conversation_id, work.trigger_message_id,
                                       work.input.get("message", ""), work.input.get("zone"))
    result = {"schema_version": 1, "status": turn.result.status, "refs": [str(r) for r in turn.result.refs()],
              "rendered": turn.rendered[:6000],
              "evidence_versions": {str(r.ref): evidence_version(r) for r in turn.result.records}}
    return result, ("failed" if turn.result.status == "failed" else "completed"), (
        "recall_failed" if turn.result.status == "failed" else None)


def _think(inp):
    """hard.py's thinker (the mind's own model settings). Replaced in tests."""
    from app.ai.mind.hard import HardThinker

    s = get_settings()
    return HardThinker(s.mind_hard_model, api_key=s.openai_api_key or "",
                       reasoning_effort=s.mind_hard_reasoning_effort).think(inp)


def _run_mind(work: CoordinatorWork) -> tuple[dict, str, str | None]:
    from app.ai.mind.hard import HardInput, Problem

    with SessionLocal() as db:
        deps = list(db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.id.in_([uuid.UUID(d) for d in work.dependency_ids])))) if work.dependency_ids else []
        trigger = _trigger(db, work)
        query = select(ChatMessage).where(ChatMessage.conversation_id == work.conversation_id)
        if trigger is not None and trigger.conversation_sequence is not None:
            query = query.where(ChatMessage.conversation_sequence <= trigger.conversation_sequence)
        recent = list(reversed(list(db.scalars(query.order_by(ChatMessage.conversation_sequence.desc()).limit(16)))))
        conversation = tuple({"role": m.role, "content": m.content} for m in recent if m.content)
    recall = next((d for d in deps if d.kind == "recall"), None)
    if recall is None:
        memories, evidence_status, refs = work.input.get("cached_memory"), None, []
    elif recall.status == "completed":
        memories = (recall.result or {}).get("rendered")
        evidence_status, refs = (recall.result or {}).get("status"), (recall.result or {}).get("refs", [])
    else:
        # Failed, expired or superseded evidence is said as such, never as "nothing recorded".
        memories = f"Looking this up in memory didn't work ({recall.status}): don't treat it as found or absent."
        evidence_status, refs = recall.status, []
    message = work.input.get("message", "")
    about = (work.input.get("think_about") or "").strip()  # what she decided to think over, in her words
    problem = Problem(
        trigger="handoff",
        text=((f'They said: "{message}"\n' if message else "")
              + (f"What you decided to think over: {about}\n" if about else "")
              + "Work out what you really think about it."),
        topic=(about or message)[:60], query=about or message)
    from app.services.personality import current

    thought = _think(HardInput(problem=problem, user=work.input.get("user_name") or "the user",
                               conversation=conversation, memories=memories,
                               character=(current().text,)))  # her personality: every model call gets it
    result = {"schema_version": 1, "kind": thought.kind, "conclusion": thought.conclusion, "question": thought.question,
              "thinking": thought.thinking, "evidence_status": evidence_status, "evidence_refs": refs,
              "latency_ms": thought.latency_ms, "error": thought.error,
              "evidence_versions": (recall.result or {}).get("evidence_versions", {}) if recall else
                  work.input.get("evidence_versions", {})}
    if thought.error:
        return result, "failed", "mind_error"
    return result, "completed", None


# --------------------------------------------------------------------------- #
# After the mind
# --------------------------------------------------------------------------- #


def _conclude(work_id: uuid.UUID) -> None:
    """Wake the mind with a finished item, once. Execution facts only: the mind decides what to do with it."""
    with SessionLocal() as db:
        work = db.get(CoordinatorWork, work_id)
        if work is None or work.status not in TERMINAL:
            return
        if work.status == "completed" and not evidence_current(db, work):
            work.status, work.error_code = "superseded", "evidence_changed"
            db.commit()
        trigger = _trigger(db, work)
        newer = 0
        if trigger is not None and trigger.conversation_sequence is not None:
            newer = db.scalar(select(func.count()).select_from(ChatMessage).where(
                ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user",
                ChatMessage.conversation_sequence > trigger.conversation_sequence)) or 0
        db.expunge(work)
    common = dict(request_id=work.request_id, user_id=work.user_id, conversation_id=work.conversation_id,
                  phase="follow_up", work_id=work.id)
    if work.input.get("autonomous"):
        from app.services import autonomous_speech
        with SessionLocal() as db:
            if not autonomous_speech.valid(db, work):
                why = (autonomous_speech.why_not(db, work)
                       if work.input.get("source") in autonomous_speech.CONVERSATIONAL else None)
                _log({"op": "autonomous_not_said", "work": str(work.id), "why": why or "stale"})
                _record(**common, decision="rejected", origin="coordinator", missing=None, status="not_sent")
                autonomous_speech.dropped(work, why or "stale")
                return
    if work.status in ("superseded", "cancelled", "blocked") or (newer and work.kind != "wake"):
        # The conversation moved on: the thought stays private; it can't answer a newer message.
        _record(**common, decision="superseded", origin="coordinator", missing=None, status="not_sent")
        return
    _dispatch_follow_up(work)


def _dispatch_follow_up(work: CoordinatorWork) -> None:
    """One wake of the mind per finished item: a chat job in the same conversation and channel as the request,
    never "the latest conversation". The output row and the job commit together; a second dispatch finds the row
    and stops."""
    from app.services import chat as chat_service

    turn = follow_up_turn(work.request_id)
    with SessionLocal() as db:
        current = db.get(CoordinatorWork, work.id, with_for_update=True)
        if current is None or current.status not in ("completed", "failed", "expired") or not evidence_current(db, current, lock=True):
            return
        trigger = _trigger(db, current)
        if work.kind != "wake" and trigger and db.scalar(select(ChatMessage.id).where(
                ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user",
                ChatMessage.conversation_sequence > trigger.conversation_sequence).limit(1)):
            return
        output = db.execute(insert(CoordinatorOutput).values(
            id=uuid.uuid4(), request_id=work.request_id, user_id=work.user_id, conversation_id=work.conversation_id,
            work_id=work.id, phase="follow_up", decision="wake", decision_origin="mind", status="dispatched",
        ).on_conflict_do_nothing(constraint="uq_coordinator_outputs_phase").returning(CoordinatorOutput.id)).scalar()
        if output is None:
            db.rollback()
            return  # already dispatched (a retry or a second worker)
        conversation = db.get(Conversation, work.conversation_id)
        request = db.scalar(select(ChatJob).where(ChatJob.turn_id == work.request_id))
        base = request.payload if request is not None else {}
        payload = {"phase": "follow_up", "content": "", "work_id": str(work.id), "request_id": str(work.request_id),
                   "channel": base.get("channel", work.input.get("channel", "web")),
                   "zone": base.get("zone") or work.input.get("zone"), "awareness": base.get("awareness", ""),
                   "awareness_at": request.created_at.isoformat() if request is not None else None}
        identity = base.get("channel_identity_id") or work.input.get("channel_identity_id")
        if identity:
            payload["channel_identity_id"] = identity
        job = chat_service.enqueue_job(db, user_id=work.user_id, conversation=conversation, turn_id=turn,
                                       payload=payload, commit=False)
        db.execute(CoordinatorOutput.__table__.update().where(CoordinatorOutput.id == output)
                   .values(chat_job_id=job.id))
        db.commit()
        chat_service.wake_chat_worker(job.id)
    # Who woke her, on the System page: another part's hand-off; her own wakes (thinking she started, a thought of
    # hers, a reconsideration she scheduled) are her mind's own and show as what she then does.
    src = {"proposal": "learning", "conflict": "memory"}.get(wake_reason(work))
    if src is not None:
        from app.services import system_signals
        system_signals.emit(work.user_id, src, "mind", wake_reason(work), work.id)
    _log({"op": "wake_dispatched", "work": str(work.id), "request": str(work.request_id),
          "reason": wake_reason(work)})


def wake_reason(work: CoordinatorWork) -> str:
    """Why this item wakes her: ``due`` (a reconsideration she scheduled), a producer's own reason (``thought``,
    ``proposal``, ``conflict``, ``finding``), or ``work_result`` (thinking she started)."""
    if work.kind == "wake":
        return "due"
    if work.input.get("autonomous"):
        return work.input.get("wake") or "thought"
    return "work_result"


# --------------------------------------------------------------------------- #
# Reconsiderations the mind scheduled
# --------------------------------------------------------------------------- #


def schedule_wake(db, *, user_id: uuid.UUID, conversation_id: uuid.UUID, about: str, due_at: datetime,
                  item_id: uuid.UUID | None = None, origin: str | None = None, zone: str | None = None,
                  channel: str = "web", channel_identity_id: uuid.UUID | str | None = None,
                  asked_by: str = "mind", origin_message: uuid.UUID | None = None,
                  their_urgency: str | None = None, moves: CoordinatorWork | None = None,
                  reason: str | None = None, third_party: bool = False) -> CoordinatorWork:
    """Keep the mind's choice to come back to something at ``due_at`` (UTC), in the conversation it chose it in.
    The caller commits.

    One intention keeps one identity across postponements: ``chain`` (the first wake's id). ``moves``: a wake of
    the same chain being revised (one still pending, or the one that just woke her): the new row inherits its
    purpose, who asked, the original request and its item, records ``reason`` and the revision, and any live wake
    of the chain is cancelled, so the chain never has two live schedules. Scheduling again for the same working item
    moves that item's wake the same way. ``asked_by``: ``user`` (a check-in they asked for: a commitment) or ``mind``
    (her own idea: optional). ``due_at`` is the deadline she set; ``scheduled_at`` is when she set it.
    ``channel_identity_id``: who to deliver to off the web (a WhatsApp identity); a wake has no request of its own
    to read it from, so it is kept here and carried through revisions. ``third_party``: chosen on a turn someone
    else's words woke (a phone notification): the wake gets no device action either, through every revision."""
    now = _now()
    if due_at.tzinfo is None:
        raise ValueError("a due time needs its time zone")
    due_at = max(due_at.astimezone(timezone.utc), now)
    if due_at - now > WAKE_MAX_AHEAD:
        raise ValueError("too far ahead: at most 60 days")
    words = " ".join((about or "").split())[:500]
    if not words:
        raise ValueError("say what to come back to")
    why = " ".join((reason or "").split())[:300] or None
    if moves is None and item_id is not None:
        moves = db.scalar(select(CoordinatorWork).where(
            CoordinatorWork.user_id == user_id, CoordinatorWork.kind == "wake", CoordinatorWork.status.in_(ACTIVE),
            CoordinatorWork.input["item"].astext == str(item_id)).order_by(CoordinatorWork.created_at.desc()).limit(1))
        why = why or ("rescheduled for the same item" if moves is not None else None)
    work_id = uuid.uuid4()
    inherited: dict[str, Any] = {}
    revisions: list[dict] = []
    chain = str(work_id)
    moved = 0
    if moves is not None:
        if moves.kind != "wake" or moves.user_id != user_id:
            raise ValueError("that isn't one of your scheduled wakes")
        if why is None:
            raise ValueError("say why you're moving it")
        old = dict(moves.input or {})
        chain = old.get("chain") or str(moves.id)
        inherited = {k: old.get(k) for k in ("purpose", "asked_by", "origin_message", "origin", "their_urgency",
                                             "first_scheduled_at", "item", "channel_identity_id", "third_party")}
        revisions = list(old.get("revisions") or [])
        revisions.append({"at": now.isoformat(), "from_wake": str(moves.id), "from_due": old.get("due_at"),
                          "to_due": due_at.isoformat(), "reason": why,
                          "came_due": moves.status not in ACTIVE})
        for row in db.scalars(select(CoordinatorWork).where(
                CoordinatorWork.user_id == user_id, CoordinatorWork.kind == "wake", CoordinatorWork.status.in_(ACTIVE),
                (CoordinatorWork.input["chain"].astext == chain) | (CoordinatorWork.id == moves.id)).with_for_update()):
            row.status, row.error_code = "cancelled", "rescheduled"
            row.cancellation_generation += 1
            moved += 1
        moves.result = {**(moves.result or {}), "moved_to": str(work_id), "moved_reason": why,
                        "moved_at": now.isoformat()}
    item = str(item_id) if item_id else inherited.get("item")
    row = CoordinatorWork(
        id=work_id, request_id=work_id, user_id=user_id, conversation_id=conversation_id, kind="wake",
        status="queued", dependency_ids=[], input_versions={}, next_attempt_at=due_at,
        deadline_at=due_at + WAKE_GRACE, idempotency_key=f"wake:{work_id}",
        result={"kind": "due", "about": words},
        input={"schema_version": 2, "about": words, "item": item, "chain": chain,
               "purpose": inherited.get("purpose") or words,
               "asked_by": inherited.get("asked_by") or ("user" if asked_by == "user" else "mind"),
               "origin_message": inherited.get("origin_message") or (str(origin_message) if origin_message else None),
               "origin": inherited.get("origin") or origin,
               "their_urgency": (" ".join((their_urgency or "").split())[:300] or None)
               or inherited.get("their_urgency"),
               "first_scheduled_at": inherited.get("first_scheduled_at") or now.isoformat(),
               "due_at": due_at.isoformat(), "scheduled_at": now.isoformat(), "scheduled_by": origin,
               "zone": zone, "channel": channel,
               "channel_identity_id": (str(channel_identity_id) if channel_identity_id else None)
               or inherited.get("channel_identity_id"),
               "moved": moved, "revisions": revisions[-12:],
               **({"third_party": True} if third_party or inherited.get("third_party") else {})})
    db.add(row)
    db.flush()
    _log({"op": "wake_scheduled", "work": str(work_id), "due": due_at.isoformat(), "item": item, "chain": chain,
          "revision": len(revisions)})
    return row


def find_wake(db, user_id: uuid.UUID, key: str, *, also: uuid.UUID | str | None = None) -> CoordinatorWork | None:
    """A wake by the id shown to her (or its prefix): one still pending, or ``also`` (the one that woke her)."""
    key = (key or "").removeprefix("wake:").strip().lower()
    if len(key) < 6:
        return None
    rows = [r for r in db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.kind == "wake", CoordinatorWork.status.in_(ACTIVE))
        .with_for_update()) if str(r.id).startswith(key)]
    if not rows and also is not None and str(also).startswith(key):
        current = db.get(CoordinatorWork, uuid.UUID(str(also)), with_for_update=True)
        rows = [current] if current is not None and current.user_id == user_id and current.kind == "wake" else []
    return rows[0] if len(rows) == 1 else None


def cancel_wake(db, user_id: uuid.UUID, key: str, *, reason: str,
                also: uuid.UUID | str | None = None) -> CoordinatorWork | None:
    """Drop a reconsideration (by the id shown to her, or its prefix), with her reason; ``also``: the wake that woke
    her, which can be let go the same way (it stays completed; the decision is kept on it). The caller commits."""
    row = find_wake(db, user_id, key, also=also)
    if row is None:
        return None
    why = " ".join((reason or "").split())[:300] or "dropped"
    if row.status in ACTIVE:
        row.status, row.error_code = "cancelled", why[:64]
        row.cancellation_generation += 1
    row.result = {**(row.result or {}), "dropped": {"reason": why, "at": _now().isoformat(), "by": "mind"}}
    return row


def close_item_wakes(db, user_id: uuid.UUID, item_id: uuid.UUID) -> int:
    """The item a reconsideration was about closed: it no longer needs coming back to. The caller commits."""
    rows = list(db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.kind == "wake", CoordinatorWork.status.in_(ACTIVE),
        CoordinatorWork.input["item"].astext == str(item_id)).with_for_update()))
    for row in rows:
        row.status, row.error_code = "cancelled", "item_closed"
        row.cancellation_generation += 1
    return len(rows)


def scheduled_wakes(db, user_id: uuid.UUID, *, limit: int = 8) -> list[CoordinatorWork]:
    return list(db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.kind == "wake", CoordinatorWork.status.in_(ACTIVE))
        .order_by(CoordinatorWork.next_attempt_at).limit(limit)))


def thinking_for(db, user_id: uuid.UUID, conversation_id: uuid.UUID | None) -> list[CoordinatorWork]:
    """Thinking she started that hasn't finished, in this conversation."""
    if conversation_id is None:
        return []
    return list(db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.conversation_id == conversation_id,
        CoordinatorWork.kind.in_(("recall", "mind")), CoordinatorWork.status.in_(ACTIVE),
        CoordinatorWork.input["autonomous"].astext.is_distinct_from("true"))
        .order_by(CoordinatorWork.created_at)))


def cancel_thinking(db, user_id: uuid.UUID, key: str, *, reason: str) -> int:
    """Stop thinking she started (by the id shown to her; its memory lookup goes with it). The caller commits."""
    key = (key or "").removeprefix("work:").strip().lower()
    if len(key) < 6:
        return 0
    rows = [r for r in db.scalars(select(CoordinatorWork).where(
        CoordinatorWork.user_id == user_id, CoordinatorWork.kind.in_(("recall", "mind")),
        CoordinatorWork.status.in_(ACTIVE)).with_for_update()) if str(r.id).startswith(key)]
    requests = {r.request_id for r in rows}
    if len(requests) != 1:
        return 0
    stopped = 0
    for row in db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.request_id.in_(requests), CoordinatorWork.kind.in_(("recall", "mind")),
            CoordinatorWork.status.in_(ACTIVE)).with_for_update()):
        row.status, row.error_code = "cancelled", (reason or "stopped")[:64]
        row.cancellation_generation += 1
        stopped += 1
    return stopped


def follow_up_still_valid(work_id: uuid.UUID) -> tuple[bool, dict | None]:
    """Checked again by the chat worker right before the mind is woken: no newer user message (for thinking a
    message started), not superseded. Returns what the wake is about, for ``render_wake``."""
    with SessionLocal() as db:
        work = db.get(CoordinatorWork, work_id)
        if work is None or work.status not in ("completed", "failed", "expired") or not evidence_current(db, work):
            return False, None
        if work.input.get("autonomous"):
            from app.services import autonomous_speech
            if not autonomous_speech.valid(db, work):
                return False, None
        trigger = _trigger(db, work)
        if work.kind != "wake" and trigger is not None and trigger.conversation_sequence is not None:
            newer = db.scalar(select(func.count()).select_from(ChatMessage).where(
                ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user",
                ChatMessage.conversation_sequence > trigger.conversation_sequence)) or 0
            if newer:
                return False, None
        info = {"result": work.result, "status": work.status, "error": work.error_code, "kind": work.kind,
                "reason": wake_reason(work), "input": dict(work.input), "work_id": str(work.id),
                "activity_id": str(work.activity_id) if work.activity_id else None,
                "message": work.input.get("message", ""), "autonomous": work.input.get("autonomous", False),
                "late": work.status == "expired" and work.kind == "wake"}
        if work.input.get("autonomous"):
            from app.services import autonomous_speech
            info["speech_facts"] = autonomous_speech.facts(db, work)
        return True, info


_EVIDENCE_WORDS = {"matched": "memory had something on it", "partial": "memory was only partly readable",
                   "no_match": "memory had nothing on it (not proof it didn't happen)",
                   "failed": "looking it up in memory failed", "expired": "the memory lookup ran out of time",
                   "superseded": "the memory lookup was dropped", "unavailable": "memory couldn't be searched"}


def render_wake(info: dict) -> str:
    """Why she was woken, as she reads it: thinking she started that finished, a thought of her own, a proposal
    from another part of her, or something she chose to come back to. Her private reasoning (``thinking``) is
    never shown."""
    r = info.get("result") or {}
    reason, inp = info.get("reason"), info.get("input") or {}
    if reason == "due":
        # Who asked for it, its deadline against now, what was promised and what changed are in "Now" (read from
        # records when she runs): this only names it.
        whose = {"user": "a check-in they asked you for", "mind": "something you chose to come back to"}.get(
            inp.get("asked_by"), "something you scheduled")
        lines = [f"[wake:{str(info.get('work_id') or '')[:8]}] came due: {whose}.",
                 f"What you wrote when you set it: {inp.get('about') or r.get('about')}"]
        if info.get("late"):
            lines.append("It came due a while ago: you couldn't be woken then. Check whether it still matters.")
        if inp.get("item"):
            lines.append(f"It is about the item [{inp['item'][:8]}] in your working memory.")
        return "\n".join(lines)
    if reason == "proposal":
        lines = ["Another part of you has a proposal for you to decide on (nobody asked the user):", r.get("conclusion") or ""]
    elif reason == "conflict":
        lines = ["Memory noticed something that doesn't fit (nothing is settled, memory changed nothing):",
                 r.get("conclusion") or ""]
    elif info.get("autonomous"):
        lines = ["A thought of your own, from your background thinking (not a user request):"]
        if r.get("question"):
            lines.append(f"A question you'd consider asking them: {r['question']}")
        elif r.get("conclusion"):
            lines.append(f"What you thought: {r['conclusion']}")
    else:
        about = inp.get("think_about") or info.get("message") or ""
        lines = [f"The thinking you started has ended. It was about: {about}"]
        if info.get("status") != "completed" or not r.get("conclusion"):
            why = info.get("error") or info.get("status")
            lines.append(f"It didn't reach a conclusion ({why}).")
        else:
            sure = "a guess, uncertain" if r.get("kind") == "guess" else r.get("kind")
            lines.append(f"Your conclusion ({sure}): {r['conclusion']}")
            if r.get("question"):
                lines.append(f"What you'd ask them: {r['question']}")
    if r.get("evidence_status"):
        lines.append(f"Memory: {_EVIDENCE_WORDS.get(r['evidence_status'], r['evidence_status'])}.")
    lines += [f"- {fact}" for fact in info.get("speech_facts") or ()]
    return "\n".join(line for line in lines if line)


def save_follow_up_receipt(db, job, message, status: str, decision: str | None = None) -> None:
    """Same transaction as the assistant row and terminal job. Publication is recoverable. ``decision``: what the
    mind did in that wake (``said`` / ``silent``), when it ran."""
    row = db.scalar(select(CoordinatorOutput).where(
        CoordinatorOutput.chat_job_id == job.id).with_for_update())
    if row is None:
        return
    row.status = status
    if decision:
        row.decision = decision[:16]
    row.assistant_message_id = message.id if message else None
    row.announcement_pending = bool(status == "delivered" and message and message.content)


def announce_follow_up(request_id: uuid.UUID) -> bool:
    from app.workers import chat as worker

    with SessionLocal() as db:
        row = db.scalar(select(CoordinatorOutput).where(
            CoordinatorOutput.request_id == request_id, CoordinatorOutput.phase == "follow_up",
            CoordinatorOutput.announcement_pending.is_(True)).with_for_update(skip_locked=True))
        if row is None:
            return False
        job = db.get(ChatJob, row.chat_job_id)
        message = db.get(ChatMessage, row.assistant_message_id)
        if job is None or message is None:
            row.announcement_pending = False
            db.commit()
            return True
        # Publish before clearing: a crash can repeat this ID, never regenerate the reply.
        if not worker._announce(job, message.content, message.id):
            return False
        row.announcement_pending = False
        work = db.get(CoordinatorWork, row.work_id) if row.work_id else None
        if work is not None:
            from app.services import autonomous_speech
            autonomous_speech.delivered(work)
        elif (job.payload or {}).get("phase") == "deliver":
            from app.services import agent_say
            agent_say.mark_said(job.user_id, job.payload["reasons"])
        db.commit()
        return True


def recover_follow_up() -> bool:
    """Repair pre-outbox crash windows and drain durable pending announcements."""
    repaired = False
    with SessionLocal() as db:
        row = db.scalar(select(CoordinatorOutput).join(ChatJob, ChatJob.id == CoordinatorOutput.chat_job_id).where(
            CoordinatorOutput.phase == "follow_up", CoordinatorOutput.status == "dispatched",
            ChatJob.status.in_(("completed", "cancelled", "failed")))
            .order_by(CoordinatorOutput.created_at).with_for_update(of=CoordinatorOutput, skip_locked=True).limit(1))
        if row is not None:
            job = db.get(ChatJob, row.chat_job_id)
            message = db.scalar(select(ChatMessage).where(
                ChatMessage.turn_id == job.turn_id, ChatMessage.role == "assistant"))
            status = ({"completed": "delivered", "partial": "partial", "cancelled": "cancelled",
                       "failed": "failed"}.get(message.status, "failed") if message else
                      ("cancelled" if job.status == "cancelled" else "failed"))
            save_follow_up_receipt(db, job, message, status)
            db.commit()
            repaired = True
        request = db.scalar(select(CoordinatorOutput.request_id).where(
            CoordinatorOutput.announcement_pending.is_(True)).order_by(CoordinatorOutput.updated_at).limit(1))
    return (announce_follow_up(request) if request else False) or repaired


def follow_up_outcome(request_id: uuid.UUID, *, status: str, assistant_message_id: uuid.UUID | None) -> None:
    try:
        with SessionLocal() as db:
            row = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.request_id == request_id,
                                                            CoordinatorOutput.phase == "follow_up"))
            if row is not None:
                row.status, row.assistant_message_id = status, assistant_message_id
                work = db.get(CoordinatorWork, row.work_id) if row.work_id else None
                if status == "delivered" and work is not None:
                    from app.services import autonomous_speech
                    autonomous_speech.delivered(work)
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("coordinator: recording the follow-up outcome failed")


def pending_for(request_id: uuid.UUID) -> tuple[PendingWork, ...]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id == request_id,
            CoordinatorWork.kind.in_(("mind", "recall")))))
    return tuple(PendingWork(str(r.id), r.kind, r.status,
                             (r.input.get("think_about") or r.input.get("message") or "")[:200], "") for r in rows)


# --------------------------------------------------------------------------- #


def _record(*, request_id, user_id, conversation_id, phase, decision, origin, missing, work_id=None,
            status="decided") -> None:
    try:
        with SessionLocal() as db:
            db.execute(insert(CoordinatorOutput).values(
                id=uuid.uuid4(), request_id=request_id, user_id=user_id, conversation_id=conversation_id,
                work_id=work_id, phase=phase, decision=decision, decision_origin=origin,
                missing_capability=missing, status=status,
            ).on_conflict_do_nothing(constraint="uq_coordinator_outputs_phase"))
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("coordinator: recording the %s output failed", phase)
    _log({"op": "output", "request": str(request_id), "phase": phase, "decision": decision, "origin": origin,
          "status": status})


def _wake() -> None:
    from app.services.coordinator_runtime import wake
    wake.set()
    s = get_settings()
    if not s.redis_url:
        return
    try:
        import redis

        with redis.from_url(s.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2) as client:
            with client.pipeline() as pipe:
                pipe.rpush(s.coordinator_queue_name, "wake")
                pipe.ltrim(s.coordinator_queue_name, -1, -1)
                pipe.execute()
    except Exception:  # noqa: BLE001 - polling is the source of truth
        pass


def _log(entry: dict[str, Any]) -> None:
    """Lifecycle lines in the mind's turn trace (``MIND_TURN_LOG_PATH``): ids, kinds, statuses. No message or
    thought text."""
    path = get_settings().mind_turn_log_path
    if not path:
        return
    try:
        if not os.path.isabs(path):
            path = os.path.join(BACKEND, path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({"at": _now().isoformat(), "component": "coordinator", **entry}) + "\n")
    except Exception:  # noqa: BLE001
        pass
