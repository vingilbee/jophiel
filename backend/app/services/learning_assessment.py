"""Evidence and attribution: learning's retrospective assessment
(docs/learning-system/02-evidence-and-attribution.md; implementation: docs/learning-system/02-implementation.md).

Learning reads what happened and what the evidence supports. It never speaks, never acts and never writes memory.

1. **Triggers** (code only; post-audit 3, ``services/learning_review.py``). The ``learning`` consumer of
   observations: every completed exchange extends the conversation's ``review`` stretch, an opportunity the
   reviewer judges (examine, wait or leave), never a verdict. Student 1 reading a message as a correction or a
   complaint, and memory reading it as correcting an earlier statement, are signals shown on the stretch, not
   reasons of their own. An interrupted spoken reply, a failed delivery, a failed skill run and a critical game
   notice stay ``execution`` facts with their own assessment. A sweep adds what isn't an observation: a failed
   or cut-off reply, a failed effect, her last reply in a conversation gone quiet, ongoing game play. One
   assessment per trigger key, so reprocessing never duplicates one; evidence is attached once per ref.
2. **Investigation.** Code gathers the conversation around the trigger (heard text, forgotten parts withheld),
   the turns before it as targets with their captured records (Student 1's reading, Student 2's decision, the
   reply's delivery, what was heard, the effect), facts checked by code (each says what it establishes and what
   it doesn't), quotes of her words, memory corrections, game outcomes and her hands' decisions, the user's
   notes, and other open assessments in the conversation. One evaluator call per round
   (``app/ai/learning/assessment.py``); code checks every ref, quote and component claim. The evaluator may ask
   to look further (earlier messages, later ones, the exact learner inputs, more game outcomes) and ask memory,
   her other conversations, other game experience and her existing lessons (``requests``). There is no round
   budget: it stops when it concludes, leaves it, waits for more, when what it needs is missing, or when it is
   stuck (what it asked for adds nothing new, or is more than can be read at once). Every stop but a conclusion
   parks a resumable inquiry (``inquiry``: question, provisional reading, alternatives, unknowns, what would
   resume it); a wake whose evidence is unchanged reads nothing again and calls no model.
3. **Revisions.** Each evaluation that read different evidence is a new revision; the one before is superseded
   and kept. Reading the same evidence again is not a revision (repetition is not evidence). New evidence
   (a capture that arrives late, a correction, a later message another assessment found relevant, the user's
   note) reopens an assessment. When conclusions change, every owner that acted on the earlier one is told to
   reconsider (training examples flagged when Student 2's choice in that turn was found at fault; lessons that
   rested on the earlier reading, docs/learning-system/03-implementation.md).
"""
from __future__ import annotations

import dataclasses
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

from sqlalchemy import Integer, String, cast, delete, exists, false, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.learning import assessment as A
from app.ai.observe import registry
from app.core.config import get_settings
from app.db.models import (
    CaptureGap, ChatMessage, Conversation, DecisionAttempt, DecisionExecution, DesktopSession, GameExperience, LearningAssessment,
    LearningAssessmentEvidence, LearningAssessmentLink, LearningAssessmentRevision, LearningAssessmentUse,
    MemoryCorrection, Observation, ObservationProcessing, TrainingExample, User,
)
from app.services.learning_capture import execution_id_for

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "learning"
TRIAGE_VERSION = "learning-triage-v3"
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
OPEN_ELSEWHERE = 5  # the user's parked inquiries in other conversations or sessions, most recent first
REQUEST_RESULTS = 6  # results shown per request (an answer is a page; asking again more precisely gets others)
RELEVANT_LESSONS = 3  # her existing lessons shown without being asked, the most relevant to what is read (post-audit 4)
RELEVANT_MEMORIES = 4  # what memory holds (facts, her open items) shown without being asked, the most relevant
PRIORITY = {"review": 0, "sample": 0, "execution": 1, "complaint": 2, "correction": 3}
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
    """A completed exchange: an opportunity on the conversation's review stretch. Whether it says anything is the
    reviewer's to judge; Student 1's reading of it is read as a signal when the stretch is gathered."""
    from app.services import learning_review

    message = db.get(ChatMessage, _uuid(p.get("message_id")))
    if message is None or message.role != "user":
        return "skipped", "the message is gone"
    before = db.scalar(select(ChatMessage.id).where(
        ChatMessage.conversation_id == message.conversation_id, ChatMessage.role == "assistant",
        ChatMessage.conversation_sequence < message.conversation_sequence).limit(1))
    if before is None:
        return "skipped", "nothing of hers before it"
    learning_review.conversation(db, observation.user_id, message, why="a completed exchange")
    return "applied", "a completed exchange: a review opportunity"


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
    done = {"replies": 0, "effects": 0, "corrections": 0, "arrived": 0, "pauses": 0, "games": 0}

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
    from app.services import learning_review
    for row in list(corrections):
        message = db.get(ChatMessage, row.source_message_id)
        from app.services.memory_reset import learning_cutoff
        if message is None or _aware(message.created_at) < learning_cutoff(db, row.user_id):
            continue
        # A signal on the stretch the message is in, never a reason of its own.
        learning_review.conversation(db, row.user_id, message, why="memory read it as correcting an earlier statement",
                                     evidence=[(f"correction:{row.id}", "memory read this message as a correction", {})])
        done["corrections"] += 1
    done["pauses"] = learning_review.sweep_pauses(db, now, horizon, limit=limit)
    done["games"] = learning_review.sweep_games(db, now)
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
    # What it asked memory and the other owners for, in order: ((source, query), ...). Kept across readings.
    requests: tuple[tuple[str, str], ...] = ()

    def expand(self, looks: Iterable[str], requests: Iterable[dict] = ()) -> "Plan":
        looks = set(looks)
        known = {(src, A._norm(q)) for src, q in self.requests}
        asked = list(self.requests)
        for r in requests:
            if (r["source"], A._norm(r["query"])) not in known:
                known.add((r["source"], A._norm(r["query"])))
                asked.append((r["source"], r["query"]))
        return Plan(earlier=self.earlier + ("earlier" in looks), later=self.later or "later" in looks,
                    inputs=self.inputs or "inputs" in looks, game=self.game + ("game" in looks),
                    requests=tuple(asked))

    def to_dict(self) -> dict:
        return {"earlier": self.earlier, "later": self.later, "inputs": self.inputs, "game": self.game,
                "requests": [list(r) for r in self.requests]}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Plan":
        data = data or {}
        return cls(earlier=int(data.get("earlier") or 0), later=bool(data.get("later")),
                   inputs=bool(data.get("inputs")), game=int(data.get("game") or 0),
                   requests=tuple((str(a), str(b)) for a, b in (data.get("requests") or []) if a in A.SOURCES))


