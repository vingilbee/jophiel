"""Evidence and attribution: learning's retrospective assessment
(docs/learning-system/02-evidence-and-attribution.md; implementation: docs/learning-system/02-implementation.md).

Learning reads what happened and what the evidence supports. It never speaks, never acts and never writes memory.

1. **Triggers** (code only). The ``learning`` consumer of observations: a user message Student 1 read as
   correcting her (``correction``) or complaining (``complaint``); otherwise a deterministic sample of ordinary
   messages (``sample``); an interrupted spoken reply, a failed delivery, a failed skill run (``execution``). A
   sweep adds what isn't an observation: a failed or cut-off reply, a failed effect, a memory correction that
   read a message as correcting an earlier statement (attached to that message's assessment). One assessment
   per trigger key, so reprocessing never duplicates one; evidence is attached once per ref.
2. **Investigation.** Code gathers the conversation around the trigger (heard text, forgotten parts withheld),
   the turns before it as targets with their captured records (Student 1's reading, Student 2's decision, the
   reply's delivery, what was heard, the effect), facts checked by code (each says what it establishes and what
   it doesn't), quotes of her words, memory corrections, game outcomes and her hands' decisions, the user's
   notes, and other open assessments in the conversation. One evaluator call per round
   (``app/ai/learning/assessment.py``); code checks every ref, quote and component claim. The evaluator may ask
   to look further (earlier messages, later ones, the exact learner inputs, more game outcomes). There is no
   round budget: it stops when it concludes, when what it needs is missing, or when it is stuck (what it asked
   for adds nothing new, or is more than can be read at once).
3. **Revisions.** Each evaluation that read different evidence is a new revision; the one before is superseded
   and kept. Reading the same evidence again is not a revision (repetition is not evidence). New evidence
   (a capture that arrives late, a correction, a later message another assessment found relevant, the user's
   note) reopens an assessment. When conclusions change, every owner that acted on the earlier one is told to
   reconsider (training examples flagged when Student 2's choice in that turn was found at fault; lessons that
   rested on the earlier reading, docs/learning-system/03-implementation.md).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import String, cast, delete, exists, false, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.learning import assessment as A
from app.ai.observe import registry
from app.core.config import get_settings
from app.db.models import (
    CaptureGap, ChatMessage, DecisionAttempt, DecisionExecution, DesktopSession, GameExperience, LearningAssessment,
    LearningAssessmentEvidence, LearningAssessmentLink, LearningAssessmentRevision, LearningAssessmentUse,
    MemoryCorrection, Observation, ObservationProcessing, TrainingExample,
)
from app.services.learning_capture import execution_id_for

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "learning"
TRIAGE_VERSION = "learning-triage-v2"
ADAPTER = "learning_assessment"  # observation_adapters row: when assessment began (nothing older is swept)
LEASE = timedelta(minutes=10)
TRIAGE_RETRY = timedelta(seconds=15)
MAX_BACKOFF = timedelta(hours=1)
SWEEP_SECONDS = 30.0
LATER_MESSAGES = 6
GAME_BEFORE = 3
GAME_MORE = 12
INPUT_CHARS = 4000
QUOTE_MIN = 12
OPEN_SHOWN = 5
PRIORITY = {"sample": 0, "execution": 1, "complaint": 2, "correction": 3}
FORGOTTEN = "[forgotten: not shown]"

_log_lock = threading.Lock()
_last_sweep = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def assessment_id(user_id: uuid.UUID, key: str) -> uuid.UUID:
    return uuid.uuid5(user_id, f"assessment:{key}")


def sampled(message_id: uuid.UUID, rate: float | None = None) -> bool:
    """Whether an ordinary message is sampled: by its id, so a retry decides the same."""
    rate = get_settings().learning_assessment_sample_rate if rate is None else rate
    return int(hashlib.sha256(str(message_id).encode()).hexdigest()[:8], 16) / 2 ** 32 < rate


# --------------------------------------------------------------------------- #
# Opening assessments and attaching evidence
# --------------------------------------------------------------------------- #


def open_assessment(db: Session, *, user_id: uuid.UUID, kind: str, key: str, anchor_at: datetime,
                    trigger: dict, conversation_id: uuid.UUID | None = None,
                    game_session_id: uuid.UUID | None = None, anchor_message_id: uuid.UUID | None = None,
                    evidence: Iterable[tuple[str, str, dict]] = ()) -> tuple[LearningAssessment, bool]:
    """The assessment for a trigger, created once, with ``evidence`` ((ref, reason, detail)) attached once each.
    In the caller's transaction. Returns (row, changed): created, or something new attached to it."""
    from app.services.memory_reset import learning_cutoff
    if _aware(anchor_at) < learning_cutoff(db, user_id):
        # In-flight capture and pending producers cannot repopulate a reset test attempt.
        raise ValueError("assessment evidence predates the test-learning reset")
    now = _now()
    settle = timedelta(seconds=get_settings().learning_assessment_settle_seconds)
    created = db.execute(insert(LearningAssessment).values(
        id=assessment_id(user_id, key), user_id=user_id, trigger_kind=kind, trigger_key=key[:160], trigger=trigger,
        conversation_id=conversation_id, game_session_id=game_session_id, anchor_message_id=anchor_message_id,
        anchor_at=_aware(anchor_at), next_attempt_at=now + settle,
    ).on_conflict_do_nothing(constraint="uq_learning_assessments_trigger").returning(LearningAssessment.id)).scalar()
    row = db.scalar(select(LearningAssessment).where(LearningAssessment.user_id == user_id,
                                                     LearningAssessment.trigger_key == key[:160]).with_for_update())
    if created is None and PRIORITY[kind] > PRIORITY[row.trigger_kind]:
        # A sampled exchange that turns out to be a correction is assessed as one.
        row.trigger = {**(row.trigger or {}), "was": row.trigger_kind, **trigger}
        row.trigger_kind = kind
        _reopen(row, now, f"also a {kind}")
    added = attach(db, row, evidence)
    if created is not None:
        from app.services import system_signals
        system_signals.emit(user_id, "observation", "learning", kind, row.id)
    if created is None and added:
        _reopen(row, now, f"new evidence: {', '.join(added)}")
    return row, created is not None or bool(added)


def attach(db: Session, row: LearningAssessment, evidence: Iterable[tuple[str, str, dict]]) -> list[str]:
    """Attach evidence refs not attached before. Returns the refs that are new."""
    added = []
    for ref, reason, detail in evidence:
        new = db.execute(insert(LearningAssessmentEvidence).values(
            assessment_id=row.id, user_id=row.user_id, ref=ref[:100], reason=(reason or "")[:300] or None,
            detail=detail or {}).on_conflict_do_nothing(constraint="uq_learning_assessment_evidence")
            .returning(LearningAssessmentEvidence.ref)).scalar()
        if new is not None:
            added.append(new)
    return added


def _reopen(row: LearningAssessment, now: datetime, why: str) -> None:
    """New evidence: count it (a run in flight will be followed by another) and queue the assessment again."""
    row.epoch += 1
    if row.state in ("done", "failed"):
        row.state, row.attempts, row.last_error = "pending", 0, None
        row.next_attempt_at = now + timedelta(seconds=get_settings().learning_assessment_settle_seconds)
    row.trigger = {**(row.trigger or {}), "reopened": why[:300]}


# --------------------------------------------------------------------------- #
# Triggers 1: the learning consumer of observations (code only)
# --------------------------------------------------------------------------- #


def triage(db: Session, now: datetime | None = None, *, limit: int = 50) -> int:
    """Decide for each due observation whether it starts or adds to an assessment. Commits. Returns how many."""
    now = now or _now()
    receipts = list(db.scalars(select(ObservationProcessing).where(
        ObservationProcessing.consumer == CONSUMER, ObservationProcessing.status.in_(("pending", "retry")),
        ObservationProcessing.next_attempt_at <= now,
    ).order_by(ObservationProcessing.next_attempt_at, ObservationProcessing.observation_id).limit(limit)
        .with_for_update(skip_locked=True)))
    for receipt in receipts:
        observation = db.get(Observation, receipt.observation_id)
        try:
            with db.begin_nested():
                outcome, reason = _triage_one(db, observation, now)
        except Exception as exc:  # noqa: BLE001 - one report never stops the others
            log.exception("learning assessment: triage of observation %s failed", receipt.observation_id)
            outcome, reason = "error", f"{type(exc).__name__}: {exc}"[:200]
        _settle_receipt(receipt, outcome, reason, now)
    db.commit()
    return len(receipts)


