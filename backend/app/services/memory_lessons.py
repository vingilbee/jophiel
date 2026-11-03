"""Lessons, memory's side (docs/learning-system/03-immediate-adaptation.md; implementation:
docs/learning-system/03-implementation.md). Memory owns every lesson row; learning (``services/learning_lessons.py``)
proposes checked changes and this module applies them by its own rules.

- **Lifecycle.** ``apply`` creates lessons, adds support or counterevidence (once per occasion and role), narrows,
  revises and withdraws. Every change to what a lesson says or how it's used is a new revision, kept whole in
  ``memory_lesson_revisions``. Status follows from the evidence (``lessons.status_for``): a stated preference is
  accepted at once, an inference only with support from ``LESSONS_MIN_OCCASIONS`` distinct occasions, a correction of
  one episode is ``episode_only``. Withdrawn is final.
- **Asynchronous work can't undo it.** Each change names the lesson revision it was made from; under the user's
  lesson lock and the row lock a different revision means the reading is stale (``Stale``: the caller reads again).
  A withdrawn lesson accepts nothing, and a new lesson saying what a withdrawn one said needs evidence newer than the
  withdrawal (checked in ``lessons.check``). When an assessment is revised, the evidence it gave stops counting until
  its new revision is read (``reconsider``), so a lesson resting only on it stops being supplied.
- **Retrieval.** ``for_decision``: a bounded selection of accepted lessons (``LESSONS_SUPPLIED_MAX``), whole
  (statement, where it applies, exceptions, the user's words), by relevance when there are more than fit; a lesson
  in an open conflict brings its counterpart. Never hypotheses, episode-only or withdrawn lessons.
- **Uses.** ``record_uses``: which revision each decision had, whether it reached the consumer and why not (cut by the
  budget, a consumer whose input has no lessons, supply switched off), and what the consumer said about it.
  ``explain`` names which of the three cases a lesson not followed is: never reached, reached but not accounted for
  (overlooked or misunderstood: cognition), or reached and reasonably set aside (judgment, not a learning failure).
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

from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.orm import Session

from app.ai.learning import lessons as L
from app.core.config import get_settings
from app.db.models import MemoryLesson, MemoryLessonEvidence, MemoryLessonRevision, MemoryLessonUse

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
SHOWN_MAX = 40  # lessons the construction call sees (most recently changed first)
WITHDRAWN_SHOWN = 10
CONSUMERS = ("student_2",)
_log_lock = threading.Lock()


class Stale(Exception):
    """A change was made from a lesson revision that is no longer current: read again."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def _min() -> int:
    return get_settings().lessons_min_occasions


# --------------------------------------------------------------------------- #
# What the construction call sees
# --------------------------------------------------------------------------- #


def _counts(db: Session, lesson_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    out: dict[uuid.UUID, dict[str, Any]] = {i: {"support": set(), "counter": set(), "occasions": set(),
                                                "assessments": {}} for i in lesson_ids}
    if not lesson_ids:
        return out
    for e in db.scalars(select(MemoryLessonEvidence).where(MemoryLessonEvidence.lesson_id.in_(lesson_ids))):
        o = out[e.lesson_id]
        o["occasions"].add(e.occasion)
        if e.current:
            o[e.role].add(e.occasion)
        if e.assessment_id is not None:
            o["assessments"][e.assessment_id] = max(o["assessments"].get(e.assessment_id, 0),
                                                    e.assessment_revision or 0)
    return out


def shown_for(db: Session, user_id: uuid.UUID, *, assessment_id: uuid.UUID,
              assessment_revision: int) -> tuple[dict[str, L.Shown], dict[str, L.Shown]]:
    """The lessons (L refs) and recently withdrawn ones (W refs) the construction call reads."""
    live = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.status != "withdrawn")
                           .order_by(MemoryLesson.updated_at.desc()).limit(SHOWN_MAX)))
    gone = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.status == "withdrawn")
                           .order_by(MemoryLesson.withdrawn_at.desc()).limit(WITHDRAWN_SHOWN)))
    counts = _counts(db, [r.id for r in live + gone])

    def shown(ref: str, r: MemoryLesson) -> L.Shown:
        c = counts[r.id]
        earlier = c["assessments"].get(assessment_id)
        return L.Shown(ref, str(r.id), r.revision, r.kind, r.scope, r.status, r.statement, r.applies_when,
                       tuple(r.exceptions or ()), tuple(r.user_words or ()), tuple(sorted(c["support"])),
                       tuple(sorted(c["counter"])), tuple(sorted(c["occasions"])),
                       earlier if earlier is not None and earlier < assessment_revision else None,
                       _aware(r.withdrawn_occasion_at), r.status_reason)

    lessons = {f"L{i}": shown(f"L{i}", r) for i, r in enumerate(reversed(live), 1)}
    withdrawn = {f"W{i}": shown(f"W{i}", r) for i, r in enumerate(gone, 1)}
    return lessons, withdrawn