@dataclass
class Gathered:
    ev: A.Evidence
    target_roots: dict[str, list[str]] = field(default_factory=dict)  # target ref -> stored refs
    attempts: dict[str, dict[str, str]] = field(default_factory=dict)  # target ref -> learner -> attempt id
    awaiting: list[str] = field(default_factory=list)  # attempt:<id> still missing that may yet arrive


def _hm(at: datetime | None) -> str:
    # With the weekday: when something happens in the week can be what a pattern is about.
    return _aware(at).strftime("%a %Y-%m-%d %H:%M:%S") if at else "?"


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
    stretch = row.trigger_kind == "review" and row.game_session_id is None and "up_to_seq" in (row.trigger or {})
    if row.conversation_id is not None:
        if stretch:
            upto = int(row.trigger["up_to_seq"])
        elif anchor is not None:
            upto = anchor.conversation_sequence
        else:
            upto = db.scalar(select(func.max(ChatMessage.conversation_sequence)).where(
                ChatMessage.conversation_id == row.conversation_id, ChatMessage.created_at <= row.anchor_at)) or 0
        window = s.learning_assessment_window_messages * 2 ** plan.earlier
        if stretch:  # the whole stretch, and the window before it
            window += max(0, upto - int(row.trigger.get("from_seq") or upto) + 1)
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
        if stretch and m.conversation_sequence == int(row.trigger.get("from_seq") or -1) and m.id != row.anchor_message_id:
            notes.append("this stretch begins here")
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

    # Evidence another assessment found bearing on this one, from another conversation or game session.
    _elsewhere(db, row, ev, attached)

    # Signals: labels other parts gave, shown as such (post-audit 3). Never evidence, never the reason to assess.
    ev.signals = _signals(db, row, messages, lines, evidence_rows)

    # What it asked memory and the other owners for (read only; each answer says its coverage).
    _requests(db, row, ev, plan, {m.id for m in messages})
    # Her existing lessons most relevant to what is read, shown without being asked (post-audit 4), so ordinary
    # experience can check them: earlier understanding, never evidence.
    _relevant_lessons(db, row, ev)
    _relevant_memories(db, row, ev)

    # Open inquiries and unresolved assessments, here first, then the user's parked inquiries elsewhere (not
    # evidence; for reopening: later evidence in another session can resume one with its state).
    here = select(LearningAssessment).where(
        LearningAssessment.user_id == row.user_id, LearningAssessment.id != row.id,
        LearningAssessment.state == "done", LearningAssessment.outcome == "unresolved")
    if row.conversation_id is not None:
        here = here.where(LearningAssessment.conversation_id == row.conversation_id)
    elif row.game_session_id is not None:
        here = here.where(LearningAssessment.game_session_id == row.game_session_id)
    else:
        here = here.where(false())
    shown = list(db.scalars(here.order_by(LearningAssessment.updated_at.desc()).limit(OPEN_SHOWN)))
    elsewhere = select(LearningAssessment).where(
        LearningAssessment.user_id == row.user_id, LearningAssessment.id != row.id,
        LearningAssessment.id.not_in([o.id for o in shown] or [uuid.uuid4()]),
        LearningAssessment.state == "done", LearningAssessment.inquiry["question"].astext != "")
    shown += list(db.scalars(elsewhere.order_by(LearningAssessment.updated_at.desc()).limit(OPEN_ELSEWHERE)))
    for k, other in enumerate(shown, 1):
        question = (other.inquiry or {}).get("question")
        where = ("this conversation" if other.conversation_id and other.conversation_id == row.conversation_id
                 else "this game session" if other.game_session_id and other.game_session_id == row.game_session_id
                 else "elsewhere")
        ev.open[f"p{k}"] = A.OpenAssessment(
            f"p{k}", str(other.id), f"{other.trigger_kind} at {_hm(other.anchor_at)} ({where}): "
            + (f"open question: {question[:300]}; waiting for: {(other.inquiry or {}).get('resume_when') or '-'}"
               if question else (other.explanation or "")[:300]))

    if plan.inputs is False and any(i.kind == "record" and i.captured for i in ev.items.values()):
        ev.can_look["inputs"] = ("the exact inputs each captured learner was given, and the exact records behind "
                                "the compact lines shown")
    ev.trigger, ev.trigger_refs = _trigger_text(row, ev, lines, anchor)
    ev.user_name = db.scalar(select(User.name).where(User.id == row.user_id))
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


# Compact views of game records (2026-11-06). A raw record ran ~1-3k characters (ids, timestamps, the whole
# inventory twice, the action list on every decision), so a 20-minute stretch was ~300k characters and every
# review of a whole stretch parked unread. These keep what a reader needs to follow the play: what was done,
# what came back, what changed. Nothing is inferred; the exact record stays one "inputs" look away.

def _call(skill, params) -> str:
    params = {k: v for k, v in (params or {}).items() if k != "time_limit"}
    return f"{skill}(" + ", ".join(f"{k}={v}" for k, v in params.items()) + ")"