def _settle_receipt(receipt: ObservationProcessing, outcome: str, reason: str, now: datetime) -> None:
    receipt.reason = reason[:200]
    if outcome == "wait":  # not a failure: what it needs hasn't arrived yet
        receipt.status, receipt.next_attempt_at = "retry", now + TRIAGE_RETRY
    elif outcome in ("applied", "skipped"):
        receipt.status, receipt.processed_at, receipt.rule_version = outcome, now, TRIAGE_VERSION
    else:
        receipt.attempts += 1
        if receipt.attempts >= get_settings().observation_max_attempts:
            receipt.status, receipt.processed_at = "failed", now
        else:
            receipt.status = "retry"
            receipt.next_attempt_at = now + min(timedelta(seconds=10 * 2 ** receipt.attempts), MAX_BACKOFF)


def _triage_one(db: Session, observation: Observation | None, now: datetime) -> tuple[str, str]:
    if observation is None:
        return "skipped", "the observation is gone"
    from app.services.memory_reset import learning_cutoff
    if _aware(observation.occurred_at or observation.received_at) < learning_cutoff(db, observation.user_id):
        return "skipped", "predates test-learning reset"
    p, key = observation.payload or {}, (observation.source, observation.kind)
    if key == ("conversation", "user_message"):
        return _triage_message(db, observation, p, now)
    if key == ("conversation", "reply_interrupted"):
        message = db.get(ChatMessage, _uuid(p.get("message_id")))
        if message is None:
            return "skipped", "the reply is gone"
        open_assessment(db, user_id=observation.user_id, kind="execution", key=f"heard:{message.id}",
                        anchor_at=message.created_at, conversation_id=message.conversation_id,
                        anchor_message_id=message.id, trigger={
                            "why": "the user cut off her spoken reply", "observation": observation.id,
                            "heard_chars": p.get("heard_chars"), "total_chars": p.get("total_chars")})
        return "applied", "an interrupted reply"
    if key == ("conversation", "delivery_status"):
        if p.get("status") != "failed":
            return "skipped", "delivered"
        message = db.get(ChatMessage, _uuid(p.get("message_id")))
        if message is None:
            return "skipped", "the reply is gone"
        open_assessment(db, user_id=observation.user_id, kind="execution", key=f"delivery:{message.id}",
                        anchor_at=message.created_at, conversation_id=message.conversation_id,
                        anchor_message_id=message.id, trigger={
                            "why": f"her {p.get('channel')} reply couldn't be delivered", "observation": observation.id})
        return "applied", "a failed delivery"
    if key == ("minecraft_pack", "notice") and observation.salience == "critical":
        experience = db.get(GameExperience, p.get("experience_id"))
        if experience is None or experience.user_id != observation.user_id:
            return "skipped", "the game experience is gone"
        session = db.get(DesktopSession, experience.session_id)
        open_assessment(db, user_id=observation.user_id, kind="execution",
                        key=f"notice:{experience.id}", anchor_at=experience.occurred_at,
                        game_session_id=experience.session_id, conversation_id=getattr(session, "conversation_id", None),
                        trigger={"why": "a critical recorded experience; assess cost without assuming a cause"},
                        evidence=[(f"experience:{experience.id}", "a critical notice", {})])
        return "applied", "a critical game experience"
    if key == ("minecraft_pack", "skill_outcome"):
        if p.get("status") not in registry.SKILL_FAILED:
            return "skipped", "not a failure"
        experience = db.get(GameExperience, p.get("experience_id"))
        if experience is None:
            return "skipped", "the game experience is gone"
        session = db.get(DesktopSession, experience.session_id)
        # Repeats of the same failure on the same step are one assessment with more evidence.
        open_assessment(db, user_id=observation.user_id, kind="execution",
                        key=f"skill:{experience.session_id}:{p.get('step_id') or '-'}:{p.get('skill')}",
                        anchor_at=experience.occurred_at, game_session_id=experience.session_id,
                        conversation_id=getattr(session, "conversation_id", None), trigger={
                            "why": f"skill {p.get('skill')} ended {p.get('status')}", "observation": observation.id},
                        evidence=[(f"experience:{experience.id}", "a failed skill run", {})])
        return "applied", "a failed skill run"
    return "skipped", "not a kind learning assesses"


def _triage_message(db: Session, observation: Observation, p: dict, now: datetime) -> tuple[str, str]:
    message = db.get(ChatMessage, _uuid(p.get("message_id")))
    if message is None or message.role != "user":
        return "skipped", "the message is gone"
    before = db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == message.conversation_id, ChatMessage.role == "assistant",
        ChatMessage.conversation_sequence < message.conversation_sequence).limit(1))
    if before is None:
        return "skipped", "nothing of hers before it"
    reading = db.scalar(select(DecisionAttempt).where(
        DecisionAttempt.user_id == observation.user_id, DecisionAttempt.learner == "student_1",
        DecisionAttempt.source_message_id == message.id).order_by(DecisionAttempt.started_at.desc()).limit(1))
    waited = (now - _aware(message.created_at)).total_seconds()
    if reading is None and waited < get_settings().learning_assessment_triage_wait_seconds:
        return "wait", "waiting for Student 1's capture of the message"
    acts = {}
    if reading is not None and reading.status == "ok":
        acts = ((reading.output or {}).get("understand") or {}).get("acts") or {}
    note = "" if reading is not None else " (Student 1's reading wasn't captured)"
    if acts.get("correcting_her"):
        kind, why = "correction", "Student 1 read it as correcting her"
    elif acts.get("complaining"):
        kind, why = "complaint", "Student 1 read it as a complaint"
    elif sampled(message.id):
        kind, why = "sample", "an ordinary exchange, sampled"
    else:
        return "skipped", f"an ordinary message, not sampled{note}"
    open_assessment(db, user_id=observation.user_id, kind=kind, key=f"message:{message.id}",
                    anchor_at=message.created_at, conversation_id=message.conversation_id,
                    anchor_message_id=message.id, trigger={
                        "why": why + note, "observation": observation.id,
                        "student_1": str(reading.id) if reading is not None else None})
    return "applied", why + note


# --------------------------------------------------------------------------- #
# Triggers 2: the sweep (what isn't an observation, and late captures)
# --------------------------------------------------------------------------- #


def started_at(db: Session) -> datetime:
    from app.services import observation

    return _aware(observation.adapter(db, ADAPTER).started_at)


def sweep(db: Session, now: datetime | None = None, *, limit: int = 50) -> dict[str, int]:
    """Failed or cut-off replies, failed effects, memory corrections and captures that arrived late. Commits."""
    now = now or _now()
    horizon = max(started_at(db), now - timedelta(days=get_settings().learning_capture_retention_days))
    done = {"replies": 0, "effects": 0, "corrections": 0, "arrived": 0}

    from app.db.models import LearningReset

    def after_reset(user_column, time_column):
        return ~exists().where(LearningReset.user_id == user_column, LearningReset.cutoff_at > time_column)

    def unassessed(prefix: str, column):
        return ~exists().where(LearningAssessment.user_id == DecisionExecution.user_id,
                               LearningAssessment.trigger_key == literal(prefix) + cast(column, String))

    replies = db.scalars(select(DecisionExecution).where(
        DecisionExecution.kind == "reply", DecisionExecution.result.in_(("failed", "deviated")),
        DecisionExecution.turn_id.is_not(None), DecisionExecution.captured_at >= horizon,
        after_reset(DecisionExecution.user_id, func.coalesce(DecisionExecution.ended_at, DecisionExecution.captured_at)),
        unassessed("reply:", DecisionExecution.turn_id)).order_by(DecisionExecution.captured_at).limit(limit))
    for row in list(replies):
        anchor = _turn_message(db, row.turn_id, row.assistant_message_id)
        if anchor is None:
            continue
        open_assessment(db, user_id=row.user_id, kind="execution", key=f"reply:{row.turn_id}",
                        anchor_at=row.ended_at or row.captured_at, conversation_id=anchor.conversation_id,
                        anchor_message_id=anchor.id, trigger={
                            "why": f"her reply {row.result}: {row.deviation or ''}".strip(), "execution": str(row.id)})
        done["replies"] += 1
    effects = db.scalars(select(DecisionExecution).where(
        DecisionExecution.kind == "effect", DecisionExecution.result == "failed",
        DecisionExecution.captured_at >= horizon,
        after_reset(DecisionExecution.user_id, func.coalesce(DecisionExecution.ended_at, DecisionExecution.captured_at)),
        unassessed("effect:", DecisionExecution.id)).order_by(DecisionExecution.captured_at).limit(limit))
    for row in list(effects):
        anchor = _turn_message(db, row.turn_id, None)
        open_assessment(db, user_id=row.user_id, kind="execution", key=f"effect:{row.id}",
                        anchor_at=row.ended_at or row.captured_at,
                        conversation_id=anchor.conversation_id if anchor else row.conversation_id,
                        anchor_message_id=anchor.id if anchor else None, game_session_id=row.game_session_id,
                        trigger={"why": f"the work part of a decision failed: {row.deviation or ''}".strip(),
                                 "execution": str(row.id)})
        done["effects"] += 1
    corrections = db.scalars(select(MemoryCorrection).where(
        MemoryCorrection.kind == "mistaken", MemoryCorrection.created_at >= horizon,
        after_reset(MemoryCorrection.user_id, MemoryCorrection.created_at),
        ~exists().where(LearningAssessmentEvidence.user_id == MemoryCorrection.user_id,
                        LearningAssessmentEvidence.ref == literal("correction:") + cast(MemoryCorrection.id, String)),
    ).order_by(MemoryCorrection.created_at).limit(limit))
    for row in list(corrections):
        message = db.get(ChatMessage, row.source_message_id)
        from app.services.memory_reset import learning_cutoff
        if message is None or _aware(message.created_at) < learning_cutoff(db, row.user_id):
            continue
        open_assessment(db, user_id=row.user_id, kind="correction", key=f"message:{message.id}",
                        anchor_at=message.created_at, conversation_id=message.conversation_id,
                        anchor_message_id=message.id,
                        trigger={"why": "memory read it as correcting an earlier statement"},
                        evidence=[(f"correction:{row.id}", "memory read this message as a correction", {})])
        done["corrections"] += 1
    waiting = db.scalars(select(LearningAssessment).where(
        LearningAssessment.state == "done", func.jsonb_array_length(LearningAssessment.awaiting) > 0,
        LearningAssessment.updated_at >= now - timedelta(days=get_settings().learning_capture_retention_days),
    ).order_by(LearningAssessment.updated_at).limit(limit).with_for_update(skip_locked=True))
    for row in list(waiting):
        ids = [i for i in (_uuid(r.split(":", 1)[1]) for r in row.awaiting if r.startswith("attempt:")) if i]
        arrived = list(db.scalars(select(DecisionAttempt.id).where(DecisionAttempt.user_id == row.user_id,
                                                                    DecisionAttempt.id.in_(ids or [uuid.uuid4()]))))
        added = attach(db, row, [(f"attempt:{i}", "a capture that was missing arrived", {}) for i in arrived])
        if added:
            _reopen(row, now, f"new evidence: {', '.join(added)}")
            done["arrived"] += 1
        row.awaiting = [r for r in row.awaiting if _uuid(r.split(":", 1)[1]) not in set(arrived)]
    db.commit()
    return done


