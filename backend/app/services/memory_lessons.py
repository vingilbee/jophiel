"""Lessons, memory's side (docs/learning-system/03-immediate-adaptation.md; implementation:
docs/learning-system/03-implementation.md). Memory owns every lesson row; learning (``services/learning_lessons.py``)
proposes checked changes and this module applies them by its own rules.

- **Lifecycle.** ``apply`` creates lessons, adds support or counterevidence (once per occasion and role), narrows,
  revises and withdraws. Every change to what a lesson says or how it's used is a new revision, kept whole in
  ``memory_lesson_revisions``. Status (``lessons.status_for``): a stated preference is accepted at once; an
  inference (strategy or understanding) is ``accepted`` or ``hypothesis`` as the builder judged its certainty on the
  evidence (post-audit 3: no count of occasions promotes it); a correction of one episode is ``episode_only``.
  Withdrawn is final. Independent support is counted with dependencies kept: evidence sharing a message, a turn, a
  run or an experience with other evidence is one occasion, whichever assessment read it.
- **Asynchronous work can't undo it.** Each change names the lesson revision it was made from; under the user's
  lesson lock and the row lock a different revision means the reading is stale (``Stale``: the caller reads again).
  A withdrawn lesson accepts nothing, and a new lesson saying what a withdrawn one said needs evidence newer than the
  withdrawal (checked in ``lessons.check``). When an assessment is revised, the evidence it gave stops counting until
  its new revision is read (``reconsider``), so a lesson resting only on it stops being supplied.
- **Retrieval.** ``for_decision`` supplies bounded, whole lessons; with ``include_tentative`` (games, and since
  post-audit 3 conversation, interpretation and her mind) hypotheses too, labelled as guesses with applicability,
  alternatives and source refs. An inference is supplied only while it has live support. Episode-only and withdrawn
  lessons stay out. Relevant conflicts are supplied together within the same budget. ``for_learning`` is the
  reviewer's read of her lessons (every status but withdrawn), to compare new experience with.
- **Uses.** ``record_uses``: which revision each decision had, whether it reached the consumer and why not (cut by the
  budget, a consumer whose input has no lessons, supply switched off), and what the consumer said about it.
  ``explain`` names which of the three cases a lesson not followed is: never reached, reached but not accounted for
  (overlooked or misunderstood: her decision), or reached and reasonably set aside (judgment, not a learning failure).
- **People on the phone** (affinity B2, docs/PHONE_PHASE_8_AFFINITY_B2.md). A lesson read from a ``phone``
  assessment is bound to that person, the conversation it was learned in and the conversations its behaviour applies
  to (``PhoneScope``), and is shadow (``phone_live`` false). Every ordinary retrieval (``shown_for`` and
  ``for_learning`` outside that person's own assessments, ``for_decision``, ``for_learning_ids``) leaves phone
  lessons out, so routing an exchange through the learner can never make its lesson reach her decisions; only
  ``for_phone_audience`` reads them, live ones only, for the reply writer of an audience they apply to. A phone
  reading changes only that person's lessons; an ordinary reading never touches a phone lesson.
- **Forgetting.** ``drop_for``: lessons whose evidence came from forgotten messages go, with their revisions and uses,
  and so do the raw decision captures that showed them (their ``lesson:<id>`` source).

Everything is per user; nothing here reads across users.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import Text, cast, delete, false, func, select
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.orm import Session

from app.ai.learning import lessons as L
from app.core.config import get_settings
from app.db.models import (
    LESSON_BASIS_RELATIONS, MemoryLesson, MemoryLessonEvidence, MemoryLessonLink, MemoryLessonRevision, MemoryLessonUse,
)

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SHOWN_MAX = 40  # lessons the construction call sees (most recently changed first)
WITHDRAWN_SHOWN = 10
CONSUMERS = ("hands", "planner", "mind")
_log_lock = threading.Lock()


class Stale(Exception):
    """A change was made from a lesson revision that is no longer current: read again."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# What the construction call sees
# --------------------------------------------------------------------------- #


SHARED_ROOTS = ("experience:", "attempt:", "execution:", "trajectory:", "message:", "turn:")


def _canonical(db, user_id: uuid.UUID, refs) -> set[str]:
    """The occurrences evidence refs come from, so the same event read twice is recognised: a run and its
    experiences are one attempt (a trial is one trial), an execution is its attempt; messages and turns are
    themselves."""
    from app.db.models import DecisionExecution, GameExperience

    canonical = set()
    for ref in {r for r in refs or [] if str(r).startswith(SHARED_ROOTS)}:
        prefix, value = ref.split(":", 1)
        if prefix == "experience" and value.isdigit():
            experience = db.get(GameExperience, int(value))
            if experience is not None and experience.user_id == user_id:
                p = experience.payload or {}
                trial = (p.get("data") or {}).get("trial_id")
                canonical.add(f"trial:{trial}" if trial else f"attempt:{p['attempt_id']}"
                              if p.get("attempt_id") else ref)
                continue
        if prefix == "execution":
            try:
                execution = db.get(DecisionExecution, uuid.UUID(value))
            except ValueError:
                execution = None
            if execution is not None and execution.user_id == user_id and execution.attempt_id is not None:
                canonical.add(f"attempt:{execution.attempt_id}")
                continue
        canonical.add(ref)
    return canonical


def _independent(db, evidence) -> set[str]:
    """Evidence sharing an occurrence is one occasion even across distinct assessments: a failure assessment and a
    later stretch reading of the same run, or two reviews of a conversation that both rest on the same message,
    never manufacture independent support (post-audit 3 extends this from game sources to messages and turns)."""
    groups = []
    for e in evidence:
        canonical = _canonical(db, e.user_id, e.refs)
        keys = canonical or {"occasion:" + e.occasion}
        touching = [g for g in groups if g[0] & keys]
        merged = set(keys)
        occasions = {e.occasion}
        for g in touching:
            merged.update(g[0])
            occasions.update(g[1])
            groups.remove(g)
        groups.append((merged, occasions))
    return {min(g[1]) for g in groups}