def _short(value, limit=300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " [cut]"


def _where(position) -> str | None:
    if not isinstance(position, dict) or "x" not in position:
        return None
    return " ".join(str(round(float(position.get(k) or 0))) for k in ("x", "y", "z"))


def _changes(before: dict, after: dict) -> list[str]:
    out = []
    a, b = _where(before.get("position")), _where(after.get("position"))
    if a and b and a != b:
        out.append(f"moved {a} -> {b}")
    for key in ("health", "food"):
        if before.get(key) is not None and before.get(key) != after.get(key):
            out.append(f"{key} {before.get(key)} -> {after.get(key)}")
    inv_a, inv_b = before.get("inventory") or {}, after.get("inventory") or {}
    diff = [f"{k} {'+' if (inv_b.get(k, 0) - inv_a.get(k, 0)) > 0 else ''}{inv_b.get(k, 0) - inv_a.get(k, 0)}"
            for k in sorted(set(inv_a) | set(inv_b)) if inv_b.get(k, 0) != inv_a.get(k, 0)]
    if diff:
        out.append("inventory " + ", ".join(diff))
    held_a, held_b = (before.get("held") or {}).get("name"), (after.get("held") or {}).get("name")
    if held_a != held_b:
        out.append(f"holding {held_a} -> {held_b}")
    return out


def _execution_brief(ex: dict) -> str:
    """One skill run as run: the call, how it ended, and what the pack observed changing."""
    intended, executed = ex.get("intended") or {}, ex.get("executed") or {}
    if not isinstance(executed, dict):
        return _short(ex, 600)
    if ex.get("kind") != "skill_run":
        if ex.get("kind") == "effect" and executed.get("effect"):
            return (f"effect of her decision (work {intended.get('work')}, target {intended.get('target')}): "
                    f"{_short(executed.get('effect'), 200)} (result {ex.get('result')})")
        if ex.get("kind") == "thought_route":
            # Its conclusion and tip are on the thought's own record; this says where they went.
            return (f"her {intended.get('kind')} thought routed"
                    + (" (with a tip for her hands)" if intended.get("for_hands") else "")
                    + f": {_short(executed.get('routed') or executed.get('status'), 160)} (result {ex.get('result')})")
        if ex.get("kind") == "plan_apply" and isinstance(intended, dict):
            # The plan itself is on the planner's record; this says it was applied and what came of it.
            steps, drop = intended.get("steps") or [], intended.get("drop") or []
            return (f"plan applied: goal \"{_short(intended.get('goal') or '', 120)}\", {len(steps)} steps, "
                    f"{len(drop)} dropped -> {_short(executed, 200)} (result {ex.get('result')})")
        return f"{ex.get('kind')}: {_short(intended, 300)} -> {_short(executed, 300)} (result {ex.get('result')})"
    observed = executed.get("observed") or {}
    head = (_call(intended.get("skill"), intended.get("params")) if intended.get("skill") else ex.get("kind"))
    status = executed.get("status") or (executed.get("report") or {}).get("status")
    result = ex.get("result")
    parts = [f"{head} -> {status or '?'}" + ("" if (result, status) in (("succeeded", "done"), ("failed", "failed"))
                                              else f" (result {result})")]
    if executed.get("seconds") is not None:
        parts.append(f"{executed['seconds']} s")
    if executed.get("reason"):
        parts.append(f"reason: {_short(executed['reason'], 200)}")
    data = {k: v for k, v in (executed.get("data") or {}).items() if k != "counted"} \
        if isinstance(executed.get("data"), dict) else executed.get("data")
    if data:
        parts.append(f"pack data: {_short(data, 200)}")
    if ex.get("deviation"):
        parts.append(f"deviation: {_short(ex['deviation'], 200)}")
    if observed.get("after"):
        changes = _changes(observed.get("before") or {}, observed["after"])
        parts.append(("observed: " + "; ".join(changes)) if changes else "observed: no change in place, health, "
                     "food or inventory")
        after = observed["after"]
        if after.get("inventory") is not None and any(c.startswith("inventory") for c in changes):
            parts.append("inventory after: " + _short(after.get("inventory"), 300))
    return "; ".join(parts)


def _execution_line(ex: dict) -> str:
    """The shortest faithful line for one run, for the condensed view: call, ending, time, reason, changes."""
    intended, executed = ex.get("intended") or {}, ex.get("executed") or {}
    if not isinstance(executed, dict):
        return "run recorded"
    observed = executed.get("observed") or {}
    parts = [f"-> {executed.get('status') or ex.get('result') or '?'}"]
    if executed.get("seconds") is not None:
        parts[0] += f" {executed['seconds']} s"
    if executed.get("reason"):
        parts.append(_short(executed["reason"], 120))
    if observed.get("after"):
        parts += _changes(observed.get("before") or {}, observed["after"])
    return "; ".join(parts)


def _experience_brief(e) -> str:
    p = e.payload or {}
    when = f"[{_hm(e.occurred_at)}]"
    if e.kind == "skill_outcome":
        parts = [f"{when} skill_outcome: {_call(p.get('skill'), p.get('params'))} -> {p.get('status')}"]
        if p.get("attempt_id"):
            # Its call and what was observed are on the decision's run record; this is the report as it arrived.
            parts = [f"{when} skill_outcome: {p.get('skill')} -> {p.get('status')}"]
        if p.get("seconds") is not None:
            parts.append(f"{p['seconds']} s")
        if p.get("reason"):
            parts.append(f"reason: {_short(p['reason'], 200)}")
        data = dict(p.get("data") or {})
        counted = data.pop("counted", None)
        if data and not p.get("attempt_id"):
            parts.append(f"pack data: {_short(data, 240)}")
        if counted:
            parts.append(f"counted while it ran: {_short(counted, 200)}")
        if p.get("step_id"):
            parts.append(f"step {p['step_id']}")
        return "; ".join(parts)
    rest = {k: v for k, v in p.items() if k not in ("seq", "pack_at", "run_id", "attempt_id")}
    return f"{when} {e.kind}: {_short(rest, 500)}"


def _answer_brief(record: DecisionAttempt) -> str:
    """A captured learner's answer in its own words: the fields that say what it saw, chose and why."""
    output = record.output or {}
    parsed = output.get("parsed") if isinstance(output.get("parsed"), dict) else None
    if record.learner == "hard_thought" and parsed:
        keys = ("kind", "conclusion", "thinking", "for_hands", "question", "assessment")
        label = "her mind's hard thought (her own reasoning at the time: not evidence of what happened)"
    elif record.learner == "planner" and parsed:
        keys = ("decision", "progress", "goal", "why", "steps", "drop", "sees", "cannot_see")
        label = "her planner's decision (its reasoning at the time: not evidence of what happened)"
    elif record.learner == "student_2" and isinstance(output.get("decision"), dict):
        d = output["decision"]
        return (f"student_2 decision: intention \"{d.get('intention')}\", communication {d.get('communication')}, "
                f"said \"{_short(d.get('what_to_communicate') or '', 200)}\", work {d.get('work')}")
    elif record.learner == "hands":
        inner = output.get("output") or {}
        return (f"hands answer: status {inner.get('status')}, action {output.get('action')}, "
                f"note \"{_short(inner.get('note') or '', 200)}\"")
    else:
        return f"{record.learner} captured answer: {_game_excerpt(record.output, 1200)}"
    shown = {k: parsed[k] for k in keys if parsed.get(k) not in (None, "", [], {})}
    return f"{label}: " + "; ".join(f"{k}: {_short(v, 600)}" for k, v in shown.items())


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
    manifest_shown, step_shown = None, None
    calls: dict[str, str] = {}  # target -> its one-line call, for the condensed view
    runs: dict[str, str] = {}  # target -> its run's one-line ending
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
        text = "hands decision: missing or expired capture"
        if record is not None:
            manifest = (record.versions or {}).get("manifest")
            step_read = (record.input or {}).get("plan") or {}
            step = step_read.get("step") or {}
            note = ((record.output or {}).get("output") or {}).get("note")
            step_key = (step_read.get("goal"), step.get("id"), step.get("do"))
            text = (f"her hands' decision ({record.status}): {_record_summary(record)}"
                    + (f'; note "{_short(note, 200)}"' if note else "")
                    + ("" if manifest == manifest_shown else f"; available actions: {_game_excerpt(manifest)}")
                    + (f"; same plan step ({step.get('id')})" if step and step_key == step_shown else
                       f'; plan and step read: goal "{step_read.get("goal")}", step {step.get("id")} '
                       f'"{step.get("do")}" (done when "{step.get("done_when")}", {step.get("state")})'
                       if step else f"; plan and step read: {_game_excerpt(step_read, 600)}"))
            manifest_shown, step_shown = manifest, step_key
            action = (record.output or {}).get("action")
            chose = _call(action[0], action[1] if len(action) > 1 else None) if action else "no action"
            calls[href] = f"[{_aware(record.started_at).strftime('%H:%M:%S')}] {chose}" + (f' "{_short(note, 110)}"' if note else "")
        ev.add(A.Item(ref=f"{href}.d", kind="record", learner="hands", event=f"run:{aid}",
                      roots=(f"attempt:{aid}",), target=href, captured=record is not None, text=text,
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
                      target=by_attempt.get(aid, "s1"), text=_experience_brief(e)))
    since = max(cutoff, min(_aware(e.occurred_at) for e in chosen) - timedelta(seconds=30))
    until = max(_aware(e.occurred_at) for e in chosen) + timedelta(seconds=5)
    # Include plans, applied rules, thoughts, measured outcomes and all three reports from step 1.
    # The hands decisions already shown as targets are left out of the query, not just of the rendering: they
    # used to fill the 100-record window, so her mind's and planner's records later in a long stretch were lost.
    records = list(db.scalars(select(DecisionAttempt).where(
        DecisionAttempt.user_id == row.user_id, DecisionAttempt.game_session_id == row.game_session_id,
        DecisionAttempt.started_at >= since, DecisionAttempt.started_at <= until,
        DecisionAttempt.expires_at > _now(),
        DecisionAttempt.id.not_in([uuid.UUID(a) for a in by_attempt] or [uuid.uuid4()]))
        .order_by(DecisionAttempt.started_at).limit(100)))
    for record in records:
        target = by_attempt.get(str(record.id), "s1")
        ev.add(A.Item(ref=f"a{record.id.hex}", kind="record", learner=record.learner,
                      event=f"attempt:{record.id}", roots=(f"attempt:{record.id}",), target=target,
                      text=_answer_brief(record),
                      detail=_game_input(db, record) if plan.inputs else None))
    executions = list(db.scalars(select(DecisionExecution).where(
        DecisionExecution.user_id == row.user_id, DecisionExecution.game_session_id == row.game_session_id,
        DecisionExecution.expires_at > _now(),
        ((DecisionExecution.attempt_id.in_([r.id for r in records] + [uuid.UUID(a) for a in by_attempt])) |
         ((DecisionExecution.ended_at >= since) & (DecisionExecution.ended_at <= until))))
        .order_by(DecisionExecution.captured_at).limit(150)))
    for ex in executions:
        if ex.kind == "skill_run" and by_attempt.get(str(ex.attempt_id)):
            runs[by_attempt[str(ex.attempt_id)]] = _execution_line(capture_chains.execution_out(ex))
        ev.add(A.Item(ref=f"x{ex.id.hex}", kind="fact", fact_kind="skill" if ex.kind == "skill_run" else ex.kind,
                      event=f"run:{ex.attempt_id}" if ex.attempt_id else f"execution:{ex.id}",
                      roots=(f"execution:{ex.id}",), target=by_attempt.get(str(ex.attempt_id), "s1"),
                      text=_execution_brief(capture_chains.execution_out(ex)),
                      detail=("exact record: " + _game_excerpt(capture_chains.execution_out(ex), 4000))
                      if plan.inputs else None))
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
    used = _lessons_used(db, row, ev, by_attempt, records)
    for href, note in used.items():
        item = ev.items.get(f"{href}.d")
        if item is not None:
            ev.items[item.ref] = dataclasses.replace(item, text=f"{item.text}; {note}")
    for href in by_attempt.values():
        brief = calls.get(href, "decision not captured") + " " + runs.get(href, "-> no run record")
        brief += f"; {used[href]}" if href in used else ""
        ev.targets[href] = dataclasses.replace(ev.targets[href], brief=brief)
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
                      text=f"[{_hm(e.occurred_at)}] {e.type}: " + _short(
                          {k: v for k, v in (e.payload or {}).items()
                           if k not in ("execution_generation", "state_revision", "plan_revision")}, 600)))
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


