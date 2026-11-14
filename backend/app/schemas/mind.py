"""Her mind: its thoughts (the thoughts panel) and its continuity (the mind panel)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ThoughtOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID | None
    level: Literal["react", "light", "quiet", "hard"]
    text: str
    said: str | None
    status: Literal["done", "stale", "unfinished", "dropped"]
    topic: str | None
    kind: str | None = None
    # Hard thoughts (services/mind_hard.py).
    trigger: str | None = None
    conclusion_kind: str | None = None
    thinking: str | None = None
    for_hands: str | None = None
    question: str | None = None
    dropped_reason: str | None = None
    source: str | None = None  # minecraft / conversation
    activity_id: uuid.UUID | None = None  # the game session or conversation it is about
    created_at: datetime


class MindWork(BaseModel):
    """Thinking she started that hasn't finished."""

    id: str
    kind: str  # mind / recall
    status: str
    about: str
    conversation_id: str | None
    created_at: datetime


class MindWake(BaseModel):
    """Something she chose to come back to (a reconsideration she scheduled)."""

    id: str
    about: str
    due_at: datetime
    item: str | None
    conversation_id: str | None


class MindItem(BaseModel):
    """An open question, promise or intention in her working memory (what she keeps between evaluations)."""

    id: str
    kind: str
    owner: str
    text: str
    status: str
    due_at: datetime | None


class MindWakeOutcome(BaseModel):
    """A wake that ran (or was kept from running), newest first: why she was woken and what she did."""

    at: datetime
    reason: str
    decision: str  # said / silent / no_words / superseded / rejected / wake
    status: str  # delivered / not_sent / cancelled / ...
    conversation_id: str | None


class MindStateOut(BaseModel):
    """Her mind's continuity (services/mind_state.py): what is under way, what she set to come back to, what she
    keeps open, and what her recent wakes came to. Read from the database; nothing here is process memory."""

    thinking: list[MindWork]
    scheduled: list[MindWake]
    open: list[MindItem]
    recent: list[MindWakeOutcome]
