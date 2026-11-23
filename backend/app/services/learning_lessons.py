"""Lessons, learning's side: what a supported assessment should change in her later decisions
(docs/learning-system/03-immediate-adaptation.md; implementation: docs/learning-system/03-implementation.md).

Learning reasons about what should persist; memory (``services/memory_lessons.py``) writes it. Learning never
speaks or acts, and an assessment doesn't become a lesson because it ran.

1. **Queuing** (``on_revision``, in the assessment revision's transaction): a revision is read for lessons when it
   has something a lesson can rest on (a stated preference or meaning, a cause supported on its component's own
   record, a supported dimension, or any finding regardless of its status) or
   when a lesson already rests on this assessment. A stretch the reviewer left is never read for lessons unless a
   lesson rests on an earlier revision of it. One job per (assessment, revision).
2. **Reading** (``process``): the revision's checked conclusions, the conversation it read (as heard, forgotten
   parts withheld, the user's notes on it), and the lessons memory holds for the user. One structured call
   (``app/ai/learning/lessons.py``) proposes changes; code checks them (``lessons.check``).
3. **Handing over.** Memory applies the checked changes under the user's lesson lock. Stale readings are never
   applied: a job whose assessment was revised meanwhile ends ``superseded`` (the newer revision has its own job),
   and a change made from a lesson revision that moved meanwhile makes the job read again. Each lesson touched is
   noted as a use of the assessment (``learning_assessment.note_use``), so a later revision that changes the
   conclusions tells memory to stop counting what it gave (``memory_lessons.reconsider``).

A ``phone`` assessment (affinity B2) is read the same way, against that person's lessons only, and what it creates
is bound to the person and the exchange's audience and stays shadow (``memory_lessons.PhoneScope``).
"""
from __future__ import annotations

import hashlib
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
    """Offer every finding to the builder, which judges what to retain and with what uncertainty.

    A reviewer's uncertainty is not a reason for code to discard a possible learning product.
    Never override the reviewer's explicit decision to leave a stretch.
    """
    if (result.get("review") or {}).get("decision") == "leave":
        return False
    if result.get("findings"):
        return True
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

    result = revision.result or {}
    plan = LA.Plan.from_dict((revision.rounds or [{}])[-1].get("plan"))
    plan.inputs = False  # learner inputs aren't evidence for a lesson
    plan.requests = ()  # nor are memory's answers: the conclusions carry what they supported
    ev = LA.gather(db, row, plan).ev
    b = L.Bundle(assessment_id=str(row.id), assessment_revision=revision.revision, occasion=row.trigger_key,
                 occasion_at=_aware(row.anchor_at), trigger=ev.trigger or row.trigger_kind,
                 explanation=revision.explanation or "", user_name=ev.user_name)
    for item in ev.items.values():
        if item.kind in ("user", "her"):
            b.lines[item.ref] = L.Line(item.ref, "user" if item.kind == "user" else "columbina", item.text,
                                       item.words or "", item.roots)
        elif item.kind == "game":
            # What the game recorded is evidence for a lesson as much as what was said (citable, its own roots).
            b.lines[item.ref] = L.Line(item.ref, "game", f"{item.ref} [game] {item.text}", item.text, item.roots)
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
    related: dict[str, str] = {}  # memory target ("fact:<uuid>") -> its M ref
    for e in result.get("findings") or []:
        parts = []
        for r in e.get("relates_to") or []:
            if r.get("target"):
                m = _related(db, row.user_id, b, related, r)
                if m is not None:
                    parts.append(f"{r['relation'].replace('_', ' ')} {m}")
            else:
                parts.append(f"{r['relation'].replace('_', ' ')} an existing lesson (id {r['id']}, revision {r['revision']})")
        relates = "; ".join(parts)
        add("finding", e["status"], f"finding ({e.get('form') or 'understanding'}): {e['statement']}"
            + (f"; applies: {e['applies_when']}" if e.get("applies_when") else "")
            + (f"; other explanations: {'; '.join(e['alternatives'])}" if e.get("alternatives") else "")
            + "".join(f"; {line}" for line in L.detail_lines(e.get("details")))
            + (f"; {relates}" if relates else ""), e)
    roots = sorted({root for key in ("targets", "causes", "user_stated", "dimensions", "findings")
                    for e in result.get(key) or [] for root in e.get("roots") or []})
    query = " ".join([revision.explanation or ""] + [e.get("statement", "") for e in result.get("findings") or []])
    b.lessons, b.withdrawn = memory_lessons.shown_for(db, row.user_id, assessment_id=row.id,
                                                      assessment_revision=revision.revision, roots=roots, query=query,
                                                      phone_person=row.phone_person_id)
    return b