LESSONS_USED_SHOWN = 8  # lessons her parts were given in one stretch, the most used first


def _lessons_used(db: Session, row: LearningAssessment, ev: A.Evidence, by_attempt: dict[str, str],
                  records: list) -> dict[str, str]:
    """The lessons her parts were given for the decisions read here, shown among her existing lessons with what
    each part said about them in this stretch, so the reviewer can judge a lesson by what followed its use
    (2026-11-06: her hands said they applied "choose a nearby passage target" 87 times while no iron was found,
    and nothing ever read that). Returns, per hands decision, the note shown on it ("applied L1").
    A part saying it applied a lesson is its own account: not proof the lesson shaped the choice."""
    from app.db.models import MemoryLessonUse
    from app.services import memory_lessons

    hands = {uuid.UUID(a): h for a, h in by_attempt.items()}
    others = {r.id: r.learner for r in records if r.learner != "hands"}  # hands outside the stretch aren't shown
    if not hands and not others:
        return {}
    uses = list(db.scalars(select(MemoryLessonUse).where(
        MemoryLessonUse.user_id == row.user_id, MemoryLessonUse.received.is_(True),
        MemoryLessonUse.attempt_id.in_(list(hands) + list(others))).order_by(MemoryLessonUse.id)))
    if not uses:
        return {}
    tally: dict[uuid.UUID, int] = {}
    for u in uses:
        tally[u.lesson_id] = tally.get(u.lesson_id, 0) + 1
    chosen = sorted(tally, key=lambda k: -tally[k])[:LESSONS_USED_SHOWN]
    refs = {v.id: v.ref for v in ev.lessons.values()}
    for found in memory_lessons.for_learning_ids(db, row.user_id, [k for k in chosen if str(k) not in refs]):
        ref = f"L{len(ev.lessons) + 1}"
        ev.lessons[ref] = A.PriorLesson(ref, found.id, found.revision, found.text)
        refs[found.id] = ref
    notes: dict[str, list[str]] = {}
    for lesson_id in chosen:
        ref = refs.get(str(lesson_id))
        if ref is None:
            continue  # withdrawn since
        said: dict[str, list[str]] = {}
        for u in (u for u in uses if u.lesson_id == lesson_id):
            who = "her hands" if u.attempt_id in hands else {
                "hard_thought": "her mind", "planner": "her planner"}.get(others.get(u.attempt_id), u.consumer)
            label = (f"{who} said {u.reported.replace('_', ' ')}" if u.reported else f"{who} was given it (no report)")
            unit = "decision" if u.attempt_id in hands else {"her mind": "thought", "her planner": "plan"}.get(who, "time")
            said.setdefault((label, unit), []).append(hands.get(u.attempt_id, ""))
            if u.attempt_id in hands:
                notes.setdefault(hands[u.attempt_id], []).append(
                    f"applied {ref}" if u.reported == "applied"
                    else f"{ref} {(u.reported or 'given, no report').replace('_', ' ')}")
        parts = []
        for (label, unit), hrefs in said.items():
            named = [h for h in hrefs if h]
            parts.append(f"{label} on {len(hrefs)} {unit}(s)"
                         + (f" ({', '.join(named[:12])}{', ...' if len(named) > 12 else ''})" if named else ""))
        lesson = ev.lessons[ref]
        ev.lessons[ref] = dataclasses.replace(lesson, text=f"{lesson.text} Given in this stretch: {'; '.join(parts)}.")
    return {h: ", ".join(n) for h, n in notes.items()}


