"""Atomic persistence boundary for the initial dynamic-state snapshot."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, literal
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session

from app.db.models import WorkingMemory

from .models import StateSnapshot

_SNAPSHOT_KEY = "dynamic_state_snapshot"


class InitialSnapshotAlreadyExists(RuntimeError):
    """The conversation already owns its one revision-1 snapshot."""


class DynamicStateSnapshotPersistence:
    """Atomically attach revision 1 to a conversation's working-memory row."""

    def persist_initial(
        self,
        db: Session,
        *,
        user_id: UUID,
        conversation_id: UUID,
        snapshot: StateSnapshot,
    ) -> StateSnapshot:
        if snapshot.revision != 1 or snapshot.supersedes_snapshot_id is not None:
            raise ValueError("persist_initial accepts only a non-superseding revision 1")

        payload = {_SNAPSHOT_KEY: snapshot.model_dump(mode="json")}
        statement = insert(WorkingMemory).values(
            user_id=user_id,
            conversation_id=conversation_id,
            cognitive_state=payload,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[WorkingMemory.conversation_id],
            set_={
                "cognitive_state": WorkingMemory.cognitive_state.op("||")(
                    literal(payload, type_=JSONB)
                )
            },
            where=and_(
                WorkingMemory.user_id == user_id,
                ~WorkingMemory.cognitive_state.op("?")(_SNAPSHOT_KEY),
            ),
        ).returning(WorkingMemory.id)

        if db.scalar(statement) is None:
            raise InitialSnapshotAlreadyExists(
                f"conversation {conversation_id} already has an initial snapshot"
            )
        return snapshot
