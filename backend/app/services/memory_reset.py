"""Wipe what Jophiel remembers about users, for testing memory and learning.

Never touches ``core_memory`` (her identity, not about any user), user accounts,
logins, integrations, desktop environments, or calorie/weight logs. Deleting a
conversation cascades to everything built on it: messages, chat/memory jobs,
and working memory.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    AgentEventJob, CaptureBlob, LearningAssessment, LearningLessonJob, MemoryLesson, CaptureGap, Conversation, DecisionAttempt, DecisionExecution, DesktopSession, EpisodicMemory, GameChallenge, GameExperience, GameLearnedSkill,
    GameLesson, GameProposal, GameReflectionJob, GameSkillAttempt, LearningMemory, MemoryConflict, MemoryContext,
    MemoryCorrection, MemoryEntity, MemoryEntityLink, MemoryEvent, MemoryForget, MemoryForgottenSource, MemoryUpkeepPair,
    MemoryEvidence, MemoryJob, MemoryManagerState, Observation, ObservationBinding, ObservationProcessing,
    ObservationSource, MemoryEventEmbedding, RecallReceipt, RecallReconstruction, RecallReconstructionSource,
    RecallReference, SemanticMemory, TrainingExample, TrainingExampleDeletion, WorkingItem, LearningTrial,
    LearningTrialTrigger,
)

# What each scope deletes, in order. Every model here has a ``user_id`` column.
SCOPES: dict[str, tuple[type, ...]] = {
    "conversations": (Conversation,),
    # Corrections go with the consolidation that read them (their memory events, if any, are working memory).
    # Forget requests and their tombstones too: a test reset starts over (a user's forget never does this).
    # Entities (who memories are about) go after the facts and episodes that point at them.
    "memory": (MemoryCorrection, MemoryConflict, MemoryForgottenSource, MemoryForget, MemoryUpkeepPair, SemanticMemory, EpisodicMemory,
               MemoryEntityLink, MemoryEntity, LearningMemory, MemoryJob),
    # Learned moves before attempts: they point at the attempt they came from.
    "game": (AgentEventJob, GameReflectionJob, GameExperience, GameLesson, GameProposal, GameChallenge,
             GameLearnedSkill, GameSkillAttempt),
    # Working memory (docs/COLUMBINA_MEMORY_SESSION1_SPEC.md 4.8): links, events, items, state, then contexts.
    # Game sessions stay; only their goal link is cleared first. Other scopes leave working memory alone:
    # deleting conversations or game experiences removes their evidence links, never the goals.
    "working": (MemoryEvidence, MemoryEvent, WorkingItem, MemoryManagerState, MemoryContext),
    # Observation (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md §12): the stream, its receipts and bindings, and
    # source state; capture then starts again from now, so nothing already seen comes back.
    "observation": (ObservationBinding, ObservationProcessing, Observation, ObservationSource),
    # Recall (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md): what replies were given (with the histories
    # rebuilt for them), and event embeddings (rebuilt in the background). Deleting memories or their
    # sources elsewhere already removes their references.
    "recall": (RecallReconstructionSource, RecallReconstruction, RecallReference, RecallReceipt, MemoryEventEmbedding),
    # Learning capture (docs/learning-system/01-implementation.md): raw decision captures and their gaps.
    "capture": (DecisionExecution, DecisionAttempt, CaptureGap, CaptureBlob),
    # Training examples derived from them, with deletion tombstones (a test reset starts over; a user's own
    # source deletion never removes examples, and their explicit deletion keeps the tombstone).
    "training": (TrainingExampleDeletion, TrainingExample),
    # Learning's assessments (docs/learning-system/02-implementation.md): revisions, links, evidence and uses
    # cascade with them. Training examples they flagged stay flagged (scope training resets those).
    "assessment": (LearningAssessment,),
    # Lessons (docs/learning-system/03-implementation.md): revisions, evidence and uses cascade with them; the
    # lesson jobs also go with scope assessment (they cascade with their assessment).
    "lessons": (LearningLessonJob, MemoryLesson),
    # Bounded game trials (docs/learning-system/04-implementation.md): revisions, events and assessments cascade
    # with their trial. What they fed (a hypothesis's status, a game lesson, a move) belongs to those owners.
    "trials": (LearningTrialTrigger, LearningTrial),
}


def reset(db: Session, *, user_ids: Iterable[uuid.UUID] | None = None,
          scopes: Iterable[str] = tuple(SCOPES)) -> dict[str, int]:
    """Delete the chosen scopes for these users (all users when ``None``).

    Returns rows deleted per table (cascaded rows aren't counted). Commits.
    """
    ids = None if user_ids is None else list(user_ids)
    chosen = list(scopes)
    unknown = set(chosen) - set(SCOPES)
    if unknown:
        raise ValueError(f"unknown scope(s): {', '.join(sorted(unknown))}")
    counts: dict[str, int] = {}
    for scope in chosen:
        if scope == "working":
            unlink = update(DesktopSession).where(DesktopSession.goal_item_id.is_not(None)).values(goal_item_id=None)
            if ids is not None:
                unlink = unlink.where(DesktopSession.owner_user_id.in_(ids))
            db.execute(unlink)
            _forget_observation_contexts(db, ids)
        for model in SCOPES[scope]:
            statement = delete(model)
            if ids is not None:
                statement = statement.where(model.user_id.in_(ids))
            counts[model.__tablename__] = db.execute(statement).rowcount or 0
    if "observation" in chosen:
        from app.services import observation

        observation.reset_restart(db, ids)
    db.commit()
    return counts


def _forget_observation_contexts(db: Session, ids: list[uuid.UUID] | None) -> None:
    """Before working memory's contexts go: observations keep their reports but lose the contexts (the one
    change an observation allows), and what memory hadn't taken in yet is skipped, so a reset never
    replays old reports into the cleared memory."""
    def mine(statement, model):
        return statement if ids is None else statement.where(model.user_id.in_(ids))

    db.execute(mine(delete(ObservationBinding), ObservationBinding))
    db.execute(mine(update(Observation).where(Observation.context_id.is_not(None)).values(context_id=None),
                    Observation))
    db.execute(mine(update(ObservationProcessing).where(
        ObservationProcessing.consumer == "memory", ObservationProcessing.status.in_(("pending", "retry")),
    ).values(status="skipped", processed_at=func.now(), reason="working memory was reset"), ObservationProcessing))


def preview(db: Session, *, user_ids: Iterable[uuid.UUID] | None = None,
            scopes: Iterable[str] = tuple(SCOPES)) -> dict[str, int]:
    """Rows ``reset`` would delete, without deleting anything."""
    ids = None if user_ids is None else list(user_ids)
    counts: dict[str, int] = {}
    for scope in scopes:
        for model in SCOPES[scope]:
            query = select(func.count()).select_from(model)
            if ids is not None:
                query = query.where(model.user_id.in_(ids))
            counts[model.__tablename__] = db.scalar(query) or 0
    return counts