def _elsewhere(db: Session, row: LearningAssessment, ev: A.Evidence, attached: set[str]) -> None:
    """Attached messages and game experiences that aren't part of this assessment's own conversation or session
    (another assessment reopened this one with them). Shown so a parked inquiry resumes on them."""
    from app.services import memory_forget

    shown = ev.evidence_roots()
    extra = sorted(r for r in attached if r.startswith(("message:", "experience:")) and r not in shown)
    if not extra:
        return
    ids = [i for i in (_uuid(r.split(":", 1)[1]) for r in extra if r.startswith("message:")) if i]
    messages = list(db.scalars(select(ChatMessage).join(Conversation, Conversation.id == ChatMessage.conversation_id)
                               .where(ChatMessage.id.in_(ids or [uuid.uuid4()]), Conversation.user_id == row.user_id)
                               .order_by(ChatMessage.created_at)))
    cuts = memory_forget.memory_cuts(db, row.user_id, [m.id for m in messages])
    n = 0
    for m in messages:
        text = memory_forget.memory_view(m.content, m.heard_chars, m.role, cuts.get(m.id, False))
        n += 1
        who = "columbina" if m.role == "assistant" else "user"
        ev.add(A.Item(ref=f"e{n}", kind="said", event=f"message:{m.id}", roots=(f"message:{m.id}",),
                      words=text, group="elsewhere",
                      text=f'[{who}, another conversation, {_hm(m.created_at)}] "{FORGOTTEN if text is None else text}"'))
    numbers = [int(r.split(":", 1)[1]) for r in extra if r.startswith("experience:") and r.split(":", 1)[1].isdigit()]
    for e in db.scalars(select(GameExperience).where(GameExperience.user_id == row.user_id,
                                                     GameExperience.id.in_(numbers or [-1])).order_by(GameExperience.id)):
        n += 1
        ev.add(A.Item(ref=f"e{n}", kind="game", fact_kind="skill" if e.kind == "skill_outcome" else "notice",
                      event=f"experience:{e.id}", roots=(f"experience:{e.id}",), group="elsewhere",
                      text=f"[{_hm(e.occurred_at)}, session {str(e.session_id)[:8]}] {e.kind}: "
                           f"{_game_excerpt(e.payload, 900)}. A recorded report from another session."))


_ACT_SIGNALS = {"correcting_her": "as correcting her", "complaining": "as complaining"}