def _counts(db: Session, lesson_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    out: dict[uuid.UUID, dict[str, Any]] = {i: {"support": set(), "counter": set(), "noted": set(),
                                                "occasions": set(), "assessments": {}} for i in lesson_ids}
    if not lesson_ids:
        return out
    evidence = list(db.scalars(select(MemoryLessonEvidence).where(MemoryLessonEvidence.lesson_id.in_(lesson_ids))))
    for e in evidence:
        o = out[e.lesson_id]
        o["occasions"].add(e.occasion)
        if e.current:
            o[e.role].add(e.occasion)
        if e.assessment_id is not None:
            o["assessments"][e.assessment_id] = max(o["assessments"].get(e.assessment_id, 0),
                                                    e.assessment_revision or 0)
    for lesson_id, counts in out.items():
        for role in ("support", "counter"):
            counts[role] = _independent(db, (e for e in evidence if e.lesson_id == lesson_id and e.current and e.role == role))
    return out


RECENT_SHOWN = 10  # past SHOWN_MAX: the most recently changed, the rest by relevance to what is being read


def _relevant(rows: list[MemoryLesson], query: str, keep: int, *, embed=None) -> list[MemoryLesson]:
    """``keep`` rows, the most relevant to ``query`` (meaning when embeddings exist, else shared words)."""
    if len(rows) <= keep:
        return rows
    q_words = _words(query)
    vector = (embed or _embed)(query[:2000]) if query.strip() and any(r.embedding for r in rows) else None

    def score(r: MemoryLesson) -> float:
        words = _words(_text(r))
        return _cosine(vector, r.embedding) + (len(q_words & words) / len(words) if words else 0.0)

    return sorted(rows, key=lambda r: (-score(r), r.created_at))[:keep]


def _audience(phone_person: uuid.UUID | None, phone_conversation: uuid.UUID | None = None):
    """Which lessons a reading may see: the ordinary ones, or (a phone assessment) only that person's lessons
    learned in that same conversation (their explanation never travels to another audience's reading). A phone
    reading whose conversation is unknown sees none."""
    if phone_person is None:
        return MemoryLesson.phone_person_id.is_(None)
    if phone_conversation is None:
        return false()
    return (MemoryLesson.phone_person_id == phone_person) & (MemoryLesson.phone_conversation_id == phone_conversation)


def shown_for(db: Session, user_id: uuid.UUID, *, assessment_id: uuid.UUID, assessment_revision: int,
              roots=(), query: str = "", embed=None,
              phone_person: uuid.UUID | None = None,
              phone_conversation: uuid.UUID | None = None) -> tuple[dict[str, L.Shown], dict[str, L.Shown]]:
    """The lessons (L refs) and recently withdrawn ones (W refs) the construction call reads: all of them while they
    fit, past SHOWN_MAX the most recently changed and the most relevant to ``query``. ``roots``: the evidence this
    reading rests on, to say which lessons already count it. ``phone_person``: a phone assessment's person, whose
    lessons alone it sees (otherwise phone lessons are left out)."""
    live = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.status != "withdrawn",
                                                      _audience(phone_person, phone_conversation))
                           .order_by(MemoryLesson.updated_at.desc())))
    if len(live) > SHOWN_MAX:
        recent = live[:RECENT_SHOWN]
        live = recent + _relevant(live[RECENT_SHOWN:], query, SHOWN_MAX - RECENT_SHOWN, embed=embed)
    gone = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.status == "withdrawn",
                                                      _audience(phone_person, phone_conversation))
                           .order_by(MemoryLesson.withdrawn_at.desc()).limit(WITHDRAWN_SHOWN)))
    counts = _counts(db, [r.id for r in live + gone])
    states = premises(db, user_id, [r.id for r in live])
    mine = _canonical(db, user_id, roots)
    held: dict[uuid.UUID, set[str]] = {}
    if mine:
        for e in db.scalars(select(MemoryLessonEvidence).where(
                MemoryLessonEvidence.lesson_id.in_([r.id for r in live] or [uuid.uuid4()]),
                MemoryLessonEvidence.current.is_(True), MemoryLessonEvidence.assessment_id != assessment_id)):
            held.setdefault(e.lesson_id, set()).update(e.refs or [])

    def shown(ref: str, r: MemoryLesson) -> L.Shown:
        c = counts[r.id]
        earlier = c["assessments"].get(assessment_id)
        shares = bool(mine and held.get(r.id) and mine & _canonical(db, user_id, held[r.id]))
        linked = states.get(r.id, [])
        return L.Shown(ref, str(r.id), r.revision, r.kind, r.scope, r.status, r.statement, r.applies_when,
                       tuple(r.exceptions or ()), tuple(r.user_words or ()), tuple(sorted(c["support"])),
                       tuple(sorted(c["counter"])), tuple(sorted(c["occasions"])),
                       earlier if earlier is not None and earlier < assessment_revision else None,
                       _aware(r.withdrawn_occasion_at), r.status_reason, r.form, tuple(r.alternatives or ()),
                       shares, details=dict(r.details or {}), links=tuple(link_lines(linked)),
                       unsettled=tuple(unsettled_lines(db, user_id, linked)), noted=tuple(sorted(c["noted"])))

    live = sorted(live, key=lambda r: r.updated_at)
    lessons = {f"L{i}": shown(f"L{i}", r) for i, r in enumerate(live, 1)}
    withdrawn = {f"W{i}": shown(f"W{i}", r) for i, r in enumerate(gone, 1)}
    return lessons, withdrawn


# --------------------------------------------------------------------------- #
# Applying checked changes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PhoneScope:
    """A phone assessment's audience: the person, the conversation it was learned in, and the conversations the
    behaviour applies to (that one, plus any the person's own words named). Lessons it creates are shadow."""

    person_id: uuid.UUID
    conversation_id: uuid.UUID
    applies_to: tuple[str, ...] = ()


@dataclass
class Source:
    """Where a set of changes comes from: one assessment revision (an occasion) read by one job."""

    occasion: str
    occasion_at: datetime
    assessment_id: uuid.UUID | None
    assessment_revision: int | None
    job_id: uuid.UUID | None = None
    phone: PhoneScope | None = None


@dataclass
class Applied:
    changed: list[dict] = field(default_factory=list)  # {"lesson", "op", "revision", "status", "note"}
    skipped: list[dict] = field(default_factory=list)

    @property
    def lessons(self) -> list[str]:
        return list(dict.fromkeys(c["lesson"] for c in self.changed))


def lock(db: Session, user_id: uuid.UUID) -> None:
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"lessons:{user_id}"))))


def apply(db: Session, user_id: uuid.UUID, changes: Iterable[L.Change], source: Source, *,
          embed=None) -> Applied:
    """Apply checked changes in the caller's transaction (the caller commits). Raises ``Stale`` when a change was
    made from a lesson revision that is no longer current: nothing should be committed then."""
    lock(db, user_id)
    out = Applied()
    changes = list(changes)
    locked: dict[str, MemoryLesson] = {}
    for c in changes:
        if c.lesson_id is None:
            continue
        row = db.scalar(select(MemoryLesson).where(MemoryLesson.id == uuid.UUID(c.lesson_id),
                                                   MemoryLesson.user_id == user_id)
                        .with_for_update().execution_options(populate_existing=True))
        if row is None:
            out.skipped.append({"lesson": c.lesson_id, "op": c.op, "why": "the lesson is gone"})
            continue
        mine = (source.phone.person_id, source.phone.conversation_id) if source.phone is not None else (None, None)
        if (row.phone_person_id, row.phone_conversation_id) != mine:
            # A phone reading changes only that person's lessons learned in its own conversation (a revision from
            # elsewhere would put another audience's words behind it); an ordinary one never a phone lesson.
            out.skipped.append({"lesson": c.lesson_id, "op": c.op, "why": "a lesson of another audience"})
            continue
        if row.revision != c.expected_revision:
            raise Stale(f"lesson {row.id} is at revision {row.revision}, the change was made from "
                        f"{c.expected_revision}")
        locked[c.lesson_id] = row
    embed = embed or _embed
    related: list[tuple[MemoryLesson, L.Change]] = []  # links are made once every change of this reading is in
    for c in changes:
        if c.op == "create":
            row = _create(db, user_id, c, source, embed)
            out.changed.append({"lesson": str(row.id), "op": "create", "revision": 1, "status": row.status,
                                "note": c.why})
            for other in c.conflicts_with:
                _conflict(db, user_id, row, other)
            related.append((row, c))
            continue
        row = locked.get(c.lesson_id or "")
        if row is None:
            continue
        if row.status == "withdrawn":
            out.skipped.append({"lesson": str(row.id), "op": c.op, "why": "withdrawn lessons stay withdrawn"})
            continue
        note = _change(db, row, c, source, embed)
        out.changed.append({"lesson": str(row.id), "op": c.op, "revision": row.revision, "status": row.status,
                            "note": note})
        for other in c.conflicts_with:
            _conflict(db, user_id, row, other)
        if row.status != "withdrawn":
            related.append((row, c))
    db.flush()
    # After every change of this reading, so a link to a lesson this same reading revised records it as revised
    # (never as a premise that changed since).
    for row, c in related:
        _relate(db, row, c, out)
    db.flush()
    if out.changed:
        from app.services import system_signals
        system_signals.emit(user_id, "learning", "memory", "lesson", out.changed[0]["lesson"])
    _log({"op": "apply", "user": str(user_id), "occasion": source.occasion, "changed": len(out.changed),
          "skipped": len(out.skipped)})
    return out


def _create(db: Session, user_id: uuid.UUID, c: L.Change, source: Source, embed) -> MemoryLesson:
    status = L.status_for(c.kind, c.scope, c.certainty or "tentative")
    row = MemoryLesson(id=uuid.uuid4(), user_id=user_id, kind=c.kind,
                       origin="stated" if c.kind == "preference" else "inferred", scope=c.scope, status=status,
                       statement=c.statement, applies_when=c.applies_when, exceptions=list(c.exceptions),
                       user_words=list(c.user_words), revision=1, conflicts_with=[],
                       form=c.form if c.kind in L.INFERRED else None,
                       alternatives=list(c.alternatives) if c.kind in L.INFERRED else [],
                       details=dict(c.details) if c.kind in L.INFERRED else {},
                       status_reason=_status_reason(c.kind, status, 1), embedding=embed(_text(c)))
    if source.phone is not None:
        row.phone_person_id, row.phone_conversation_id = source.phone.person_id, source.phone.conversation_id
        row.phone_applies_to = list(dict.fromkeys([str(source.phone.conversation_id), *source.phone.applies_to]))
        row.phone_live = False  # shadow: nothing activates it automatically
    db.add(row)
    db.flush()
    _evidence(db, row, "support", c, source)
    _revision(db, row, "create", c.why or "first reading", source)
    return row