def _turn_message(db: Session, turn_id: uuid.UUID | None, assistant_message_id: uuid.UUID | None) -> ChatMessage | None:
    if assistant_message_id is not None:
        found = db.get(ChatMessage, assistant_message_id)
        if found is not None:
            return found
    if turn_id is None:
        return None
    return db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id)
                     .order_by(ChatMessage.conversation_sequence.desc()).limit(1))


# --------------------------------------------------------------------------- #
# Gathering the evidence
# --------------------------------------------------------------------------- #


@dataclass
class Plan:
    """How far the investigation looks. Each look only ever adds."""

    earlier: int = 0  # the window doubles per step
    later: bool = False
    inputs: bool = False
    game: int = 0

    def expand(self, looks: Iterable[str]) -> "Plan":
        looks = set(looks)
        return Plan(earlier=self.earlier + ("earlier" in looks), later=self.later or "later" in looks,
                    inputs=self.inputs or "inputs" in looks, game=self.game + ("game" in looks))

    def to_dict(self) -> dict:
        return {"earlier": self.earlier, "later": self.later, "inputs": self.inputs, "game": self.game}


@dataclass
class Gathered:
    ev: A.Evidence
    target_roots: dict[str, list[str]] = field(default_factory=dict)  # target ref -> stored refs
    attempts: dict[str, dict[str, str]] = field(default_factory=dict)  # target ref -> learner -> attempt id
    awaiting: list[str] = field(default_factory=list)  # attempt:<id> still missing that may yet arrive


def _hm(at: datetime | None) -> str:
    return _aware(at).strftime("%Y-%m-%d %H:%M:%S") if at else "?"


def gather(db: Session, row: LearningAssessment, plan: Plan, *, prior: dict | None = None) -> Gathered:
    """Everything the evaluator may read for this assessment, as of now."""
    from app.services import memory_forget

    s = get_settings()
    retention = timedelta(days=s.learning_capture_retention_days)
    evidence_rows = list(db.scalars(select(LearningAssessmentEvidence).where(
        LearningAssessmentEvidence.assessment_id == row.id).order_by(LearningAssessmentEvidence.id)))
    attached = {e.ref for e in evidence_rows}
    ev = A.Evidence(trigger="", trigger_refs=())
    out = Gathered(ev)
    facts = _Counter("c")

    # The conversation: the window before the anchor, later lines when asked for or attached.
    anchor = db.get(ChatMessage, row.anchor_message_id) if row.anchor_message_id else None
    messages: list[ChatMessage] = []
    if row.conversation_id is not None:
        if anchor is not None:
            upto = anchor.conversation_sequence
        else:
            upto = db.scalar(select(func.max(ChatMessage.conversation_sequence)).where(
                ChatMessage.conversation_id == row.conversation_id, ChatMessage.created_at <= row.anchor_at)) or 0
        window = s.learning_assessment_window_messages * 2 ** plan.earlier
        messages = list(reversed(list(db.scalars(select(ChatMessage).where(
            ChatMessage.conversation_id == row.conversation_id, ChatMessage.conversation_sequence <= upto,
        ).order_by(ChatMessage.conversation_sequence.desc()).limit(window)))))
        earlier = db.scalar(select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == row.conversation_id,
            ChatMessage.conversation_sequence < (messages[0].conversation_sequence if messages else 1))) or 0
        if earlier:
            ev.can_look["earlier"] = f"{earlier} earlier messages in this conversation"
        later_q = select(ChatMessage).where(ChatMessage.conversation_id == row.conversation_id,
                                            ChatMessage.conversation_sequence > upto)
        forced = [m for m in (_uuid(r.split(":", 1)[1]) for r in attached if r.startswith("message:")) if m]
        later = list(db.scalars(later_q.order_by(ChatMessage.conversation_sequence).limit(LATER_MESSAGES))) \
            if plan.later else []
        if forced:
            later += [m for m in db.scalars(later_q.where(ChatMessage.id.in_(forced))) if m not in later]
        if not plan.later:
            more = db.scalar(select(func.count()).select_from(later_q.subquery())) or 0
            more -= len(later)
            if more > 0:
                ev.can_look["later"] = f"{more} later messages"
        messages += sorted(later, key=lambda m: m.conversation_sequence)
    cuts = memory_forget.memory_cuts(db, row.user_id, [m.id for m in messages])
    lines: dict[uuid.UUID, str] = {}
    for m in messages:
        ref = f"m{m.conversation_sequence}"
        lines[m.id] = ref
        text = memory_forget.memory_view(m.content, m.heard_chars, m.role, cuts.get(m.id, False))
        text = FORGOTTEN if text is None else text
        her = m.role == "assistant"
        turn = f"t{m.conversation_sequence}" if her and m.turn_id else None
        notes = []
        if her and m.status != "completed":
            notes.append(f"stored as {m.status}")
        if her and m.heard_chars is not None and m.heard_chars < len(m.content or ""):
            notes.append(f"heard {m.heard_chars} of {len(m.content or '')} characters")
        if m.id == row.anchor_message_id:
            notes.append("this started it")
        label = f"{ref} [{'columbina, ' + turn if turn else ('columbina' if her else 'user')}, {_hm(m.created_at)}]"
        ev.add(A.Item(ref=ref, kind="her" if her else "user",
                      event=f"turn:{m.turn_id}" if her and m.turn_id else f"message:{m.id}",
                      roots=(f"message:{m.id}",), text=f'{label} "{text}"' + (f" ({'; '.join(notes)})" if notes else ""),
                      target=turn, words=text))

    # Her turns before (and around) the trigger: the targets, with their records.
    for m in messages:
        if m.role == "assistant" and m.turn_id:
            _turn_target(db, row, out, m, lines[m.id], facts, plan, retention)

    # Quotes of her words in what the user said (explicit references).
    user_lines = [m for m in messages if m.role == "user" and (m.id == row.anchor_message_id
                                                               or f"message:{m.id}" in attached)]
    for m in user_lines:
        for quote in _quotes(m.content or ""):
            for her in messages:
                if her.role == "assistant" and her.turn_id and her.conversation_sequence < m.conversation_sequence \
                        and A._norm(quote) in A._norm(ev.items[lines[her.id]].words or ""):
                    ref = facts.next()
                    ev.add(A.Item(ref=ref, kind="fact", fact_kind="quote", event=f"message:{m.id}",
                                  roots=(f"message:{m.id}", f"message:{her.id}"), target=f"t{her.conversation_sequence}",
                                  text=f'quote: in {lines[m.id]} the user quoted her words from {lines[her.id]}: '
                                       f'"{quote}". Establishes: which reply they referred to. Does not establish: '
                                       "what was wrong with it."))

    # Memory corrections read from these messages.
    for ref in sorted(r for r in attached if r.startswith("correction:")):
        correction = db.get(MemoryCorrection, _uuid(ref.split(":", 1)[1]))
        if correction is None or correction.user_id != row.user_id:
            continue
        where = lines.get(correction.source_message_id, "a message not shown")
        was = f'"{correction.previous_value}"' if correction.previous_value else "not known"
        ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="correction",
                      event=f"message:{correction.source_message_id}",
                      roots=(ref, f"message:{correction.source_message_id}"),
                      text=f'memory correction: memory read {where} as correcting an earlier statement about '
                           f'"{correction.subject}": was {was}, now "{correction.new_value}" ({correction.kind}). '
                           "Establishes: what memory recorded from the message. Does not establish: whose mistake it "
                           "was, or which part of her caused it."))

    # The game: the failed runs attached, her hands' decisions behind them, and the outcomes around them.
    if row.game_session_id is not None:
        _game(db, row, out, attached, plan)

    # The user's notes on this assessment.
    for k, e in enumerate((e for e in evidence_rows if e.ref.startswith("note:")), 1):
        words = str((e.detail or {}).get("text") or "")
        ev.add(A.Item(ref=f"n{k}", kind="note", event=e.ref, roots=(e.ref,), text=words, words=words))

    # Other open assessments here (not evidence; for reopening).
    others = select(LearningAssessment).where(
        LearningAssessment.user_id == row.user_id, LearningAssessment.id != row.id,
        LearningAssessment.state == "done", LearningAssessment.outcome == "unresolved")
    if row.conversation_id is not None:
        others = others.where(LearningAssessment.conversation_id == row.conversation_id)
    elif row.game_session_id is not None:
        others = others.where(LearningAssessment.game_session_id == row.game_session_id)
    else:
        others = others.where(false())
    for k, other in enumerate(db.scalars(others.order_by(LearningAssessment.updated_at.desc()).limit(OPEN_SHOWN)), 1):
        ev.open[f"p{k}"] = A.OpenAssessment(f"p{k}", str(other.id), f"{other.trigger_kind} at {_hm(other.anchor_at)}: "
                                                                    f"{(other.explanation or '')[:300]}")

    if plan.inputs is False and any(i.kind == "record" and i.captured for i in ev.items.values()):
        ev.can_look["inputs"] = "the exact inputs each captured learner was given"
    ev.trigger, ev.trigger_refs = _trigger_text(row, ev, lines, anchor)
    if prior:
        ev.prior = A.render_prior(prior, ev)
    return out


