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


class MemoryCorrectionOut(BaseModel):
    """A correction the user made and what the memory manager did with it (docs/COLUMBINA_MEMORY_CORRECTIONS.md)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_message_id: uuid.UUID
    subject: str
    attribute: str | None
    previous_value: str | None
    new_value: str
    kind: str
    status: str
    attempts: int
    last_error: str | None
    result: dict
    event_id: int | None
    said_at: datetime
    applied_at: datetime | None


class WorkingMemoryOut(BaseModel):
    state: ManagerStateOut
    contexts: list[MemoryContextOut] = []
    items: list[WorkingItemOut] = []
    events: list[MemoryEventOut] = []
    corrections: list[MemoryCorrectionOut] = []


# --------------------------------------------------------------------------- #
# Observation, for inspection (GET /api/memory/observations)
# --------------------------------------------------------------------------- #


class ObservationSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    source_instance: str
    enabled: bool
    availability: str
    last_received_at: datetime | None
    last_change_at: datetime | None
    next_check_at: datetime | None
    position: dict
    last_decision: dict
    last_error: str | None


class ObservationOut(BaseModel):
    id: int
    source: str
    source_instance: str
    kind: str
    actor: str
    epistemic: str
    content_origin: str
    salience: str
    summary: str
    occurred_at: datetime | None
    received_at: datetime
    context_id: uuid.UUID | None
    # How the context was bound after the fact (None: the source knew it, or it isn't bound yet).
    bound_by: str | None
    # What working memory did with it: pending, retry, applied, skipped or failed, and why.
    memory_status: str
    memory_reason: str | None
    attempts: int
    next_attempt_at: datetime


class ObservationAdapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    started_at: datetime
    last_run_at: datetime | None
    last_decision: dict


class ObservationsOut(BaseModel):
    counts: dict[str, int] = {}
    # Age of the oldest report memory hasn't dealt with yet.
    lag_seconds: float | None = None
    sources: list[ObservationSourceOut] = []
    recent: list[ObservationOut] = []
    failures: list[ObservationOut] = []
    adapters: list[ObservationAdapterOut] = []


class RecallReceiptOut(BaseModel):
    """What one reply was given from memory (no memory text: ids live in the references)."""

    model_config = ConfigDict(from_attributes=True)

    assistant_message_id: uuid.UUID
    status: str
    counts: dict = {}
    coverage: dict = {}
    association_status: str
    association_note: str | None = None
    referenced: list[str] = []
    supplied: list[str] = []
    histories: list[dict] = []  # rebuilt histories: status, account (spans only), coverage, passage sources
    created_at: datetime


class RecallOut(BaseModel):
    embeddings: dict[str, int] = {}
    receipts: list[RecallReceiptOut] = []
