"""Wipe what Columbina remembers about users, for testing memory and learning.

Never touches ``core_memory`` (her identity, not about any user), user accounts,
logins, integrations, desktop environments, or calorie/weight logs. Deleting a
conversation cascades to everything built on it: messages, chat/memory jobs,
working memory, dialogue memory, interpreted dialogue relationships, and cognitive
shadow jobs/traces.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentEventJob, Conversation, EpisodicMemory, GameExperience, GameLesson, GameProposal, GameReflectionJob,
    LearningMemory, MemoryJob, SemanticMemory,
)

# What each scope deletes, in order. Every model here has a ``user_id`` column.
SCOPES: dict[str, tuple[type, ...]] = {
    "conversations": (Conversation,),
    "memory": (SemanticMemory, EpisodicMemory, LearningMemory, MemoryJob),
    "game": (AgentEventJob, GameReflectionJob, GameExperience, GameLesson, GameProposal),
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
    conversation_ids: list[uuid.UUID] = []
    if "conversations" in chosen:
        query = select(Conversation.id)
        if ids is not None:
            query = query.where(Conversation.user_id.in_(ids))
        conversation_ids = list(db.scalars(query))
    counts: dict[str, int] = {}
    for scope in chosen:
        for model in SCOPES[scope]:
            statement = delete(model)
            if ids is not None:
                statement = statement.where(model.user_id.in_(ids))
            counts[model.__tablename__] = db.execute(statement).rowcount or 0
    db.commit()
    if conversation_ids:
        from app.services import dialogue_memory

        for conversation_id in conversation_ids:
            dialogue_memory.delete_cached_conversation(conversation_id)
    return counts


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