def _related(db: Session, user_id: uuid.UUID, b: L.Bundle, seen: dict[str, str], r: dict) -> str | None:
    """A memory record a finding relates to, shown to the builder as an M ref (memory's record as it is now, to
    link to; never evidence). Only facts and her working items: what a lesson can rest on."""
    from app.services import memory_lessons

    kind, _, key = str(r.get("target") or "").partition(":")
    if kind not in ("fact", "item") or not key:
        return None
    target = f"{kind}:{key}"
    if target in seen:
        return seen[target]
    now = memory_lessons.related_target(db, user_id, kind, key)
    if now is None:
        return None
    ref = f"M{len(b.related) + 1}"
    b.related[ref] = L.Related(ref, kind, key, now.revision, now.status, now.text, now.what,
                               r.get("relation") or "related", now.carryable)
    seen[target] = ref
    return ref


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


def run_once(builder: Callable[[str], L.Answer] | None = None, *, embed=None,
             reconsiderer: Callable[[str], L.Answer] | None = None) -> bool:
    """The learning-lessons worker: at most one due job, after queuing reconsiderations owed to lessons whose
    premises changed (every ``SWEEP_SECONDS``)."""
    from app.db.session import SessionLocal

    global _last_sweep
    if not get_settings().learning_lessons_enabled:
        return False
    if time.monotonic() - _last_sweep >= SWEEP_SECONDS:
        _last_sweep = time.monotonic()
        try:
            with SessionLocal() as db:
                if sweep_premises(db):
                    db.commit()
        except Exception:  # noqa: BLE001 - a failed sweep is tried again next time; nothing is lost
            log.exception("learning lessons: the premise sweep failed")
    with SessionLocal() as db:
        claimed = claim(db)
    if claimed is None:
        return False
    with SessionLocal() as db:
        process(db, *claimed, builder=builder, embed=embed, reconsiderer=reconsiderer)
    return True


def _mine(db: Session, job_id: uuid.UUID, attempts: int, *, lock: bool = False) -> LearningLessonJob | None:
    query = select(LearningLessonJob).where(LearningLessonJob.id == job_id)
    job = db.scalar(query.with_for_update() if lock else query)
    if job is None or job.state != "running" or job.attempts != attempts:
        return None
    return job


def process(db: Session, job_id: uuid.UUID, attempts: int, *, builder: Callable[[str], L.Answer] | None = None,
            embed=None, reconsiderer: Callable[[str], L.Answer] | None = None) -> str:
    """Read one claimed job (an assessment revision, or a lesson to reconsider) and hand the checked changes to
    memory. Returns what happened."""
    from app.services import learning_assessment as LA
    from app.services import memory_lessons

    job = db.get(LearningLessonJob, job_id)
    if job is not None and job.kind == "reconsider":
        db.rollback()
        return process_reconsider(db, job_id, attempts, reconsiderer=reconsiderer, embed=embed)
    db.rollback()
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
        phone = None
        if row.trigger_kind == "phone" or row.phone_person_id is not None:
            from app.services import phone_relationships
            phone = phone_relationships.lesson_scope(db, row)
            if phone is None:
                return _finish(db, job_id, attempts, "gone", {"why": "the phone exchange it read is gone"})
        assessment_id, assessment_revision = row.id, revision.revision
        db.rollback()  # nothing held while the model reads
        text = L.render(bundle)
        answer = build(text)
        if answer.raw is None:
            return _fail(db, job_id, attempts, answer.error or "no answer")
        checked = L.check(answer.raw, bundle)
        prepared = memory_lessons.prepare_for_search(checked.changes, embed=embed)  # before any lock
        # Write: the assessment row locked (its revision can't move under us), then memory's own lock.
        job = _mine(db, job_id, attempts, lock=True)
        if job is None:
            db.rollback()
            return "gone"
        row = db.scalar(select(LearningAssessment).where(LearningAssessment.id == assessment_id).with_for_update())
        if row is None or row.revision != assessment_revision:
            db.rollback()
            continue  # revised or gone while it read: the top of the loop settles it
        source = memory_lessons.Source(occasion, occasion_at, assessment_id, assessment_revision, job_id, phone=phone)
        try:
            applied = memory_lessons.apply(db, user_id, checked.changes, source,
                                           embed=prepared.embed)
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


