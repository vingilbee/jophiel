"""Lessons, learning's side: what a supported assessment should change in her later decisions
(docs/learning-system/03-immediate-adaptation.md; implementation: docs/learning-system/03-implementation.md).

Learning reasons about what should persist; memory (``services/memory_lessons.py``) writes it. Learning never
speaks or acts, and an assessment doesn't become a lesson because it ran.

1. **Queuing** (``on_revision``, in the assessment revision's transaction): a revision is read for lessons when it
   has something a lesson can rest on (a stated preference or meaning, a cause supported on its component's own
   record) or when a lesson already rests on this assessment. One job per (assessment, revision).
2. **Reading** (``process``): the revision's checked conclusions, the conversation it read (as heard, forgotten
   parts withheld, the user's notes on it), and the lessons memory holds for the user. One structured call
   (``app/ai/learning/lessons.py``) proposes changes; code checks them (``lessons.check``).
3. **Handing over.** Memory applies the checked changes under the user's lesson lock. Stale readings are never
   applied: a job whose assessment was revised meanwhile ends ``superseded`` (the newer revision has its own job),
   and a change made from a lesson revision that moved meanwhile makes the job read again. Each lesson touched is
   noted as a use of the assessment (``learning_assessment.note_use``), so a later revision that changes the
   conclusions tells memory to stop counting what it gave (``memory_lessons.reconsider``).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.learning import assessment as A
from app.ai.learning import lessons as L
from app.core.config import get_settings
from app.db.models import (
    LearningAssessment, LearningAssessmentRevision, LearningLessonJob, MemoryLessonEvidence,
)

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "lessons"
LEASE = timedelta(minutes=10)
MAX_BACKOFF = timedelta(hours=1)
STALE_READS = 3  # a reading made stale by concurrent lesson changes is read again, this many times per run
_log_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Queuing
# --------------------------------------------------------------------------- #


def worth_reading(result: dict, *, game: bool = False) -> bool:
    """A revision a lesson could rest on: the user's own stated meaning or preference, or a cause supported on its
    component's own record (code-checked in step 2)."""
    return bool(result.get("user_stated")) or any(
        c.get("status") == "supported" and c.get("component") != "unclear" for c in result.get("causes") or []) or any(
        d.get("status") == "supported" and d.get("value") != "unknown"
        and d.get("dimension") in ("cost", "goal_progress", "knowledge_gain")
        and (game or any(r.startswith(("experience:", "trajectory:")) for r in d.get("roots") or []))
        for d in result.get("dimensions") or [])


def on_revision(db: Session, row: LearningAssessment, revision: int, result: dict) -> bool:
    """Queue reading this revision for lessons (in the caller's transaction). Returns whether a job was queued."""
    if not get_settings().learning_lessons_enabled:
        return False
    tied = db.scalar(select(exists().where(MemoryLessonEvidence.user_id == row.user_id,
                                           MemoryLessonEvidence.assessment_id == row.id)))
    worth = worth_reading(result, game=row.game_session_id is not None)
    if not (worth or tied):
        return False
    reason = ("a lesson rests on an earlier revision of it" if tied and not worth else
              "it has conclusions a lesson could rest on" + (", and a lesson rests on it" if tied else ""))
    made = db.execute(insert(LearningLessonJob).values(
        id=uuid.uuid5(row.id, f"lessons:{revision}"), user_id=row.user_id, assessment_id=row.id,
        assessment_revision=revision, reason=reason, next_attempt_at=_now(),
    ).on_conflict_do_nothing(constraint="uq_learning_lesson_jobs").returning(LearningLessonJob.id)).scalar()
    return made is not None


def reconsider(db: Session, use, row: LearningAssessment, revision: int) -> None:
    """The assessment was revised and no longer supports what a lesson took from it (``learning_assessment`` calls
    this through its RECONSIDER registry): memory stops counting the earlier reading; the new revision's job reads
    the lessons again."""
    from app.services import memory_lessons

    memory_lessons.reconsider(db, row.user_id, row.id, revision)


