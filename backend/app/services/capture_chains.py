"""Reading the learning capture back as chains (docs/learning-system/01-implementation.md, "Reconstruction").

A chain is what saved records say happened, never more: a conversation turn (Student 1's reading -> Student 2's
choice -> its effect and the reply's delivery -> the channel's settlement and what was heard) or one decision of
her hands (the skill choice -> the skill run). What a chain needs and doesn't have is listed under ``missing``
(an upstream attempt never saved, no delivery record, a known gap), so an incomplete chain is never shown as
complete. ``leaks`` checks the one thing exact capture must guarantee: no source an attempt read was written after
the attempt started. Read only, and only one user's records.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    CaptureBlob, CaptureGap, ChatMessage, DecisionAttempt, DecisionAttemptSource, DecisionExecution,
    TrainingExample,
)


def _aware(at):
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def _resolve(db: Session, user_id: uuid.UUID, value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$blob"}:
        blob = db.get(CaptureBlob, (user_id, value["$blob"]))
        return blob.content if blob is not None else {"$blob": value["$blob"], "missing": True}
    return value


def attempt_out(db: Session, row: DecisionAttempt, *, blobs: bool = False) -> dict[str, Any]:
    inp = dict(row.input or {})
    if blobs:
        inp = {k: _resolve(db, row.user_id, v) for k, v in inp.items()}
    return {"id": str(row.id), "learner": row.learner, "trigger": row.trigger, "provider": row.provider,
            "model": row.model, "status": row.status, "error": row.error, "turn_id": _s(row.turn_id),
            "conversation_id": _s(row.conversation_id), "source_message_id": _s(row.source_message_id),
            "game_session_id": _s(row.game_session_id), "prior_attempt_id": _s(row.prior_attempt_id),
            "upstream": row.upstream, "versions": row.versions, "settings": row.settings, "input": inp,
            "input_refs": row.input_refs, "output": row.output, "absent": row.absent,
            "prediction_ref": row.prediction_ref, "selection": row.selection,
            "started_at": _iso(row.started_at), "produced_at": _iso(row.produced_at),
            "captured_at": _iso(row.captured_at), "expires_at": _iso(row.expires_at)}


def execution_out(row: DecisionExecution) -> dict[str, Any]:
    return {"id": str(row.id), "kind": row.kind, "executor": row.executor, "attempt_id": _s(row.attempt_id),
            "turn_id": _s(row.turn_id), "assistant_message_id": _s(row.assistant_message_id),
            "game_session_id": _s(row.game_session_id), "intended": row.intended, "executed": row.executed,
            "result": row.result, "deviation": row.deviation, "started_at": _iso(row.started_at),
            "ended_at": _iso(row.ended_at), "captured_at": _iso(row.captured_at)}


def gap_out(row: CaptureGap) -> dict[str, Any]:
    return {"record_kind": row.record_kind, "record_id": str(row.record_id), "label": row.label,
            "reason": row.reason, "tries": row.tries, "detail": row.detail, "turn_id": _s(row.turn_id),
            "detected_at": _iso(row.detected_at)}


def _s(value) -> str | None:
    return str(value) if value is not None else None


def _iso(value) -> str | None:
    return _aware(value).isoformat() if value is not None else None


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def leaks(db: Session, row: DecisionAttempt) -> list[str]:
    """Messages this attempt read that were written after it started: must always be empty."""
    refs = list(db.scalars(select(DecisionAttemptSource.ref).where(DecisionAttemptSource.attempt_id == row.id,
                                                                   DecisionAttemptSource.ref.like("message:%"))))
    ids = [i for i in (_uuid(r.split(":", 1)[1]) for r in refs) if i]
    if not ids:
        return []
    started = _aware(row.started_at)
    return [f"message:{m.id}" for m in db.scalars(select(ChatMessage).where(ChatMessage.id.in_(ids)))
            if _aware(m.created_at) > started]


def _chain(db: Session, user_id: uuid.UUID, attempts: list[DecisionAttempt], executions: list[DecisionExecution],
           *, blobs: bool, expect: tuple[str, ...]) -> dict[str, Any]:
    by_id = {a.id: a for a in attempts}
    missing: list[dict[str, Any]] = []
    # Every upstream attempt named must be there (the role's own status notes, e.g. "failed", travel along).
    for a in attempts:
        for role, value in (a.upstream or {}).items():
            ref = _uuid(value)
            if ref is None or role in ("work", "request"):
                continue
            if ref not in by_id:
                found = db.scalar(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                                DecisionAttempt.id == ref))
                if found is not None:
                    by_id[found.id] = found
                    attempts.append(found)
                else:
                    missing.append({"what": f"{role} attempt", "id": str(ref),
                                    "note": (a.upstream or {}).get(f"{role}_status") or "never saved"})
        if a.prior_attempt_id and a.prior_attempt_id not in by_id:
            prior = db.scalar(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                            DecisionAttempt.id == a.prior_attempt_id))
            if prior is not None:
                by_id[prior.id] = prior
                attempts.append(prior)
            else:
                missing.append({"what": "earlier try", "id": str(a.prior_attempt_id), "note": "never saved"})
    attempt_ids = set(by_id)
    if attempt_ids:
        known = {e.id for e in executions}
        executions += [e for e in db.scalars(select(DecisionExecution).where(
            DecisionExecution.user_id == user_id, DecisionExecution.attempt_id.in_(attempt_ids))) if e.id not in known]
    messages = {e.assistant_message_id for e in executions if e.assistant_message_id}
    if messages:
        known = {e.id for e in executions}
        executions += [e for e in db.scalars(select(DecisionExecution).where(
            DecisionExecution.user_id == user_id, DecisionExecution.assistant_message_id.in_(messages)))
            if e.id not in known]
    kinds = {e.kind for e in executions}
    learners = {a.learner for a in attempts}
    for need in expect:
        if need not in learners and need not in kinds:
            missing.append({"what": need, "note": "no record saved"})
    ids = attempt_ids | {e.id for e in executions}
    gaps = list(db.scalars(select(CaptureGap).where(CaptureGap.user_id == user_id,
                                                    CaptureGap.record_id.in_(ids or {uuid.uuid4()}))))
    leaked = {str(a.id): leaks(db, a) for a in attempts}
    leaked = {k: v for k, v in leaked.items() if v}
    attempts.sort(key=lambda a: (_aware(a.started_at), a.learner))
    executions.sort(key=lambda e: (_aware(e.ended_at or e.started_at or e.captured_at), e.kind))
    examples = list(db.scalars(select(TrainingExample.id).where(TrainingExample.user_id == user_id,
                                                                TrainingExample.attempt_id.in_(attempt_ids or {uuid.uuid4()}))))
    return {"complete": not missing and not gaps, "attempts": [attempt_out(db, a, blobs=blobs) for a in attempts],
            "executions": [execution_out(e) for e in executions], "missing": missing,
            "gaps": [gap_out(g) for g in gaps], "leaks": leaked, "training_examples": [str(i) for i in examples]}


TURN_EXPECTS = ("student_1", "student_2", "reply")


def turn(db: Session, user_id: uuid.UUID, turn_id: uuid.UUID, *, blobs: bool = False) -> dict[str, Any] | None:
    """One conversation turn's chain (the request's own turn, or a follow-up's)."""
    attempts = list(db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                             DecisionAttempt.turn_id == turn_id)))
    executions = list(db.scalars(select(DecisionExecution).where(DecisionExecution.user_id == user_id,
                                                                 DecisionExecution.turn_id == turn_id)))
    gaps = list(db.scalars(select(CaptureGap).where(CaptureGap.user_id == user_id, CaptureGap.turn_id == turn_id)))
    if not attempts and not executions and not gaps:
        return None
    # A follow-up has no Student 1: its decision is on a finished thought.
    expects = TURN_EXPECTS if any(a.trigger == "user_message" for a in attempts) or not attempts else (
        "student_2", "reply")
    chain = _chain(db, user_id, attempts, executions, blobs=blobs, expect=expects)
    known = {g["record_id"] for g in chain["gaps"]}
    chain["gaps"] += [gap_out(g) for g in gaps if str(g.record_id) not in known]
    chain["complete"] = chain["complete"] and not gaps
    chain["turn_id"] = str(turn_id)
    return chain