class _Counter:
    def __init__(self, prefix: str):
        self.prefix, self.n = prefix, 0

    def next(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n}"


def _turn_target(db: Session, row: LearningAssessment, out: Gathered, message: ChatMessage, line: str,
                 facts: _Counter, plan: Plan, retention: timedelta) -> None:
    ev, turn_id, tref = out.ev, message.turn_id, f"t{message.conversation_sequence}"
    attempts = list(db.scalars(select(DecisionAttempt).where(DecisionAttempt.user_id == row.user_id,
                                                             DecisionAttempt.turn_id == turn_id)
                               .order_by(DecisionAttempt.started_at)))
    latest = {a.learner: a for a in attempts}  # an actual retry: the last try is what produced the reply
    reply = db.get(DecisionExecution, execution_id_for(turn_id, "reply"))
    reply = reply if reply is not None and reply.user_id == row.user_id else None
    around = list(db.scalars(select(DecisionExecution).where(
        DecisionExecution.user_id == row.user_id, DecisionExecution.assistant_message_id == message.id,
        DecisionExecution.kind.in_(("heard", "channel_delivery"))).order_by(DecisionExecution.captured_at)))
    gaps = list(db.scalars(select(CaptureGap).where(CaptureGap.user_id == row.user_id, CaptureGap.turn_id == turn_id)))
    s2 = latest.get("student_2")
    expected_s2 = _uuid(reply.attempt_id) if reply is not None and reply.attempt_id else (s2.id if s2 else None)
    expected_s1 = _uuid((s2.upstream or {}).get("student_1")) if s2 is not None else None
    asked = db.scalar(select(ChatMessage.id).where(ChatMessage.turn_id == turn_id, ChatMessage.role == "user").limit(1))
    expired = _aware(message.created_at) < _now() - retention
    nothing = not attempts and reply is None and not around

    def why_missing(record_id: uuid.UUID | None) -> str:
        gap = next((g for g in gaps if record_id is not None and g.record_id == record_id), None) or (
            gaps[0] if gaps and record_id is None else None)
        if gap is not None:
            return f"lost in capture ({gap.reason})"
        if expired:
            return f"expired (raw captures are kept {retention.days} days)"
        return "nothing was captured for this turn" if nothing else "never saved"

    roots = [f"turn:{turn_id}", f"message:{message.id}"]
    ids: dict[str, str] = {}
    intention = ""
    for learner, expected, suffix in (("student_1", expected_s1, "s1"), ("student_2", expected_s2, "s2")):
        if learner == "student_1" and asked is None and "student_1" not in latest:
            continue  # a follow-up (a finished thought) has no Student 1
        record = latest.get(learner)
        rid = record.id if record is not None else expected
        if rid is not None:
            ids[learner] = str(rid)
            roots.append(f"attempt:{rid}")
        word = "Student 1's reading" if learner == "student_1" else "Student 2's decision"
        if record is None:
            text = f"{word}: not captured ({why_missing(rid)})"
            if rid is not None and not expired and not any(g.record_id == rid for g in gaps):
                out.awaiting.append(f"attempt:{rid}")
        else:
            text = f"{word} ({record.status}): {_record_summary(record)}"
            tries = sum(1 for a in attempts if a.learner == learner)
            if tries > 1:
                text += f" [try {tries}; earlier tries were retried]"
            if learner == "student_2":
                intention = ((record.output or {}).get("decision") or {}).get("intention") or ""
        detail = _input_detail(record) if plan.inputs and record is not None else None
        ev.add(A.Item(ref=f"{tref}.{suffix}", kind="record", learner=learner, event=f"turn:{turn_id}",
                      roots=(f"attempt:{rid}",) if rid else (f"turn:{turn_id}",), target=tref, text=text,
                      captured=record is not None, detail=detail))
    if reply is not None:
        roots.append(f"execution:{reply.id}")
        if (reply.intended or {}).get("origin") == "operational_fallback":
            ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="capture", event=f"turn:{turn_id}",
                          roots=(f"execution:{reply.id}",), target=tref,
                          text="decision: Student 2 couldn't decide; the reply used the operational fallback "
                               "(respond). Establishes: no valid decision was made. Does not establish: why."))
        delivery = {"succeeded": "the reply was stored and sent in full",
                    "deviated": "the reply was sent, but not as intended",
                    "failed": "the reply failed", "cancelled": "the reply was stopped before any of it was kept",
                    "not_sent": "nothing was sent (silence was chosen)",
                    "unknown": "handed to the channel; whether it arrived isn't known"}.get(reply.result, reply.result)
        deviation = reply.deviation or ""
        if "spoke although the decision was silence" in deviation:
            ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="deviation", event=f"turn:{turn_id}",
                          roots=(f"execution:{reply.id}",), target=tref,
                          text="deviation: the mouth spoke although Student 2 chose silence. Establishes: what was "
                               "done differed from what was chosen. Does not establish: which was right."))
        ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="delivery", event=f"turn:{turn_id}",
                      roots=(f"execution:{reply.id}",), target=tref,
                      text=f"delivery ({reply.executor}): {reply.result}: {delivery}"
                           + (f" ({deviation})" if deviation else "")
                           + ". Establishes: what happened to the reply on its way. Does not establish: whether its "
                             "content or timing was right."))
        effect = db.scalar(select(DecisionExecution).where(
            DecisionExecution.user_id == row.user_id, DecisionExecution.kind == "effect",
            DecisionExecution.attempt_id == reply.attempt_id).limit(1)) if reply.attempt_id else None
        if effect is not None and effect.result != "succeeded":
            ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="effect", event=f"turn:{turn_id}",
                          roots=(f"execution:{effect.id}",), target=tref,
                          text=f"effect: the work part ({(effect.intended or {}).get('work')}) {effect.result}"
                               + (f": {effect.deviation}" if effect.deviation else "")
                               + ". Establishes: whether the work part was carried out. Does not establish: whether "
                                 "choosing it was right."))
    elif not nothing:
        ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="capture", event=f"turn:{turn_id}",
                      roots=(f"turn:{turn_id}",), target=tref,
                      text=f"delivery: no delivery record ({why_missing(None)}). Establishes: nothing about delivery."))
    for e in around:
        roots.append(f"execution:{e.id}")
        text = (f"heard: the user heard {e.executed.get('heard_chars')} of {e.executed.get('reply_chars')} characters "
                "(cut off). Establishes: how much of the reply reached them. Does not establish: why they cut in."
                if e.kind == "heard" else
                f"channel ({e.executor}) part {int(e.executed.get('part_index', 0)) + 1}: {e.executed.get('status')}"
                + (f" ({e.deviation})" if e.deviation else "") + ". Establishes: the channel's report. Does not "
                "establish: whether it was read.")
        ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="delivery", event=f"turn:{turn_id}",
                      roots=(f"execution:{e.id}",), target=tref, text=text))
    for gap in gaps:
        ev.add(A.Item(ref=facts.next(), kind="fact", fact_kind="capture", event=f"turn:{turn_id}",
                      roots=(f"turn:{turn_id}",), target=tref,
                      text=f"capture: a {gap.label} record of this turn was lost ({gap.reason}). Establishes: that "
                           "record is missing. Does not establish: anything about what it said."))
    label = f"her reply {line}" + (f' (intention: "{intention[:160]}")' if intention else "")
    ev.targets[tref] = A.Target(tref, "turn", label, f"turn:{turn_id}", tuple(roots))
    out.target_roots[tref] = list(dict.fromkeys(roots))
    out.attempts[tref] = ids