# --------------------------------------------------------------------------- #
# Reading one revision
# --------------------------------------------------------------------------- #


def bundle_for(db: Session, row: LearningAssessment, revision: LearningAssessmentRevision) -> L.Bundle:
    """What the construction call reads, gathered fresh (the same evidence the revision read, as of now)."""
    from app.services import learning_assessment as LA
    from app.services import memory_lessons

    s = get_settings()
    result = revision.result or {}
    plan_dict = (revision.rounds or [{}])[-1].get("plan") or {}
    plan = LA.Plan(**{k: plan_dict[k] for k in ("earlier", "later", "inputs", "game") if k in plan_dict})
    plan.inputs = False  # learner inputs aren't evidence for a lesson
    ev = LA.gather(db, row, plan).ev
    b = L.Bundle(assessment_id=str(row.id), assessment_revision=revision.revision, occasion=row.trigger_key,
                 occasion_at=_aware(row.anchor_at), trigger=ev.trigger or row.trigger_kind,
                 explanation=revision.explanation or "", min_occasions=s.lessons_min_occasions)
    for item in ev.items.values():
        if item.kind in ("user", "her"):
            b.lines[item.ref] = L.Line(item.ref, "user" if item.kind == "user" else "columbina", item.text,
                                       item.words or "", item.roots)
        elif item.kind == "note":
            b.lines[item.ref] = L.Line(item.ref, "user", f'{item.ref} [the user\'s note on this assessment] '
                                                         f'"{item.words}"', item.words or "", item.roots)
    labels = result.get("target_labels") or {}

    def where(ref: str) -> str:
        return f"{ref} ({labels[ref]})" if ref in labels else ref

    def cited(entry: dict) -> str:
        return f" [cites {', '.join(entry.get('cites') or [])}]" if entry.get("cites") else ""

    n = 0

    def add(kind: str, status: str, text: str, entry: dict) -> None:
        nonlocal n
        n += 1
        b.conclusions[f"a{n}"] = L.Conclusion(f"a{n}", kind, status, text + cited(entry),
                                              tuple(entry.get("roots") or ()))

    for e in result.get("user_stated") or []:
        add("stated", e.get("status", "supported"),
            f'the user stated ({e["kind"].replace("_", " ")}): "{e["quote"]}"', e)
    for e in result.get("targets") or []:
        add("target", e["status"], f"what it was about: {where(e['ref'])}"
            + (f" with {', '.join(e['also'])}" if e.get("also") else "") + f": {e.get('why', '')}", e)
    for e in result.get("causes") or []:
        add("cause", e["status"], f"cause in {where(e['target'])}: "
            f"{A.COMPONENT_WORDS.get(e['component'], e['component'])}: {e.get('why', '')}", e)
    for e in result.get("dimensions") or []:
        if e.get("value") != "unknown":
            add("dimension", e["status"], f"{A.DIMENSION_WORDS.get(e['dimension'], e['dimension'])} in "
                f"{where(e['target'])}: {e['value']}" + (f" ({e['note']})" if e.get("note") else ""), e)
    for e in result.get("choice_vs_outcome") or []:
        if e.get("choice") != "unclear":
            add("choice", "supported", f"her choice in {where(e['target'])} was {e['choice']} given what she had; "
                f"outcome {e['outcome']}" + (f" ({e['note']})" if e.get("note") else ""), e)
    b.lessons, b.withdrawn = memory_lessons.shown_for(db, row.user_id, assessment_id=row.id,
                                                      assessment_revision=revision.revision)
    return b


def _builder() -> Callable[[str], L.Answer]:
    s = get_settings()
    return L.Builder(s.learning_lessons_model, api_key=s.openai_api_key,
                     reasoning_effort=s.learning_lessons_reasoning_effort,
                     timeout=s.learning_lessons_timeout_seconds)