def _change(db: Session, row: MemoryLesson, c: L.Change, source: Source, embed) -> str:
    if c.op == "note":
        # It bore on the lesson without support or counter (its conditions didn't hold there, or partly covered):
        # recorded for coverage, counted for nothing, never a revision.
        added = _evidence(db, row, "noted", c, source)
        return "noted" if added else "this occasion was already noted"
    if c.op in ("support", "counter"):
        before = _occasions(db, row.id, "support")
        added = _evidence(db, row, c.op, c, source)
        certainty = c.certainty if added else None
        if certainty == "established" and row.status != "accepted" and _occasions(db, row.id, "support") <= before:
            # Raising it needs independent support: evidence sharing an occurrence with what it already rests on
            # (the same run, message or turn, under another assessment) is never a new occasion.
            certainty = None
        moved = _restatus(db, row, c.op, c.why, source, certainty=certainty)
        return ("status now " + row.status if moved else "evidence added") if added else \
            "this occasion was already counted"
    if c.op in ("narrow", "revise"):
        _evidence(db, row, "counter" if c.op == "narrow" else "support", c, source)
        row.scope, row.statement, row.applies_when = c.scope, c.statement, c.applies_when
        row.exceptions, row.user_words = list(c.exceptions), list(c.user_words)
        if row.kind in L.INFERRED:
            row.form, row.alternatives, row.details = c.form, list(c.alternatives), dict(c.details)
        row.embedding = embed(_text(c))
        support = _occasions(db, row.id, "support")
        row.status = L.status_for(row.kind, row.scope, c.certainty or L.certainty_of(row.status))
        row.status_reason = _status_reason(row.kind, row.status, support)
        row.revision += 1
        _revision(db, row, c.op, c.why or c.op, source)
        return f"{c.op}d"
    # withdraw
    _evidence(db, row, "counter", c, source)
    row.status, row.withdrawn_at, row.withdrawn_occasion_at = "withdrawn", _now(), source.occasion_at
    row.status_reason = (f'the user said: "{c.quote}"' if c.quote else
                         "the builder judged against it on cited records") + (f" ({c.why})" if c.why else "")
    row.status_reason = row.status_reason[:500]
    row.revision += 1
    _revision(db, row, "withdraw", c.why or "withdrawn", source)
    return "withdrawn"


def _evidence(db: Session, row: MemoryLesson, role: str, c: L.Change, source: Source) -> bool:
    """Once per occasion and role. Reading the same occasion again (a newer revision of its assessment) refreshes
    the row and makes it count again; it is never a second occasion. Returns whether it newly counts."""
    existing = db.scalar(select(MemoryLessonEvidence).where(
        MemoryLessonEvidence.lesson_id == row.id, MemoryLessonEvidence.role == role,
        MemoryLessonEvidence.occasion == source.occasion[:160]))
    if existing is not None:
        newly = not existing.current
        existing.current = True
        existing.assessment_id, existing.assessment_revision = source.assessment_id, source.assessment_revision
        existing.refs = sorted(set(existing.refs or []) | set(c.roots))
        existing.why = (c.why or existing.why)[:400]
        existing.quote = c.quote or existing.quote
        return newly
    db.add(MemoryLessonEvidence(lesson_id=row.id, user_id=row.user_id, role=role, occasion=source.occasion[:160],
                                occasion_at=source.occasion_at, assessment_id=source.assessment_id,
                                assessment_revision=source.assessment_revision, refs=list(c.roots), quote=c.quote,
                                why=(c.why or "")[:400], current=True, added_revision=row.revision))
    db.flush()
    return True


def _occasions(db: Session, lesson_id: uuid.UUID, role: str) -> int:
    return len(_independent(db, db.scalars(select(MemoryLessonEvidence).where(
        MemoryLessonEvidence.lesson_id == lesson_id, MemoryLessonEvidence.role == role,
        MemoryLessonEvidence.current.is_(True)))))


UNSUPPORTED = "not supplied while the assessment it rested on is read again"


def _supplied(kind: str, status: str, support: int) -> bool:
    """Whether memory gives it to decisions at all: never an episode correction or a withdrawn lesson, and an
    inference only while it has live support (an assessment it rested on may be being read again)."""
    if status in ("episode_only", "withdrawn"):
        return False
    return kind not in L.INFERRED or support > 0


def _restatus(db: Session, row: MemoryLesson, op: str, why: str, source: Source | None, *,
              certainty: str | None = None) -> bool:
    """A change in the builder's judgment of an inference's certainty, or in whether it has live support, changes
    whether and how it is supplied: that is a revision. Returns whether one was made."""
    support = _occasions(db, row.id, "support")
    status = row.status
    if certainty is not None and row.kind in L.INFERRED and row.status != "withdrawn":
        status = L.status_for(row.kind, row.scope, certainty)
    reason = _status_reason(row.kind, status, support)
    before = _supplied(row.kind, row.status, 0 if row.status_reason == UNSUPPORTED else 1)
    if status == row.status and before == _supplied(row.kind, status, support):
        row.status_reason = reason
        return False
    row.status, row.status_reason = status, reason
    row.revision += 1
    _revision(db, row, op, why or f"status {status}", source)
    return True


def _status_reason(kind: str, status: str, support: int) -> str | None:
    if status == "episode_only":
        return "about one past episode, not a standing preference"
    if kind in L.INFERRED and support == 0 and status != "withdrawn":
        return UNSUPPORTED
    if status == "hypothesis":
        return f"tentative: a guess resting on {support} independent occasion(s)"
    return None


def _revision(db: Session, row: MemoryLesson, op: str, reason: str, source: Source | None, *,
              prompted_by: str | None = None, job_id: uuid.UUID | None = None) -> None:
    db.add(MemoryLessonRevision(
        lesson_id=row.id, user_id=row.user_id, revision=row.revision, op=op, kind=row.kind, scope=row.scope,
        status=row.status, statement=row.statement, applies_when=row.applies_when,
        exceptions=list(row.exceptions or []), user_words=list(row.user_words or []), form=row.form,
        alternatives=list(row.alternatives or []), details=dict(row.details or {}), reason=reason[:1000],
        assessment_id=source.assessment_id if source else None,
        assessment_revision=source.assessment_revision if source else None,
        job_id=source.job_id if source else job_id, prompted_by=prompted_by))
    db.flush()


def _conflict(db: Session, user_id: uuid.UUID, row: MemoryLesson, other_id: str) -> None:
    other = db.scalar(select(MemoryLesson).where(MemoryLesson.id == uuid.UUID(other_id),
                                                 MemoryLesson.user_id == user_id).with_for_update())
    if other is None or other.id == row.id:
        return
    row.conflicts_with = sorted(set(row.conflicts_with or []) | {str(other.id)})
    other.conflicts_with = sorted(set(other.conflicts_with or []) | {str(row.id)})


# --------------------------------------------------------------------------- #
# Links to other memories, premises (post-audit 4)
# --------------------------------------------------------------------------- #

TARGET_WORDS = {"lesson": "another lesson of hers", "fact": "a fact memory holds about the user",
                "item": "one of her working items"}
DEPTH = 3  # how far an unsettled premise is followed up a chain of lessons


def _target_record(db: Session, user_id: uuid.UUID, kind: str, target_id: str) -> Any | None:
    """The record a link's target names (a lesson, a fact or a working item) as it is now, this user's only;
    None when it is malformed, gone, deleted or another user's. The one place the target-reading rules live."""
    from app.db.models import SemanticMemory, WorkingItem

    try:
        key = uuid.UUID(str(target_id))
    except ValueError:
        return None
    model = {"lesson": MemoryLesson, "fact": SemanticMemory, "item": WorkingItem}.get(kind)
    if model is None:
        return None
    r = db.get(model, key)
    if r is None or r.user_id != user_id or (kind == "fact" and r.status == "deleted"):
        return None
    return r


def _target_fields(kind: str, r: Any) -> tuple[int, str, str, str]:
    """(revision, status, words, what it is) of a target record."""
    if kind == "lesson":
        return r.revision, r.status, r.statement, TARGET_WORDS["lesson"]
    if kind == "fact":
        return r.revision, r.status, r.content, TARGET_WORDS["fact"]
    whose = "her" if r.owner == "columbina" else "the user's" if r.owner == "user" else "a shared"
    return r.revision, r.status, r.text, f"{whose} {r.kind}"


def _target_now(db: Session, user_id: uuid.UUID, kind: str, target_id: str) -> tuple[int, str, str, str] | None:
    """(revision, status, words, what it is) of a link's target as it is now; None when it is gone."""
    r = _target_record(db, user_id, kind, target_id)
    return None if r is None else _target_fields(kind, r)