# --------------------------------------------------------------------------- #
# Reconsideration (post-audit 4): a lesson read again on the evidence it already rests on
# --------------------------------------------------------------------------- #

SWEEP_SECONDS = 30.0
_last_sweep = 0.0
EVIDENCE_SHOWN = 12  # records of a lesson's evidence read again (its most recent occasions)


def _key(*parts: str) -> str:
    return ":".join(parts)[:200]


def _queue(db: Session, row, *, key: str, prompted_by: str, reason: str, detail: dict) -> bool:
    made = db.execute(insert(LearningLessonJob).values(
        id=uuid.uuid5(row.id, key), user_id=row.user_id, kind="reconsider", lesson_id=row.id, job_key=key,
        input={"prompted_by": prompted_by, "revision": row.revision, **detail}, reason=reason[:300],
        next_attempt_at=_now(),
    ).on_conflict_do_nothing().returning(LearningLessonJob.id)).scalar()
    return made is not None


def sweep_premises(db: Session, *, limit: int = 50) -> int:
    """Queue a reconsideration for every lesson whose basis changed since it was last assessed, once per state of
    its premises (in the caller's transaction). The lesson is unsettled for every consumer from the moment its
    premise changed, whether or not this has run: queuing is not what makes the change visible."""
    from app.services import memory_lessons

    if not get_settings().learning_lessons_enabled:
        return 0
    n = 0
    for row, changed in memory_lessons.changed_lessons(db, limit=limit):
        state = sorted((p.target_kind, p.target_id, str(p.now[0] if p.now else None),
                        str(p.now[1] if p.now else "gone")) for p in changed)
        digest = hashlib.sha256(json.dumps(state).encode()).hexdigest()[:16]
        n += _queue(db, row, key=_key("premise", str(row.id), str(row.revision), digest), prompted_by="premise",
                    reason="something it rests on changed",
                    detail={"premises": [p.to_dict() for p in changed]})
    return n


def critique(db: Session, user_id: uuid.UUID, lesson_id: uuid.UUID, text: str, *, source: str) -> bool:
    """Her mind questioned the reasoning of a lesson she was shown: queue reading it again on the same evidence
    (in the caller's transaction). Once per lesson and wording: the same critique again adds nothing.
    Her thought is never evidence; it is what prompts the reading. Returns whether it was queued."""
    from app.db.models import MemoryLesson

    words = " ".join((text or "").split())[:600]
    row = db.get(MemoryLesson, lesson_id)
    if not words or row is None or row.user_id != user_id or row.status == "withdrawn":
        return False
    digest = hashlib.sha256(A._norm(words).encode()).hexdigest()[:16]
    # Keyed by the lesson and the critique's words, not its revision: the same critique again, after it was read
    # (and perhaps changed the lesson), adds nothing.
    return _queue(db, row, key=_key("critique", str(row.id), digest), prompted_by="critique",
                  reason="her mind questioned its reasoning", detail={"critique": words, "source": source[:120]})