def claim(db: Session, now: datetime | None = None) -> tuple[uuid.UUID, int] | None:
    now = now or _now()
    job = db.scalar(select(LearningLessonJob).where(or_(
        (LearningLessonJob.state == "pending") & (LearningLessonJob.next_attempt_at <= now),
        (LearningLessonJob.state == "running") & (LearningLessonJob.lease_expires_at < now),
    )).order_by(LearningLessonJob.next_attempt_at).limit(1).with_for_update(skip_locked=True))
    if job is None:
        db.rollback()
        return None
    job.state, job.attempts, job.lease_expires_at = "running", job.attempts + 1, now + LEASE
    found = (job.id, job.attempts)
    db.commit()
    return found


def run_once(builder: Callable[[str], L.Answer] | None = None, *, embed=None) -> bool:
    """The learning-lessons worker: at most one due job."""
    from app.db.session import SessionLocal

    if not get_settings().learning_lessons_enabled:
        return False
    with SessionLocal() as db:
        claimed = claim(db)
    if claimed is None:
        return False
    with SessionLocal() as db:
        process(db, *claimed, builder=builder, embed=embed)
    return True


def _mine(db: Session, job_id: uuid.UUID, attempts: int, *, lock: bool = False) -> LearningLessonJob | None:
    query = select(LearningLessonJob).where(LearningLessonJob.id == job_id)
    job = db.scalar(query.with_for_update() if lock else query)
    if job is None or job.state != "running" or job.attempts != attempts:
        return None
    return job


def process(db: Session, job_id: uuid.UUID, attempts: int, *, builder: Callable[[str], L.Answer] | None = None,
            embed=None) -> str:
    """Read one claimed job's assessment revision and hand the checked changes to memory. Returns what happened."""
    from app.services import learning_assessment as LA
    from app.services import memory_lessons

    build = builder or _builder()
    name = getattr(build, "name", None) or f"{L.PROMPT_VERSION}:scripted"
    for _ in range(STALE_READS):
        job = _mine(db, job_id, attempts)
        if job is None:
            db.rollback()
            return "gone"
        row = db.get(LearningAssessment, job.assessment_id)
        if row is None:
            return _finish(db, job_id, attempts, "gone", {"why": "the assessment is gone"})
        if row.revision != job.assessment_revision:
            return _finish(db, job_id, attempts, "superseded", {
                "why": f"the assessment was revised to revision {row.revision} meanwhile; that revision is read "
                       "on its own"})
        revision = db.scalar(select(LearningAssessmentRevision).where(
            LearningAssessmentRevision.assessment_id == row.id,
            LearningAssessmentRevision.revision == job.assessment_revision))
        if revision is None:
            return _finish(db, job_id, attempts, "gone", {"why": "the revision is gone"})
        try:
            bundle = bundle_for(db, row, revision)
        except Exception as exc:  # noqa: BLE001 - a failed gathering retries; never a lesson
            log.exception("learning lessons %s: gathering failed", job_id)
            db.rollback()
            return _fail(db, job_id, attempts, f"{type(exc).__name__}: {exc}"[:500])
        user_id, occasion, occasion_at = row.user_id, row.trigger_key, _aware(row.anchor_at)
        assessment_id, assessment_revision = row.id, revision.revision
        db.rollback()  # nothing held while the model reads
        text = L.render(bundle)
        answer = build(text)
        if answer.raw is None:
            return _fail(db, job_id, attempts, answer.error or "no answer")
        checked = L.check(answer.raw, bundle)
        vectors = _vectors(checked.changes, embed)
        # Write: the assessment row locked (its revision can't move under us), then memory's own lock.
        job = _mine(db, job_id, attempts, lock=True)
        if job is None:
            db.rollback()
            return "gone"
        row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == assessment_id).with_for_update())
        if row is None or row.revision != assessment_revision:
            db.rollback()
            continue  # revised or gone while it read: the top of the loop settles it
        source = memory_lessons.Source(occasion, occasion_at, assessment_id, assessment_revision, job_id)
        try:
            applied = memory_lessons.apply(db, user_id, checked.changes, source,
                                           embed=lambda text: vectors.get(text))
        except memory_lessons.Stale as exc:
            db.rollback()
            log.info("learning lessons %s: stale reading (%s); reading again", job_id, exc)
            continue
        for lesson in applied.lessons:
            LA.note_use(db, row, assessment_revision, CONSUMER, f"lesson:{lesson}",
                        reason=f"lesson {lesson} changed from this revision"[:500])
        outcome = "changed" if applied.changed else "no_change"
        job.state, job.outcome, job.lease_expires_at, job.last_error = "done", outcome, None, None
        job.result = {"builder": name, "digest": L.digest(bundle), "changes": [c.to_dict() for c in checked.changes],
                      "applied": applied.changed, "skipped": applied.skipped, "dropped": checked.dropped,
                      "explanation": checked.explanation, "latency_ms": answer.latency_ms,
                      "usage": {"input_tokens": answer.input_tokens, "output_tokens": answer.output_tokens}}
        db.commit()
        _log({"op": "done", "job": str(job_id), "assessment": str(assessment_id), "revision": assessment_revision,
              "outcome": outcome, "changed": len(applied.changed), "dropped": len(checked.dropped)})
        return outcome
    return _fail(db, job_id, attempts, "lessons kept changing while it read; read again later")