@dataclass(frozen=True)
class RelatedTarget:
    """A fact or working item of the user's as it is now, for learning to relate a finding to (never evidence)."""

    revision: int
    status: str
    text: str
    what: str
    carryable: bool  # an open hypothesis of hers: a lesson can carry it forward (memory then closes it)


def related_target(db: Session, user_id: uuid.UUID, kind: str, target_id: str) -> RelatedTarget | None:
    """A fact or working item of this user's as it is now, read once; None when it is malformed, gone, deleted or
    another user's (and for any other kind: a finding relates only to what a lesson can rest on). Read only."""
    if kind not in ("fact", "item"):
        return None
    r = _target_record(db, user_id, kind, target_id)
    if r is None:
        return None
    revision, status, words, what = _target_fields(kind, r)
    carryable = kind == "item" and r.kind == "hypothesis" and r.owner == "columbina" and r.closed_at is None
    return RelatedTarget(revision, status, words, what, carryable)


def _rests_on(db: Session, lesson_id: uuid.UUID) -> set[uuid.UUID]:
    """Every lesson this one rests on or builds on, through any chain (a cycle guard)."""
    seen: set[uuid.UUID] = set()
    frontier = {lesson_id}
    while frontier:
        rows = db.scalars(select(MemoryLessonLink.target_id).where(
            MemoryLessonLink.lesson_id.in_(list(frontier)), MemoryLessonLink.target_kind == "lesson",
            MemoryLessonLink.relation.in_(LESSON_BASIS_RELATIONS)))
        nxt = set()
        for t in rows:
            try:
                key = uuid.UUID(t)
            except ValueError:
                continue
            if key not in seen:
                seen.add(key)
                nxt.add(key)
        frontier = nxt
    return seen


def _relate(db: Session, row: MemoryLesson, c: L.Change, out: "Applied") -> None:
    """The change's links (made or remade as their targets are now) and the hypotheses of hers it carries forward
    (memory closes them, pointing here). Never support."""
    for link in c.links:
        now = _target_now(db, row.user_id, link["target_kind"], link["target_id"])
        if now is None:
            out.skipped.append({"lesson": str(row.id), "op": "link", "why": "the memory it names is gone"})
            continue
        if link["relation"] in LESSON_BASIS_RELATIONS and link["target_kind"] == "lesson" and (
                uuid.UUID(link["target_id"]) == row.id or row.id in _rests_on(db, uuid.UUID(link["target_id"]))):
            out.skipped.append({"lesson": str(row.id), "op": "link",
                                "why": "it would make the lesson rest on itself"})
            continue
        _link(db, row, link["relation"], link["target_kind"], link["target_id"], now, link.get("why") or "")
    for item_id in c.carries:
        _carry(db, row, item_id, out)


def _link(db: Session, row: MemoryLesson, relation: str, kind: str, target_id: str,
          now: tuple[int, str, str, str], why: str) -> None:
    revision, status, words, _ = now
    values = {"lesson_id": row.id, "user_id": row.user_id, "relation": relation, "target_kind": kind,
              "target_id": str(target_id), "target_revision": revision, "target_status": status,
              "target_text": (words or "")[:600], "why": (why or "")[:400], "lesson_revision": row.revision}
    db.execute(insert(MemoryLessonLink).values(**values).on_conflict_do_update(
        constraint="uq_memory_lesson_links", set_={k: values[k] for k in (
            "relation", "target_revision", "target_status", "target_text", "why", "lesson_revision")}
        | {"settled_note": None, "updated_at": func.now()}))


def _carry(db: Session, row: MemoryLesson, item_id: str, out: "Applied") -> None:
    """One of her open hypotheses that this lesson now carries forward: closed by memory's own rule, pointing here,
    and linked as the same understanding (its closing by this lesson is not a changed premise). Lock order: the
    user's lesson lock, then memory's (nothing takes them the other way)."""
    from app.services import memory_manager

    try:
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"memory:{row.user_id}"))))
        with db.begin_nested():
            entry = memory_manager.item(db, row.user_id, uuid.UUID(item_id), lock=True)
            if entry.kind != "hypothesis" or entry.owner != "columbina" or entry.closed_at is not None:
                out.skipped.append({"lesson": str(row.id), "op": "carry", "why": "not an open hypothesis of hers"})
                return
            memory_manager.resolve(db, row.user_id, entry.id, status="unresolved",
                                   reason=f"carried forward as her lesson {row.id} (revision {row.revision})",
                                   expected_revision=entry.revision)
            _link(db, row, "same_as", "item", item_id, _target_now(db, row.user_id, "item", item_id),
                  "carried forward by this lesson")
    except Exception as exc:  # noqa: BLE001 - an item memory won't close stays open, said
        out.skipped.append({"lesson": str(row.id), "op": "carry", "why": f"{type(exc).__name__}: {exc}"[:200]})


@dataclass(frozen=True)
class PremiseState:
    """One link of a lesson, with its target as it was then and as it is now."""

    link_id: int
    relation: str
    target_kind: str
    target_id: str
    what: str
    then_text: str
    then_revision: int | None
    then_status: str | None
    now: tuple[int, str, str, str] | None
    note: str | None

    @property
    def basis(self) -> bool:
        return self.relation in LESSON_BASIS_RELATIONS

    @property
    def changed(self) -> bool:
        return self.now is None or (self.now[0], self.now[1]) != (self.then_revision, self.then_status)

    def change_words(self) -> str:
        if self.now is None:
            return "is gone (forgotten or deleted)"
        revision, status, words, _ = self.now
        if status != self.then_status:
            said = f'; it now says "{words}"' if words != self.then_text else ""
            return f"is now {STATUS_WORDS.get(status, status)}{said}"
        return f'was revised: it now says "{words}"'

    def to_dict(self) -> dict:
        return {"relation": self.relation, "target_kind": self.target_kind, "target_id": self.target_id,
                "what": self.what, "then_text": self.then_text, "then_revision": self.then_revision,
                "then_status": self.then_status, "now_revision": self.now[0] if self.now else None,
                "now_status": self.now[1] if self.now else None, "now_text": self.now[2] if self.now else None,
                "changed": self.changed, "note": self.note}


STATUS_WORDS = {"withdrawn": "withdrawn", "hypothesis": "only a guess", "accepted": "established",
                "superseded": "superseded (memory replaced it)", "disproved": "closed as disproved",
                "supported": "closed as supported", "unresolved": "closed unresolved", "answered": "answered",
                "dropped": "dropped"}


def premises(db: Session, user_id: uuid.UUID, lesson_ids) -> dict[uuid.UUID, list[PremiseState]]:
    """Every link of these lessons, with its target now. Read only."""
    ids = list(lesson_ids)
    out: dict[uuid.UUID, list[PremiseState]] = {i: [] for i in ids}
    if not ids:
        return out
    for link in db.scalars(select(MemoryLessonLink).where(MemoryLessonLink.user_id == user_id,
                                                          MemoryLessonLink.lesson_id.in_(ids))
                           .order_by(MemoryLessonLink.id)):
        now = _target_now(db, user_id, link.target_kind, link.target_id)
        what = now[3] if now else TARGET_WORDS.get(link.target_kind, link.target_kind)
        out[link.lesson_id].append(PremiseState(link.id, link.relation, link.target_kind, link.target_id, what,
                                                link.target_text, link.target_revision, link.target_status, now,
                                                link.settled_note))
    return out


def unsettled_lines(db: Session, user_id: uuid.UUID, states: list[PremiseState], *, depth: int = DEPTH,
                    seen: frozenset = frozenset()) -> list[str]:
    """What a consumer is told about changed premises (said the moment the change commits, before any
    reassessment): which one, what it said, what became of it. A premise lesson that is itself unsettled makes
    this one unsettled too, said as such."""
    out = []
    for p in states:
        if not p.basis:
            continue
        verb = "rests on" if p.relation == "depends_on" else "builds on"
        if p.changed:
            out.append(f'it {verb} {p.what} that said "{p.then_text}", which {p.change_words()} since it was last '
                       "assessed; it is being reassessed")
        elif p.target_kind == "lesson" and depth > 0 and p.target_id not in seen:
            inner = premises(db, user_id, [uuid.UUID(p.target_id)]).get(uuid.UUID(p.target_id), [])
            if unsettled_lines(db, user_id, inner, depth=depth - 1, seen=seen | {p.target_id}):
                out.append(f'it {verb} {p.what} that says "{p.then_text}", which is itself unsettled (something it '
                           "rests on changed)")
    return out