def _evidence_lines(db: Session, user_id: uuid.UUID, lesson_id: uuid.UUID) -> dict[str, L.Examined]:
    """What the lesson rests on or was found against, read again from its owners (forgotten parts withheld)."""
    from app.db.models import ChatMessage, Conversation, GameExperience, MemoryLessonEvidence
    from app.services import learning_assessment as LA
    from app.services import memory_forget

    rows = list(db.scalars(select(MemoryLessonEvidence).where(MemoryLessonEvidence.lesson_id == lesson_id)
                           .order_by(MemoryLessonEvidence.occasion_at.desc()).limit(EVIDENCE_SHOWN)))
    out: dict[str, L.Examined] = {}
    for e in reversed(rows):
        role = e.role + ("" if e.current else ", not counting while its assessment is read again")
        for ref in e.refs or []:
            kind, _, key = ref.partition(":")
            text = None
            if kind == "message":
                try:
                    m = db.scalar(select(ChatMessage).join(Conversation, Conversation.id == ChatMessage.conversation_id)
                                  .where(ChatMessage.id == uuid.UUID(key), Conversation.user_id == user_id))
                except ValueError:
                    m = None
                if m is not None:
                    cuts = memory_forget.memory_cuts(db, user_id, [m.id])
                    words = memory_forget.memory_view(m.content, m.heard_chars, m.role, cuts.get(m.id, False))
                    who = "her" if m.role == "assistant" else "user"
                    text = f'[{who}, {LA._hm(m.created_at)}] "{LA.FORGOTTEN if words is None else words}"'
            elif kind == "experience" and key.isdigit():
                g = db.get(GameExperience, int(key))
                if g is not None and g.user_id == user_id:
                    text = f"[game, {LA._hm(g.occurred_at)}] {g.kind}: {LA._game_excerpt(g.payload, 600)}"
            if text is None:
                continue
            if any(x.roots == (ref,) for x in out.values()):
                continue
            n = f"e{len(out) + 1}"
            out[n] = L.Examined(n, role, text + (f" (why it counted: {e.why})" if e.why else ""), (ref,))
    return out


def reconsider_bundle(db: Session, job: LearningLessonJob):
    """(the reconsideration's view, its premises by P ref) for one job, gathered as of now; None when the job's user
    holds no such lesson. Memory says what the lesson is; this makes the reviewer's input of it."""
    from app.services import memory_lessons

    held = memory_lessons.lesson_for_review(db, job.user_id, job.lesson_id)
    if held is None:
        return None
    shown = held.view
    lesson_id = uuid.UUID(shown.id)
    by_ref = {f"P{i}": p for i, p in enumerate(held.premises, 1)}
    history = [f"revision {v.revision} {v.op}" + (f" ({v.prompted_by})" if v.prompted_by else "")
               + f': "{v.statement}" ({v.status}, applies: {v.applies_when}) because: {v.reason}'
               for v in held.revisions]
    earlier = []
    for other in db.scalars(select(LearningLessonJob).where(
            LearningLessonJob.lesson_id == lesson_id, LearningLessonJob.kind == "reconsider",
            LearningLessonJob.state == "done", LearningLessonJob.id != job.id)):
        if (other.input or {}).get("revision") == shown.revision and other.outcome:
            said = (other.input or {}).get("critique") or "a premise changed"
            earlier.append(f'{said[:200]} -> {other.outcome}: {((other.result or {}).get("why") or "")[:200]}')
    r = L.Reconsider(shown, (job.input or {}).get("prompted_by") or "premise", (job.input or {}).get("critique"),
                     {ref: L.Premise(ref, p.relation, p.what, p.then_text, p.then_status,
                                     p.now[2] if p.now else None, p.now[1] if p.now else None, p.changed, p.note)
                      for ref, p in by_ref.items()},
                     _evidence_lines(db, job.user_id, lesson_id), history, earlier)
    return r, by_ref


def _reconsiderer() -> Callable[[str], L.Answer]:
    s = get_settings()
    return L.Reconsiderer(s.learning_lessons_model, api_key=s.openai_api_key,
                          reasoning_effort=s.learning_lessons_reasoning_effort,
                          timeout=s.learning_lessons_timeout_seconds)