def _vectors(changes: list[L.Change], embed) -> dict[str, list[float] | None]:
    """Embeddings for the texts memory will store, computed before any lock is taken."""
    from app.services import memory_lessons

    out = {}
    for c in changes:
        if c.op in ("create", "narrow", "revise"):
            text = memory_lessons._text(c)
            if text not in out:
                out[text] = (embed or memory_lessons._embed)(text)
    return out


def _finish(db: Session, job_id: uuid.UUID, attempts: int, outcome: str, result: dict) -> str:
    job = _mine(db, job_id, attempts, lock=True)
    if job is None:
        db.rollback()
        return "gone"
    job.state, job.outcome, job.result, job.lease_expires_at, job.last_error = "done", outcome, result, None, None
    db.commit()
    _log({"op": outcome, "job": str(job_id)})
    return outcome


def _fail(db: Session, job_id: uuid.UUID, attempts: int, error: str) -> str:
    job = _mine(db, job_id, attempts, lock=True)
    if job is None:
        db.rollback()
        return "gone"
    now = _now()
    job.last_error, job.lease_expires_at = error, None
    if job.attempts >= get_settings().learning_lessons_max_attempts:
        job.state = "failed"
    else:
        job.state, job.next_attempt_at = "pending", now + min(timedelta(seconds=30 * 2 ** job.attempts), MAX_BACKOFF)
    db.commit()
    _log({"op": "failed", "job": str(job_id), "state": job.state, "attempts": job.attempts, "error": error})
    return job.state


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #


def jobs(db: Session, user_id: uuid.UUID, *, limit: int = 30) -> list[dict]:
    rows = db.scalars(select(LearningLessonJob).where(LearningLessonJob.user_id == user_id)
                      .order_by(LearningLessonJob.updated_at.desc()).limit(limit))
    return [{"id": str(j.id), "assessment_id": str(j.assessment_id), "assessment_revision": j.assessment_revision,
             "reason": j.reason, "state": j.state, "outcome": j.outcome, "attempts": j.attempts,
             "last_error": j.last_error, "result": j.result, "updated_at": _aware(j.updated_at).isoformat()}
            for j in rows]


def _log(record: dict) -> None:
    path = Path(get_settings().learning_lessons_log_path)
    path = path if path.is_absolute() else BACKEND_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **record}, default=str) + "\n")
    except OSError:
        log.warning("learning lessons: could not write %s", path)


__all__ = ["on_revision", "worth_reading", "reconsider", "bundle_for", "claim", "run_once", "process", "jobs",
           "CONSUMER"]