def basis_lines(states: list[PremiseState]) -> list[str]:
    """What a consumer is told a lesson rests on or builds on (unchanged ones; changed ones are unsettled lines)."""
    out = []
    for p in states:
        if p.basis and not p.changed:
            note = f" (reassessed: {p.note})" if p.note else ""
            if p.then_status in ENDED:
                verb = "Rested on" if p.relation == "depends_on" else "Built on"
                out.append(f'{verb} {p.what}: "{p.then_text}", since {STATUS_WORDS.get(p.then_status, p.then_status)}'
                           f"{note}")
                continue
            verb = "Rests on" if p.relation == "depends_on" else "Builds on"
            out.append(f'{verb} {p.what}: "{p.then_text}"{note}')
    return out


# A target in one of these states no longer holds as it did: a lesson that rests on it says so in the past.
ENDED = ("withdrawn", "superseded", "deleted", "disproved", "dropped", "unresolved", "cancelled", "lifted", "abandoned",
         "lapsed")


def link_lines(states: list[PremiseState]) -> list[str]:
    """Every link, for learning's readers (the reviewer and the builder): never evidence."""
    out = []
    for p in states:
        state = f"CHANGED: {p.change_words()}" if p.changed else "unchanged"
        out.append(f'{p.relation.replace("_", " ")} {p.what}: "{p.then_text}" ({state})'
                   + (f"; reassessed: {p.note}" if p.note else ""))
    return out


def _details_tuple(details: dict | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in (details or {}).items() if v))


def _text(c: L.Change | MemoryLesson) -> str:
    return f"{c.statement} Applies: {c.applies_when}. " + " ".join(f"Except: {e}." for e in c.exceptions)


def _embed(text: str) -> list[float] | None:
    from app.services import recall

    try:
        return recall.embed(text)
    except Exception:  # noqa: BLE001 - lessons rank lexically without one
        return None


@dataclass(frozen=True)
class Prepared:
    """Lesson changes made ready for search ahead of ``apply``: what memory will store for each, computed before
    any lock is taken. Pass ``embed`` to ``apply``; text it wasn't prepared for gets no embedding."""

    vectors: dict[str, list[float] | None]

    def embed(self, text: str) -> list[float] | None:
        return self.vectors.get(text)


def prepare_for_search(changes: Iterable[L.Change], *, embed=None) -> Prepared:
    """Prepare these lesson changes for search: the searchable text memory will store for each change that writes
    one (create, narrow, revise) and its embedding, each distinct text once. Calls the embedding model; takes no
    lock and reads no rows, so callers run it before taking theirs."""
    vectors: dict[str, list[float] | None] = {}
    for c in changes:
        if c.op in ("create", "narrow", "revise"):
            text = _text(c)
            if text not in vectors:
                vectors[text] = (embed or _embed)(text)
    return Prepared(vectors)


def reconsider(db: Session, user_id: uuid.UUID, assessment_id: uuid.UUID, revision: int) -> list[str]:
    """An assessment was revised and its conclusions changed: the evidence its earlier revisions gave stops counting
    until the new revision is read (the lesson job for it re-reads and may support again). A strategy that loses
    its support stops being supplied. In the caller's transaction; returns the lessons touched."""
    lock(db, user_id)
    rows = list(db.scalars(select(MemoryLessonEvidence).where(
        MemoryLessonEvidence.user_id == user_id, MemoryLessonEvidence.assessment_id == assessment_id,
        MemoryLessonEvidence.assessment_revision < revision, MemoryLessonEvidence.current.is_(True))))
    touched = sorted({r.lesson_id for r in rows}, key=str)
    for r in rows:
        r.current = False
    db.flush()
    for lesson_id in touched:
        row = db.scalar(select(MemoryLesson).where(MemoryLesson.id == lesson_id).with_for_update())
        if row is not None and row.status != "withdrawn":
            _restatus(db, row, "reconsider", f"assessment {assessment_id} was revised (revision {revision}); "
                                             "its earlier reading no longer counts until read again", None)
    return [str(t) for t in touched]


def reconsidered(db: Session, user_id: uuid.UUID, c: L.Change, *, job_id: uuid.UUID | None,
                 premises_read: dict[str, PremiseState], embed=None) -> dict:
    """Apply a reconsideration (post-audit 4): the lesson read again on the evidence it already rests on, after a
    critique of its reasoning or a changed premise. In the caller's transaction; raises ``Stale`` when the lesson
    moved since it was read. No evidence is ever added and nothing is counted: a reconsideration is not an
    occasion. ``keep`` changes nothing it says (no revision) and settles the premises it read; ``narrow`` /
    ``revise`` / ``withdraw`` make a revision marked with what prompted it. ``premises_read``: the links as the
    reconsideration saw them, by their ref there (``P1``); each is settled at what it showed, so a premise that
    changed again meanwhile stays unsettled."""
    lock(db, user_id)
    row = db.scalar(select(MemoryLesson).where(MemoryLesson.id == uuid.UUID(c.lesson_id),
                                               MemoryLesson.user_id == user_id)
                    .with_for_update().execution_options(populate_existing=True))
    if row is None:
        return {"lesson": c.lesson_id, "op": c.op, "applied": False, "why": "the lesson is gone"}
    if row.revision != c.expected_revision:
        raise Stale(f"lesson {row.id} is at revision {row.revision}, it was reconsidered at {c.expected_revision}")
    if row.status == "withdrawn":
        return {"lesson": str(row.id), "op": c.op, "applied": False, "why": "withdrawn lessons stay withdrawn"}
    reason = f"reconsidered on the same evidence ({c.prompted_by}): {c.why}"
    if c.op in ("narrow", "revise"):
        row.scope, row.statement, row.applies_when = c.scope, c.statement, c.applies_when
        row.exceptions = list(c.exceptions)
        if row.kind in L.INFERRED:
            row.form, row.alternatives, row.details = c.form, list(c.alternatives), dict(c.details)
        row.status = L.status_for(row.kind, row.scope, c.certainty or L.certainty_of(row.status))
        row.status_reason = _status_reason(row.kind, row.status, _occasions(db, row.id, "support"))
        row.embedding = (embed or _embed)(_text(row))
        row.revision += 1
        _revision(db, row, c.op, reason, None, prompted_by=c.prompted_by, job_id=job_id)
    elif c.op == "withdraw":
        # Older work can't bring it back: the evidence it was read on is no newer than this.
        latest = db.scalar(select(func.max(MemoryLessonEvidence.occasion_at)).where(
            MemoryLessonEvidence.lesson_id == row.id))
        row.status, row.withdrawn_at, row.withdrawn_occasion_at = "withdrawn", _now(), latest or _now()
        row.status_reason = f"withdrawn when reconsidered ({c.prompted_by}): {c.why}"[:500]
        row.revision += 1
        _revision(db, row, "withdraw", reason, None, prompted_by=c.prompted_by, job_id=job_id)
    for ref, p in premises_read.items():
        link = db.get(MemoryLessonLink, p.link_id)
        if link is None or link.lesson_id != row.id:
            continue
        if p.now is None:
            # Gone: nothing left to rest on. The link stays as lineage, recorded as gone, no longer a basis.
            link.relation, link.target_status = "related", "gone"
            link.settled_note = f"gone; reassessed: {c.why}"[:400]
            continue
        link.target_revision, link.target_status, link.target_text = p.now[0], p.now[1], (p.now[2] or "")[:600]
        if p.changed:
            link.settled_note = c.why[:400]
        if ref in c.unlink and p.basis:
            link.relation = "related"
            link.settled_note = f"not a premise after all: {c.why}"[:400]
        link.lesson_revision = row.revision
    db.flush()
    if c.op != "keep":
        from app.services import system_signals
        system_signals.emit(user_id, "learning", "memory", "lesson", str(row.id))
    _log({"op": "reconsidered", "user": str(user_id), "lesson": str(row.id), "decision": c.op,
          "prompted_by": c.prompted_by})
    return {"lesson": str(row.id), "op": c.op, "applied": True, "revision": row.revision, "status": row.status}