def process_reconsider(db: Session, job_id: uuid.UUID, attempts: int, *,
                       reconsiderer: Callable[[str], L.Answer] | None = None, embed=None) -> str:
    """Read one lesson again on the evidence it rests on and hand the checked decision to memory."""
    from app.db.models import MemoryLesson
    from app.services import memory_lessons

    build = reconsiderer or _reconsiderer()
    name = getattr(build, "name", None) or f"{L.RECONSIDER_VERSION}:scripted"
    for _ in range(STALE_READS):
        job = _mine(db, job_id, attempts)
        if job is None:
            db.rollback()
            return "gone"
        row = db.get(MemoryLesson, job.lesson_id)
        if row is None or row.user_id != job.user_id:  # another user's lesson is gone to this job: nothing of it shown
            return _finish(db, job_id, attempts, "gone", {"why": "the lesson is gone"})
        if row.status == "withdrawn":
            return _finish(db, job_id, attempts, "superseded", {"why": "the lesson was withdrawn meanwhile"})
        prompted = (job.input or {}).get("prompted_by")
        if prompted == "critique" and row.revision != (job.input or {}).get("revision"):
            return _finish(db, job_id, attempts, "superseded", {
                "why": f"the lesson was revised to revision {row.revision} since the critique; it was about "
                       f"revision {(job.input or {}).get('revision')}"})
        try:
            bundled = reconsider_bundle(db, job)
        except Exception as exc:  # noqa: BLE001 - a failed gathering retries; never a change
            log.exception("learning lessons %s: gathering a reconsideration failed", job_id)
            db.rollback()
            return _fail(db, job_id, attempts, f"{type(exc).__name__}: {exc}"[:500])
        if bundled is None:
            return _finish(db, job_id, attempts, "gone", {"why": "the lesson is gone"})
        r, by_ref = bundled
        if prompted == "premise" and not any(p.changed for p in by_ref.values()):
            return _finish(db, job_id, attempts, "settled", {"why": "its premises were reassessed meanwhile"})
        user_id, lesson_id = row.user_id, row.id
        db.rollback()  # nothing held while the model reads
        text = L.render_reconsider(r)
        answer = build(text)
        if answer.raw is None:
            return _fail(db, job_id, attempts, answer.error or "no answer")
        checked = L.check_reconsider(answer.raw, r)
        job = _mine(db, job_id, attempts, lock=True)
        if job is None:
            db.rollback()
            return "gone"
        if not checked.changes:
            outcome, applied = "no_change", None
        else:
            try:
                applied = memory_lessons.reconsidered(db, user_id, checked.changes[0], job_id=job_id,
                                                      premises_read=by_ref, embed=embed)
            except memory_lessons.Stale as exc:
                db.rollback()
                log.info("learning lessons %s: the lesson moved while it was reconsidered (%s); reading again",
                         job_id, exc)
                continue
            outcome = checked.changes[0].op if applied.get("applied") else "no_change"
        job.state, job.outcome, job.lease_expires_at, job.last_error = "done", outcome, None, None
        job.result = {"reconsiderer": name, "lesson": str(lesson_id), "decision": checked.changes[0].op if checked.changes else None,
                      "why": checked.changes[0].why if checked.changes else None, "applied": applied,
                      "examined": [e.roots[0] for e in r.evidence.values()],
                      "premises": {ref: p.to_dict() for ref, p in by_ref.items()},
                      "dropped": checked.dropped, "explanation": checked.explanation,
                      "digest": hashlib.sha256(text.encode()).hexdigest()[:32], "latency_ms": answer.latency_ms,
                      "usage": {"input_tokens": answer.input_tokens, "output_tokens": answer.output_tokens}}
        db.commit()
        _log({"op": "reconsidered", "job": str(job_id), "lesson": str(lesson_id), "outcome": outcome,
              "prompted_by": prompted})
        return outcome
    return _fail(db, job_id, attempts, "the lesson kept changing while it was reconsidered; read again later")


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
    return [{"id": str(j.id), "kind": j.kind,
             "assessment_id": str(j.assessment_id) if j.assessment_id else None,
             "assessment_revision": j.assessment_revision, "lesson_id": str(j.lesson_id) if j.lesson_id else None,
             "input": j.input, "reason": j.reason, "state": j.state, "outcome": j.outcome, "attempts": j.attempts,
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
           "CONSUMER", "sweep_premises", "critique", "process_reconsider", "reconsider_bundle"]
