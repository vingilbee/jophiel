"""Predictions and bounded game trials: learning's experiment lifecycle (docs/learning-system/04-implementation.md;
decisions: docs/learning-system/04-predictions-and-trials.md; pure parts: app/ai/learning/trials.py).

Who does what:

| Part | Owner | Here |
| --- | --- | --- |
| What to investigate | learning (code) | ``consider`` (her hands struggling or stuck on a step of a goal; an approach changed mid-trial), ``sweep`` (her open hypotheses in a live game) |
| A proposal: hypothesis, alternatives, predictions, costs, limits, recommendation | learning (one call) | ``investigate_once`` -> ``trials.check`` -> ``LearningTrialRevision`` (persisted before her mind sees it) |
| Whether and when to run it, and which way | her mind | ``present_once`` -> a wake (``autonomous_speech.submit``, ``wake="proposal"``) -> her ``decide_proposal`` tool -> ``on_pursue`` / ``on_decline`` / ``on_defer``; no decision in that wake: ``undecided`` |
| A deferral's reminder | memory (working item, an intention of hers) | ``_remind`` / ``_settle_reminder`` |
| Arranging what's missing | the planner | ``game_planning.request`` (reason ``trial: ...``), ``planning_lines`` |
| Running it | her body (game practice, ``origin="trial"``) | ``_start`` -> ``game_practice.run_once`` -> ``on_execution_finished`` |
| Bounds | code | ``_advance`` (time, readiness), the practice run (attempts, time), the pack (blocks) |
| What happened | code | ``trials.measure`` / ``evaluate`` |
| What it supports | learning (one call, checked) | ``_assess`` -> ``trials.check_assessment`` |
| What persists | hypothesis (memory), lesson (game lessons), move (game skills) | ``_feed`` |

Her mind is the only one who starts a trial: a proposal's arrival interrupts nothing, hard thoughts and the
planner never start one, and every trial taken up was chosen in an evaluation of her mind whose capture id is in
its selection. A trial ends at its limits; another needs a fresh proposal she selects.

Never rewritten: proposal revisions (immutable; none once selected), the event log, her selection. A
changed approach ends the trial ("approach_changed") and queues a fresh prediction as a new, linked trial.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.learning import trials as T
from app.core.config import get_settings
from app.db.models import (
    DESKTOP_TERMINAL_STATES, TRIAL_CLOSED, TRIAL_LIVE, TRIAL_PENDING, ChatMessage, CoordinatorWork, DesktopSession,
    GameExperience, GameSkillAttempt, LearningTrial, LearningTrialAssessment, LearningTrialEvent,
    LearningTrialRevision, LearningTrialTrigger, WorkingItem,
)
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = "climb_obstacle"
LEASE = timedelta(minutes=5)
CHECK_EVERY = timedelta(seconds=5)  # a live trial's readiness / budget / window
DEFERRED_EVERY = timedelta(seconds=15)  # a deferral's reconsideration condition
MAX_BACKOFF = timedelta(minutes=30)
DECIDE_TRIES = 4  # her mind woken this often without deciding it: deferred with that reason
EVIDENCE_KEPT = 10
TRIALS_SHOWN = 6  # earlier trials a proposer reads
LESSONS_SHOWN = 8
GRACE = timedelta(seconds=45)  # past its time budget, an attempt that hasn't ended is asked to stop
_log_lock = threading.Lock()


def enabled() -> bool:
    """New investigations and proposals. Live trials finish and are assessed regardless."""
    s = get_settings()
    return bool(s.desktop_agent_enabled) and bool(s.learning_trials_enabled)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=timezone.utc) if at is not None and at.tzinfo is None else at


def default_limits() -> T.Limits:
    s = get_settings()
    return T.Limits(s.trial_max_attempts, s.trial_max_seconds, s.trial_max_blocks)


def _event(db: Session, trial: LearningTrial, kind: str, payload: dict | None = None, *,
           decision_attempt_id: uuid.UUID | None = None, skill_attempt_id: uuid.UUID | None = None,
           experience_id: int | None = None) -> None:
    db.add(LearningTrialEvent(trial_id=trial.id, user_id=trial.user_id, kind=kind,
                              payload=json.loads(json.dumps(payload or {}, default=str)),
                              decision_attempt_id=decision_attempt_id, skill_attempt_id=skill_attempt_id,
                              experience_id=experience_id))


def _log(record: dict) -> None:
    path = Path(get_settings().learning_trials_log_path)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, path.open("a") as f:
            f.write(json.dumps({"at": _now().isoformat(), **record}, default=str) + "\n")
    except OSError:
        pass


def current_revision(db: Session, trial: LearningTrial, revision: int | None = None) -> LearningTrialRevision:
    return db.scalar(select(LearningTrialRevision).where(
        LearningTrialRevision.trial_id == trial.id, LearningTrialRevision.revision == (revision or trial.revision)))


def _live_pack_session(db: Session, session_id: uuid.UUID | None) -> DesktopSession | None:
    from app.services import game_pack

    session = db.get(DesktopSession, session_id) if session_id else None
    if session is None or session.state in DESKTOP_TERMINAL_STATES or not game_pack.is_pack_profile(session.profile_id):
        return None
    return session


def _active_step(session: DesktopSession) -> dict | None:
    return next((st for st in (session.plan or {}).get("steps", []) if st.get("state") == "active"), None)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:80]


# --------------------------------------------------------------------------- #
# What to investigate (code only, in the caller's transaction)
# --------------------------------------------------------------------------- #


def consider(db: Session, session: DesktopSession, *, kind: str, text: str, hypothesis_item_id: uuid.UUID | None = None,
             trial_id: uuid.UUID | None = None, approach: str | None = None) -> bool:
    """Queue an investigation (never an action). ``struggling`` / ``stuck``: her hands on a step of the goal the
    session plays for. ``hypothesis``: an open hypothesis of hers in this game. ``approach_changed``: a trial's
    body wanted another way. A repeat about the same problem adds its evidence to the waiting one. Runs in a
    savepoint: it never fails the caller. Returns whether anything was queued or added."""
    if not enabled():
        return False
    from app.services import game_pack

    if not game_pack.is_pack_profile(session.profile_id):
        return False
    try:
        with db.begin_nested():
            return _consider(db, session, kind=kind, text=text, hypothesis_item_id=hypothesis_item_id,
                             trial_id=trial_id, approach=approach)
    except Exception:  # noqa: BLE001 - investigating is never worth failing play for
        log.exception("learning trials: queuing an investigation failed")
        return False


def _consider(db: Session, session: DesktopSession, *, kind: str, text: str, hypothesis_item_id, trial_id,
              approach) -> bool:
    step = _active_step(session)
    if kind in ("struggling", "stuck"):
        if session.goal_item_id is None or step is None:
            return False  # only problems relevant to an active goal
        key = f"step:{session.goal_item_id.hex[:12]}:{_norm(step.get('do', ''))}"
        problem = f'her hands keep failing at the step "{step.get("do")}"'
    elif kind == "hypothesis":
        key, problem = f"hypothesis:{hypothesis_item_id}", f"her hypothesis: {text}"
    elif kind == "approach_changed":
        key, problem = f"approach:{trial_id}", f"a trial's approach changed: {approach}"
    else:
        raise ValueError(kind)
    evidence = {"kind": kind, "text": " ".join((text or "").split())[:500], "at": _now().isoformat(),
                "step_id": step.get("id") if step else None,
                "experience_id": _last_outcome(db, session.id, step.get("id") if step else None)}
    found = db.scalar(select(LearningTrialTrigger).where(
        LearningTrialTrigger.user_id == session.owner_user_id, LearningTrialTrigger.problem_key == key[:160],
        LearningTrialTrigger.state.in_(("pending", "running"))).with_for_update())
    if found is not None:
        payload = dict(found.payload)
        payload["evidence"] = (payload.get("evidence", []) + [evidence])[-EVIDENCE_KEPT:]
        found.payload = payload
        return True
    db.add(LearningTrialTrigger(
        user_id=session.owner_user_id, session_id=session.id, kind=kind, problem_key=key[:160],
        payload={"problem": problem[:400], "evidence": [evidence], "step": step,
                 "hypothesis_item_id": str(hypothesis_item_id) if hypothesis_item_id else None,
                 "trial_id": str(trial_id) if trial_id else None, "approach": approach}))
    db.flush()
    return True


def _last_outcome(db: Session, session_id: uuid.UUID, step_id: str | None) -> int | None:
    query = select(GameExperience.id).where(GameExperience.session_id == session_id,
                                            GameExperience.kind == "skill_outcome")
    if step_id:
        query = query.where(GameExperience.payload["step_id"].astext == str(step_id))
    return db.scalar(query.order_by(GameExperience.id.desc()).limit(1))


def sweep(db: Session, now: datetime | None = None) -> int:
    """Her open hypotheses in a live game, each investigated once (per revision), and pending work whose session
    ended. Code only."""
    now = now or _now()
    made = 0
    # Proposals and live trials whose session ended or is gone.
    for trial in db.scalars(select(LearningTrial).where(LearningTrial.status.in_(TRIAL_PENDING))
                            .with_for_update(skip_locked=True)):
        if _live_pack_session(db, trial.session_id) is None:
            _expire(db, trial, "the session ended")
    db.commit()
    if not enabled():
        return made
    from app.services import game_pack, memory_manager

    sessions = list(db.scalars(select(DesktopSession).where(DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))))
    for session in sessions:
        if not game_pack.is_pack_profile(session.profile_id):
            continue
        context = memory_manager.game_context(db, session)
        for item in db.scalars(select(WorkingItem).where(
                WorkingItem.user_id == session.owner_user_id, WorkingItem.context_id == context.id,
                WorkingItem.kind == "hypothesis", WorkingItem.owner == "columbina", WorkingItem.closed_at.is_(None))):
            key = f"hypothesis:{item.id}"
            seen = db.scalar(select(LearningTrialTrigger.id).where(
                LearningTrialTrigger.user_id == session.owner_user_id, LearningTrialTrigger.problem_key == key,
                LearningTrialTrigger.payload["revision"].astext == str(item.revision)).limit(1))
            linked = db.scalar(select(LearningTrial.id).where(LearningTrial.hypothesis_item_id == item.id).limit(1))
            if seen or linked:
                continue
            if consider(db, session, kind="hypothesis", text=item.text, hypothesis_item_id=item.id):
                trigger = db.scalar(select(LearningTrialTrigger).where(
                    LearningTrialTrigger.problem_key == key, LearningTrialTrigger.state == "pending"))
                if trigger is not None:
                    trigger.payload = {**trigger.payload, "revision": item.revision}
                made += 1
    db.commit()
    return made


# --------------------------------------------------------------------------- #
# Investigation: a proposal (one model call)
# --------------------------------------------------------------------------- #


def _claim_trigger(db: Session, now: datetime) -> LearningTrialTrigger | None:
    row = db.scalar(select(LearningTrialTrigger).where(
        LearningTrialTrigger.state.in_(("pending", "running")), LearningTrialTrigger.next_attempt_at <= now,
        or_(LearningTrialTrigger.lease_expires_at.is_(None), LearningTrialTrigger.lease_expires_at < now))
        .order_by(LearningTrialTrigger.next_attempt_at).limit(1).with_for_update(skip_locked=True))
    if row is None:
        db.commit()
        return None
    row.state, row.attempts, row.lease_expires_at = "running", row.attempts + 1, now + LEASE
    db.commit()
    return row


def _proposer():
    s = get_settings()
    return T.proposer(s.learning_trials_model, api_key=s.openai_api_key or "",
                      reasoning_effort=s.learning_trials_reasoning_effort, timeout=s.learning_trials_timeout_seconds)


def _pack():
    from app.ai.desktop.pack_client import PackClient

    return PackClient.from_settings()


def investigate_once(*, proposer: Callable[[str], T.Answer] | None = None, pack=None) -> bool:
    """Read one waiting trigger into a proposal (or nothing). Never starts anything."""
    with SessionLocal() as db:
        trigger = _claim_trigger(db, _now())
        if trigger is None:
            return False
        trigger_id = trigger.id
    try:
        outcome = _investigate(trigger_id, proposer=proposer, pack=pack)
    except Exception as exc:  # noqa: BLE001 - retried with backoff
        log.exception("learning trials: investigation failed")
        outcome = None
        with SessionLocal() as db:
            row = db.get(LearningTrialTrigger, trigger_id)
            if row is not None:
                give_up = row.attempts >= get_settings().learning_trials_max_attempts
                row.state = "failed" if give_up else "pending"
                row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                row.lease_expires_at = None
                row.next_attempt_at = _now() + min(MAX_BACKOFF, timedelta(seconds=30 * 2 ** row.attempts))
                db.commit()
    if outcome is not None:
        _log({"op": "investigated", "trigger": str(trigger_id), **outcome})
    return True


def _finish_trigger(db: Session, trigger: LearningTrialTrigger, result: dict) -> dict:
    trigger.state, trigger.result, trigger.lease_expires_at = "done", result, None
    db.commit()
    return result


def _bundle(db: Session, trigger: LearningTrialTrigger, session: DesktopSession, state: dict, manifest: list[dict],
            pending: LearningTrial | None) -> T.Bundle:
    from app.services import game_experience

    s = get_settings()
    evidence = [T.Line(f"x{i}", f"{e.get('kind')}: {e.get('text')}",
                       f"experience:{e['experience_id']}" if e.get("experience_id") else "")
                for i, e in enumerate(trigger.payload.get("evidence") or [], 1)]
    game = game_experience.game_for_profile(session.profile_id) or "minecraft"
    from app.services import memory_lessons
    from app.ai.learning import lessons as LS
    lessons = [T.Line(f"g{i}", f"({v.status}" + (", UNSETTLED" if v.unsettled else "") + f") {v.statement}; "
                               f"applies: {v.applies_when}; exceptions: {'; '.join(v.exceptions)}"
                               + "".join(f"; {d}" for d in LS.detail_lines(v.detail))
                               + "".join(f"; unsettled: {u}" for u in v.unsettled), f"lesson:{v.id}")
               for i, v in enumerate(memory_lessons.for_game(
                   db, session.owner_user_id, game, query=str(trigger.payload)).views, 1)]
    trials = []
    for i, (row, reading) in enumerate(_assessed(db, session.owner_user_id, game, limit=TRIALS_SHOWN), 1):
        revision = current_revision(db, row, row.selected_revision)
        trials.append(T.Line(f"t{i}", T.finding(revision.proposal, row.selected_alternative, reading.result,
                                                reading.measurements), f"trial:{row.id}"))
    pending_line = None
    if pending is not None:
        revision = current_revision(db, pending)
        deferred = "; ".join(d.get("reason") or "" for d in pending.deferrals) or "never"
        pending_line = T.Line("p1", f"{T.summary(revision.proposal)}; deferred before: {deferred}", f"trial:{pending.id}")
    position = state.get("position") if isinstance(state.get("position"), dict) else None
    skills = [f"- {m['name']}: {m.get('description', '')}"[:200] for m in manifest]
    return T.Bundle(T.SCENARIOS[SCENARIO], default_limits(), s.trial_window_seconds, s.trial_near_m,
                    trigger.payload.get("problem") or "", position, state.get("text") or "", skills, evidence,
                    lessons, trials, pending_line, trigger.payload.get("approach"))


def _assessed(db: Session, user_id: uuid.UUID, game: str, *, limit: int) -> list[tuple[LearningTrial, LearningTrialAssessment]]:
    rows = db.execute(select(LearningTrial, LearningTrialAssessment).join(
        LearningTrialAssessment, (LearningTrialAssessment.trial_id == LearningTrial.id) & LearningTrialAssessment.current)
        .where(LearningTrial.user_id == user_id, LearningTrial.game == game, LearningTrial.status == "assessed")
        .order_by(LearningTrialAssessment.created_at.desc()).limit(limit)).all()
    return [(t, a) for t, a in rows]


def _investigate(trigger_id: uuid.UUID, *, proposer=None, pack=None) -> dict | None:
    from app.ai.desktop.pack_client import PackError

    with SessionLocal() as db:
        trigger = db.get(LearningTrialTrigger, trigger_id)
        if trigger is None or trigger.state != "running":
            db.commit()
            return None
        session = _live_pack_session(db, trigger.session_id)
        if session is None:
            return _finish_trigger(db, trigger, {"outcome": "skipped", "why": "the session ended"})
        if not enabled():
            return _finish_trigger(db, trigger, {"outcome": "skipped", "why": "trials are switched off"})
        if trigger.kind == "hypothesis":
            item = db.get(WorkingItem, uuid.UUID(trigger.payload["hypothesis_item_id"]))
            if item is None or item.closed_at is not None:
                return _finish_trigger(db, trigger, {"outcome": "skipped", "why": "the hypothesis is closed"})
        try:
            client = pack or _pack()
            state, manifest = client.state(), client.manifest()
        except PackError as exc:
            raise RuntimeError(f"the game isn't reachable: {exc}") from exc
        pending = db.scalar(select(LearningTrial).where(
            LearningTrial.user_id == trigger.user_id, LearningTrial.problem_key == trigger.problem_key,
            LearningTrial.status.in_(TRIAL_PENDING)))
        bundle = _bundle(db, trigger, session, state, manifest, pending)
        text = T.render_bundle(bundle)
        db.commit()
    call = proposer or _proposer()
    answer = call(text)
    if answer.raw is None:
        raise RuntimeError(f"the proposer failed: {answer.error}")
    checked = T.check(answer.raw, bundle)
    with SessionLocal() as db:
        trigger = db.get(LearningTrialTrigger, trigger_id, with_for_update=True)
        if trigger is None or trigger.state != "running":
            db.commit()
            return None
        if checked.proposal is None:
            return _finish_trigger(db, trigger, {"outcome": "no_proposal", "why": checked.why_none,
                                                 "dropped": checked.dropped})
        session = db.get(DesktopSession, trigger.session_id)
        trial, made = _write_proposal(db, trigger, session, checked, bundle, model=getattr(call, "name", None),
                                      input_digest=T.digest(text))
        result = {"outcome": made, "trial": str(trial.id) if trial else None, "dropped": checked.dropped}
        return _finish_trigger(db, trigger, result)


def _write_proposal(db: Session, trigger: LearningTrialTrigger, session: DesktopSession, checked: T.Checked,
                    bundle: T.Bundle, *, model: str | None, input_digest: str) -> tuple[LearningTrial | None, str]:
    """Persist the proposal before her mind sees it: a new trial, or a new revision of the one waiting for this
    problem (unless the proposer says it's a materially different test: then a new trial, linked)."""
    from app.services import game_experience

    proposal = checked.proposal
    refs = bundle.refs()
    evidence = [refs[c].source for c in checked.cites if refs[c].source]
    pending = db.scalar(select(LearningTrial).where(
        LearningTrial.user_id == trigger.user_id, LearningTrial.problem_key == trigger.problem_key,
        LearningTrial.status.in_(TRIAL_PENDING)).with_for_update())
    linked = uuid.UUID(trigger.payload["trial_id"]) if trigger.payload.get("trial_id") else None
    if pending is not None and not proposal["materially_different"]:
        pending.revision += 1
        db.add(LearningTrialRevision(trial_id=pending.id, user_id=pending.user_id, revision=pending.revision,
                                     proposal=proposal, dropped=checked.dropped, reason="new evidence about the same problem",
                                     evidence=evidence, model=model, input_digest=input_digest))
        was = pending.status
        pending.status, pending.next_check_at, pending.decide_failures = "proposed", _now(), 0
        if was == "deferred":
            _settle_reminder(db, pending, "dropped", "the proposal was revised with new evidence")
            pending.deferral = None
        _event(db, pending, "revised", {"revision": pending.revision, "trigger": str(trigger.id), "was": was})
        db.flush()
        return pending, "revised"
    if pending is not None:
        linked = linked or pending.id
        # A different test of the same question: its own problem key, so both can wait for her mind.
        key = f"{trigger.problem_key}:{uuid.uuid4().hex[:6]}"
    else:
        key = trigger.problem_key
    trial = LearningTrial(
        user_id=trigger.user_id, session_id=session.id, game=game_experience.game_for_profile(session.profile_id)
        or "minecraft", scenario=proposal["scenario"], problem_key=key[:160], problem=proposal["problem"],
        status="proposed", revision=1, linked_trial_id=linked, limits=proposal["limits"])
    db.add(trial)
    db.flush()
    trial.hypothesis_item_id = _hypothesis(db, trigger, session, trial, proposal)
    db.add(LearningTrialRevision(trial_id=trial.id, user_id=trial.user_id, revision=1, proposal=proposal,
                                 dropped=checked.dropped, reason=f"investigated: {trigger.kind}", evidence=evidence,
                                 model=model, input_digest=input_digest))
    _event(db, trial, "proposed", {"trigger": str(trigger.id), "kind": trigger.kind, "linked": str(linked) if linked
                                   else None})
    db.flush()
    return trial, "proposed"


def _hypothesis(db: Session, trigger: LearningTrialTrigger, session: DesktopSession, trial: LearningTrial,
                proposal: dict) -> uuid.UUID | None:
    """Her hypothesis, kept by memory: the one that triggered it, the one a linked trial tests, or a new one."""
    from app.services import memory_manager

    if trigger.payload.get("hypothesis_item_id"):
        return uuid.UUID(trigger.payload["hypothesis_item_id"])
    if trial.linked_trial_id:
        linked = db.get(LearningTrial, trial.linked_trial_id)
        if linked is not None and linked.hypothesis_item_id:
            item = db.get(WorkingItem, linked.hypothesis_item_id)
            if item is not None and item.closed_at is None:
                return item.id
    try:
        with db.begin_nested():
            source = None
            experience = next((e.get("experience_id") for e in reversed(trigger.payload.get("evidence") or [])
                               if e.get("experience_id")), None)
            if experience:
                source = memory_manager.Source("experience", int(experience))
            item = memory_manager.add_item(
                db, trial.user_id, kind="hypothesis", text_=proposal["hypothesis"], owner="columbina",
                epistemic="inferred", context_id=memory_manager.game_context(db, session).id, source=source,
                reason=f"learning proposed a trial to test it ({proposal['problem']})"[:500])
            return item.id
    except Exception:  # noqa: BLE001 - the trial stands without memory's copy
        log.exception("learning trials: keeping the hypothesis in memory failed")
        return None


# --------------------------------------------------------------------------- #
# Her mind: whether and when, and which way
# --------------------------------------------------------------------------- #


def _claim_trial(db: Session, now: datetime, statuses: tuple[str, ...]) -> LearningTrial | None:
    row = db.scalar(select(LearningTrial).where(
        LearningTrial.status.in_(statuses), LearningTrial.next_check_at <= now,
        or_(LearningTrial.lease_expires_at.is_(None), LearningTrial.lease_expires_at < now))
        .order_by(LearningTrial.next_check_at).limit(1).with_for_update(skip_locked=True))
    if row is None:
        db.commit()
        return None
    row.lease_expires_at = now + LEASE
    db.commit()
    return row


def _release(db: Session, trial: LearningTrial, *, after: timedelta) -> None:
    trial.lease_expires_at = None
    trial.next_check_at = _now() + after


PRESENT_TTL = 600  # a proposal waits this long for her mind to consider it, then is presented again


def present_once(*, decide=None) -> bool:
    """Hand one waiting proposal to her mind: a durable wake in the game's own conversation. Her decision arrives
    through her ``decide_proposal`` tool (``on_pursue`` / ``on_decline`` / ``on_defer``); a wake that ends without
    one, or never runs, is ``undecided``. ``decide``: tests decide in place of the wake."""
    with SessionLocal() as db:
        trial = _claim_trial(db, _now(), ("proposed",))
        if trial is None:
            return False
        trial_id = trial.id
    try:
        _present(trial_id, decide=decide)
    except Exception:  # noqa: BLE001 - kept, presented again later
        log.exception("learning trials: presenting failed")
        with SessionLocal() as db:
            row = db.get(LearningTrial, trial_id)
            if row is not None:
                _release(db, row, after=timedelta(minutes=1))
                db.commit()
    return True


def proposal_text(proposal: dict, *, deferred_because: list[str] = ()) -> tuple[str, list[str]]:
    """The proposal as her mind reads it, and its option keys."""
    views = T.proposal_views(proposal, deferred_because=list(deferred_because))
    lines = ["An experiment you could run in your game, proposed by your own learning from what happened there. "
             "It competes with what your hands are doing now (they do one thing at a time). Options:"]
    lines += [f"[{v['key']}] {v['goal']}: {v['purpose']}" for v in views]
    lines.append("Decide with decide_proposal: accept one option, decline, or defer. Say something to the user only "
                 "if you choose to.")
    return "\n".join(lines), [v["key"] for v in views]


def _present(trial_id: uuid.UUID, *, decide=None) -> None:
    from app.services import autonomous_speech

    with SessionLocal() as db:
        trial = db.get(LearningTrial, trial_id, with_for_update=True)
        if trial is None or trial.status != "proposed":
            db.commit()
            return
        session = _live_pack_session(db, trial.session_id)
        if session is None:
            _expire(db, trial, "the session ended")
            db.commit()
            return
        revision = current_revision(db, trial)
        text, options = proposal_text(revision.proposal,
                                      deferred_because=[d.get("reason") or "" for d in trial.deferrals or []])
        n = trial.presentations + 1
        trial.presentations, trial.presented_at, trial.status = n, _now(), "presented"
        trial.next_check_at = _now() + timedelta(seconds=PRESENT_TTL)
        trial.lease_expires_at = None
        _event(db, trial, "presented", {"revision": trial.revision, "presentation": n})
        user_id, key = trial.user_id, f"trial:{trial.id}:{trial.revision}"
        db.commit()
        if decide is not None:
            decide(db, trial_id=trial_id, revision=trial.revision, options=options, text=text)
            outcome = "decided in place"
        else:
            outcome = autonomous_speech.submit(
                db, user_id=user_id, activity_id=session.id, source="minecraft",
                event_id=f"{key}:{n}", text=text, reasons=[f"trial: {revision.proposal.get('hypothesis', '')[:200]}"],
                commentary_mode=session.commentary_mode, ttl=PRESENT_TTL, wake="proposal",
                extra={"trial": str(trial_id), "revision": trial.revision, "options": options})
    if not outcome.startswith(("Proposed", "decided")):
        undecided(trial_id, why=outcome)
    _log({"op": "presented", "trial": str(trial_id), "outcome": outcome})


def unanswered_once() -> int:
    """Proposals handed to her mind whose wake never came back with a decision in time (dropped as stale, a
    worker restart): ``undecided``, so they are presented again. Restart-safe: the trial row is the record."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(LearningTrial.id).where(
            LearningTrial.status == "presented", LearningTrial.next_check_at <= _now()).limit(20)))
    for trial_id in ids:
        undecided(trial_id, why="no decision came back in time")
    return len(ids)


def undecided(trial_id: uuid.UUID, *, why: str) -> None:
    """Her mind was woken with the proposal and made no decision about it (or couldn't be woken with it): it is
    presented again later, and after DECIDE_TRIES put off with that reason. Never a decision made for her."""
    with SessionLocal() as db:
        trial = db.get(LearningTrial, trial_id, with_for_update=True)
        if trial is None or trial.status != "presented":
            db.commit()
            return
        trial.decide_failures += 1
        _event(db, trial, "undecided", {"why": why[:300]})
        if trial.decide_failures >= DECIDE_TRIES:
            _defer(db, trial, reason=f"her mind made no decision about it ({why})"[:300], condition=None,
                   decision_id=None, expiry=None)
        else:
            trial.status = "proposed"
            _release(db, trial, after=min(MAX_BACKOFF, timedelta(seconds=60 * 2 ** trial.decide_failures)))
        db.commit()


def settle_presented(*, user_id: uuid.UUID, trial_id: str | uuid.UUID, decided: bool, why: str) -> None:
    """After the wake that carried a proposal ended: no decision in it is ``undecided``."""
    if not decided:
        undecided(uuid.UUID(str(trial_id)), why=why)


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def parse_key(key: str | None) -> tuple[uuid.UUID, int, str] | None:
    """A proposal [P*]'s key: ``trial:<id>:<revision>:<alternative>``."""
    parts = (key or "").split(":")
    if len(parts) != 4 or parts[0] != "trial":
        return None
    try:
        return uuid.UUID(parts[1]), int(parts[2]), parts[3]
    except ValueError:
        return None


def on_pursue(db: Session, user_id: uuid.UUID, key: str, *, reason: str, decision_id: str | None,
              goal_handling: str | None = None) -> str:
    """Her mind took an alternative up: the trial is selected (its limits fixed) and preparation begins on the
    next check. Only the revision it was shown, only while it waits for a decision."""
    parsed = parse_key(key)
    if parsed is None:
        return "not carried out: not a trial proposal"
    trial_id, revision, alt = parsed
    trial = db.scalar(select(LearningTrial).where(LearningTrial.id == trial_id, LearningTrial.user_id == user_id)
                      .with_for_update())
    if trial is None or trial.status not in ("proposed", "presented") or trial.revision != revision:
        db.commit()
        return "not carried out: the proposal changed or was decided meanwhile"
    proposal = current_revision(db, trial).proposal
    if T.alternative(proposal, alt) is None:
        db.commit()
        return "not carried out: no such alternative"
    busy = db.scalar(select(LearningTrial.id).where(LearningTrial.session_id == trial.session_id,
                                                    LearningTrial.status.in_(TRIAL_LIVE)).limit(1))
    if busy is not None:
        db.commit()
        return "not carried out: another experiment is already under way in this game"
    now = _now()
    trial.selection = {"decision_id": decision_id, "alternative": alt, "revision": revision,
                       "recommended": alt == proposal["recommendation"],
                       "reason": (reason or "").strip()[:300],
                       "other_goal_handling": goal_handling, "at": now.isoformat()}
    trial.selected_alternative, trial.selected_revision = alt, revision
    trial.limits = proposal["limits"]
    trial.costs = {"prep_seconds": 0.0, "test_seconds": 0.0, "attempts": 0, "test_blocks": 0, "prep_blocks": 0}
    trial.status, trial.selected_at, trial.active_since, trial.next_check_at = "selected", now, now, now
    trial.lease_expires_at = None
    if trial.deferral:
        trial.deferral = None
    _settle_reminder(db, trial, "done", "she chose to run it")
    _event(db, trial, "selected", trial.selection, decision_attempt_id=_uuid(decision_id))
    db.commit()
    wake()
    return f"experiment selected: {(T.alternative(proposal, alt) or {}).get('action')}"


def on_decline(db: Session, user_id: uuid.UUID, key: str, *, reason: str, decision_id: str | None) -> str:
    parsed = parse_key(key)
    trial = _pending(db, user_id, parsed)
    if trial is None:
        return "not carried out: the proposal changed or was decided meanwhile"
    trial.status, trial.closed_at = "rejected", _now()
    trial.stop_reason = (reason or "").strip()[:300] or "declined"
    _settle_reminder(db, trial, "dropped", "she turned it down")
    _event(db, trial, "rejected", {"reason": trial.stop_reason}, decision_attempt_id=_uuid(decision_id))
    db.commit()
    return "proposal declined"


def on_defer(db: Session, user_id: uuid.UUID, key: str, *, reason: str, decision_id: str | None,
             condition: dict | None = None, expiry: str | None = None) -> str:
    """Her mind put it off. ``condition``: when she chose to look again (``time_reached`` with ``at``, or
    ``user_returns``); with none, learning's own condition (what it competes with finishing)."""
    parsed = parse_key(key)
    trial = _pending(db, user_id, parsed)
    if trial is None:
        return "not carried out: the proposal changed or was decided meanwhile"
    _defer(db, trial, reason=(reason or "").strip()[:300] or "put off", condition=condition,
           decision_id=decision_id, expiry=expiry)
    db.commit()
    return "proposal deferred"


def _pending(db: Session, user_id: uuid.UUID, parsed) -> LearningTrial | None:
    if parsed is None:
        return None
    trial_id, revision, _ = parsed
    trial = db.scalar(select(LearningTrial).where(LearningTrial.id == trial_id, LearningTrial.user_id == user_id)
                      .with_for_update())
    if trial is None or trial.status not in ("proposed", "presented") or trial.revision != revision:
        db.commit()
        return None
    return trial


_EXPIRY = {"30_minutes": timedelta(minutes=30), "2_hours": timedelta(hours=2), "today": timedelta(hours=12),
           "this_week": timedelta(days=7)}


def _default_condition(db: Session, trial: LearningTrial) -> dict:
    """Learning's condition when her mind named none: the goal it competed with finishes, or what it needs is in
    hand."""
    session = db.get(DesktopSession, trial.session_id) if trial.session_id else None
    proposal = current_revision(db, trial).proposal
    rec = T.alternative(proposal, proposal["recommendation"]) or {}
    out: dict[str, Any] = {"kind": "any", "by": "learning", "of": []}
    if session is not None and session.goal_item_id is not None:
        out["of"].append({"kind": "goal_finished", "goal": str(session.goal_item_id)})
    if rec.get("prerequisites"):
        out["of"].append({"kind": "prerequisites_met", "items": rec["prerequisites"]})
    if not out["of"]:
        out["of"].append({"kind": "time_reached", "after_seconds": 600})
    return out


def _defer(db: Session, trial: LearningTrial, *, reason: str, condition: dict | None, decision_id: str | None,
           expiry: str | None) -> None:
    now = _now()
    condition = condition or _default_condition(db, trial)
    if condition.get("kind") == "user_returns":
        session = db.get(DesktopSession, trial.session_id) if trial.session_id else None
        condition["after_sequence"] = _last_user_sequence(db, session)
    if condition.get("kind") == "any":
        for part in condition["of"]:
            if part["kind"] == "time_reached" and "after_seconds" in part:
                part["at"] = (now + timedelta(seconds=part.pop("after_seconds"))).isoformat()
    expires = now + _EXPIRY.get(expiry or "", timedelta(hours=get_settings().trial_defer_hours))
    trial.deferral = {"reason": reason, "condition": condition, "since": now.isoformat(),
                      "expires_at": expires.isoformat(), "decision_id": decision_id, "revision": trial.revision}
    trial.deferrals = [*trial.deferrals, trial.deferral]
    trial.status = "deferred"
    trial.next_check_at = now + DEFERRED_EVERY
    _remind(db, trial, reason, condition, expires)
    _event(db, trial, "deferred", trial.deferral, decision_attempt_id=_uuid(decision_id))


def _last_user_sequence(db: Session, session: DesktopSession | None) -> int:
    if session is None or session.conversation_id is None:
        return 0
    return db.scalar(select(func.max(ChatMessage.conversation_sequence)).where(
        ChatMessage.conversation_id == session.conversation_id, ChatMessage.role == "user")) or 0


def condition_words(condition: dict | None) -> str:
    if not condition:
        return "when things change"
    kind = condition.get("kind")
    if kind == "any":
        return " or ".join(condition_words(c) for c in condition.get("of") or [])
    return {"goal_finished": "the goal it competes with is finished",
            "prerequisites_met": "she has " + ", ".join(f"{p['count']} {p['item']}" for p in condition.get("items") or []),
            "time_reached": f"after {(condition.get('at') or '')[:16].replace('T', ' ')} UTC",
            "work_finished": "the work it waits for has finished",
            "user_returns": "the user writes again"}.get(kind, kind or "")


def _remind(db: Session, trial: LearningTrial, reason: str, condition: dict, expires: datetime) -> None:
    """A deferral's reminder in working memory: an intention of hers, with her own reason, through memory's
    own owner. Never fails the deferral."""
    from app.services import memory_manager

    session = db.get(DesktopSession, trial.session_id) if trial.session_id else None
    if session is None:
        return
    proposal = current_revision(db, trial).proposal
    try:
        with db.begin_nested():
            item = memory_manager.add_item(
                db, trial.user_id, kind="intention", owner="columbina", epistemic="inferred",
                text_=f"Reconsider the experiment \"{proposal['hypothesis'][:160]}\" when {condition_words(condition)}",
                context_id=memory_manager.game_context(db, session).id, due_at=expires,
                reason=f"put off: {reason}"[:500])
            trial.reminder_item_id = item.id
    except Exception:  # noqa: BLE001
        log.exception("learning trials: keeping the deferral's reminder failed")


def _settle_reminder(db: Session, trial: LearningTrial, status: str, reason: str) -> None:
    from app.services import memory_manager

    if trial.reminder_item_id is None:
        return
    try:
        with db.begin_nested():
            item = db.get(WorkingItem, trial.reminder_item_id)
            if item is not None and item.closed_at is None:
                memory_manager.resolve(db, trial.user_id, item.id, status=status, reason=reason, background=True)
    except Exception:  # noqa: BLE001 - it lapses on its own
        log.exception("learning trials: closing the reminder failed")
    trial.reminder_item_id = None


def _expire(db: Session, trial: LearningTrial, why: str) -> None:
    trial.status, trial.closed_at, trial.stop_reason = "expired", _now(), why
    _settle_reminder(db, trial, "dropped", why)
    _event(db, trial, "expired", {"why": why})


def _condition_met(db: Session, trial: LearningTrial, condition: dict, state: Callable[[], dict | None]) -> str | None:
    kind = condition.get("kind")
    if kind == "any":
        for part in condition.get("of") or []:
            why = _condition_met(db, trial, part, state)
            if why:
                return why
        return None
    if kind == "goal_finished":
        goal = db.get(WorkingItem, uuid.UUID(condition["goal"]))
        session = db.get(DesktopSession, trial.session_id) if trial.session_id else None
        if goal is None or goal.closed_at is not None:
            return "the goal it competed with is closed"
        if session is not None and session.goal_item_id != goal.id:
            return "the game plays for another goal now"
        return None
    if kind == "prerequisites_met":
        from app.ai.desktop import step_check

        now = state()
        if now is None:
            return None
        if all(step_check.met({"kind": "has", "what": p["item"], "count": p["count"]}, now, None)
               for p in condition.get("items") or []):
            return "she has what it needs"
        return None
    if kind == "time_reached":
        at = condition.get("at") or condition.get("not_before")
        try:
            when = datetime.fromisoformat(at) if "T" in at else datetime.strptime(at, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return "the time came" if _now() >= when else None
    if kind == "work_finished":
        key = condition.get("work") or ""
        if key.startswith("coordinator:"):
            work = db.get(CoordinatorWork, uuid.UUID(key.split(":", 1)[1]))
            return "the work finished" if work is None or work.status not in ("queued", "running",
                                                                             "waiting_for_memory") else None
        if key.startswith("step:"):
            session = db.get(DesktopSession, trial.session_id) if trial.session_id else None
            step = _active_step(session) if session else None
            return "the step finished" if step is None or f"step:{session.id}:{step.get('id')}" != key else None
        return None
    if kind == "user_returns":
        session = db.get(DesktopSession, trial.session_id) if trial.session_id else None
        return "the user wrote again" if _last_user_sequence(db, session) > int(condition.get("after_sequence") or 0) \
            else None
    return None


def reconsider_once(*, pack=None) -> int:
    """Deferred proposals whose condition came true go back to her mind (once); expired ones close."""
    changed = 0
    with SessionLocal() as db:
        rows = list(db.scalars(select(LearningTrial).where(
            LearningTrial.status == "deferred", LearningTrial.next_check_at <= _now())
            .with_for_update(skip_locked=True).limit(20)))
        cached: dict = {}

        def state() -> dict | None:
            if "state" not in cached:
                try:
                    cached["state"] = (pack or _pack()).state()
                except Exception:  # noqa: BLE001 - unknown, not met
                    cached["state"] = None
            return cached["state"]

        for trial in rows:
            deferral = trial.deferral or {}
            if _live_pack_session(db, trial.session_id) is None:
                _expire(db, trial, "the session ended")
                changed += 1
                continue
            if _now() >= datetime.fromisoformat(deferral.get("expires_at") or _now().isoformat()):
                _expire(db, trial, "kept as long as it was deferred for, and never reconsidered")
                changed += 1
                continue
            why = _condition_met(db, trial, deferral.get("condition") or {}, state)
            if why:
                trial.status, trial.next_check_at, trial.decide_failures = "proposed", _now(), 0
                _settle_reminder(db, trial, "done", f"reconsidering: {why}")
                _event(db, trial, "reconsidered", {"why": why, "deferral": deferral})
                changed += 1
            else:
                trial.next_check_at = _now() + DEFERRED_EVERY
        db.commit()
    return changed


# --------------------------------------------------------------------------- #
# Preparation and execution
# --------------------------------------------------------------------------- #


def _spent_seconds(trial: LearningTrial, now: datetime) -> float:
    """Active seconds so far (preparation and test; paused stretches don't count)."""
    costs = trial.costs or {}
    total = float(costs.get("prep_seconds") or 0) + float(costs.get("test_seconds") or 0)
    if trial.active_since is not None and trial.status in ("selected", "preparing"):
        total += max(0.0, (now - _aware(trial.active_since)).total_seconds())
    elif trial.status == "executing" and trial.exec_started_at is not None:
        total += max(0.0, (now - _aware(trial.exec_started_at)).total_seconds())
    return total


def _remaining(trial: LearningTrial, now: datetime) -> dict:
    limits, costs = trial.limits, trial.costs or {}
    return {"attempts": limits["attempts"] - int(costs.get("attempts") or 0),
            "seconds": limits["seconds"] - _spent_seconds(trial, now),
            "blocks": limits["blocks"] - int(costs.get("test_blocks") or 0) - int(costs.get("prep_blocks") or 0)}


def missing_items(alternative: dict, state: dict | None) -> list[dict]:
    from app.ai.desktop import step_check

    if state is None:
        return list(alternative.get("prerequisites") or [])
    return [p for p in alternative.get("prerequisites") or []
            if not step_check.met({"kind": "has", "what": p["item"], "count": p["count"]}, state, None)]


def readiness(proposal: dict, alternative: dict, state: dict | None, *, near_m: float) -> str | None:
    """Why she isn't ready to run the test now, read off the game, or None."""
    import math

    if not isinstance(state, dict):
        return "the game isn't reachable"
    missing = missing_items(alternative, state)
    if missing:
        return "she still needs " + ", ".join(f"{p['count']} {p['item']}" for p in missing)
    p, d = state.get("position"), proposal["destination"]
    if not isinstance(p, dict):
        return "her position isn't known"
    if math.dist((p["x"], p["z"]), (d["x"] + 0.5, d["z"] + 0.5)) > near_m:
        return f"she is more than {near_m:g} m from the obstacle"
    return None


def advance_once(*, pack=None) -> int:
    """Move every due live trial on: readiness, preparation, starting, budget, the observation window. Cheap, code
    only; the assessment (a model call) is ``assess_once``."""
    moved = 0
    for _ in range(10):
        with SessionLocal() as db:
            trial = _claim_trial(db, _now(), ("selected", "preparing", "executing", "observing"))
            if trial is None:
                break
            trial_id = trial.id
        try:
            moved += _advance(trial_id, pack=pack)
        except Exception:  # noqa: BLE001 - checked again soon
            log.exception("learning trials: advancing failed")
            with SessionLocal() as db:
                row = db.get(LearningTrial, trial_id)
                if row is not None:
                    _release(db, row, after=timedelta(seconds=30))
                    db.commit()
    return moved


def _advance(trial_id: uuid.UUID, *, pack=None) -> int:
    from app.ai.desktop.pack_client import PackError

    with SessionLocal() as db:
        trial = db.get(LearningTrial, trial_id, with_for_update=True)
        now = _now()
        if trial.status == "executing":
            return _watch(db, trial, now)
        if trial.status == "observing":
            if trial.window_ends_at is not None and now < _aware(trial.window_ends_at):
                _release(db, trial, after=min(CHECK_EVERY, _aware(trial.window_ends_at) - now))
                db.commit()
                return 0
            end = None
            if _live_pack_session(db, trial.session_id) is not None and trial.exec_started_at is not None:
                try:
                    end = (pack or _pack()).state()
                except PackError:
                    end = None
            _event(db, trial, "observed", {"window_end_state": _brief_state(end), "read": end is not None})
            trial.status, trial.next_check_at, trial.lease_expires_at = "assessing", now, None
            db.commit()
            wake()
            return 1
        # selected / preparing
        session = _live_pack_session(db, trial.session_id)
        if session is None:
            _end_before_test(db, trial, "the session ended before the test could start")
            db.commit()
            return 1
        remaining = _remaining(trial, now)
        if remaining["seconds"] <= 0:
            _end_before_test(db, trial, "the time for this experiment ran out while getting ready", ending="limit")
            db.commit()
            return 1
        proposal = current_revision(db, trial, trial.selected_revision).proposal
        alt = T.alternative(proposal, trial.selected_alternative)
        try:
            state = (pack or _pack()).state()
        except PackError:
            state = None
        why = None if session.state == "running" else f"the game is {session.state}"
        why = why or readiness(proposal, alt, state, near_m=get_settings().trial_near_m)
        from app.services import game_practice

        busy = game_practice.active(db, session.id)
        if why is None and busy is not None:
            why = f'her body is busy learning "{busy.task}"'
        if why is None:
            _start(db, trial, session, proposal, alt)
            db.commit()
            return 1
        if trial.status == "selected" or (trial.costs or {}).get("asked_for") != why:
            _prepare(db, trial, session, alt, state, why)
        _release(db, trial, after=CHECK_EVERY)
        db.commit()
        return 0


def _brief_state(state: dict | None) -> dict | None:
    if not isinstance(state, dict):
        return None
    return {k: state.get(k) for k in ("position", "health", "food", "on_ground", "in_water")} | {
        "inventory": {i["name"]: i["count"] for i in state.get("inventory") or [] if isinstance(i, dict)}}


def _prepare(db: Session, trial: LearningTrial, session: DesktopSession, alt: dict, state: dict | None,
             why: str) -> None:
    """Not ready: what's missing goes to her planner, which arranges it; she prepares with her hands. Asked once
    per reason (a new reason asks again)."""
    from app.services import agent_events, game_planning

    trial.status = "preparing"
    trial.costs = {**(trial.costs or {}), "asked_for": why}
    missing = missing_items(alt, state)
    asked = False
    if missing or "m from the obstacle" in why:
        if game_planning.handles(session):
            game_planning.request(db, session, f"trial: {why}"[:400])
            asked = True
    _event(db, trial, "preparation", {"not_ready": why, "missing": missing, "planner_asked": asked})
    if asked:
        db.flush()
        agent_events.wake()


def planning_lines(db: Session, session: DesktopSession) -> list[str]:
    """For her planner's input: an experiment she chose, and what it still needs. The planner arranges
    the prerequisites; it never runs the test (it starts by itself once she's ready)."""
    trial = db.scalar(select(LearningTrial).where(LearningTrial.session_id == session.id,
                                                  LearningTrial.status.in_(("selected", "preparing"))))
    if trial is None:
        return []
    proposal = current_revision(db, trial, trial.selected_revision).proposal
    alt = T.alternative(proposal, trial.selected_alternative) or {}
    d = proposal["destination"]
    needs = ", ".join(f"{p['count']} {p['item']}" for p in alt.get("prerequisites") or []) or "nothing to carry"
    left = _remaining(trial, _now())
    return [f"She chose to run an experiment: {alt.get('action')} to get up to x {d['x']} y {d['y']} "
            f"z {d['z']}. It needs {needs}, and her within {get_settings().trial_near_m:g} m of that spot.",
            f"Not ready yet: {(trial.costs or {}).get('asked_for') or 'being checked'}. Add steps that get what's "
            "missing (and her back there, if she's far), before or after the current step as fits the goal; don't "
            "do the experiment itself: it starts by itself once she's ready, and her hands wait while it runs.",
            f"Time left for it, getting ready included: {max(0, int(left['seconds']))} s."]


def _start(db: Session, trial: LearningTrial, session: DesktopSession, proposal: dict, alt: dict) -> None:
    """Ready: her body runs it (game practice, origin "trial"), with what's left of its budget."""
    from app.services import game_practice

    now = _now()
    prep = max(0.0, (now - _aware(trial.active_since or now)).total_seconds())
    blocks = _prep_blocks(db, session.id, _aware(trial.active_since or now), now)
    costs = dict(trial.costs or {})
    costs["prep_seconds"] = round(float(costs.get("prep_seconds") or 0) + prep, 1)
    costs["prep_blocks"] = int(costs.get("prep_blocks") or 0) + blocks
    costs.pop("asked_for", None)
    trial.costs = costs
    attempt = GameSkillAttempt(user_id=trial.user_id, session_id=session.id, game=trial.game,
                               task=T.task_words(proposal, alt["key"]), origin="trial")
    db.add(attempt)
    db.flush()
    trial.attempt_id = attempt.id
    trial.status, trial.exec_started_at, trial.active_since = "executing", now, None
    _event(db, trial, "execution_started", {"prep_seconds": prep, "prep_blocks": blocks,
                                            "remaining": _remaining(trial, now)}, skill_attempt_id=attempt.id)
    _release(db, trial, after=CHECK_EVERY)
    db.flush()
    game_practice.wake()


def _prep_blocks(db: Session, session_id: uuid.UUID, since: datetime, until: datetime) -> int:
    """Blocks her hands placed while getting ready (place skills that ended done)."""
    return db.scalar(select(func.count()).select_from(GameExperience).where(
        GameExperience.session_id == session_id, GameExperience.kind == "skill_outcome",
        GameExperience.payload["skill"].astext == "place", GameExperience.payload["status"].astext == "done",
        GameExperience.occurred_at >= since, GameExperience.occurred_at <= until)) or 0


def spec_for(db: Session, attempt: GameSkillAttempt):
    """What her body needs to run a trial's attempt (game practice): the approach, destination, what's left of
    the budget and the readiness check."""
    from app.ai.desktop.practice import TrialSpec

    trial = db.scalar(select(LearningTrial).where(LearningTrial.attempt_id == attempt.id))
    if trial is None or trial.status != "executing":
        return None
    proposal = current_revision(db, trial, trial.selected_revision).proposal
    alt = T.alternative(proposal, trial.selected_alternative)
    left = _remaining(trial, _now())
    near = get_settings().trial_near_m
    return TrialSpec(approach=alt["approach"], destination=proposal["destination"], radius=proposal["radius"],
                     attempts=max(1, left["attempts"]), seconds=max(10.0, left["seconds"]),
                     blocks=max(0, left["blocks"]), ready=lambda state: readiness(proposal, alt, state, near_m=near))


def _watch(db: Session, trial: LearningTrial, now: datetime) -> int:
    """While her body runs it: an attempt past its time is asked to stop; an attempt that ended without telling us
    (a restart) is collected from what it saved, never repeated."""
    from app.services import game_pack

    attempt = db.get(GameSkillAttempt, trial.attempt_id) if trial.attempt_id else None
    if attempt is None:
        _collect(db, trial, None, ending="interrupted", reason="its execution record is gone")
        db.commit()
        return 1
    if attempt.status not in ("queued", "running"):
        if not db.scalar(select(LearningTrialEvent.id).where(LearningTrialEvent.trial_id == trial.id,
                                                             LearningTrialEvent.kind == "execution_ended",
                                                             LearningTrialEvent.skill_attempt_id == attempt.id)):
            _collect(db, trial, attempt, ending="interrupted",
                     reason=f"recovered after the fact: {attempt.result or attempt.status}")
            db.commit()
            return 1
        _release(db, trial, after=CHECK_EVERY)
        db.commit()
        return 0
    if _spent_seconds(trial, now) > trial.limits["seconds"] + GRACE.total_seconds() and not attempt.stop_requested:
        attempt.stop_requested = True
        # Its run ends as "stopped"; the trial records the agreed limit it reached.
        trial.costs = {**(trial.costs or {}), "limit_stop": True, "stop_reason": "the time for this experiment ran out"}
        _event(db, trial, "limit", {"what": "time", "spent": _spent_seconds(trial, now)}, skill_attempt_id=attempt.id)
        db.commit()
        game_pack.stop_driver_skill()
        return 1
    _release(db, trial, after=CHECK_EVERY)
    db.commit()
    return 0


def on_execution_finished(db: Session, attempt: GameSkillAttempt, result) -> str | None:
    """Game practice finished a trial's attempt (in its transaction): its tries, how it ended, and what it says
    about readiness. Returns the line for her mind, or None."""
    trial = db.scalar(select(LearningTrial).where(LearningTrial.attempt_id == attempt.id).with_for_update())
    if trial is None:
        return None
    ending = result.ending or "completed"
    if ending == "not_started" and trial.status == "paused":
        trial.attempt_id = None  # paused before its test began: nothing ran
        _event(db, trial, "readiness", {"not_started": result.reason}, skill_attempt_id=attempt.id)
        return None
    if ending == "not_started" and (result.reason or "").startswith("not ready"):
        # Checked right before the test, and it wasn't ready after all: preparation goes on.
        trial.status, trial.attempt_id, trial.active_since = "preparing", None, _now()
        trial.exec_started_at = None
        trial.costs = {**(trial.costs or {}), "asked_for": result.reason}
        _event(db, trial, "readiness", {"not_ready": result.reason}, skill_attempt_id=attempt.id)
        _release(db, trial, after=CHECK_EVERY)
        return None
    _collect(db, trial, attempt, ending=ending, reason=result.reason, result=result)
    return f"ended an experiment ({ending}): {result.reason}"[:300]


def _collect(db: Session, trial: LearningTrial, attempt: GameSkillAttempt | None, *, ending: str, reason: str,
             result=None) -> None:
    """One execution segment's record, then the trial watches out its window (or waits, paused)."""
    now = _now()
    tries = list((result.tries if result is not None else (attempt.tries if attempt else None)) or [])
    ran = [t for t in tries if t.get("program")]
    seconds = result.seconds if result is not None and result.seconds is not None else (
        (_aware(attempt.ended_at or now) - _aware(attempt.started_at or trial.exec_started_at or now)).total_seconds()
        if attempt else 0.0)
    placed = 0
    for t in ran:
        counted = T._placed(t.get("program"))
        placed += counted or 0
    costs = dict(trial.costs or {})
    costs["test_seconds"] = round(float(costs.get("test_seconds") or 0) + max(0.0, seconds or 0.0), 1)
    costs["attempts"] = int(costs.get("attempts") or 0) + len(ran)
    costs["test_blocks"] = int(costs.get("test_blocks") or 0) + placed
    trial.costs = costs
    start = result.start_state if result is not None else None
    if start is None and ran:
        start = {"position": dict(zip("xyz", (ran[0].get("facts") or {}).get("from") or [])) or None}
    _event(db, trial, "execution_ended", {
        "ending": ending, "reason": reason, "tries": _slim(tries), "seconds": seconds, "placed": placed,
        "start_state": _brief_state(start) if start and start.get("position") else None,
        "other_approach": getattr(result, "other_approach", None), "recovered": result is None},
        skill_attempt_id=attempt.id if attempt else None)
    trial.attempt_id = None
    if trial.status == "paused":
        trial.exec_started_at = None
        _release(db, trial, after=CHECK_EVERY)
        return
    asked = costs.pop("stop_reason", None)  # her own words (or the limit), when it was stopped
    if costs.pop("limit_stop", False) and ending == "stopped":
        ending = "limit"
    trial.costs = costs
    trial.ending, trial.stop_reason = ending, (asked or reason or "")[:500] or None
    trial.exec_ended_at = now
    trial.status = "observing"
    window = int(current_revision(db, trial, trial.selected_revision).proposal.get("window_seconds") or 0)
    trial.window_ends_at = now + timedelta(seconds=window)
    _release(db, trial, after=timedelta(seconds=min(window, 5)))
    if ending == "approach_changed" and trial.session_id:
        session = db.get(DesktopSession, trial.session_id)
        if session is not None:
            consider(db, session, kind="approach_changed", text=reason, trial_id=trial.id,
                     approach=getattr(result, "other_approach", None))


def _slim(tries: list[dict]) -> list[dict]:
    """Each try as the assessment needs it (code, its program's result, the counted facts, the verdict)."""
    out = []
    for t in tries:
        out.append({"n": t.get("n"), "approach": t.get("approach"), "program": t.get("program"),
                    "facts": t.get("facts"), "verdict": t.get("verdict")})
    return json.loads(json.dumps(out, default=str))


def _end_before_test(db: Session, trial: LearningTrial, why: str, *, ending: str = "not_started") -> None:
    """Selected, but the test never started: still assessed (as "never started")."""
    now = _now()
    if trial.active_since is not None:
        trial.costs = {**(trial.costs or {}), "prep_seconds": round(float((trial.costs or {}).get("prep_seconds") or 0)
                                                                    + (now - _aware(trial.active_since)).total_seconds(), 1)}
    trial.costs = {k: v for k, v in (trial.costs or {}).items() if k != "asked_for"}
    trial.ending, trial.stop_reason = "not_started" if ending == "not_started" else ending, why
    trial.status, trial.active_since, trial.window_ends_at = "observing", None, now
    _event(db, trial, "ended_before_test", {"why": why, "ending": ending})
    _release(db, trial, after=timedelta(0))


# --------------------------------------------------------------------------- #
# Changing her mind: pause, resume, stop
# --------------------------------------------------------------------------- #


def _live(db: Session, user_id: uuid.UUID, trial_id: uuid.UUID) -> LearningTrial | None:
    return db.scalar(select(LearningTrial).where(LearningTrial.id == trial_id, LearningTrial.user_id == user_id)
                     .with_for_update())


def pause(db: Session, user_id: uuid.UUID, trial_id: uuid.UUID, *, reason: str) -> str:
    """Hold it: an attempt running stops (its tries are kept), the budget's clock stops."""
    from app.services import game_pack

    trial = _live(db, user_id, trial_id)
    if trial is None or trial.status not in ("selected", "preparing", "executing"):
        db.commit()
        return "not carried out: no experiment to pause"
    now = _now()
    if trial.status in ("selected", "preparing") and trial.active_since is not None:
        trial.costs = {**(trial.costs or {}), "prep_seconds": round(float((trial.costs or {}).get("prep_seconds") or 0)
                                                                    + (now - _aware(trial.active_since)).total_seconds(), 1)}
    stopping = False
    if trial.status == "executing" and trial.attempt_id:
        attempt = db.get(GameSkillAttempt, trial.attempt_id, with_for_update=True)
        if attempt is not None and attempt.status in ("queued", "running"):
            attempt.stop_requested = True
            if attempt.status == "queued":  # nothing ran: no segment to collect
                attempt.status, attempt.result, attempt.ended_at = "stopped", "paused before it began", now
                trial.attempt_id, trial.exec_started_at = None, None
            else:
                stopping = True
    trial.status, trial.active_since = "paused", None
    _event(db, trial, "paused", {"reason": reason})
    db.commit()
    if stopping:
        game_pack.stop_driver_skill()
    return "experiment paused"


def resume(db: Session, user_id: uuid.UUID, trial_id: uuid.UUID, *, reason: str) -> str:
    """Take it up again with what's left of its budget: readiness is checked again before anything runs."""
    trial = _live(db, user_id, trial_id)
    if trial is None or trial.status != "paused":
        db.commit()
        return "not carried out: no paused experiment"
    if trial.attempt_id:
        db.commit()
        return "not carried out: its last attempt is still ending"
    now = _now()
    trial.status, trial.active_since, trial.next_check_at = "selected", now, now
    _event(db, trial, "resumed", {"reason": reason, "remaining": _remaining(trial, now)})
    db.commit()
    wake()
    return "experiment resumed"


def stop(db: Session, user_id: uuid.UUID, trial_id: uuid.UUID, *, reason: str) -> str:
    """End it now. What was observed is kept and assessed; nothing is repeated."""
    from app.services import game_pack

    trial = _live(db, user_id, trial_id)
    if trial is None or trial.status not in ("selected", "preparing", "executing", "paused"):
        db.commit()
        return "not carried out: no experiment to stop"
    had_test = db.scalar(select(LearningTrialEvent.id).where(LearningTrialEvent.trial_id == trial.id,
                                                             LearningTrialEvent.kind == "execution_ended").limit(1))
    if trial.status == "executing" and trial.attempt_id:
        attempt = db.get(GameSkillAttempt, trial.attempt_id, with_for_update=True)
        if attempt is not None and attempt.status in ("queued", "running"):
            attempt.stop_requested = True
            _event(db, trial, "stop_requested", {"reason": reason}, skill_attempt_id=attempt.id)
            trial.costs = {**(trial.costs or {}), "stop_reason": reason}
            db.commit()
            game_pack.stop_driver_skill()
            return "experiment stopping"
    if had_test:
        trial.ending, trial.stop_reason = "stopped", reason
        trial.status, trial.exec_ended_at, trial.window_ends_at = "observing", _now(), _now()
        _event(db, trial, "stopped", {"reason": reason})
        _release(db, trial, after=timedelta(0))
    else:
        _end_before_test(db, trial, f"stopped before the test started: {reason}", ending="not_started")
    db.commit()
    wake()
    return "experiment stopped"


def on_work(db: Session, user_id: uuid.UUID, key: str, work: str, *, reason: str) -> str:
    """Her mind acting on a live trial (her ``experiment`` tool; key ``trial:<id>``)."""
    try:
        trial_id = uuid.UUID(key.split(":", 1)[1])
    except (IndexError, ValueError):
        return "not carried out: not a trial"
    if work == "pause":
        return pause(db, user_id, trial_id, reason=reason)
    if work == "resume":
        return resume(db, user_id, trial_id, reason=reason)
    if work == "cancel":
        return stop(db, user_id, trial_id, reason=reason)
    return f"not carried out: no {work} for an experiment"


# --------------------------------------------------------------------------- #
# Assessment and what persists
# --------------------------------------------------------------------------- #


def _assessor():
    s = get_settings()
    return T.assessor(s.learning_trials_model, api_key=s.openai_api_key or "",
                      reasoning_effort=s.learning_trials_reasoning_effort, timeout=s.learning_trials_timeout_seconds)


def assess_once(*, assessor: Callable[[str], T.Answer] | None = None) -> bool:
    with SessionLocal() as db:
        trial = _claim_trial(db, _now(), ("assessing",))
        if trial is None:
            return False
        trial_id = trial.id
    try:
        _assess(trial_id, assessor=assessor)
    except Exception:  # noqa: BLE001 - retried
        log.exception("learning trials: assessment failed")
        with SessionLocal() as db:
            row = db.get(LearningTrial, trial_id)
            if row is not None:
                _release(db, row, after=timedelta(minutes=2))
                db.commit()
    return True


def evidence_for(db: Session, trial: LearningTrial) -> T.TrialEvidence:
    """Everything the trial's record holds, measured and checked by code."""
    proposal = current_revision(db, trial, trial.selected_revision).proposal
    alt = T.alternative(proposal, trial.selected_alternative)
    events = list(db.scalars(select(LearningTrialEvent).where(LearningTrialEvent.trial_id == trial.id)
                             .order_by(LearningTrialEvent.id)))
    segments = [e for e in events if e.kind == "execution_ended"]
    tries = [t for e in segments for t in e.payload.get("tries") or []]
    started = any(t.get("program") for t in tries)
    start = next((e.payload.get("start_state") for e in segments if e.payload.get("start_state")), None)
    observed = next((e.payload for e in reversed(events) if e.kind == "observed"), {})
    end = observed.get("window_end_state")
    measured = T.measure(proposal, start=start, tries=tries, end=end,
                         test_seconds=(trial.costs or {}).get("test_seconds"), started=started)
    complete = (trial.ending or "") in T.COMPLETE
    checks = T.evaluate(alt["predictions"], measured.values, complete=complete, started=started)
    lacking = T.missing(proposal, measured.values)
    attempts = []
    for t in tries:
        program, facts, verdict = t.get("program") or {}, t.get("facts") or {}, t.get("verdict") or {}
        text = (f"try {t.get('n')}: {t.get('approach') or '(no program)'}; program {program.get('status')}"
                + (f" ({program.get('reason')})" if program.get("reason") else "")
                + (f"; placed {program.get('placed')}" if program.get("placed") is not None else "")
                + (f"; moved {facts.get('from')} -> {facts.get('to')}" if facts.get("to") else "")
                + f"; verdict: {verdict.get('reason')}")
        attempts.append(T.Line(f"a{len(attempts) + 1}", text[:600]))
    lines = []
    for e in events:
        if e.kind in ("preparation", "readiness", "execution_started", "paused", "resumed", "stop_requested",
                      "stopped", "limit", "ended_before_test", "execution_ended", "observed"):
            payload = {k: v for k, v in e.payload.items() if k not in ("tries",)}
            lines.append(T.Line(f"e{len(lines) + 1}", f"{e.kind}: {json.dumps(payload, default=str)[:400]}"))
    return T.TrialEvidence(proposal, alt, trial.selection or {}, trial.ending or "completed", trial.stop_reason,
                           trial.costs or {}, measured, checks, lacking, started, attempts, lines)


def _assess(trial_id: uuid.UUID, *, assessor=None) -> None:
    with SessionLocal() as db:
        trial = db.get(LearningTrial, trial_id)
        if trial is None or trial.status != "assessing":
            db.commit()
            return
        ev = evidence_for(db, trial)
        db.commit()
    answer, model = None, None
    if ev.started:  # a test that never started is code's to record: nothing to interpret
        call = assessor or _assessor()
        model = getattr(call, "name", None)
        answer = call(T.render_evidence(ev, T.allowed(ev.checks, ev.missing, started=ev.started)))
        if answer.raw is None:
            raise RuntimeError(f"the assessor failed: {answer.error}")
    result, dropped = T.check_assessment(answer.raw if answer else None, ev)
    if not ev.started:
        result["why"] = f"the test never started ({ev.stop_reason or ev.ending}): nothing about the technique was seen"
    _write_assessment(trial_id, ev, result, dropped, model=model, reason="first reading")


def _write_assessment(trial_id: uuid.UUID, ev: T.TrialEvidence, result: dict, dropped: list, *, model: str | None,
                      reason: str) -> None:
    from app.services import game_experience

    with SessionLocal() as db:
        trial = db.get(LearningTrial, trial_id, with_for_update=True)
        if trial is None or trial.status != "assessing":
            return  # deleted/reset or superseded while the assessor was running
        previous = db.scalar(select(LearningTrialAssessment).where(LearningTrialAssessment.trial_id == trial.id,
                                                                   LearningTrialAssessment.current))
        next_n = previous.n + 1 if previous else 1
        experience_id = None
        session_id = trial.session_id
        if session_id and db.get(DesktopSession, session_id) is not None:
            db.commit()
            # The trial's outcome is one of her game experiences (what reflection and her lessons cite).
            experience_id = game_experience.record_experience(db, session_id, "skill_outcome", {
                "event_key": f"trial:{trial.id}:assessment:{next_n}",
                "skill": f"trial:{trial.scenario}", "status": result["hypothesis"],
                "reason": T.finding(ev.proposal, trial.selected_alternative, result, ev.measured.values)[:300],
                "data": {"trial_id": str(trial.id), "ending": trial.ending, "measured": ev.measured.values,
                         "measurement_sources": ev.measured.sources, "missing": ev.missing,
                         "interpretation": result, "interpretation_is_not_independent_evidence": True},
                "seconds": (trial.costs or {}).get("test_seconds"), "step_id": None})
            trial = db.get(LearningTrial, trial_id, with_for_update=True, populate_existing=True)
            if trial is None or trial.status != "assessing":
                return
        previous = db.scalar(select(LearningTrialAssessment).where(LearningTrialAssessment.trial_id == trial.id,
                                                                   LearningTrialAssessment.current))
        n = (previous.n + 1) if previous else 1
        if previous is not None:
            previous.current = False
            db.flush()
        reading = LearningTrialAssessment(
            trial_id=trial.id, user_id=trial.user_id, n=n, hypothesis=result["hypothesis"], test_started=ev.started,
            measurements={"values": ev.measured.values, "sources": ev.measured.sources, "notes": ev.measured.notes},
            prediction_checks=ev.checks, missing=ev.missing, result=result, dropped=dropped, model=model,
            reason=reason)
        db.add(reading)
        fed = _feed(db, trial, ev, result, experience_id)
        trial.status, trial.closed_at, trial.lease_expires_at = "assessed", trial.closed_at or _now(), None
        _event(db, trial, "assessed", {"n": n, "hypothesis": result["hypothesis"], "fed": fed},
               experience_id=experience_id)
        db.commit()
    _log({"op": "assessed", "trial": str(trial_id), "hypothesis": result["hypothesis"], "fed": fed})


def _feed(db: Session, trial: LearningTrial, ev: T.TrialEvidence, result: dict, experience_id: int | None) -> dict:
    """Feed trial measurements and attributed conclusions to shared assessment; preserve memory's
    hypothesis resolution and counted successful programs. No independent game lesson writes."""
    from app.services import game_experience, game_skills, memory_manager

    fed: dict[str, Any] = {}
    status = result["hypothesis"]
    if experience_id and trial.session_id:
        from app.services import learning_assessment as LA
        experience = db.get(GameExperience, experience_id)
        assessment, _ = LA.open_assessment(
            db, user_id=trial.user_id, kind="execution", key=f"trial:{trial.id}",
            anchor_at=experience.occurred_at, game_session_id=trial.session_id,
            trigger={"why": "a bounded trial result, with measured facts and attributed interpretation"},
            evidence=[(f"experience:{experience_id}", "trial result", {})])
        fed["assessment"] = str(assessment.id)
    if status == "unresolved":
        return {**fed, "note": "unresolved: shared assessment revisits earlier learning; no move or hypothesis promotion"}
    if trial.hypothesis_item_id and experience_id:
        try:
            with db.begin_nested():
                item = db.get(WorkingItem, trial.hypothesis_item_id)
                if item is not None and item.closed_at is None:
                    memory_manager.resolve(db, trial.user_id, item.id,
                                           status="supported" if status == "supported" else "disproved",
                                           source=memory_manager.Source("experience", experience_id),
                                           reason=f"a bounded trial: {result.get('why') or status}"[:300])
                    fed["hypothesis"] = "supported" if status == "supported" else "disproved"
        except Exception as exc:  # noqa: BLE001
            fed["hypothesis"] = f"not changed: {type(exc).__name__}"
    if status == "supported":
        attempt_id = next((e.skill_attempt_id for e in db.scalars(select(LearningTrialEvent).where(
            LearningTrialEvent.trial_id == trial.id, LearningTrialEvent.kind == "execution_ended")
            .order_by(LearningTrialEvent.id.desc())) if e.skill_attempt_id), None)
        attempt = db.get(GameSkillAttempt, attempt_id) if attempt_id else None
        if attempt is not None and attempt.status == "learned":
            move = game_skills.save_learned(db, attempt)
            fed["move"] = move.name if move is not None else "not kept"
    return fed


def reassess(db: Session, trial_id: uuid.UUID, *, why: str) -> bool:
    """Later evidence: read it again (a new reading; the earlier one stays)."""
    trial = db.get(LearningTrial, trial_id, with_for_update=True)
    if trial is None or trial.status != "assessed":
        return False
    trial.status, trial.closed_at, trial.next_check_at = "assessing", None, _now()
    _event(db, trial, "reopened", {"why": why})
    db.commit()
    wake()
    return True


# --------------------------------------------------------------------------- #
# For her: summaries
# --------------------------------------------------------------------------- #


def summary_lines(db: Session, session: DesktopSession) -> list[str]:
    """For her game summary (the mouth): the experiment under way, and the latest finding."""
    lines = []
    live = db.scalar(select(LearningTrial).where(LearningTrial.session_id == session.id,
                                                 LearningTrial.status.in_(TRIAL_LIVE)))
    if live is not None:
        proposal = current_revision(db, live, live.selected_revision).proposal
        alt = T.alternative(proposal, live.selected_alternative) or {}
        left = _remaining(live, _now())
        lines.append(f"An experiment you chose is {live.status}: {alt.get('action')} (testing: {proposal['hypothesis']}). "
                     f"Left: {left['attempts']} tries, {max(0, int(left['seconds']))} s, {left['blocks']} blocks.")
    latest = db.execute(select(LearningTrial, LearningTrialAssessment).join(
        LearningTrialAssessment, (LearningTrialAssessment.trial_id == LearningTrial.id) & LearningTrialAssessment.current)
        .where(LearningTrial.session_id == session.id).order_by(LearningTrialAssessment.created_at.desc()).limit(1)).first()
    if latest is not None:
        trial, reading = latest
        proposal = current_revision(db, trial, trial.selected_revision).proposal
        lines.append("Your last experiment: " + T.finding(proposal, trial.selected_alternative, reading.result,
                                                          reading.measurements.get("values") or {}) + ".")
    return lines


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #


def _trial_out(db: Session, trial: LearningTrial) -> dict:
    revision = current_revision(db, trial)
    return {"id": str(trial.id), "status": trial.status, "scenario": trial.scenario, "problem": trial.problem,
            "revision": trial.revision, "hypothesis": revision.proposal.get("hypothesis") if revision else None,
            "selected": trial.selected_alternative, "ending": trial.ending, "stop_reason": trial.stop_reason,
            "costs": trial.costs, "limits": trial.limits, "deferral": trial.deferral,
            "linked_trial_id": str(trial.linked_trial_id) if trial.linked_trial_id else None,
            "created_at": trial.created_at.isoformat() if trial.created_at else None,
            "closed_at": trial.closed_at.isoformat() if trial.closed_at else None}


def listing(db: Session, user_id: uuid.UUID, *, status: str | None = None, limit: int = 50) -> dict:
    query = select(LearningTrial).where(LearningTrial.user_id == user_id)
    if status:
        query = query.where(LearningTrial.status == status)
    rows = list(db.scalars(query.order_by(LearningTrial.created_at.desc()).limit(limit)))
    counts = dict(db.execute(select(LearningTrial.status, func.count()).where(LearningTrial.user_id == user_id)
                             .group_by(LearningTrial.status)).all())
    triggers = list(db.scalars(select(LearningTrialTrigger).where(LearningTrialTrigger.user_id == user_id)
                               .order_by(LearningTrialTrigger.created_at.desc()).limit(10)))
    return {"counts": counts, "trials": [_trial_out(db, t) for t in rows],
            "investigations": [{"id": str(t.id), "kind": t.kind, "state": t.state, "problem": t.payload.get("problem"),
                                "result": t.result, "error": t.last_error,
                                "at": t.created_at.isoformat() if t.created_at else None} for t in triggers]}


def detail(db: Session, user_id: uuid.UUID, trial_id: uuid.UUID) -> dict | None:
    trial = db.scalar(select(LearningTrial).where(LearningTrial.id == trial_id, LearningTrial.user_id == user_id))
    if trial is None:
        return None
    out = _trial_out(db, trial)
    out["selection"] = trial.selection
    out["deferrals"] = trial.deferrals
    out["hypothesis_item_id"] = str(trial.hypothesis_item_id) if trial.hypothesis_item_id else None
    out["revisions"] = [{"revision": r.revision, "proposal": r.proposal, "dropped": r.dropped, "reason": r.reason,
                         "evidence": r.evidence, "model": r.model, "at": r.created_at.isoformat()}
                        for r in db.scalars(select(LearningTrialRevision).where(
                            LearningTrialRevision.trial_id == trial.id).order_by(LearningTrialRevision.revision))]
    out["events"] = [{"kind": e.kind, "payload": e.payload, "decision_attempt_id": str(e.decision_attempt_id)
                      if e.decision_attempt_id else None, "skill_attempt_id": str(e.skill_attempt_id)
                      if e.skill_attempt_id else None, "experience_id": e.experience_id, "at": e.at.isoformat()}
                     for e in db.scalars(select(LearningTrialEvent).where(LearningTrialEvent.trial_id == trial.id)
                                         .order_by(LearningTrialEvent.id))]
    out["assessments"] = [{"n": a.n, "current": a.current, "hypothesis": a.hypothesis, "test_started": a.test_started,
                           "measurements": a.measurements, "prediction_checks": a.prediction_checks,
                           "missing": a.missing, "result": a.result, "dropped": a.dropped, "model": a.model,
                           "reason": a.reason, "at": a.created_at.isoformat()}
                          for a in db.scalars(select(LearningTrialAssessment).where(
                              LearningTrialAssessment.trial_id == trial.id).order_by(LearningTrialAssessment.n))]
    return out


# --------------------------------------------------------------------------- #
# The worker
# --------------------------------------------------------------------------- #

QUEUE = "macrovision:learning-trials:wakeups"
_last_sweep = 0.0


def wake() -> None:
    url = get_settings().redis_url
    if not url:
        return
    try:
        import redis

        redis.from_url(url).rpush(QUEUE, "1")
    except Exception:  # noqa: BLE001 - the poll finds it anyway
        pass


def run_once() -> bool:
    """One pass: sweep (every 30 s), live trials, deferrals, one investigation, one presentation, one assessment."""
    import time as _time

    global _last_sweep
    did = False
    if _time.monotonic() - _last_sweep >= 30.0:
        _last_sweep = _time.monotonic()
        with SessionLocal() as db:
            did |= sweep(db) > 0
    did |= advance_once() > 0
    did |= reconsider_once() > 0
    if enabled():
        did |= investigate_once()
    did |= present_once()
    did |= unanswered_once() > 0
    did |= assess_once()
    return did


__all__ = ["consider", "sweep", "investigate_once", "present_once", "reconsider_once", "advance_once",
           "assess_once", "unanswered_once", "undecided", "settle_presented", "proposal_text", "on_pursue", "on_decline", "on_defer", "on_work", "on_execution_finished", "spec_for",
           "pause", "resume", "stop", "reassess", "planning_lines", "summary_lines", "listing", "detail",
           "run_once", "wake", "parse_key", "readiness"]