def _signals(db: Session, row: LearningAssessment, messages: list[ChatMessage], lines: dict,
             evidence_rows: list[LearningAssessmentEvidence]) -> list[str]:
    """Labels other parts gave the activity shown: Student 1's reading of a user line as a correction or complaint,
    and her mind pointing at something. Said as labels; whether they matter is the reviewer's."""
    out = []
    users = {m.id: lines[m.id] for m in messages if m.role == "user" and m.id in lines}
    if users:
        readings = db.scalars(select(DecisionAttempt).where(
            DecisionAttempt.user_id == row.user_id, DecisionAttempt.learner == "student_1",
            DecisionAttempt.status == "ok", DecisionAttempt.source_message_id.in_(list(users)))
            .order_by(DecisionAttempt.started_at))
        latest = {r.source_message_id: r for r in readings}
        for message_id, reading in latest.items():
            acts = ((reading.output or {}).get("understand") or {}).get("acts") or {}
            for act, words in _ACT_SIGNALS.items():
                if acts.get(act):
                    out.append(f"Student 1 read {users[message_id]} {words} (its label; it can misread jokes, "
                               "quotes and remarks aimed at someone else)")
    for e in evidence_rows:
        if e.ref.startswith("nomination:"):
            out.append(f'her mind pointed at this: "{(e.detail or {}).get("text", "")}" (her own thought: not '
                       "evidence; check it against the records)")
    return out


def _requests(db: Session, row: LearningAssessment, ev: A.Evidence, plan: Plan, shown: set[uuid.UUID]) -> None:
    """Answer each request from its owner, read only, in order. Results already shown are named, not repeated."""
    n = 0
    shown_roots = ev.evidence_roots()
    lessons_seen: set[str] = set()
    for k, (source, query) in enumerate(plan.requests, 1):
        qref = f"q{k}"
        try:
            with db.begin_nested():
                status, coverage, found = _ask(db, row, source, query, shown)
        except Exception as exc:  # noqa: BLE001 - an owner failing is a gap, said as such
            log.exception("learning assessment %s: request %s failed", row.id, qref)
            status, coverage, found = "failed", f"the request failed ({type(exc).__name__}); nothing is known "                                                 "from it either way", []
        new = 0
        for item in found:
            if isinstance(item, A.PriorLesson):
                if item.id in lessons_seen or any(v.id == item.id for v in ev.lessons.values()):
                    continue
                lessons_seen.add(item.id)
                ref = f"L{len(ev.lessons) + 1}"
                ev.lessons[ref] = A.PriorLesson(ref, item.id, item.revision, item.text)
                new += 1
                continue
            if set(item.roots) <= shown_roots:
                continue  # already shown here
            n += 1
            ev.add(A.Item(ref=f"r{n}", kind=item.kind, event=item.event, roots=item.roots, text=item.text,
                          words=item.words, fact_kind=item.fact_kind, group=qref,
                          own_inference=item.own_inference, memory=item.memory))
            shown_roots |= set(item.roots)
            new += 1
        ev.requests.append(A.Request(qref, source, query, status, coverage, new))


def _ask(db: Session, row: LearningAssessment, source: str, query: str,
         shown: set[uuid.UUID]) -> tuple[str, str, list]:
    """One request to one owner: (status, coverage in words, items or prior lessons)."""
    now = _now()
    if source == "memory":
        from app.ai.recall.contracts import RecallRequest
        from app.services import recall

        result = recall.search(db, RecallRequest(user_id=row.user_id, query=query[:2000], purpose="explicit_search",
                                                 now=now, kinds=("fact", "episode", "event", "item"),
                                                 limit=REQUEST_RESULTS))
        items = []
        for record in result.records:
            roots = [str(record.ref)]
            based = []
            for e in record.evidence or ():
                if e.source in ("message", "experience", "observation", "session_event"):
                    roots.append(f"{e.source}:{e.source_id}")
                elif e.source == "thought":
                    based.append("her own thought")
            when = _hm(record.occurred_at or record.recorded_at)
            # Her own hypotheses and decisions (and anything resting on her thought) are her inferences: shown so
            # learning can relate to them, never evidence that what they say is so.
            own = bool(based) or (record.ref.kind == "item" and record.owner == "columbina"
                                  and record.epistemic == "inferred")
            whose = (f"her own {record.subkind}" if record.ref.kind == "item" and record.owner == "columbina"
                     else f"{record.owner}'s {record.subkind}" if record.ref.kind == "item" and record.owner
                     else record.ref.kind)
            items.append(A.Item(ref="", kind="memory", event=f"memory:{record.ref}", roots=tuple(dict.fromkeys(roots)),
                                fact_kind=record.ref.kind, own_inference=own,
                                memory=str(record.ref) if record.ref.kind in ("fact", "item") else None,
                                text=f"[memory {whose}, {record.epistemic}, {record.status}, {when}] "
                                     f"{record.content[:400]}" + (f" (rests on {', '.join(based)})" if based else "")
                                     + (". Her own inference: never evidence" if own else
                                        ". Memory's record, not proof of what it says")
                                     + (f"; relate to it in relates_to as {{lesson: this ref}}"
                                        if record.ref.kind in ("fact", "item") else "") + "."))
        coverage = f"searched {', '.join(result.searched_kinds) or 'nothing'}" + (
            f"; errors: {', '.join(result.errors)}" if result.errors else "")
        return result.status, coverage, items
    if source == "conversations":
        from app.services import transcript_search

        result = transcript_search.search(db, row.user_id, query, now=now, exclude=tuple(shown),
                                          limit=REQUEST_RESULTS)
        items = []
        for record in result.records:
            message_id = record.ref.id
            words = FORGOTTEN if record.forgotten else record.content[:500]
            who = "columbina" if record.subkind == "columbina" else "user"
            items.append(A.Item(ref="", kind="said", event=f"message:{message_id}", roots=(f"message:{message_id}",),
                                words=None if record.forgotten else words,
                                text=f'[{who}, another conversation, {_hm(record.occurred_at or record.recorded_at)}] '
                                     f'"{words}"'))
        return result.status, "what was said in her other conversations, as far as it was heard", items
    if source == "experiences":
        from app.services.memory_reset import learning_cutoff

        words = [w for w in re.findall(r"[a-z0-9_]{3,}", query.lower())][:6]
        if not words:
            return "no_match", "no searchable words in the query", []
        rows = list(db.scalars(select(GameExperience).where(
            GameExperience.user_id == row.user_id, GameExperience.occurred_at >= learning_cutoff(db, row.user_id),
            or_(*[cast(GameExperience.payload, String).ilike(f"%{w}%") for w in words]),
            *([GameExperience.session_id != row.game_session_id] if row.game_session_id else []),
        ).order_by(GameExperience.id.desc()).limit(REQUEST_RESULTS)))
        items = [A.Item(ref="", kind="game", fact_kind="skill" if e.kind == "skill_outcome" else "notice",
                        event=f"experience:{e.id}", roots=(f"experience:{e.id}",),
                        text=f"[{_hm(e.occurred_at)}, session {str(e.session_id)[:8]}] {e.kind}: {_game_excerpt(e.payload, 900)}. "
                             "A recorded report from another stretch.") for e in rows]
        return ("matched" if items else "no_match"), (
            f"game experience outside this stretch's session matching any of: {', '.join(words)}"), items
    if source == "lessons":
        from app.services import memory_lessons

        found = memory_lessons.for_learning(db, row.user_id, query, limit=REQUEST_RESULTS)
        return ("matched" if found else "no_match"), "her lessons for this user, any status but withdrawn", found
    return "not_available", f"{source} can't be asked", []


