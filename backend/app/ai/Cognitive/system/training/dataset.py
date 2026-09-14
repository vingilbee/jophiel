"""Validated JSONL format for intent examples."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..schemas import Intent


class IntentExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    intent: Intent


def load_examples(path: Path) -> list[IntentExample]:
    examples: list[IntentExample] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            examples.append(IntentExample.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid training record at {path}:{line_number}: {exc}") from exc
    if not examples:
        raise ValueError(f"training dataset is empty: {path}")
    return examples
