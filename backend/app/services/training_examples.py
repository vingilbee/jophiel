"""Training examples: learning's self-contained derivatives of captured decisions
(docs/learning-system/01-capture-and-ownership.md, "Retention, access, and training derivatives").

- **Derived automatically** while the user's learning setting allows it (``LEARNING_DERIVE_EXAMPLES``), without
  asking per example. Step 1 derives one kind only: a Student 2 decision made by the teacher, valid when made,
  becomes an unreviewed ``teacher_decision`` example (exact decision-time input -> the teacher's decision). An
  attempt missing what the example needs (no input, no output, not valid) is never derived. Labels, attribution
  and review rules are later plans' (steps 2 and 5).
- **Self-contained.** The example copies the decision-time input and target it needs; it doesn't need the raw
  capture or the source to stay. ``source_refs`` are opaque ids (``message:<uuid>``), not links: a correction can
  still find what came from a claim after the claim's source is gone.
- **Independent of source deletion.** Forgetting a message or memory, or the raw capture's retention, doesn't
  touch examples already made. Only ``delete_examples`` removes one (from future training uses and from the exports listed
  in ``EXPORTS``), leaving a content-free tombstone so the same attempt is never derived again. Influence on a
  model already trained is not undone.
- **Corrections invalidate.** ``invalidate_for_sources`` (from memory corrections) marks examples made from a
  corrected claim ineligible and ``needs_review``; they're kept for inspection.
- **Private.** Every read and write is by ``user_id``; nothing here reads across users.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from typing import Any, Iterable

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    DecisionAttempt, DecisionAttemptSource, TrainingExample, TrainingExampleDeletion, TrainingExampleSource,
)

logger = logging.getLogger(__name__)

TEACHER_DECISION = "teacher_decision"


def _path(path: str) -> str:
    from app.services.learning_capture import BACKEND
    return path if os.path.isabs(path) else os.path.join(BACKEND, path)


def exports() -> list[str]:
    """Dataset files that hold copies of examples (one JSON object per line, ``decision_id`` = the attempt):
    the live teacher capture is the only one so far."""
    return [_path(get_settings().cognition_teacher_data_path)]


# --------------------------------------------------------------------------- #
# Deriving
# --------------------------------------------------------------------------- #


def _derivable():
    """Teacher decisions, valid when made, with their input and output captured, not derived or deleted yet."""
    done = exists().where(TrainingExample.attempt_id == DecisionAttempt.id,
                          TrainingExample.label_source == TEACHER_DECISION)
    deleted = exists().where(TrainingExampleDeletion.attempt_id == DecisionAttempt.id,
                             TrainingExampleDeletion.label_source == TEACHER_DECISION)
    return (select(DecisionAttempt).where(
        DecisionAttempt.learner == "student_2", DecisionAttempt.provider == "teacher",
        DecisionAttempt.status == "ok", ~DecisionAttempt.absent.op("?")("input"),
        ~DecisionAttempt.absent.op("?")("output"), ~done, ~deleted)
        .order_by(DecisionAttempt.captured_at))


def example_from(attempt: DecisionAttempt) -> dict[str, Any] | None:
    """The example's own content, from the attempt alone (decision-time input; the teacher's output)."""
    inp, out = attempt.input or {}, attempt.output or {}
    if not inp.get("prompt") or not out.get("decision"):
        return None
    return {
        "input": {"prompt": inp["prompt"], "snapshot": inp.get("snapshot"), "rendering": inp.get("rendering")},
        "target": {"decision": out["decision"]},
        "versions": {**(attempt.versions or {}), "model": attempt.model, "provider": attempt.provider},
        "evidence": {"label": "the teacher's own decision, valid when it was made; not reviewed",
                     "trigger": attempt.trigger, "absent": list(attempt.absent or [])},
    }


def derive(db: Session, attempt: DecisionAttempt) -> TrainingExample | None:
    content = example_from(attempt)
    if content is None:
        return None
    example_id = uuid.uuid5(attempt.id, f"example:{TEACHER_DECISION}")
    inserted = db.execute(insert(TrainingExample).values(
        id=example_id, user_id=attempt.user_id, learner="student_2", attempt_id=attempt.id,
        label_source=TEACHER_DECISION, origin="real", decided_at=attempt.started_at, **content,
    ).on_conflict_do_nothing(constraint="uq_training_examples_derivation").returning(TrainingExample.id)).scalar()
    if inserted is None:
        return None
    refs = set(db.scalars(select(DecisionAttemptSource.ref).where(DecisionAttemptSource.attempt_id == attempt.id)))
    refs |= {f"{kind}:{value}" for kind, value in (("conversation", attempt.conversation_id),
                                                   ("message", attempt.source_message_id),
                                                   ("session", attempt.game_session_id)) if value}
    if refs:
        db.execute(insert(TrainingExampleSource).values(
            [{"example_id": example_id, "ref": ref[:80], "user_id": attempt.user_id} for ref in sorted(refs)])
            .on_conflict_do_nothing())
    return db.get(TrainingExample, example_id)


def derive_pending(db: Session, *, limit: int = 50) -> int:
    """One batch of derivations (the learning worker). Nothing when the user's learning setting is off."""
    if not get_settings().learning_derive_examples:
        return 0
    made = 0
    for attempt in db.scalars(_derivable().limit(limit)):
        made += derive(db, attempt) is not None
    db.commit()
    return made


