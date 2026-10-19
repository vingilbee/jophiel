"""Her mind's thoughts, for the chat page's thoughts panel."""
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
    created_at: datetime