def _relevant_lessons(db: Session, row: LearningAssessment, ev: A.Evidence) -> None:
    """Up to RELEVANT_LESSONS of her lessons for this user, the most relevant to what is shown, added to those it
    asked for (never twice). Read only; a failure shows none, said."""
    from app.services import memory_lessons

    query = " ".join((i.words or i.text) for i in ev.items.values()
                     if i.kind in ("user", "her", "game", "said"))[-4000:]
    if not query.strip():
        return
    try:
        with db.begin_nested():
            found = memory_lessons.for_learning(db, row.user_id, query, limit=RELEVANT_LESSONS)
    except Exception:  # noqa: BLE001 - shown none, said
        log.exception("learning assessment %s: relevant lessons failed", row.id)
        ev.signals.append("her existing lessons couldn't be read for this (none are shown unless asked for)")
        return
    known = {v.id for v in ev.lessons.values()}
    for item in found:
        if item.id not in known:
            ref = f"L{len(ev.lessons) + 1}"
            ev.lessons[ref] = A.PriorLesson(ref, item.id, item.revision, item.text)
            known.add(item.id)


def _relevant_memories(db: Session, row: LearningAssessment, ev: A.Evidence) -> None:
    """Up to RELEVANT_MEMORIES facts and working items memory holds that are most relevant to what is shown (read only,
    recall's explicit search, no access counting), so the reviewer can relate this stretch to them without having
    thought to ask. Records drawn from these very messages, or already shown, are left out. A failure shows none,
    said."""
    query = " ".join((i.words or i.text) for i in ev.items.values()
                     if i.kind in ("user", "her", "game", "said"))[-2000:]
    if not query.strip():
        return
    try:
        with db.begin_nested():
            status, coverage, found = _ask(db, row, "memory", query, set())
    except Exception:  # noqa: BLE001 - shown none, said
        log.exception("learning assessment %s: relevant memories failed", row.id)
        ev.signals.append("memory couldn't be read for this (nothing it holds is shown unless asked for)")
        return
    shown_roots = ev.evidence_roots()
    n = sum(1 for i in ev.items.values() if i.group == "related")
    for item in found:
        if n >= RELEVANT_MEMORIES or item.memory is None:
            continue
        sources = set(item.roots) - {item.memory}
        if set(item.roots) <= shown_roots or (sources and sources <= shown_roots):
            continue  # memory's record of what is shown here: not something else it knows
        n += 1
        ev.add(A.Item(ref=f"k{n}", kind=item.kind, event=item.event, roots=item.roots, text=item.text,
                      words=item.words, fact_kind=item.fact_kind, group="related",
                      own_inference=item.own_inference, memory=item.memory))
        shown_roots |= set(item.roots)


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
    if row.trigger_kind == "review" and row.game_session_id is None:
        return (f"A review opportunity over a stretch of the conversation ({why}), up to m{row.trigger.get('up_to_seq')}. "
                "Nothing is assumed to have happened: decide whether there is anything worth examining.", refs)
    if row.trigger_kind == "review":
        game = [ref for ref in ev.targets if ref.startswith("h")]
        return (f"A review opportunity over a stretch of play ({why}). Nothing is assumed to have happened: decide "
                "whether there is anything worth examining." + (f" Targets: {', '.join(game)}." if game else ""),
                refs + tuple(game))
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
    if found is UNCHANGED:
        return _settle_unchanged(db, claimed)
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
    plan: Plan = field(default_factory=Plan)


UNCHANGED = object()  # a wake whose evidence is what the current revision already read: no model call