def _record_summary(record: DecisionAttempt) -> str:
    output = record.output or {}
    if record.status != "ok" and record.learner != "student_2":
        return f"failed: {record.error or 'no output'}"
    if record.learner == "student_1":
        understand = output.get("understand") or {}
        acts = [a for a, on in (understand.get("acts") or {}).items() if on]
        return (f"acts {acts or ['none']}, tone {understand.get('tone')}, request {understand.get('request')}, "
                f"sure {understand.get('sure')}")
    if record.learner == "student_2":
        decision = output.get("decision") or {}
        if record.status != "ok" or not decision:
            return f"no valid decision ({record.error or '; '.join(map(str, output.get('problems') or [])) or 'invalid'})"
        return (f'intention "{decision.get("intention")}", communication {decision.get("communication")}, '
                f'move {decision.get("move")}, what to get across "{decision.get("what_to_communicate")}", '
                f'work {decision.get("work")}')
    action = output.get("action")
    return f"chose {action}" if action else "no action"


def _input_detail(record: DecisionAttempt) -> str | None:
    inp = record.input or {}
    if record.learner == "student_2":
        text = inp.get("prompt")
    elif record.learner == "student_1":
        text = json.dumps(inp.get("bundle"), ensure_ascii=False) if inp.get("bundle") else None
    else:
        text = inp.get("text")
    if not text:
        return None
    text = str(text)
    cut = f"\n[... {len(text) - INPUT_CHARS} more characters]" if len(text) > INPUT_CHARS else ""
    return "input as given:\n" + text[:INPUT_CHARS] + cut


def _game_excerpt(value, limit=2400):
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + " [excerpt truncated; exact record available by source ref]"


def _game_input(db, record):
    from app.services import capture_chains
    captured = capture_chains.attempt_out(db, record, blobs=True)
    return "Captured input, including available actions and constraints: " + _game_excerpt(captured["input"], 12000)