def attempt(db: Session, user_id: uuid.UUID, attempt_id: uuid.UUID, *, blobs: bool = True) -> dict[str, Any] | None:
    """One attempt with what it links to (for her hands: the skill run it chose)."""
    row = db.scalar(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id,
                                                  DecisionAttempt.id == attempt_id))
    if row is None:
        return None
    if row.turn_id is not None:
        return turn(db, user_id, row.turn_id, blobs=blobs)
    expect: tuple[str, ...] = ()
    if row.status == "ok":
        if row.learner == "hands" and (row.output or {}).get("action"):
            expect = ("skill_run",)
        elif row.learner == "planner":
            expect = ("plan_apply",)
        elif row.learner == "hard_thought" and ((row.output or {}).get("parsed") or {}).get("kind"):
            expect = ("thought_route",)
    return _chain(db, user_id, [row], [], blobs=blobs, expect=expect)


# --------------------------------------------------------------------------- #
# A game session (docs/learning-system/POST-AUDIT-01-implementation.md, "Reconstruction")
# --------------------------------------------------------------------------- #

SESSION_WINDOW = timedelta(hours=1)
SESSION_LIMIT = 2000


def _hands_reports(attempt: DecisionAttempt, runs: list[DecisionExecution]) -> dict[str, Any]:
    """One decision of her hands, three reports kept apart: what her hands said (their status for the step and
    note), what the action reported (the pack's result for the skill it started), what code observed (her state
    before and after, with when). Never folded into one success label."""
    output = (attempt.output or {}).get("output") or {}
    run = next((r for r in runs if r.kind == "skill_run"), None)
    executed = (run.executed or {}) if run is not None else {}
    observed = executed.get("observed")
    return {
        "claim": {"status": output.get("status"), "note": output.get("note"), "step_id": output.get("step_id"),
                  "by": "her hands (the decision's own words)"} if output else None,
        "action": {"result": run.result, "status": executed.get("status"), "reason": executed.get("reason"),
                   "data": executed.get("data"), "run_id": executed.get("run_id"), "deviation": run.deviation,
                   "by": "the game pack's report of the skill"} if run is not None else None,
        "observed": ({**observed, "by": "her state, read by code"} if observed else None),
    }


