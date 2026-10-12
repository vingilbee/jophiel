"""Columbina's character in words: values, principles and beliefs (core memory v2).

Her numeric core memory says how she tends to act (curiosity 0.88, warmth
0.8). It says nothing about what she cares about or holds true, so until now
her opinions came from a style formula. This is that missing part:

* **values**: what she cares about; stable, approved by the user;
* **principles**: how she acts on them; stable, approved by the user;
* **beliefs**: claims about the world; she starts with them and evidence
  could change them (only by a new core-memory version for now).

Opinions are reasoned from these together with the situation, never looked
up: a view may also rest on experience or plain reasoning. Safety boundaries
are not part of the character and hold whatever it says.

It is read once at capture time from ``core_memory.identity["character"]``
and frozen in the capture, so replay never re-reads the database.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .interpretation import FrozenModel

CHARACTER_VERSION = "character-v1"
MAX_CHARACTER_ITEMS = 12
MAX_CHARACTER_TEXT = 300
_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

CharacterSection = Literal["values", "principles", "beliefs"]


class CharacterItem(FrozenModel):
    id: str
    text: str = Field(min_length=1, max_length=MAX_CHARACTER_TEXT)

    @model_validator(mode="after")
    def valid(self) -> CharacterItem:
        if not _ID.fullmatch(self.id):
            raise ValueError("character ids are short snake_case keys")
        return self


class Character(FrozenModel):
    version: Literal["character-v1"] = CHARACTER_VERSION
    core_memory_ref: str = Field(min_length=1)
    values: tuple[CharacterItem, ...] = Field(min_length=1, max_length=MAX_CHARACTER_ITEMS)
    principles: tuple[CharacterItem, ...] = Field(max_length=MAX_CHARACTER_ITEMS)
    beliefs: tuple[CharacterItem, ...] = Field(max_length=MAX_CHARACTER_ITEMS)

    @model_validator(mode="after")
    def unique(self) -> Character:
        ids = [item.id for item in (*self.values, *self.principles, *self.beliefs)]
        if len(set(ids)) != len(ids):
            raise ValueError("character ids are unique across sections")
        return self

    def section_of(self, item_id: str) -> CharacterSection | None:
        for section in ("values", "principles", "beliefs"):
            if any(item.id == item_id for item in getattr(self, section)):
                return section
        return None


def character_from_identity(identity: dict | None, *, core_memory_ref: str) -> Character | None:
    """The character stored in a core-memory identity group, or ``None`` when it has none."""
    raw = (identity or {}).get("character")
    if raw is None:
        return None
    return Character.model_validate({**raw, "core_memory_ref": core_memory_ref})


__all__ = ["CHARACTER_VERSION", "Character", "CharacterItem", "character_from_identity"]