def _game(db: Session, row: LearningAssessment, out: Gathered, attached: set[str], plan: Plan) -> None:
    """Read factual outcomes and step-1 capture without merging claims into observations."""
    from app.db.models import DesktopSessionEvent
    from app.services import capture_chains, trajectory

    ev = out.ev
    from app.services.memory_reset import learning_cutoff
    cutoff = learning_cutoff(db, row.user_id)
    ids = [int(r.split(":", 1)[1]) for r in attached
           if r.startswith("experience:") and r.split(":", 1)[1].isdigit()]
    chosen = list(db.scalars(select(GameExperience).where(
        GameExperience.user_id == row.user_id, GameExperience.session_id == row.game_session_id,
        GameExperience.id.in_(ids or [-1])).order_by(GameExperience.id)))
    if not chosen:
        out.awaiting.append("game experience missing or removed")
        return
    first = chosen[0].id
    before = list(db.scalars(select(GameExperience).where(
        GameExperience.user_id == row.user_id, GameExperience.session_id == row.game_session_id,
        GameExperience.id < first, GameExperience.occurred_at >= cutoff).order_by(GameExperience.id.desc()).limit(GAME_BEFORE + GAME_MORE * plan.game)))
    if before:
        ev.can_look["game"] = "more earlier game outcomes and detailed capture"
    by_attempt = {}
    for e in chosen:
        aid = _uuid((e.payload or {}).get("attempt_id"))
        if aid is None or str(aid) in by_attempt:
            continue
        href = f"h{len(by_attempt) + 1}"
        by_attempt[str(aid)] = href
        record = db.get(DecisionAttempt, aid)
        record = record if record is not None and record.user_id == row.user_id and _aware(record.expires_at) > _now() else None
        ev.targets[href] = A.Target(href, "hands", "her hands' decision", f"run:{aid}", (f"attempt:{aid}",))
        out.target_roots[href] = [f"attempt:{aid}", f"execution:{execution_id_for(aid, 'skill_run')}"]
        out.attempts[href] = {"hands": str(aid)}
        if record is None:
            out.awaiting.append(f"attempt:{aid}")
        ev.add(A.Item(ref=f"{href}.d", kind="record", learner="hands", event=f"run:{aid}",
                      roots=(f"attempt:{aid}",), target=href, captured=record is not None,
                      text=(f"her hands' decision ({record.status}): {_record_summary(record)}; "
                            f"available actions: {_game_excerpt((record.versions or {}).get('manifest'))}; "
                            f"plan and step read: {_game_excerpt((record.input or {}).get('plan'))}"
                            if record else "hands decision: missing or expired capture"),
                      detail=_game_input(db, record) if record and plan.inputs else None))
    # A stretch remains assessable when no hands decision was captured (ordinary play, notices,
    # trials). That never establishes the quality of an uncaptured choice.
    ev.targets["s1"] = A.Target("s1", "stretch", "the recorded stretch of play", f"assessment:{row.id}",
                                tuple(f"experience:{e.id}" for e in chosen))
    out.target_roots["s1"] = list(ev.targets["s1"].roots)
    for e in sorted(before + chosen, key=lambda x: x.id):
        p = e.payload or {}
        aid = str(_uuid(p.get("attempt_id")) or "")
        ev.add(A.Item(ref=f"g{e.id}", kind="game", fact_kind="skill" if e.kind == "skill_outcome" else "notice",
                      event=f"run:{aid}" if aid else f"experience:{e.id}", roots=(f"experience:{e.id}",),
                      target=by_attempt.get(aid, "s1"),
                      text=f"[{_hm(e.occurred_at)}] {e.kind}: {_game_excerpt(p)}; counted while it ran: {json.dumps((p.get('data') or {}).get('counted'))}. "
                           "A recorded report, not proof of the cause or the quality of the choice."))
    since = max(cutoff, min(_aware(e.occurred_at) for e in chosen) - timedelta(seconds=30))
    until = max(_aware(e.occurred_at) for e in chosen) + timedelta(seconds=5)
    # Include plans, applied rules, thoughts, measured outcomes and all three reports from step 1.
    records = list(db.scalars(select(DecisionAttempt).where(
        DecisionAttempt.user_id == row.user_id, DecisionAttempt.game_session_id == row.game_session_id,
        DecisionAttempt.started_at >= since, DecisionAttempt.started_at <= until,
        DecisionAttempt.expires_at > _now())
        .order_by(DecisionAttempt.started_at).limit(100)))
    for record in records:
        target = by_attempt.get(str(record.id), "s1")
        if record.learner == "hands" and target != "s1":
            continue
        ev.add(A.Item(ref=f"a{record.id.hex}", kind="record", learner=record.learner,
                      event=f"attempt:{record.id}", roots=(f"attempt:{record.id}",), target=target,
                      text=f"{record.learner} captured answer: {_game_excerpt(record.output)}",
                      detail=_game_input(db, record) if plan.inputs else None))
    executions = list(db.scalars(select(DecisionExecution).where(
        DecisionExecution.user_id == row.user_id, DecisionExecution.game_session_id == row.game_session_id,
        DecisionExecution.expires_at > _now(),
        ((DecisionExecution.attempt_id.in_([r.id for r in records] + [uuid.UUID(a) for a in by_attempt])) |
         ((DecisionExecution.ended_at >= since) & (DecisionExecution.ended_at <= until))))
        .order_by(DecisionExecution.captured_at).limit(150)))
    for ex in executions:
        ev.add(A.Item(ref=f"x{ex.id.hex}", kind="fact", fact_kind="skill" if ex.kind == "skill_run" else ex.kind,
                      event=f"run:{ex.attempt_id}" if ex.attempt_id else f"execution:{ex.id}",
                      roots=(f"execution:{ex.id}",), target=by_attempt.get(str(ex.attempt_id), "s1"),
                      text=_game_excerpt(capture_chains.execution_out(ex))))
    held_executions = {str(e.attempt_id) for e in executions if e.kind == "skill_run"}
    for aid in by_attempt:
        if aid not in held_executions:
            out.awaiting.append(f"execution:{execution_id_for(uuid.UUID(aid), 'skill_run')}")
    gaps = list(db.scalars(select(CaptureGap).where(
        CaptureGap.user_id == row.user_id,
        CaptureGap.record_id.in_([uuid.UUID(a) for a in by_attempt] +
                                [execution_id_for(uuid.UUID(a), 'skill_run') for a in by_attempt]))))
    for gap in gaps:
        ev.add(A.Item(ref=f"gap{gap.id}", kind="fact", event=f"gap:{gap.id}", roots=(f"gap:{gap.id}",),
                      text=f"Missing capture: {gap.record_id} ({gap.reason}): {gap.detail or ''}"))
    if len(records) == 100 or len(executions) == 150:
        out.awaiting.append("capture window truncated at 100 attempts / 150 executions")
    events = list(db.scalars(select(DesktopSessionEvent).where(
        DesktopSessionEvent.session_id == row.game_session_id,
        DesktopSessionEvent.occurred_at >= since, DesktopSessionEvent.occurred_at <= until)
        .order_by(DesktopSessionEvent.id).limit(100)))
    goal = db.scalar(select(DesktopSessionEvent).where(
        DesktopSessionEvent.session_id == row.game_session_id, DesktopSessionEvent.type == "desktop.plan_set",
        DesktopSessionEvent.occurred_at < since).order_by(DesktopSessionEvent.id.desc()).limit(1))
    for e in ([goal] if goal else []) + events:
        ev.add(A.Item(ref=f"p{e.id}", kind="fact", event=f"session_event:{e.id}",
                      roots=(f"session_event:{e.id}",), target="s1",
                      text=f"[{_hm(e.occurred_at)}] {e.type}: {_game_excerpt(e.payload)}"))
    streams = trajectory.streams(db, row.user_id, session_id=row.game_session_id, since=since, until=until)
    if not streams:
        out.awaiting.append("trajectory missing or expired for this stretch")
    for st in streams:
        detail = trajectory.read(db, row.user_id, uuid.UUID(st["id"]), since=since, until=until,
                                 ticks=False, max_chunks=20 + 20 * plan.game)
        if detail:
            events = detail.pop("events", [])
            bound = 12 + 12 * plan.game
            if len(events) > bound:
                detail["events_omitted"] = len(events) - bound
                ev.can_look["game"] = "more trajectory events from the recorded stretch"
                events = events[:bound // 2] + events[-bound // 2:]
            detail["events"] = [_game_excerpt(e, 900) for e in events]
            ev.add(A.Item(ref=f"t{st['id'].replace('-', '')}", kind="game", fact_kind="trajectory",
                          event=f"trajectory:{st['id']}", roots=(f"trajectory:{st['id']}",), target="s1",
                          text="Pack clock and coverage (not the backend clock): " + json.dumps(detail, default=str)))


def _quotes(text: str) -> list[str]:
    found = re.findall(r'"([^"]+)"|“([^”]+)”|(?<!\w)\'([^\']+)\'(?!\w)', text or "")
    return [q.strip() for groups in found for q in groups if q and len(q.strip()) >= QUOTE_MIN]


def _trigger_text(row: LearningAssessment, ev: A.Evidence, lines: dict, anchor: ChatMessage | None) -> tuple[str, tuple]:
    where = lines.get(anchor.id) if anchor is not None else None
    why = (row.trigger or {}).get("why") or row.trigger_kind
    refs = (where,) if where else ()
    if row.trigger_kind in ("correction", "complaint"):
        return (f"The user's message {where} ({why}). Assess what it is about and what, if anything, it says about "
                "the parts of her involved.", refs)
    if row.trigger_kind == "sample":
        return (f"An ordinary exchange, sampled ({why}): assess how her turn(s) before {where} went, with the user's "
                f"message {where} as their response. There may be no feedback in it at all.", refs)
    game = [ref for ref in ev.targets if ref.startswith("h")]
    return (f"Recorded experience: {why}" + (f" ({where})." if where else ".")
            + (f" Targets: {', '.join(game)}." if game else ""), refs + tuple(game))


# --------------------------------------------------------------------------- #
# Running one assessment
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Claim:
    id: uuid.UUID
    attempts: int
    epoch: int


class EvaluationFailed(RuntimeError):
    pass


def _evaluator() -> Callable[[str], A.Answer]:
    s = get_settings()
    return A.Evaluator(s.learning_assessment_model, api_key=s.openai_api_key,
                       reasoning_effort=s.learning_assessment_reasoning_effort,
                       timeout=s.learning_assessment_timeout_seconds)


def claim(db: Session, now: datetime | None = None) -> Claim | None:
    """Take the next due assessment for a lease (a run whose lease ran out is taken again). Commits."""
    now = now or _now()
    row = db.scalar(select(LearningAssessment).where(or_(
        (LearningAssessment.state == "pending") & (LearningAssessment.next_attempt_at <= now),
        (LearningAssessment.state == "running") & (LearningAssessment.lease_expires_at < now),
    )).order_by(LearningAssessment.next_attempt_at).limit(1).with_for_update(skip_locked=True))
    if row is None:
        db.rollback()
        return None
    row.state, row.attempts, row.lease_expires_at = "running", row.attempts + 1, now + LEASE
    found = Claim(row.id, row.attempts, row.epoch)
    db.commit()
    return found


def run_once(evaluator: Callable[[str], A.Answer] | None = None) -> bool:
    """The learning-assessment worker: triggers, the sweep every SWEEP_SECONDS, then at most one assessment."""
    global _last_sweep
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        triaged = triage(db)
    if time.monotonic() - _last_sweep >= SWEEP_SECONDS:
        _last_sweep = time.monotonic()
        with SessionLocal() as db:
            sweep(db)
    with SessionLocal() as db:
        claimed = claim(db)
    if claimed is None:
        return triaged > 0
    with SessionLocal() as db:
        assess(db, claimed, evaluator=evaluator)
    return True


def assess(db: Session, claimed: Claim, *, evaluator: Callable[[str], A.Answer] | None = None) -> str:
    """Investigate one claimed assessment and write its revision. Returns what happened."""
    evaluate = evaluator or _evaluator()
    name = getattr(evaluate, "name", None) or f"{A.PROMPT_VERSION}:{getattr(evaluate, 'model', 'scripted')}"
    try:
        found = _investigate(db, claimed, evaluate)
    except Exception as exc:  # noqa: BLE001 - a failed evaluation retries; it never becomes a conclusion
        log.exception("learning assessment %s: evaluation failed", claimed.id)
        db.rollback()
        return _fail(db, claimed, f"{type(exc).__name__}: {exc}"[:500])
    if found is None:
        return "gone"
    return _write(db, claimed, found, name)


@dataclass
class _Found:
    gathered: Gathered
    checked: A.Checked
    fingerprint: str
    rounds: list[dict]
    stop: str
    stop_why: str
    usage: dict


def _investigate(db: Session, claimed: Claim, evaluate: Callable[[str], A.Answer]) -> _Found | None:
    s = get_settings()
    row = db.get(LearningAssessment, claimed.id)
    if row is None:
        return None
    prior = None
    if row.revision:
        prior = db.scalar(select(LearningAssessmentRevision.result).where(
            LearningAssessmentRevision.assessment_id == row.id, LearningAssessmentRevision.revision == row.revision))
    plan = Plan(game=0)
    rounds: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    last: tuple[Gathered, A.Checked, str] | None = None
    previous_digest = None
    stop, why = "concluded", ""
    while True:
        gathered = gather(db, row, plan, prior=prior)
        db.rollback()  # nothing held while the model thinks
        row = db.get(LearningAssessment, claimed.id)
        if row is None:
            return None
        current = A.digest(gathered.ev)
        if previous_digest is not None and current == previous_digest:
            stop, why = "stuck", "what it asked to look at added nothing new"
            break
        text = A.render(gathered.ev)
        if len(text) > s.learning_assessment_max_chars:
            if last is None:
                checked = A.check({"feedback": "unclear", "missing": [{
                    "what": "evidence small enough to read at once", "needed_for": "any conclusion"}]}, gathered.ev)
                last = (gathered, checked, A.fingerprint(checked.result))
                stop, why = "missing_evidence", "the evidence is more than can be read at once"
            else:
                stop, why = "stuck", "looking further would be more than can be read at once"
            rounds.append({"plan": plan.to_dict(), "digest": current, "chars": len(text), "skipped": why})
            break
        answer = evaluate(text)
        usage["calls"] += 1
        usage["input_tokens"] += answer.input_tokens or 0
        usage["output_tokens"] += answer.output_tokens or 0
        if answer.raw is None:
            raise EvaluationFailed(answer.error or "no answer")
        checked = A.check(answer.raw, gathered.ev)
        fp = A.fingerprint(checked.result)
        nxt = checked.result["next"]
        rounds.append({"plan": plan.to_dict(), "digest": current, "chars": len(text), "fingerprint": fp,
                       "next": nxt, "dropped": len(checked.dropped), "latency_ms": answer.latency_ms,
                       "summary": A.summarize(checked.result)})
        last = (gathered, checked, fp)
        if nxt["step"] == "conclude":
            stop, why = "concluded", nxt["why"]
            break
        if nxt["step"] == "need_missing":
            stop, why = "missing_evidence", nxt["why"]
            break
        if not nxt["look"]:
            stop, why = "stuck", "asked to look further, at nothing that's available"
            break
        previous_digest = current
        plan = plan.expand(nxt["look"])
    gathered, checked, fp = last
    return _Found(gathered, checked, fp, rounds, stop, why, usage)


def _write(db: Session, claimed: Claim, found: _Found, evaluator: str) -> str:
    now = _now()
    row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == claimed.id).with_for_update())
    if row is None or row.state != "running" or row.attempts != claimed.attempts:
        db.rollback()  # settled or taken again meanwhile
        return "gone"
    result = found.checked.result
    digest = A.digest(found.gathered.ev)
    current = db.scalar(select(LearningAssessmentRevision).where(
        LearningAssessmentRevision.assessment_id == row.id, LearningAssessmentRevision.revision == row.revision)) \
        if row.revision else None
    outcome = "unchanged"
    if current is not None and current.input_digest == digest and current.prompt_version == A.PROMPT_VERSION:
        # The same evidence read again: not a new revision (repetition is not evidence).
        _log({"op": "unchanged", "assessment": str(row.id), "revision": row.revision})
    else:
        outcome = _revise(db, row, current, found, evaluator, digest, now)
    row.lease_expires_at, row.last_error = None, None
    if row.epoch != claimed.epoch:
        row.state, row.next_attempt_at = "pending", now  # evidence arrived while it ran
    else:
        row.state = "done"
    db.commit()
    return outcome