# --------------------------------------------------------------------------- #
# Applying checked changes
# --------------------------------------------------------------------------- #


@dataclass
class Source:
    """Where a set of changes comes from: one assessment revision (an occasion) read by one job."""

    occasion: str
    occasion_at: datetime
    assessment_id: uuid.UUID | None
    assessment_revision: int | None
    job_id: uuid.UUID | None = None


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
        if row.revision != c.expected_revision:
            raise Stale(f"lesson {row.id} is at revision {row.revision}, the change was made from "
                        f"{c.expected_revision}")
        locked[c.lesson_id] = row
    embed = embed or _embed
    for c in changes:
        if c.op == "create":
            row = _create(db, user_id, c, source, embed)
            out.changed.append({"lesson": str(row.id), "op": "create", "revision": 1, "status": row.status,
                                "note": c.why})
            for other in c.conflicts_with:
                _conflict(db, user_id, row, other)
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
    db.flush()
    _log({"op": "apply", "user": str(user_id), "occasion": source.occasion, "changed": len(out.changed),
          "skipped": len(out.skipped)})
    return out


def _create(db: Session, user_id: uuid.UUID, c: L.Change, source: Source, embed) -> MemoryLesson:
    status = L.status_for(c.kind, c.scope, 1, _min())
    row = MemoryLesson(id=uuid.uuid4(), user_id=user_id, kind=c.kind,
                       origin="stated" if c.kind == "preference" else "inferred", scope=c.scope, status=status,
                       statement=c.statement, applies_when=c.applies_when, exceptions=list(c.exceptions),
                       user_words=list(c.user_words), revision=1, conflicts_with=[],
                       status_reason=_status_reason(c.kind, status, 1), embedding=embed(_text(c)))
    db.add(row)
    db.flush()
    _evidence(db, row, "support", c, source)
    _revision(db, row, "create", c.why or "first reading", source)
    return row


def _change(db: Session, row: MemoryLesson, c: L.Change, source: Source, embed) -> str:
    if c.op in ("support", "counter"):
        added = _evidence(db, row, c.op, c, source)
        moved = _restatus(db, row, c.op, c.why, source)
        return ("status now " + row.status if moved else "evidence added") if added else \
            "this occasion was already counted"
    if c.op in ("narrow", "revise"):
        _evidence(db, row, "counter" if c.op == "narrow" else "support", c, source)
        row.scope, row.statement, row.applies_when = c.scope, c.statement, c.applies_when
        row.exceptions, row.user_words = list(c.exceptions), list(c.user_words)
        row.embedding = embed(_text(c))
        support = _occasions(db, row.id, "support")
        row.status = L.status_for(row.kind, row.scope, support, _min())
        row.status_reason = _status_reason(row.kind, row.status, support)
        row.revision += 1
        _revision(db, row, c.op, c.why or c.op, source)
        return f"{c.op}d"
    # withdraw
    _evidence(db, row, "counter", c, source)
    row.status, row.withdrawn_at, row.withdrawn_occasion_at = "withdrawn", _now(), source.occasion_at
    row.status_reason = (f'the user said: "{c.quote}"' if c.quote else
                         "counterevidence from several occasions") + (f" ({c.why})" if c.why else "")
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
    return db.scalar(select(func.count(func.distinct(MemoryLessonEvidence.occasion))).where(
        MemoryLessonEvidence.lesson_id == lesson_id, MemoryLessonEvidence.role == role,
        MemoryLessonEvidence.current.is_(True))) or 0


def _restatus(db: Session, row: MemoryLesson, op: str, why: str, source: Source | None) -> bool:
    """A strategy's status follows its current support; a change of status is a revision (it changes whether the
    lesson is supplied)."""
    support = _occasions(db, row.id, "support")
    status = L.status_for(row.kind, row.scope, support, _min())
    reason = _status_reason(row.kind, status, support)
    if status == row.status:
        row.status_reason = reason
        return False
    row.status, row.status_reason = status, reason
    row.revision += 1
    _revision(db, row, op, why or f"status {status}", source)
    return True


def _status_reason(kind: str, status: str, support: int) -> str | None:
    if status == "hypothesis":
        return f"an inference resting on {support} occasion(s); accepted from {_min()}"
    if status == "episode_only":
        return "about one past episode, not a standing preference"
    return None