def session(db: Session, user_id: uuid.UUID, session_id: uuid.UUID, *, since: datetime | None = None,
            until: datetime | None = None, blobs: bool = False, limit: int = SESSION_LIMIT) -> dict[str, Any] | None:
    """A game session's experience in a window (default: the last hour): her planner's decisions and what code
    applied, her hard thoughts and where they went, her hands' decisions and what each run did, code's step
    checks, and her trajectory's streams, in time order, with every link that is known and what is missing.
    Activity without a goal is valid (her hands' plan may have none); nothing is invented to fill a link."""
    from app.db.models import DesktopSession
    from app.services import trajectory

    owned = db.scalar(select(DesktopSession.id).where(DesktopSession.id == session_id,
                                                      DesktopSession.owner_user_id == user_id))
    if owned is None:
        return None
    until = until or datetime.now(timezone.utc)
    since = since or until - SESSION_WINDOW
    attempts = list(db.scalars(select(DecisionAttempt).where(
        DecisionAttempt.user_id == user_id, DecisionAttempt.game_session_id == session_id,
        DecisionAttempt.started_at >= since, DecisionAttempt.started_at <= until)
        .order_by(DecisionAttempt.started_at).limit(limit)))
    ids = {a.id for a in attempts}
    executions = list(db.scalars(select(DecisionExecution).where(
        DecisionExecution.user_id == user_id, DecisionExecution.game_session_id == session_id,
        ((DecisionExecution.attempt_id.in_(ids or {uuid.uuid4()}))
         | ((DecisionExecution.attempt_id.is_(None)) & (DecisionExecution.ended_at >= since)
            & (DecisionExecution.ended_at <= until))))))
    by_attempt: dict[uuid.UUID, list[DecisionExecution]] = {}
    for e in executions:
        if e.attempt_id is not None:
            by_attempt.setdefault(e.attempt_id, []).append(e)
    missing: list[dict[str, Any]] = []
    # Which planner attempt made each plan revision (its plan_apply says the revision after).
    made_by = {(e.executed or {}).get("plan_revision_after"): str(e.attempt_id) for e in executions
               if e.kind == "plan_apply" and (e.executed or {}).get("plan_revision_after") is not None
               and e.result in ("succeeded", "deviated")}
    decisions = []
    for a in attempts:
        runs = by_attempt.get(a.id, [])
        item: dict[str, Any] = {"id": str(a.id), "learner": a.learner, "trigger": a.trigger, "status": a.status,
                                "error": a.error, "started_at": _iso(a.started_at), "produced_at": _iso(a.produced_at),
                                "executions": [str(e.id) for e in runs]}
        if a.learner == "hands":
            plan = (a.input or {}).get("plan") or {}
            item["plan"] = {"revision": plan.get("revision"), "step_id": plan.get("step_id"),
                            "goal": plan.get("goal"), "made_by_planner_attempt": made_by.get(plan.get("revision"))}
            item["reports"] = _hands_reports(a, runs)
            action = ((a.output or {}).get("action"))
            if a.status == "ok" and action and not runs:
                missing.append({"what": "skill run", "attempt": str(a.id), "note": "no record of what it did"})
            if plan.get("revision") is not None and plan.get("revision") not in made_by:
                item["plan"]["made_by"] = ("unknown here: no planner record made this revision in the window "
                                           "(set by the user, before the window, or not captured)")
        elif a.learner == "planner":
            apply = next((e for e in runs if e.kind == "plan_apply"), None)
            parsed = (a.output or {}).get("parsed") or {}
            item["proposal"] = {"decision": parsed.get("decision"), "why": parsed.get("why"),
                                "rules": parsed.get("rules"), "follows": parsed.get("follows")}
            item["applied"] = ({"result": apply.result, "deviation": apply.deviation,
                                "revision_after": (apply.executed or {}).get("plan_revision_after"),
                                "rules": (apply.executed or {}).get("rules")} if apply is not None else None)
            if a.status == "ok" and apply is None:
                missing.append({"what": "plan_apply", "attempt": str(a.id), "note": "no record of what code applied"})
        elif a.learner == "hard_thought":
            route = next((e for e in runs if e.kind == "thought_route"), None)
            parsed = (a.output or {}).get("parsed") or {}
            item["conclusion"] = {"kind": parsed.get("kind"), "conclusion": parsed.get("conclusion"),
                                  "follows": parsed.get("follows"), "assessment": parsed.get("assessment")}
            item["went"] = ({"result": route.result, "status": (route.executed or {}).get("status"),
                             "routed": (route.executed or {}).get("routed")} if route is not None else None)
            if a.status == "ok" and parsed.get("kind") and route is None:
                missing.append({"what": "thought_route", "attempt": str(a.id), "note": "no record of where it went"})
        if a.prior_attempt_id and a.prior_attempt_id not in ids:
            if db.scalar(select(DecisionAttempt.id).where(DecisionAttempt.id == a.prior_attempt_id)) is None:
                missing.append({"what": "earlier try", "id": str(a.prior_attempt_id), "note": "never saved"})
        decisions.append(item)
    step_checks = [{"id": str(e.id), "at": _iso(e.ended_at), "step": (e.intended or {}).get("step_id"),
                    "counted": (e.executed or {}).get("counted"), "observed": (e.executed or {}).get("observed")}
                   for e in executions if e.kind == "step_check"]
    record_ids = ids | {e.id for e in executions}
    gaps = list(db.scalars(select(CaptureGap).where(CaptureGap.user_id == user_id, (
        CaptureGap.record_id.in_(record_ids or {uuid.uuid4()}))
        | ((CaptureGap.label.in_(("planner", "hard_thought", "hands", "plan_apply", "thought_route", "skill_run",
                                  "step_check", "trajectory")))
           & (CaptureGap.detected_at >= since) & (CaptureGap.detected_at <= until)))))
    streams = trajectory.streams(db, user_id, session_id=session_id, since=since, until=until)
    for st in streams:
        for hole in st["missing"]:
            missing.append({"what": "trajectory chunks", "stream": st["id"], **hole})
        if not st["end_seen"]:
            missing.append({"what": "trajectory stream end", "stream": st["id"],
                            "note": "never seen: still connected, or the pack stopped without saying"})
    attempts_out = [attempt_out(db, a, blobs=blobs) for a in attempts]
    executions.sort(key=lambda e: (_aware(e.ended_at or e.started_at or e.captured_at), e.kind))
    return {
        "session_id": str(session_id), "since": _iso(since), "until": _iso(until), "truncated": len(attempts) >= limit,
        "decisions": decisions, "step_checks": step_checks, "trajectory": streams,
        "attempts": attempts_out, "executions": [execution_out(e) for e in executions],
        "missing": missing, "gaps": [gap_out(g) for g in gaps],
        "leaks": {str(a.id): v for a in attempts if (v := leaks(db, a))},
        "complete": not missing and not gaps,
    }