def _revise(db: Session, row: LearningAssessment, current: LearningAssessmentRevision | None, found: _Found,
            evaluator: str, digest: str, now: datetime) -> str:
    result, gathered = found.checked.result, found.gathered
    summary = A.summarize(result)
    n = row.revision + 1
    if current is None:
        reason = "first assessment"
    else:
        added = list(db.scalars(select(LearningAssessmentEvidence.ref).where(
            LearningAssessmentEvidence.assessment_id == row.id,
            LearningAssessmentEvidence.added_at > current.created_at)))
        reason = (f"new evidence: {', '.join(added)}" if added else "the evidence available changed")
        current.superseded_at, current.superseded_by = now, n
    stop = found.stop
    stored = _store_result(result, gathered)
    db.add(LearningAssessmentRevision(
        assessment_id=row.id, user_id=row.user_id, revision=n, evaluator=evaluator[:120],
        prompt_version=A.PROMPT_VERSION, reason=reason, input_digest=digest, fingerprint=found.fingerprint,
        result=stored, dropped=found.checked.dropped, rounds=found.rounds, outcome=summary["outcome"],
        target_status=summary["target_status"], cause_status=summary["cause_status"], stop_reason=stop,
        independent=A.independent_events(result),
        explanation=result["explanation"] or f"({found.stop_why})", usage=found.usage))
    db.flush()
    _links(db, row, n, result, gathered)
    row.revision, row.outcome = n, summary["outcome"]
    row.target_status, row.cause_status, row.stop_reason = summary["target_status"], summary["cause_status"], stop
    row.explanation = result["explanation"] or found.stop_why
    row.awaiting = sorted(set(gathered.awaiting))
    _owners(db, row, n, result, gathered, changed=current is None or current.fingerprint != found.fingerprint)
    # Lessons (docs/learning-system/03-implementation.md): learning reads this revision for what should persist.
    from app.services import learning_lessons
    learning_lessons.on_revision(db, row, n, result)
    from app.services import memory_episodes
    memory_episodes.from_assessment(db, row, n, result.get("episode"))
    for entry in result["reopens"]:
        other = db.scalar(select(LearningAssessment).where(LearningAssessment.id == _uuid(entry["id"]),
                                                           LearningAssessment.user_id == row.user_id).with_for_update())
        if other is None:
            continue
        added = attach(db, other, [(root, f"assessment {row.id} found this bears on it", {})
                                   for root in entry["roots"] if root.startswith(("message:", "experience:", "execution:", "attempt:"))])
        if added:
            _reopen(other, now, f"new evidence: {', '.join(added)}")
    _log({"op": "revision", "assessment": str(row.id), "revision": n, "outcome": summary["outcome"],
          "target": summary["target_status"], "cause": summary["cause_status"], "stop": stop,
          "rounds": len(found.rounds), "dropped": len(found.checked.dropped), **found.usage})
    return "revised"


def _store_result(result: dict, gathered: Gathered) -> dict:
    """The checked result with each target's stored refs, so it can be read after the raw captures are gone."""
    out = json.loads(json.dumps(result))
    for t in out["targets"]:
        t["target_roots"] = gathered.target_roots.get(t["ref"], [])
    out["target_labels"] = {ref: t.label for ref, t in gathered.ev.targets.items()}
    out["trigger"] = gathered.ev.trigger
    return out


def _links(db: Session, row: LearningAssessment, n: int, result: dict, gathered: Gathered) -> None:
    values: dict[tuple, dict] = {}

    def link(role: str, ref: str, status: str | None = None, component: str = "") -> None:
        values.setdefault((role, ref[:100], component), {"assessment_id": row.id, "user_id": row.user_id,
                                                         "revision": n, "role": role, "ref": ref[:100],
                                                         "component": component, "status": status})

    for t in result["targets"]:
        for ref in (t["ref"], *t["also"]):
            for root in gathered.target_roots.get(ref, []):
                link("target", root, t["status"])
    for c in result["causes"]:
        own = gathered.attempts.get(c["target"], {}).get(c["component"])
        for root in [f"attempt:{own}"] if own else gathered.target_roots.get(c["target"], [])[:1]:
            link("cause", root, c["status"], c["component"])
    for key in ("targets", "causes", "user_stated", "dimensions", "choice_vs_outcome", "reopens"):
        for entry in result[key]:
            for root in entry.get("roots", []):
                link("cited", root)
    if values:
        db.execute(insert(LearningAssessmentLink).values(list(values.values())).on_conflict_do_nothing())


# --------------------------------------------------------------------------- #
# Owners of conclusions: flagged for reconsideration through them
# --------------------------------------------------------------------------- #


def note_use(db: Session, row: LearningAssessment, revision: int, consumer: str, ref: str,
             reason: str | None = None) -> None:
    """An owner acted on this assessment's conclusion (``ref`` names what it changed)."""
    statement = insert(LearningAssessmentUse).values(
        assessment_id=row.id, user_id=row.user_id, revision=revision, consumer=consumer, ref=ref[:100],
        state="current", reason=reason)
    db.execute(statement.on_conflict_do_update(constraint="uq_learning_assessment_uses", set_={
        "revision": revision, "state": "current", "reason": reason, "changed_at": func.now()}))


def _faulted_choices(result: dict, gathered: Gathered) -> dict[str, str]:
    """Student 2 attempts a supported cause found at fault: attempt id -> why."""
    out = {}
    for c in result["causes"]:
        if c["component"] == "student_2" and c["status"] == "supported":
            attempt = gathered.attempts.get(c["target"], {}).get("student_2")
            if attempt:
                out[attempt] = c["why"]
    return out


def _owners(db: Session, row: LearningAssessment, n: int, result: dict, gathered: Gathered, *, changed: bool) -> None:
    """Training examples whose label is a choice this revision found at fault are flagged for review (their owner's
    rule: ineligible until reviewed). When a revision changes the conclusions, uses that no longer hold are marked
    ``reconsider`` and their owner is told; the owner decides."""
    from app.services import training_examples

    faulted = _faulted_choices(result, gathered)
    flagged = set()
    for attempt, why in faulted.items():
        for example_id in training_examples.flag_for_review(
                db, row.user_id, uuid.UUID(attempt),
                reason=f"assessment {row.id} (revision {n}) supports that Student 2's choice was at fault: {why}"):
            ref = f"example:{example_id}"
            flagged.add(ref)
            note_use(db, row, n, "training_examples", ref, reason=f"flagged: {why}"[:500])
    if not changed:
        return
    uses = list(db.scalars(select(LearningAssessmentUse).where(LearningAssessmentUse.assessment_id == row.id,
                                                               LearningAssessmentUse.state == "current",
                                                               LearningAssessmentUse.revision < n)))
    for use in uses:
        if use.ref in flagged:
            continue  # still holds: note_use moved it to this revision
        use.state, use.changed_at = "reconsider", _now()
        use.reason = f"revision {n} no longer supports the conclusion it acted on"
        RECONSIDER.get(use.consumer, _nobody)(db, use, row, n)


def _nobody(db: Session, use: LearningAssessmentUse, row: LearningAssessment, n: int) -> None:
    log.warning("learning assessment: no owner registered for %s", use.consumer)