def changed_lessons(db: Session, *, limit: int = 50) -> list[tuple[MemoryLesson, list[PremiseState]]]:
    """Lessons (not withdrawn) whose basis changed since they were last assessed: what reassessment owes a reading.
    Read only. A lesson is unsettled from the moment its premise changed, whether or not this has run."""
    candidates = list(db.scalars(select(MemoryLessonLink.lesson_id).where(
        MemoryLessonLink.relation.in_(LESSON_BASIS_RELATIONS)).distinct()))
    out = []
    for lesson_id in candidates:
        row = db.get(MemoryLesson, lesson_id)
        if row is None or row.status == "withdrawn":
            continue
        states = [p for p in premises(db, row.user_id, [row.id])[row.id] if p.basis and p.changed]
        if states:
            out.append((row, states))
            if len(out) >= limit:
                break
    return out


REVIEW_REVISIONS = 6  # the most recent revisions a reconsideration reads


@dataclass(frozen=True)
class LessonForReview:
    """One lesson as memory holds it now, for reading it again: the lesson (ref ``L``), the premises it rests on or
    builds on (its basis, in link order) and its most recent revisions (oldest first). What it means is Learning's."""

    view: L.Shown
    premises: tuple[PremiseState, ...]
    revisions: tuple[MemoryLessonRevision, ...]


def lesson_for_review(db: Session, user_id: uuid.UUID, lesson_id: uuid.UUID) -> LessonForReview | None:
    """The lesson read again by a reconsideration, gathered as of now in the caller's session. Read only. None when
    the user holds no such lesson."""
    row = db.get(MemoryLesson, lesson_id)
    if row is None or row.user_id != user_id:
        return None
    counts = _counts(db, [row.id])[row.id]
    view = L.Shown("L", str(row.id), row.revision, row.kind, row.scope, row.status, row.statement, row.applies_when,
                   tuple(row.exceptions or ()), tuple(row.user_words or ()), tuple(sorted(counts["support"])),
                   tuple(sorted(counts["counter"])), tuple(sorted(counts["occasions"])), None,
                   _aware(row.withdrawn_occasion_at), row.status_reason, row.form, tuple(row.alternatives or ()),
                   details=dict(row.details or {}))
    basis = tuple(p for p in premises(db, user_id, [row.id])[row.id] if p.basis)
    revisions = tuple(db.scalars(select(MemoryLessonRevision).where(MemoryLessonRevision.lesson_id == row.id)
                                 .order_by(MemoryLessonRevision.revision.desc()).limit(REVIEW_REVISIONS)))[::-1]
    return LessonForReview(view, basis, revisions)


# --------------------------------------------------------------------------- #
# Retrieval for a decision
# --------------------------------------------------------------------------- #


@dataclass
class Supply:
    """What a decision is given: the lessons shown (in order, as ``L1``..), and accepted ones left out, with why."""

    views: tuple[L.LessonView, ...] = ()
    left_out: tuple[tuple[str, int, str], ...] = ()  # (lesson id, revision, reason)
    accepted: int = 0
    ranked_by: str = "none"  # none / all_fit / lexical / embedding+lexical
    error: str | None = None

    def refs(self) -> list[str]:
        return [v.ref for v in self.views]

    def to_dict(self) -> dict:
        return {"lessons": [v.to_dict() for v in self.views], "left_out": [
            {"id": i, "revision": r, "reason": why} for i, r, why in self.left_out],
            "accepted": self.accepted, "ranked_by": self.ranked_by, "interface": L.INTERFACE_VERSION,
            "error": self.error}


def _words(text: str) -> set[str]:
    from app.services.long_term_memory import _content_words

    return _content_words(text or "")


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def for_decision(db: Session, user_id: uuid.UUID, query: str, *, budget: int | None = None,
                 embed=None, include_tentative: bool = False) -> Supply:
    """Accepted lessons, with supported situation hypotheses when include_tentative is requested.
    All of them while they fit; past the budget, the
    most relevant (meaning when embeddings are available, else shared words), with a conflict's counterpart kept
    beside it. Whole lessons only: nothing is cut out of one."""
    budget = get_settings().lessons_supplied_max if budget is None else budget
    rows = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.phone_person_id.is_(None),
                                                      MemoryLesson.status.in_(("accepted", "hypothesis") if include_tentative else ("accepted",)))
                           .order_by(MemoryLesson.created_at)))
    if not rows:
        return Supply()
    counts = _counts(db, [r.id for r in rows])
    # An inference is supplied only with live support: a revised assessment may leave one with no current
    # evidence while it is read again, and that is never supplied, accepted or not.
    rows = [r for r in rows if _supplied(r.kind, r.status, len(counts[r.id]["support"]))]
    ranked_by = "all_fit"
    chosen = rows
    left: list[tuple[MemoryLesson, str]] = []
    if len(rows) > budget:
        q_words = _words(query)
        vector = None
        if any(r.embedding for r in rows):
            vector = (embed or _embed)(query[:2000]) if query.strip() else None
        ranked_by = "embedding+lexical" if vector else "lexical"

        def score(r: MemoryLesson) -> float:
            words = _words(_text(r))
            lexical = len(q_words & words) / len(words) if words else 0.0
            return _cosine(vector, r.embedding) + lexical

        order = sorted(rows, key=lambda r: (-score(r), r.created_at))
        chosen = order[:budget]
        ids = {r.id for r in chosen}
        by_id = {str(r.id): r for r in rows}
        # A conflict is shown as a pair, never as whichever side ranked higher.
        for r in list(chosen):
            if r.id not in ids:
                continue  # swapped out for another lesson's counterpart
            for other_id in r.conflicts_with or []:
                other = by_id.get(other_id)
                if other is None or other.id in ids:
                    continue
                victims = [x for x in reversed(chosen) if not (set(x.conflicts_with or []) & {str(i) for i in ids})
                           and x.id != r.id]
                if victims:
                    chosen.remove(victims[0])
                    ids.discard(victims[0].id)
                    chosen.append(other)
                    ids.add(other.id)
        chosen = sorted(chosen, key=lambda r: order.index(r))
        left = [(r, "cut by the budget: less relevant here") for r in order if r.id not in ids]
    refs = {r.id: f"L{i}" for i, r in enumerate(chosen, 1)}
    states = premises(db, user_id, [r.id for r in chosen])
    views = [_view(db, user_id, r, refs, counts, states.get(r.id, [])) for r in chosen]
    return Supply(tuple(views), tuple((str(r.id), r.revision, why) for r, why in left), sum(r.status == "accepted" for r in rows), ranked_by)


def _view(db: Session, user_id: uuid.UUID, r: MemoryLesson, refs: dict, counts: dict, linked: list) -> L.LessonView:
    """One lesson as a decision reads it: whole, with what it rests on and whether that changed since."""
    conflicts = tuple(refs[uuid.UUID(o)] for o in r.conflicts_with or [] if uuid.UUID(o) in refs)
    sources = tuple(sorted({ref for e in db.scalars(select(MemoryLessonEvidence).where(
        MemoryLessonEvidence.lesson_id == r.id, MemoryLessonEvidence.current.is_(True))) for ref in e.refs or []}))
    return L.LessonView(refs[r.id], str(r.id), r.revision, r.kind, r.scope, r.statement, r.applies_when,
                        tuple(r.exceptions or ()), tuple(r.user_words or ()), len(counts[r.id]["support"]), conflicts,
                        r.status, r.status_reason, sources, r.form, tuple(r.alternatives or ()),
                        _details_tuple(r.details), tuple(basis_lines(linked)),
                        tuple(unsettled_lines(db, user_id, linked)))


def refresh(db: Session, user_id: uuid.UUID, supply: "Supply") -> "Supply":
    """A supply prepared earlier, made current before it is delivered (post-audit 4): each lesson is read again; one
    that changed since (a new revision, or a premise that changed: unsettled) is given as it is now, and one no
    longer supplied (withdrawn, gone, or without live support) is left out, said. Refs stay as they were."""
    if not supply.views:
        return supply
    ids = [uuid.UUID(v.id) for v in supply.views]
    rows = {r.id: r for r in db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                                   MemoryLesson.id.in_(ids)))}
    counts = _counts(db, list(rows))
    states = premises(db, user_id, list(rows))
    refs = {uuid.UUID(v.id): v.ref for v in supply.views}
    views, left = [], list(supply.left_out)
    for v in supply.views:
        r = rows.get(uuid.UUID(v.id))
        if r is None or not _supplied(r.kind, r.status, len(counts[r.id]["support"])):
            left.append((v.id, r.revision if r else v.revision, "changed since it was prepared: no longer supplied"))
            continue
        now = _view(db, user_id, r, refs, counts, states.get(r.id, []))
        views.append(now if now != v else v)
    return Supply(tuple(views), tuple(left), supply.accepted, supply.ranked_by, supply.error)