# --------------------------------------------------------------------------- #
# Corrections, deletion, access
# --------------------------------------------------------------------------- #


def invalidate_for_sources(db: Session, user_id: uuid.UUID, refs: Iterable[str], *, reason: str) -> int:
    """Examples made from a corrected claim: no longer eligible until reviewed, kept for inspection. In the
    caller's transaction."""
    refs = sorted(set(refs))
    if not refs:
        return 0
    ids = select(TrainingExampleSource.example_id).where(TrainingExampleSource.user_id == user_id,
                                                         TrainingExampleSource.ref.in_(refs))
    rows = list(db.scalars(select(TrainingExample).where(TrainingExample.user_id == user_id,
                                                         TrainingExample.id.in_(ids))))
    for row in rows:
        row.eligible = False
        row.review_status = "needs_review"
        row.ineligible_reason = reason[:500]
    return len(rows)


def flag_for_review(db: Session, user_id: uuid.UUID, attempt_id: uuid.UUID, *, reason: str) -> list[uuid.UUID]:
    """An assessment found the decision behind these examples at fault (services/learning_assessment.py): they're
    ineligible until reviewed, kept for inspection. A rejected example stays rejected. In the caller's transaction.
    Returns the examples made from that attempt (flagged now or already)."""
    rows = list(db.scalars(select(TrainingExample).where(TrainingExample.user_id == user_id,
                                                         TrainingExample.attempt_id == attempt_id)))
    for row in rows:
        if row.review_status == "rejected":
            continue
        if row.review_status != "needs_review" or row.ineligible_reason != reason[:500]:
            row.eligible, row.review_status, row.ineligible_reason = False, "needs_review", reason[:500]
    return [r.id for r in rows if r.review_status != "rejected"]


def reconsider(db: Session, user_id: uuid.UUID, example_id: uuid.UUID, *, note: str) -> None:
    """The assessment that flagged an example was revised and no longer supports the flag. Review decides: the
    example stays as it is, with the note added. In the caller's transaction."""
    row = db.scalar(select(TrainingExample).where(TrainingExample.user_id == user_id,
                                                  TrainingExample.id == example_id))
    if row is not None and row.review_status == "needs_review":
        row.ineligible_reason = f"{row.ineligible_reason or ''}; {note}".strip("; ")[:2000]


def delete_examples(db: Session, user_id: uuid.UUID, ids: Iterable[uuid.UUID]) -> int:
    """Explicit training-data deletion: the examples go, a tombstone keeps only ids (so the attempt is never
    derived again and exports can exclude it), and the export files lose their copies. Commits."""
    rows = list(db.scalars(select(TrainingExample).where(TrainingExample.user_id == user_id,
                                                         TrainingExample.id.in_(list(ids)))))
    for row in rows:
        db.execute(insert(TrainingExampleDeletion).values(
            example_id=row.id, user_id=user_id, attempt_id=row.attempt_id, learner=row.learner,
            label_source=row.label_source).on_conflict_do_nothing())
        db.delete(row)
    db.commit()
    if rows:
        scrub_exports(user_id, {str(r.attempt_id) for r in rows})
    return len(rows)


def scrub_exports(user_id: uuid.UUID, attempt_ids: set[str], paths: list[str] | None = None) -> int:
    """Remove this user's lines for these attempts from each export file, under the writers' file lock."""
    removed = 0
    for path in paths or exports():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                kept = []
                for line in f:
                    try:
                        data = json.loads(line)
                    except ValueError:
                        kept.append(line)
                        continue
                    if data.get("decision_id") in attempt_ids and data.get("user_id") == str(user_id):
                        removed += 1
                    else:
                        kept.append(line)
                f.seek(0)
                f.writelines(kept)
                f.truncate()
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            logger.exception("training_examples: scrubbing %s failed", path)
    return removed


def eligible(db: Session, user_id: uuid.UUID, learner: str) -> list[TrainingExample]:
    """What a future dataset for this user and learner may use: eligible, not rejected."""
    return list(db.scalars(select(TrainingExample).where(
        TrainingExample.user_id == user_id, TrainingExample.learner == learner, TrainingExample.eligible.is_(True),
        TrainingExample.review_status != "rejected").order_by(TrainingExample.decided_at)))


def review(db: Session, user_id: uuid.UUID, example_id: uuid.UUID, status: str) -> TrainingExample | None:
    """A reviewer's verdict: accepted makes it eligible again, rejected keeps it out (kept for inspection)."""
    row = db.scalar(select(TrainingExample).where(TrainingExample.user_id == user_id,
                                                  TrainingExample.id == example_id))
    if row is None:
        return None
    row.review_status = status
    row.eligible = status == "accepted" or (status == "unreviewed" and row.ineligible_reason is None)
    if status == "accepted":
        row.ineligible_reason = None
    db.commit()
    return row


def forget_user(db: Session, user_id: uuid.UUID) -> int:
    """Everything of one user's (the memory reset's ``training`` scope), tombstones included."""
    db.execute(delete(TrainingExampleDeletion).where(TrainingExampleDeletion.user_id == user_id))
    return db.execute(delete(TrainingExample).where(TrainingExample.user_id == user_id)).rowcount or 0
