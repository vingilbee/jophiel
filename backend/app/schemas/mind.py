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
    status: Literal["done", "stale", "unfinished"]
    topic: str | None
    kind: str | None = None
    created_at: datetime