def current(supply: "Supply") -> tuple["Supply", str | None]:
    """``refresh`` in its own session, for a supply prepared earlier and about to be delivered: (the supply as it is
    now, None), or, when that can't be confirmed, (no lessons, why): a lesson never goes out unconfirmed."""
    from app.db.session import SessionLocal

    if not supply.views:
        return supply, None
    try:
        with SessionLocal() as db:
            owners = set(db.scalars(select(MemoryLesson.user_id).where(
                MemoryLesson.id.in_([uuid.UUID(v.id) for v in supply.views]))))
            if len(owners) > 1:
                raise ValueError("a supply mixes users")
            user_id = next(iter(owners), None)
            fresh = refresh(db, user_id, supply) if user_id else Supply(
                (), tuple(supply.left_out) + tuple((v.id, v.revision, "gone since it was prepared")
                                                   for v in supply.views), supply.accepted, supply.ranked_by)
            db.rollback()
            return fresh, None
    except Exception as exc:  # noqa: BLE001 - unconfirmed lessons are not delivered
        log.exception("memory_lessons: confirming a prepared supply failed")
        return Supply(error=f"{type(exc).__name__}"[:100]), (
            f"its lessons couldn't be confirmed current ({type(exc).__name__}), so none were given")


def for_learning(db: Session, user_id: uuid.UUID, query: str, *, limit: int = 6, embed=None,
                 phone_person: uuid.UUID | None = None, phone_conversation: uuid.UUID | None = None) -> list:
    """Her lessons for this user (every status but withdrawn), the most relevant to ``query``: what learning's
    reviewer compares new experience with. Read only. Earlier understanding, never evidence. ``phone_person``: a
    phone assessment's person, whose lessons alone it reads (otherwise phone lessons are left out)."""
    rows = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.status != "withdrawn",
                                                      _audience(phone_person, phone_conversation))
                           .order_by(MemoryLesson.created_at)))
    return _for_learning(db, user_id, _relevant(rows, query, limit, embed=embed))


def for_learning_ids(db: Session, user_id: uuid.UUID, ids) -> list:
    """These lessons of this user (every status but withdrawn), shown to learning's reviewer the same way as
    ``for_learning``: the ones her parts were given during what it reads. Read only."""
    ids = [i for i in ids if i]
    if not ids:
        return []
    rows = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id, MemoryLesson.id.in_(ids),
                                                      MemoryLesson.phone_person_id.is_(None),
                                                      MemoryLesson.status != "withdrawn")
                           .order_by(MemoryLesson.created_at)))
    return _for_learning(db, user_id, rows)


def _for_learning(db: Session, user_id: uuid.UUID, rows: list) -> list:
    from app.ai.learning import assessment as A

    counts = _counts(db, [r.id for r in rows])
    states = premises(db, user_id, [r.id for r in rows])
    out = []
    for r in rows:
        what = r.kind + (f": {r.form}" if r.form else "")
        c = counts[r.id]
        text = (f"({r.status}, {what}, {r.scope}) {r.statement} Applies: {r.applies_when}."
                + "".join(f" Except: {e}." for e in r.exceptions or [])
                + (f" Other explanations: {'; '.join(r.alternatives)}." if r.alternatives else "")
                + "".join(f" {d[0].upper()}{d[1:]}." for d in L.detail_lines(r.details))
                + f" Rests on {len(c['support'])} independent occasion(s), found against on {len(c['counter'])}"
                + (f", noted without either on {len(c['noted'])}" if c["noted"] else "") + "."
                + "".join(f" Link: {line}." for line in link_lines(states.get(r.id, [])))
                + "".join(f" UNSETTLED: {line}." for line in unsettled_lines(db, user_id, states.get(r.id, []))))
        out.append(A.PriorLesson("", str(r.id), r.revision, text))
    return out


def for_game(db: Session, user_id: uuid.UUID, game: str | None, *, query: str = "") -> Supply:
    """The same bounded retrieval used by conversation, with game context for ranking."""
    if not game or not get_settings().lessons_supply_enabled:
        return Supply(ranked_by="supply_off")
    return for_decision(db, user_id, f"{game} {query}", include_tentative=True)


@dataclass(frozen=True)
class AudienceLesson:
    """A live phone lesson as the reply writer of one audience reads it: its behaviour, and only in the conversation
    it was learned in, the person's own words behind it."""

    id: str
    statement: str
    applies_when: str
    their_words: tuple[str, ...]
    learned_here: bool


def for_phone_audience(db: Session, user_id: uuid.UUID, person_id: uuid.UUID,
                       conversation_id: uuid.UUID) -> list[AudienceLesson]:
    """Live, accepted lessons about this person whose behaviour applies to this conversation, filtered in the query
    itself (before anything is read): shadow lessons, other people's and other audiences' never come back. The
    explanation (the person's words) only where it was learned."""
    rows = db.scalars(select(MemoryLesson).where(
        MemoryLesson.user_id == user_id, MemoryLesson.phone_person_id == person_id,
        MemoryLesson.phone_live.is_(True), MemoryLesson.status == "accepted",
        MemoryLesson.phone_applies_to.contains([str(conversation_id)])).order_by(MemoryLesson.created_at))
    out = []
    for r in rows:
        here = r.phone_conversation_id == conversation_id
        out.append(AudienceLesson(str(r.id), r.statement, r.applies_when,
                                  tuple(r.user_words or ()) if here else (), here))
    return out


def offered(user_id: uuid.UUID, query: str) -> Supply:
    """``for_decision`` (tentative ones labelled) in its own session, never raising (a failure is an empty supply
    that says so)."""
    from app.db.session import SessionLocal

    if not get_settings().lessons_supply_enabled:
        return Supply(ranked_by="supply_off")
    try:
        with SessionLocal() as db:
            supply = for_decision(db, user_id, query, include_tentative=True)
            db.rollback()
            return supply
    except Exception as exc:  # noqa: BLE001 - no lessons this decision, said as such
        log.exception("memory_lessons: retrieval failed")
        return Supply(error=f"{type(exc).__name__}"[:100])


# --------------------------------------------------------------------------- #
# What each decision received, and what it said about it
# --------------------------------------------------------------------------- #


# Which part a lesson consumer is, for the System page's signals.
_SIGNAL_TO = {"hands": "hands", "planner": "mind", "mind": "mind"}


def record_uses(user_id: uuid.UUID, attempt_id: uuid.UUID | str, supply: Supply, *, consumer: str,
                received: bool, reason: str | None = None, weighed: list[dict] | None = None,
                decision_status: str | None = None) -> int:
    """One row per lesson the decision could have had: shown (received or not, with why not) or left out by the
    budget. Its own session, never raising. Idempotent per (attempt, lesson, consumer)."""
    from app.db.session import SessionLocal

    if not supply.views and not supply.left_out:
        return 0
    by_ref = {w["lesson"]: w for w in weighed or []}
    rows = []
    for rank, v in enumerate(supply.views, 1):
        w = by_ref.get(v.ref) if received else None
        rows.append({"user_id": user_id, "lesson_id": uuid.UUID(v.id), "revision": v.revision, "consumer": consumer,
                     "attempt_id": uuid.UUID(str(attempt_id)), "received": received,
                     "reason": None if received else (reason or "not given to it"), "ref": v.ref, "rank": rank,
                     "reported": w["use"] if w else None, "reported_why": w["why"] if w else None,
                     "decision_status": decision_status})
    for lesson_id, revision, why in supply.left_out:
        rows.append({"user_id": user_id, "lesson_id": uuid.UUID(lesson_id), "revision": revision,
                     "consumer": consumer, "attempt_id": uuid.UUID(str(attempt_id)), "received": False,
                     "reason": why, "ref": None, "rank": None, "reported": None, "reported_why": None,
                     "decision_status": decision_status})
    try:
        with SessionLocal() as db:
            db.execute(insert(MemoryLessonUse).values(rows).on_conflict_do_nothing(
                constraint="uq_memory_lesson_uses"))
            db.commit()
        if supply.views and _SIGNAL_TO.get(consumer):
            from app.services import system_signals
            system_signals.emit(user_id, "memory", _SIGNAL_TO[consumer], f"lessons_{consumer}", attempt_id)
        return len(rows)
    except Exception:  # noqa: BLE001 - a lost use record is logged, never fails the decision
        log.exception("memory_lessons: recording lesson uses failed")
        return 0