def _revision(db: Session, row: MemoryLesson, op: str, reason: str, source: Source | None) -> None:
    db.add(MemoryLessonRevision(
        lesson_id=row.id, user_id=row.user_id, revision=row.revision, op=op, kind=row.kind, scope=row.scope,
        status=row.status, statement=row.statement, applies_when=row.applies_when,
        exceptions=list(row.exceptions or []), user_words=list(row.user_words or []), reason=reason[:1000],
        assessment_id=source.assessment_id if source else None,
        assessment_revision=source.assessment_revision if source else None,
        job_id=source.job_id if source else None))
    db.flush()


def _conflict(db: Session, user_id: uuid.UUID, row: MemoryLesson, other_id: str) -> None:
    other = db.scalar(select(MemoryLesson).where(MemoryLesson.id == uuid.UUID(other_id),
                                                 MemoryLesson.user_id == user_id).with_for_update())
    if other is None or other.id == row.id:
        return
    row.conflicts_with = sorted(set(row.conflicts_with or []) | {str(other.id)})
    other.conflicts_with = sorted(set(other.conflicts_with or []) | {str(row.id)})


def _text(c: L.Change | MemoryLesson) -> str:
    return f"{c.statement} Applies: {c.applies_when}. " + " ".join(f"Except: {e}." for e in c.exceptions)


def _embed(text: str) -> list[float] | None:
    from app.services import recall

    try:
        return recall.embed(text)
    except Exception:  # noqa: BLE001 - lessons rank lexically without one
        return None


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
                 embed=None) -> Supply:
    """A bounded selection of accepted lessons for one decision. All of them while they fit; past the budget, the
    most relevant (meaning when embeddings are available, else shared words), with a conflict's counterpart kept
    beside it. Whole lessons only: nothing is cut out of one."""
    budget = get_settings().lessons_supplied_max if budget is None else budget
    rows = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user_id,
                                                      MemoryLesson.status == "accepted")
                           .order_by(MemoryLesson.created_at)))
    if not rows:
        return Supply()
    counts = _counts(db, [r.id for r in rows])
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
    views = []
    for r in chosen:
        conflicts = tuple(refs[uuid.UUID(o)] for o in r.conflicts_with or [] if uuid.UUID(o) in refs)
        views.append(L.LessonView(refs[r.id], str(r.id), r.revision, r.kind, r.scope, r.statement, r.applies_when,
                                  tuple(r.exceptions or ()), tuple(r.user_words or ()),
                                  max(1, len(counts[r.id]["support"])), conflicts))
    return Supply(tuple(views), tuple((str(r.id), r.revision, why) for r, why in left), len(rows), ranked_by)


def offered(user_id: uuid.UUID, query: str) -> Supply:
    """``for_decision`` in its own session, never raising (a failure is an empty supply that says so)."""
    from app.db.session import SessionLocal

    if not get_settings().lessons_supply_enabled:
        return Supply(ranked_by="supply_off")
    try:
        with SessionLocal() as db:
            supply = for_decision(db, user_id, query)
            db.rollback()
            return supply
    except Exception as exc:  # noqa: BLE001 - no lessons this decision, said as such
        log.exception("memory_lessons: retrieval failed")
        return Supply(error=f"{type(exc).__name__}"[:100])


# --------------------------------------------------------------------------- #
# What each decision received, and what it said about it
# --------------------------------------------------------------------------- #


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
        return len(rows)
    except Exception:  # noqa: BLE001 - a lost use record is logged, never fails the decision
        log.exception("memory_lessons: recording lesson uses failed")
        return 0


EXPLAIN = {
    "not_received": "never reached it: {reason}. Look at memory, retrieval or delivery to the decision, not at "
                    "cognition.",
    "no_account": "it reached the decision, which says nothing about how it weighed it (overlooked or misunderstood): "
                  "look at cognition, including the model.",
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
            "conflicts_with": list(r.conflicts_with or []), "support": len(counts["support"]),
            "counter": len(counts["counter"]),
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
    return {**_summary(row, _counts(db, [row.id])[row.id]),
            "revisions": [{"revision": v.revision, "op": v.op, "status": v.status, "scope": v.scope,
                           "statement": v.statement, "applies_when": v.applies_when, "exceptions": v.exceptions,
                           "user_words": v.user_words, "reason": v.reason,
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


__all__ = ["Stale", "Source", "Applied", "Supply", "apply", "shown_for", "reconsider", "for_decision", "offered",
           "record_uses", "explain", "uses_for_attempt", "listing", "detail", "drop_for", "lock"]
