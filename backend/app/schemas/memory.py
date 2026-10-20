"""Her working memory, for the inspector (GET /api/memory/working)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MemoryContextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    label: str
    source: str | None
    external_ref: str | None
    parent_id: uuid.UUID | None
    status: str
    status_reason: str | None
    created_at: datetime
    last_active_at: datetime
    closed_at: datetime | None


class WorkingItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    context_id: uuid.UUID
    kind: str
    parent_id: uuid.UUID | None
    owner: str
    owed_to: str | None
    text: str
    done_when: str | None
    status: str
    status_reason: str | None
    epistemic: str
    due_at: datetime | None
    closed_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime
    # False once the message (or event) it came from is gone: it still stands, without its source.
    has_source: bool = False
    needs_look: bool = False


class MemoryEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    context_id: uuid.UUID
    item_id: uuid.UUID | None
    kind: str
    text: str
    epistemic: str
    occurred_at: datetime
    recorded_at: datetime


class ManagerStateOut(BaseModel):
    focus_context_id: uuid.UUID | None
    last_tick_at: datetime | None
    next_check_at: datetime | None
    last_decision: dict
    last_event_id: int


class WorkingMemoryOut(BaseModel):
    enabled: bool
    state: ManagerStateOut | None = None
    contexts: list[MemoryContextOut] = []
    items: list[WorkingItemOut] = []
    events: list[MemoryEventOut] = []
