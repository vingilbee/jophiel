"""The coordinator's background work: recall and thinking for a user request, and what happens to the result
(docs/SHARED_STATE_STEP4_SPEC.md). Code, not a model. Tables: ``coordinator_work``, ``coordinator_outputs``.

Lifecycle of a request that needs thinking:

1. ``schedule`` (in ``shared_state.prepare``, before the mouth): commits a mind work item, plus a recall item it
   depends on when recall is also needed. Idempotent per request, so a retried chat job schedules nothing new.
2. ``decide_immediate``: cognition decides acknowledge / partial / silent (``cognition_decisions``). The
   student can't decide it yet, so the labelled fallback is ``respond``: the mouth replies, told that the
   thinking is pending and not done. Recorded as the request's ``immediate`` output.
3. The ``coordinator`` worker thread (``run_once``) claims ready work: recall through memory's own reply search,
   then the mind through ``app/ai/mind/hard.py``, which waits for its recall dependency. Every result is
   checked against the lease and the cancellation generation before it's accepted.
4. ``_conclude``: a result for a conversation that moved on is kept private (``superseded``). Otherwise cognition
   decides respond / ask / defer / silent. Unsupported (today) means ``blocked``: nothing is said. respond/ask
   queues a follow-up chat job, ``uuid5(request, "follow_up")``, which the chat worker runs through the
   mouth after the immediate reply. Chat jobs run one at a time per conversation, so the follow-up can't
   overtake or interleave with it.

A new user message supersedes every unfinished item in its conversation (``supersede_older``): its result can
never become the answer to the new message. Deadlines end work as ``expired``; failures as ``failed``. Both
still reach ``_conclude``, so a waiting user isn't left with silence nobody decided on.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.ai.state.decisions import PendingDecision, PendingInput, PendingWork, ResultInput, ThoughtResult
from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation, CoordinatorOutput, CoordinatorWork
from app.db.session import SessionLocal
from app.services import cognition_decisions

logger = logging.getLogger(__name__)

ACTIVE = ("queued", "waiting_for_memory", "running")
TERMINAL = ("completed", "failed", "cancelled", "expired", "superseded")
LEASE_SECONDS = 120


def enabled() -> bool:
    return bool(get_settings().shared_state_pipeline_enabled)


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
                CoordinatorWork.status.in_(ACTIVE),
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
    """Commit the request's work (a mind item, after a recall item when ``with_recall``). Returns what the mouth
    may know about it, or None when it couldn't be committed (then no work exists, and nobody may say it does)."""
    deadline = _now() + timedelta(seconds=get_settings().shared_state_work_deadline_seconds)
    subject = (work_input.get("message") or "")[:200]
    try:
        with SessionLocal() as db:
            ids: dict[str, uuid.UUID] = {}
            for kind in (("recall", "mind") if with_recall else ("mind",)):
                deps = [str(ids["recall"])] if kind == "mind" and with_recall else []
                key = f"{request_id}:{kind}"
                db.execute(insert(CoordinatorWork).values(
                    id=uuid.uuid4(), request_id=request_id, user_id=user_id, conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id, kind=kind, dependency_ids=deps,
                    status="queued", input=work_input, input_versions=input_versions, deadline_at=deadline,
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


def decide_immediate(*, user_id: uuid.UUID, conversation_id: uuid.UUID, request_id: uuid.UUID,
                     trigger_ref: str, interpretation: dict | None, pending: tuple[PendingWork, ...],
                     channel: str, voice: bool) -> tuple[str, PendingDecision]:
    """Cognition's decision for the reply while she thinks, and the action the turn takes. Unsupported (today):
    ``respond``, labelled ``policy_fallback``: never silence nobody chose. Recorded once per request."""
    decision = cognition_decisions.decide_pending(PendingInput(str(request_id), trigger_ref, interpretation, pending,
                                                               channel, voice))
    action = decision.action if decision.supported and decision.action else "respond"
    origin = decision.origin if decision.supported else "policy_fallback"
    _record(request_id=request_id, user_id=user_id, conversation_id=conversation_id, phase="immediate",
            decision=action, origin=origin, missing=decision.missing_capability,
            work_id=uuid.UUID(pending[-1].work_id) if pending else None)
    return action, decision


def immediate_outcome(request_id: uuid.UUID, *, status: str, assistant_message_id: uuid.UUID | None) -> None:
    """What the immediate phase actually did: delivered / partial / cancelled / failed / not_sent (silent)."""
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


def _claim(db, token: str) -> tuple[CoordinatorWork | None, str | None]:
    """The next ready item, leased; or one past its deadline, returned to be ended (``expired``)."""
    now = _now()
    candidates = list(db.scalars(select(CoordinatorWork).where(
        (CoordinatorWork.status.in_(("queued", "waiting_for_memory")))
        | ((CoordinatorWork.status == "running") & (CoordinatorWork.lease_expires_at <= now)))
        .order_by(CoordinatorWork.created_at, CoordinatorWork.id).with_for_update(skip_locked=True).limit(20)))
    for work in candidates:
        if work.deadline_at <= now:
            work.status, work.error_code, work.lease_owner = "expired", "deadline", None
            return work, "expired"
        deps = list(db.scalars(select(CoordinatorWork).where(
            CoordinatorWork.id.in_([uuid.UUID(d) for d in work.dependency_ids])))) if work.dependency_ids else []
        if any(d.status in ACTIVE for d in deps):
            work.status = "waiting_for_memory"  # it can't start before the evidence it depends on
            continue
        work.status, work.attempt = "running", work.attempt + 1
        work.lease_owner, work.lease_expires_at = token, now + timedelta(seconds=LEASE_SECONDS)
        return work, None
    return None, None


def run_once() -> bool:
    token = uuid.uuid4().hex
    with SessionLocal() as db:
        work, ended = _claim(db, token)
        db.commit()
        if work is None:
            return False
        db.expunge(work)
    if ended == "expired":
        _log({"op": "expired", "work": str(work.id), "kind": work.kind})
        if work.kind == "mind":
            _conclude(work.id)
        return True
    generation = work.cancellation_generation
    try:
        if work.kind == "recall":
            result, status, error = _run_recall(work)
        else:
            result, status, error = _run_mind(work)
    except Exception as exc:  # noqa: BLE001 - a failed job is a recorded terminal outcome
        logger.exception("coordinator: %s work %s failed", work.kind, work.id)
        result, status, error = None, "failed", type(exc).__name__[:64]
    accepted = _accept(work.id, token, generation, status, result, error)
    _log({"op": "finished", "work": str(work.id), "kind": work.kind, "status": status, "accepted": accepted,
          "error": error})
    if work.kind == "mind":
        _conclude(work.id)
    _wake()  # a dependent item may be ready now
    return True


def _accept(work_id: uuid.UUID, token: str, generation: int, status: str, result: dict | None,
            error: str | None) -> bool:
    """Keep a result only while this worker still owns the item and nothing superseded it. A late result is
    kept on the row as a private outcome, and the status stays what superseded it."""
    with SessionLocal() as db:
        row = db.get(CoordinatorWork, work_id, with_for_update=True)
        if row is None:
            return False
        owned = row.status == "running" and row.lease_owner == token and row.cancellation_generation == generation
        if row.result is None:
            row.result = result
        if owned:
            row.status, row.error_code, row.lease_owner, row.lease_expires_at = status, error, None, None
        db.commit()
        return owned


def _trigger(db, work: CoordinatorWork) -> ChatMessage | None:
    return db.get(ChatMessage, work.trigger_message_id) if work.trigger_message_id else None


def _run_recall(work: CoordinatorWork) -> tuple[dict, str, str | None]:
    """Memory's own reply search (the previous answer's references plus a search for the message). Its status
    stays memory's: matched / partial / no_match are completed results; failed is a failed item."""
    from app.services import recall_references

    if not recall_references.enabled():
        return {"status": "unavailable"}, "failed", "recall_disabled"
    turn = recall_references.for_reply(work.user_id, work.conversation_id, work.trigger_message_id,
                                       work.input.get("message", ""), work.input.get("zone"))
    result = {"status": turn.result.status, "refs": [str(r) for r in turn.result.refs()],
              "rendered": turn.rendered[:6000]}
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
    reading = work.input.get("interpretation")
    problem = Problem(
        trigger="handoff",
        text=(f'They said: "{message}"\n'
              + (f"Your quick reading of it: {json.dumps(reading)}\n" if reading else "")
              + "Work out what you really think before you answer them properly."),
        topic=message[:60], query=message)
    thought = _think(HardInput(problem=problem, user=work.input.get("user_name") or "the user",
                               conversation=conversation, memories=memories))
    result = {"kind": thought.kind, "conclusion": thought.conclusion, "question": thought.question,
              "thinking": thought.thinking, "evidence_status": evidence_status, "evidence_refs": refs,
              "latency_ms": thought.latency_ms, "error": thought.error}
    if thought.error:
        return result, "failed", "mind_error"
    return result, "completed", None


# --------------------------------------------------------------------------- #
# After the mind
# --------------------------------------------------------------------------- #


def _conclude(work_id: uuid.UUID) -> None:
    """Decide what happens to a mind item's outcome and dispatch it once. Never speaks directly."""
    with SessionLocal() as db:
        work = db.get(CoordinatorWork, work_id)
        if work is None:
            return
        trigger = _trigger(db, work)
        newer = 0
        if trigger is not None and trigger.conversation_sequence is not None:
            newer = db.scalar(select(func.count()).select_from(ChatMessage).where(
                ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user",
                ChatMessage.conversation_sequence > trigger.conversation_sequence)) or 0
        immediate = db.scalar(select(CoordinatorOutput.decision).where(
            CoordinatorOutput.request_id == work.request_id, CoordinatorOutput.phase == "immediate"))
        db.expunge(work)
    common = dict(request_id=work.request_id, user_id=work.user_id, conversation_id=work.conversation_id,
                  phase="follow_up", work_id=work.id)
    if work.status == "superseded" or newer:
        # The conversation moved on: the thought stays private; it can't answer a newer message.
        _record(**common, decision="superseded", origin="coordinator", missing=None, status="not_sent")
        return
    r = work.result or {}
    result = ThoughtResult(
        str(work.id), r.get("kind"), r.get("conclusion"), r.get("question"),
        uncertain=r.get("kind") == "guess" or r.get("evidence_status") not in (None, "matched"),
        evidence_refs=tuple(r.get("evidence_refs") or ()), evidence_status=r.get("evidence_status"),
        failed=None if work.status == "completed" else (work.error_code or work.status))
    decision = cognition_decisions.decide_result(ResultInput(
        str(work.request_id), f"message:{work.trigger_message_id}", result, work.input.get("interpretation"),
        newer, immediate, work.input.get("channel", "web")))
    if not decision.supported or not decision.action:
        _record(**common, decision="blocked", origin=decision.origin, missing=decision.missing_capability,
                status="not_sent")
        return
    if decision.action in ("silent", "defer"):
        _record(**common, decision=decision.action, origin=decision.origin, missing=None, status="not_sent")
        return
    _dispatch_follow_up(work, decision.action, decision.origin)


def _dispatch_follow_up(work: CoordinatorWork, action: str, origin: str) -> None:
    """One follow-up chat job per request: same conversation, same channel as the request, never "the latest
    conversation". The output row and the job commit together; a second dispatch finds the row and stops."""
    from app.services import chat as chat_service

    turn = follow_up_turn(work.request_id)
    with SessionLocal() as db:
        output = db.execute(insert(CoordinatorOutput).values(
            id=uuid.uuid4(), request_id=work.request_id, user_id=work.user_id, conversation_id=work.conversation_id,
            work_id=work.id, phase="follow_up", decision=action, decision_origin=origin, status="dispatched",
        ).on_conflict_do_nothing(constraint="uq_coordinator_outputs_phase").returning(CoordinatorOutput.id)).scalar()
        if output is None:
            db.rollback()
            return  # already dispatched (a retry or a second worker)
        conversation = db.get(Conversation, work.conversation_id)
        request = db.scalar(select(ChatJob).where(ChatJob.turn_id == work.request_id))
        base = request.payload if request is not None else {}
        payload = {"phase": "follow_up", "content": "", "work_id": str(work.id), "request_id": str(work.request_id),
                   "action": action, "channel": base.get("channel", work.input.get("channel", "web")),
                   "zone": base.get("zone"), "awareness": base.get("awareness", "")}
        if base.get("channel_identity_id"):
            payload["channel_identity_id"] = base["channel_identity_id"]
        job = chat_service.enqueue_job(db, user_id=work.user_id, conversation=conversation, turn_id=turn,
                                       payload=payload, commit=False)
        db.execute(CoordinatorOutput.__table__.update().where(CoordinatorOutput.id == output)
                   .values(chat_job_id=job.id))
        db.commit()
        chat_service.wake_chat_worker(job.id)
    _log({"op": "follow_up_dispatched", "work": str(work.id), "request": str(work.request_id), "action": action})


def follow_up_still_valid(work_id: uuid.UUID) -> tuple[bool, dict | None]:
    """Checked again by the chat worker right before the mouth speaks: no newer user message, not superseded."""
    with SessionLocal() as db:
        work = db.get(CoordinatorWork, work_id)
        if work is None or work.status == "superseded":
            return False, None
        trigger = _trigger(db, work)
        if trigger is not None and trigger.conversation_sequence is not None:
            newer = db.scalar(select(func.count()).select_from(ChatMessage).where(
                ChatMessage.conversation_id == work.conversation_id, ChatMessage.role == "user",
                ChatMessage.conversation_sequence > trigger.conversation_sequence)) or 0
            if newer:
                return False, None
        return True, {"result": work.result, "status": work.status, "error": work.error_code,
                      "message": work.input.get("message", "")}


_EVIDENCE_WORDS = {"matched": "memory had something on it", "partial": "memory was only partly readable",
                   "no_match": "memory had nothing on it (not proof it didn't happen)",
                   "failed": "looking it up in memory failed", "expired": "the memory lookup ran out of time",
                   "superseded": "the memory lookup was dropped", "unavailable": "memory couldn't be searched"}


def render_result(info: dict) -> str:
    """The finished thought as the mouth reads it: what it was about, the conclusion and how sure, what memory
    gave it. Her private reasoning (``thinking``) is never shown."""
    r = info.get("result") or {}
    lines = [f'What they said: "{info.get("message", "")}"']
    if info.get("status") != "completed" or not r.get("conclusion"):
        why = info.get("error") or info.get("status")
        lines.append(f"Your thinking didn't reach a conclusion ({why}).")
    else:
        sure = "a guess, uncertain" if r.get("kind") == "guess" else r.get("kind")
        lines.append(f"Your conclusion ({sure}): {r['conclusion']}")
        if r.get("question"):
            lines.append(f"What you'd ask them: {r['question']}")
    if r.get("evidence_status"):
        lines.append(f"Memory: {_EVIDENCE_WORDS.get(r['evidence_status'], r['evidence_status'])}.")
    return "\n".join(lines)


def follow_up_outcome(request_id: uuid.UUID, *, status: str, assistant_message_id: uuid.UUID | None) -> None:
    try:
        with SessionLocal() as db:
            row = db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.request_id == request_id,
                                                            CoordinatorOutput.phase == "follow_up"))
            if row is not None:
                row.status, row.assistant_message_id = status, assistant_message_id
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("coordinator: recording the follow-up outcome failed")


def pending_for(request_id: uuid.UUID) -> tuple[PendingWork, ...]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id == request_id)))
    return tuple(PendingWork(str(r.id), r.kind, r.status, (r.input.get("message") or "")[:200], "") for r in rows)


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
    s = get_settings()
    if not s.redis_url:
        return
    try:
        import redis

        redis.from_url(s.redis_url).rpush(s.coordinator_queue_name, "wake")
    except Exception:  # noqa: BLE001 - polling is the source of truth
        pass


def _log(entry: dict[str, Any]) -> None:
    """Lifecycle lines in the shared-state trace: ids, kinds, statuses. No message or thought text."""
    path = get_settings().shared_state_log_path
    if not path:
        return
    try:
        from app.services.cognitive_live import BACKEND

        if not os.path.isabs(path):
            path = os.path.join(str(BACKEND), path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({"at": _now().isoformat(), "component": "coordinator", **entry}) + "\n")
    except Exception:  # noqa: BLE001
        pass