def overview(db: Session, user_id: uuid.UUID, *, limit: int = 20) -> dict[str, Any]:
    from app.services import learning_capture

    by_learner = dict(db.execute(select(DecisionAttempt.learner, func.count()).where(
        DecisionAttempt.user_id == user_id).group_by(DecisionAttempt.learner)).all())
    by_result = dict(db.execute(select(DecisionExecution.result, func.count()).where(
        DecisionExecution.user_id == user_id).group_by(DecisionExecution.result)).all())
    examples = dict(db.execute(select(TrainingExample.eligible, func.count()).where(
        TrainingExample.user_id == user_id).group_by(TrainingExample.eligible)).all())
    gaps = list(db.scalars(select(CaptureGap).where(CaptureGap.user_id == user_id)
                           .order_by(CaptureGap.detected_at.desc()).limit(limit)))
    recent = list(db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == user_id)
                             .order_by(DecisionAttempt.captured_at.desc()).limit(limit)))
    from app.services import trajectory

    return {"process": learning_capture.service().stats(),
            "attempts": by_learner, "executions": by_result,
            "trajectory": trajectory.measure(db, user_id),
            "training_examples": {"eligible": examples.get(True, 0), "ineligible": examples.get(False, 0)},
            "gaps": [gap_out(g) for g in gaps],
            "recent": [{"id": str(a.id), "learner": a.learner, "status": a.status, "turn_id": _s(a.turn_id),
                        "game_session_id": _s(a.game_session_id), "started_at": _iso(a.started_at)} for a in recent]}


__all__ = ["turn", "attempt", "session", "overview", "leaks"]