def _investigate(db: Session, claimed: Claim, evaluate: Callable[[str], A.Answer]) -> _Found | object | None:
    s = get_settings()
    row = db.get(LearningAssessment, claimed.id)
    if row is None:
        return None
    prior, plan, read_digest = None, Plan(game=0), None
    if row.revision:
        current = db.scalar(select(LearningAssessmentRevision).where(
            LearningAssessmentRevision.assessment_id == row.id, LearningAssessmentRevision.revision == row.revision))
        if current is not None:
            prior = current.result
            # Continue from where the last reading got to (its looks and requests), not from scratch: the last
            # round the model actually read (one skipped as too large to read is not where it got to).
            read_rounds = [r for r in current.rounds or [] if "fingerprint" in r]
            plan = Plan.from_dict(read_rounds[-1].get("plan") if read_rounds else None)
            if current.prompt_version == A.PROMPT_VERSION:
                read_digest = current.input_digest
    rounds: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    last: tuple[Gathered, A.Checked, str] | None = None
    previous_digest = previous_held = None
    barren = 0  # expansions in a row that brought no new evidence (a request answered with nothing new)
    stop, why = "concluded", ""
    while True:
        gathered = gather(db, row, plan, prior=prior)
        db.rollback()  # nothing held while the model thinks
        row = db.get(LearningAssessment, claimed.id)
        if row is None:
            return None
        current = A.digest(gathered.ev)
        held = _held(gathered.ev)
        barren = barren + 1 if previous_held is not None and held == previous_held else 0
        previous_held = held
        if barren >= 2:
            # It was shown that its last request added nothing, and asked again with the same result: no useful
            # progress is possible now. Parked, resumable; never a verdict on the question.
            stop, why = "stuck", "its requests brought no new evidence"
            break
        if not rounds and read_digest is not None and current == read_digest:
            _log({"op": "unchanged_wake", "assessment": str(row.id), "revision": row.revision})
            return UNCHANGED
        if previous_digest is not None and current == previous_digest:
            stop, why = "stuck", "what it asked to look at added nothing new"
            break
        text = A.render(gathered.ev)
        if len(text) > s.learning_assessment_max_chars:
            # Too much to read at once in full: read it condensed (one line per decision of her hands) before
            # parking. Parking a whole stretch unread is what kept her longer stretches from ever being learned from.
            shorter = A.render(gathered.ev, condensed=True)
            if len(shorter) <= s.learning_assessment_max_chars:
                rounds_note = {"condensed": True, "full_chars": len(text)}
                text = shorter
            else:
                rounds_note = None
        else:
            rounds_note = None
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
                       "next": nxt, "requests": checked.result["requests"], "dropped": len(checked.dropped),
                       "latency_ms": answer.latency_ms, "summary": A.summarize(checked.result),
                       **(rounds_note or {})})
        last = (gathered, checked, fp)
        if checked.result["review"]["decision"] == "leave":
            stop, why = "left", checked.result["review"]["why"] or "nothing worth examining"
            break
        if nxt["step"] == "conclude":
            stop, why = "concluded", nxt["why"]
            break
        if nxt["step"] == "wait":
            stop, why = "waiting", nxt["why"] or checked.result["review"]["why"] or "waiting for more"
            break
        if nxt["step"] == "need_missing":
            stop, why = "missing_evidence", nxt["why"]
            break
        expanded = plan.expand(nxt["look"], checked.result["requests"])
        if not nxt["look"] and expanded.requests == plan.requests:
            stop, why = "stuck", "asked to look further, at nothing new that's available"
            break
        previous_digest = current
        plan = expanded
    gathered, checked, fp = last
    return _Found(gathered, checked, fp, rounds, stop, why, usage, plan)


def _held(ev: A.Evidence) -> str:
    """What the evidence is, apart from how requests were answered: the citable items and lessons shown."""
    parts = sorted((i.ref, i.roots, i.text) for i in ev.items.values())
    parts += sorted(("lesson", v.id, v.revision) for v in ev.lessons.values())
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:32]


def _settle_unchanged(db: Session, claimed: Claim) -> str:
    """Nothing new to read: the assessment goes back to what it was (parked or done), with no new revision."""
    row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == claimed.id).with_for_update())
    if row is None or row.state != "running" or row.attempts != claimed.attempts:
        db.rollback()
        return "gone"
    row.lease_expires_at, row.last_error = None, None
    if row.epoch != claimed.epoch:
        row.state, row.next_attempt_at = "pending", _now()
    else:
        row.state = "done"
    db.commit()
    return "unchanged"


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
    if result["follow_up"]["propose"] and stop != "left":
        # A proposal for cognition, kept by memory as an open item of hers; learning never asks or acts.
        from app.services import learning_review
        result["follow_up"]["item"] = learning_review.propose(db, row, n, result["follow_up"], result["inquiry"])
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
    row.inquiry = _inquiry(result, found, digest, n, now)
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
        if added and other.inquiry:
            other.inquiry = {**other.inquiry, "resumed_by": str(row.id)}
        if added:
            _reopen(other, now, f"new evidence: {', '.join(added)}")
    _log({"op": "revision", "assessment": str(row.id), "revision": n, "outcome": summary["outcome"],
          "target": summary["target_status"], "cause": summary["cause_status"], "stop": stop,
          "rounds": len(found.rounds), "dropped": len(found.checked.dropped), **found.usage})
    return "revised"


PARKED = ("waiting", "stuck", "missing_evidence")


def _inquiry(result: dict, found: _Found, digest: str, n: int, now: datetime) -> dict:
    """The inquiry kept between readings: the reviewer's own (question, provisional reading, alternatives,
    unknowns, what would resume it) with what was examined. A parked stop always keeps one, so it can resume with
    its state; a stretch left alone, or concluded with nothing open, keeps none."""
    own = result.get("inquiry") or {}
    if found.stop == "left" or (found.stop not in PARKED and not own.get("question")):
        return {}
    return {**own,
            "question": own.get("question") or (result.get("explanation") or found.stop_why)[:400],
            "resume_when": own.get("resume_when") or "new or revised evidence about it, here or in an activity "
                                                     "found to bear on it",
            "stop": found.stop, "stop_why": found.stop_why[:300], "revision": n, "digest": digest,
            "requests": [list(r) for r in found.plan.requests], "at": now.isoformat()}


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
    for key in ("targets", "causes", "user_stated", "dimensions", "choice_vs_outcome", "findings", "reopens"):
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
    for conversation_id, at, seq in db.execute(select(ChatMessage.conversation_id, ChatMessage.created_at,
                                                      ChatMessage.conversation_sequence)
                                               .where(ChatMessage.id.in_(messages))):
        ids |= set(db.scalars(select(LearningAssessment.id).where(
            LearningAssessment.user_id == user_id, LearningAssessment.conversation_id == conversation_id,
            LearningAssessment.anchor_at >= _aware(at))))
        # A review stretch that began earlier but reaches the message showed it (post-audit 3).
        ids |= set(db.scalars(select(LearningAssessment.id).where(
            LearningAssessment.user_id == user_id, LearningAssessment.conversation_id == conversation_id,
            LearningAssessment.trigger_kind == "review",
            cast(LearningAssessment.trigger["up_to_seq"].astext, Integer) >= seq)))
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
            "inquiry": row.inquiry or {},
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
           "drop_for", "listing", "detail", "for_ref", "note_use", "Plan", "Claim", "RECONSIDER", "UNCHANGED"]