EXPLAIN = {
    "not_received": "never reached it: {reason}. Look at memory, retrieval or delivery to the decision, not at "
                    "the decision itself.",
    "no_account": "it reached the decision, which says nothing about how it weighed it (overlooked or misunderstood): "
                  "look at the deciding part (her mind, planner or hands), including its model.",
    "no_decision": "it reached the decision step, which produced no valid decision ({status}).",
    "applied": "it reached the decision, which says it applied it: {why} (its own account, not proof of influence).",
    "unclear": "it reached the decision, which couldn't tell whether it applied: {why}",
    "set_aside": "it reached the decision, which says it chose not to follow it ({use}): {why}. That can be sound "
                 "judgment, not a learning failure.",
}


def explain(use: MemoryLessonUse) -> tuple[str, str]:
    """(case, sentence) for one use, from what was recorded."""
    if not use.received:
        return "not_received", EXPLAIN["not_received"].format(reason=use.reason or "not given to it")
    if use.decision_status and use.decision_status != "ok":
        return "no_decision", EXPLAIN["no_decision"].format(status=use.decision_status)
    if use.reported is None:
        return "no_account", EXPLAIN["no_account"]
    if use.reported == "applied":
        return "applied", EXPLAIN["applied"].format(why=use.reported_why or "no reason given")
    if use.reported == "unclear":
        return "unclear", EXPLAIN["unclear"].format(why=use.reported_why or "no reason given")
    return "set_aside", EXPLAIN["set_aside"].format(use=use.reported.replace("_", " "),
                                                    why=use.reported_why or "no reason given")


def _use_out(u: MemoryLessonUse) -> dict:
    case, sentence = explain(u)
    return {"lesson_id": str(u.lesson_id), "revision": u.revision, "consumer": u.consumer,
            "attempt_id": str(u.attempt_id), "received": u.received, "reason": u.reason, "ref": u.ref,
            "rank": u.rank, "reported": u.reported, "reported_why": u.reported_why,
            "decision_status": u.decision_status, "case": case, "explanation": sentence,
            "at": _aware(u.created_at).isoformat()}


def uses_for_attempt(db: Session, user_id: uuid.UUID, attempt_id: uuid.UUID) -> list[dict]:
    rows = list(db.scalars(select(MemoryLessonUse).where(MemoryLessonUse.user_id == user_id,
                                                         MemoryLessonUse.attempt_id == attempt_id)
                           .order_by(MemoryLessonUse.rank.nulls_last())))
    lessons = {r.id: r for r in db.scalars(select(MemoryLesson).where(
        MemoryLesson.id.in_([u.lesson_id for u in rows]), MemoryLesson.user_id == user_id))} if rows else {}
    out = []
    for u in rows:
        item = _use_out(u)
        lesson = lessons.get(u.lesson_id)
        if lesson is not None:
            item["statement_now"] = lesson.statement
            item["revision_now"] = lesson.revision
            item["status_now"] = lesson.status
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #


def _summary(r: MemoryLesson, counts: dict[str, Any]) -> dict:
    return {"id": str(r.id), "kind": r.kind, "origin": r.origin, "scope": r.scope, "status": r.status,
            "statement": r.statement, "applies_when": r.applies_when, "exceptions": list(r.exceptions or []),
            "user_words": list(r.user_words or []), "revision": r.revision, "status_reason": r.status_reason,
            "form": r.form, "alternatives": list(r.alternatives or []), "details": dict(r.details or {}),
            "conflicts_with": list(r.conflicts_with or []), "support": len(counts["support"]),
            "counter": len(counts["counter"]), "noted": len(counts.get("noted") or ()),
            "withdrawn_at": _aware(r.withdrawn_at).isoformat() if r.withdrawn_at else None,
            "created_at": _aware(r.created_at).isoformat(), "updated_at": _aware(r.updated_at).isoformat()}


def listing(db: Session, user_id: uuid.UUID, *, status: str | None = None, limit: int = 100) -> dict:
    counts = {s: n for s, n in db.execute(select(MemoryLesson.status, func.count()).where(
        MemoryLesson.user_id == user_id).group_by(MemoryLesson.status))}
    query = select(MemoryLesson).where(MemoryLesson.user_id == user_id)
    if status:
        query = query.where(MemoryLesson.status == status)
    rows = list(db.scalars(query.order_by(MemoryLesson.updated_at.desc()).limit(limit)))
    c = _counts(db, [r.id for r in rows])
    return {"counts": counts, "items": [_summary(r, c[r.id]) for r in rows]}


def detail(db: Session, user_id: uuid.UUID, lesson_id: uuid.UUID, *, uses: int = 30) -> dict | None:
    row = db.scalar(select(MemoryLesson).where(MemoryLesson.id == lesson_id, MemoryLesson.user_id == user_id))
    if row is None:
        return None
    revisions = list(db.scalars(select(MemoryLessonRevision).where(MemoryLessonRevision.lesson_id == row.id)
                                .order_by(MemoryLessonRevision.revision.desc())))
    evidence = list(db.scalars(select(MemoryLessonEvidence).where(MemoryLessonEvidence.lesson_id == row.id)
                               .order_by(MemoryLessonEvidence.occasion_at)))
    recent = list(db.scalars(select(MemoryLessonUse).where(MemoryLessonUse.lesson_id == row.id)
                             .order_by(MemoryLessonUse.created_at.desc()).limit(uses)))
    linked = premises(db, user_id, [row.id])[row.id]
    return {**_summary(row, _counts(db, [row.id])[row.id]),
            "links": [p.to_dict() for p in linked], "unsettled": unsettled_lines(db, user_id, linked),
            "revisions": [{"revision": v.revision, "op": v.op, "status": v.status, "scope": v.scope,
                           "statement": v.statement, "applies_when": v.applies_when, "exceptions": v.exceptions,
                           "user_words": v.user_words, "form": v.form, "alternatives": v.alternatives,
                           "details": v.details, "prompted_by": v.prompted_by, "reason": v.reason,
                           "assessment_id": str(v.assessment_id) if v.assessment_id else None,
                           "assessment_revision": v.assessment_revision,
                           "at": _aware(v.created_at).isoformat()} for v in revisions],
            "evidence": [{"role": e.role, "occasion": e.occasion, "occasion_at": _aware(e.occasion_at).isoformat(),
                          "assessment_id": str(e.assessment_id) if e.assessment_id else None,
                          "assessment_revision": e.assessment_revision, "refs": e.refs, "quote": e.quote,
                          "why": e.why, "current": e.current, "added_revision": e.added_revision}
                         for e in evidence],
            "uses": [_use_out(u) for u in recent]}


# --------------------------------------------------------------------------- #
# Forgetting, deletion
# --------------------------------------------------------------------------- #


def drop_for(db: Session, user_id: uuid.UUID, refs: Iterable[str]) -> int:
    """Forgetting: lessons resting on (or found against with) forgotten messages go, whole. In the caller's
    transaction."""
    refs = sorted({r for r in refs if r})
    if not refs:
        return 0
    ids = set(db.scalars(select(MemoryLessonEvidence.lesson_id).where(
        MemoryLessonEvidence.user_id == user_id, MemoryLessonEvidence.refs.has_any(cast(refs, ARRAY(Text))))))
    if not ids:
        return 0
    # Raw decision captures that showed one of them go too (training examples keep their own rules).
    from app.services import learning_capture
    learning_capture.drop_raw_for(db, user_id, [f"lesson:{i}" for i in ids])
    removed = db.execute(delete(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                    MemoryLesson.id.in_(ids))).rowcount or 0
    # A lesson left pointing at a removed one no longer conflicts with anything there.
    for row in db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                     MemoryLesson.conflicts_with.has_any(
                                                         cast([str(i) for i in ids], ARRAY(Text))))):
        row.conflicts_with = [c for c in row.conflicts_with or [] if uuid.UUID(c) not in ids]
    _log({"op": "forget", "user": str(user_id), "removed": removed})
    return removed


def _log(record: dict) -> None:
    path = Path(get_settings().learning_lessons_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **record}, default=str) + "\n")
    except OSError:
        log.warning("memory_lessons: could not write %s", path)


__all__ = ["Stale", "Source", "Applied", "Supply", "apply", "shown_for", "reconsider", "for_decision", "for_learning", "for_learning_ids",
           "offered", "refresh", "premises", "unsettled_lines", "basis_lines", "link_lines", "PremiseState",
           "reconsidered", "changed_lessons", "lesson_for_review", "LessonForReview", "current",
           "record_uses", "explain", "uses_for_attempt", "listing", "detail", "drop_for", "lock"]