def _examples_reconsider(db: Session, use: LearningAssessmentUse, row: LearningAssessment, n: int) -> None:
    from app.services import training_examples

    example_id = _uuid(use.ref.split(":", 1)[1])
    if example_id is not None:
        training_examples.reconsider(db, row.user_id, example_id,
                                     note=f"assessment {row.id} was revised (revision {n}) and no longer supports it")


def _lessons_reconsider(db: Session, use: LearningAssessmentUse, row: LearningAssessment, n: int) -> None:
    from app.services import learning_lessons

    learning_lessons.reconsider(db, use, row, n)


RECONSIDER: dict[str, Callable[[Session, LearningAssessmentUse, LearningAssessment, int], None]] = {
    "training_examples": _examples_reconsider,
    "lessons": _lessons_reconsider,
}


def _fail(db: Session, claimed: Claim, error: str) -> str:
    row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == claimed.id).with_for_update())
    if row is None or row.state != "running" or row.attempts != claimed.attempts:
        db.rollback()
        return "gone"
    now = _now()
    row.last_error, row.lease_expires_at = error, None
    if row.attempts >= get_settings().learning_assessment_max_attempts:
        row.state = "failed"
    else:
        row.state, row.next_attempt_at = "pending", now + min(timedelta(seconds=30 * 2 ** row.attempts), MAX_BACKOFF)
    db.commit()
    _log({"op": "failed", "assessment": str(row.id), "state": row.state, "attempts": row.attempts, "error": error})
    return row.state


# --------------------------------------------------------------------------- #
# The user's corrections, forgetting, inspection
# --------------------------------------------------------------------------- #


def add_note(db: Session, user_id: uuid.UUID, assessment: uuid.UUID, text: str) -> LearningAssessment | None:
    """The user correcting an assessment: their note is new evidence (authoritative about what they meant and
    prefer), and the assessment is evaluated again. Commits."""
    row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == assessment,
                                                     LearningAssessment.user_id == user_id).with_for_update())
    if row is None:
        return None
    words = " ".join(text.split())[:2000]
    ref = f"note:{uuid.uuid4()}"
    if attach(db, row, [(ref, "the user's note", {"text": words})]):
        _reopen(row, _now(), "the user's note")
        row.next_attempt_at = _now()
    db.commit()
    return row


def drop_for(db: Session, user_id: uuid.UUID, refs: Iterable[str]) -> int:
    """Forgetting: assessments that read forgotten messages go (in the caller's transaction). An assessment's
    window can show a message without citing it, so every assessment in that conversation from that message on
    goes too, and so does an earlier one that looked at later messages."""
    refs = sorted(set(refs))
    messages = [m for m in (_uuid(r.split(":", 1)[1]) for r in refs if r.startswith("message:")) if m]
    if not messages:
        return 0
    ids = set(db.scalars(select(LearningAssessmentLink.assessment_id).where(
        LearningAssessmentLink.user_id == user_id, LearningAssessmentLink.ref.in_(refs))))
    ids |= set(db.scalars(select(LearningAssessmentEvidence.assessment_id).where(
        LearningAssessmentEvidence.user_id == user_id, LearningAssessmentEvidence.ref.in_(refs))))
    ids |= set(db.scalars(select(LearningAssessment.id).where(LearningAssessment.user_id == user_id,
                                                              LearningAssessment.anchor_message_id.in_(messages))))
    for conversation_id, at in db.execute(select(ChatMessage.conversation_id, ChatMessage.created_at)
                                          .where(ChatMessage.id.in_(messages))):
        ids |= set(db.scalars(select(LearningAssessment.id).where(
            LearningAssessment.user_id == user_id, LearningAssessment.conversation_id == conversation_id,
            LearningAssessment.anchor_at >= _aware(at))))
        looked_later = select(LearningAssessmentRevision.assessment_id).where(
            LearningAssessmentRevision.user_id == user_id,
            LearningAssessmentRevision.rounds.contains([{"plan": {"later": True}}]))
        ids |= set(db.scalars(select(LearningAssessment.id).where(
            LearningAssessment.user_id == user_id, LearningAssessment.conversation_id == conversation_id,
            LearningAssessment.id.in_(looked_later))))
    if not ids:
        return 0
    return db.execute(delete(LearningAssessment).where(LearningAssessment.user_id == user_id,
                                                       LearningAssessment.id.in_(ids))).rowcount or 0


def _summary(row: LearningAssessment) -> dict:
    return {"id": str(row.id), "trigger_kind": row.trigger_kind, "trigger": row.trigger, "state": row.state,
            "outcome": row.outcome, "target_status": row.target_status, "cause_status": row.cause_status,
            "stop_reason": row.stop_reason, "explanation": row.explanation, "revision": row.revision,
            "attempts": row.attempts, "last_error": row.last_error, "awaiting": row.awaiting,
            "conversation_id": str(row.conversation_id) if row.conversation_id else None,
            "game_session_id": str(row.game_session_id) if row.game_session_id else None,
            "anchor_message_id": str(row.anchor_message_id) if row.anchor_message_id else None,
            "anchor_at": _aware(row.anchor_at).isoformat(), "updated_at": _aware(row.updated_at).isoformat()}


def listing(db: Session, user_id: uuid.UUID, *, outcome: str | None = None, limit: int = 50) -> dict:
    counts = {f"{state}:{out or 'none'}": n for state, out, n in db.execute(
        select(LearningAssessment.state, LearningAssessment.outcome, func.count()).where(
            LearningAssessment.user_id == user_id).group_by(LearningAssessment.state, LearningAssessment.outcome))}
    query = select(LearningAssessment).where(LearningAssessment.user_id == user_id)
    if outcome:
        query = query.where(LearningAssessment.outcome == outcome)
    rows = list(db.scalars(query.order_by(LearningAssessment.updated_at.desc()).limit(limit)))
    return {"counts": counts, "items": [_summary(r) for r in rows]}


def detail(db: Session, user_id: uuid.UUID, assessment: uuid.UUID) -> dict | None:
    row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == assessment,
                                                     LearningAssessment.user_id == user_id))
    if row is None:
        return None
    revisions = list(db.scalars(select(LearningAssessmentRevision).where(
        LearningAssessmentRevision.assessment_id == row.id).order_by(LearningAssessmentRevision.revision.desc())))
    evidence = list(db.scalars(select(LearningAssessmentEvidence).where(
        LearningAssessmentEvidence.assessment_id == row.id).order_by(LearningAssessmentEvidence.id)))
    links = list(db.scalars(select(LearningAssessmentLink).where(
        LearningAssessmentLink.assessment_id == row.id, LearningAssessmentLink.revision == row.revision)))
    uses = list(db.scalars(select(LearningAssessmentUse).where(LearningAssessmentUse.assessment_id == row.id)))
    return {**_summary(row),
            "evidence": [{"ref": e.ref, "reason": e.reason, "detail": e.detail,
                          "added_at": _aware(e.added_at).isoformat()} for e in evidence],
            "revisions": [{"revision": r.revision, "evaluator": r.evaluator, "reason": r.reason,
                           "outcome": r.outcome, "target_status": r.target_status, "cause_status": r.cause_status,
                           "stop_reason": r.stop_reason, "independent": r.independent, "explanation": r.explanation,
                           "result": r.result, "dropped": r.dropped, "rounds": r.rounds, "usage": r.usage,
                           "superseded_by": r.superseded_by,
                           "superseded_at": _aware(r.superseded_at).isoformat() if r.superseded_at else None,
                           "created_at": _aware(r.created_at).isoformat()} for r in revisions],
            "links": [{"role": li.role, "ref": li.ref, "component": li.component or None, "status": li.status}
                      for li in links],
            "uses": [{"consumer": u.consumer, "ref": u.ref, "revision": u.revision, "state": u.state,
                      "reason": u.reason} for u in uses]}


def for_ref(db: Session, user_id: uuid.UUID, ref: str) -> list[dict]:
    """Assessments that target, blame or cite a record (``attempt:<id>``, ``message:<id>``, ``turn:<id>``)."""
    ids = select(LearningAssessmentLink.assessment_id).where(LearningAssessmentLink.user_id == user_id,
                                                             LearningAssessmentLink.ref == ref)
    rows = db.scalars(select(LearningAssessment).where(LearningAssessment.user_id == user_id,
                                                       LearningAssessment.id.in_(ids)))
    return [_summary(r) for r in rows]


def forget_user(db: Session, user_id: uuid.UUID) -> int:
    return db.execute(delete(LearningAssessment).where(LearningAssessment.user_id == user_id)).rowcount or 0


def _log(record: dict) -> None:
    path = Path(get_settings().learning_assessment_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **record}, default=str) + "\n")
    except OSError:
        log.warning("learning assessment: could not write %s", path)


__all__ = ["triage", "sweep", "claim", "assess", "run_once", "gather", "open_assessment", "attach", "add_note",
           "drop_for", "listing", "detail", "for_ref", "note_use", "sampled", "Plan", "Claim", "RECONSIDER"]
