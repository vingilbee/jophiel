"""Interpreted dialogue relationships (``dialogue-interpretation-v1``).

Dialogue Working Memory is built only from persisted messages. Some meaning
lives *between* messages, though: which options were offered, what "the loud
one" or "the first one" refers to, and which earlier value a correction
replaced. Call 1 (argument registry v1.7) proposes those relationships for the
current user message, as exact spans of user messages. Code validates every
span and keeps the result.

An interpreted relationship is a model's reading, never something the user
said, so it is always labelled ``interpreted``. It cites the user's words; it
never adds words of its own. A later correction supersedes values without
deleting them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .interpretation import FrozenModel

DIALOGUE_UPDATE_VERSION = "dialogue-interpretation-v1"
INTERPRETED_FOLD_VERSION = "interpreted-dialogue-fold-v1"
DIALOGUE_UPDATE_KINDS = ("option_set", "same_referent", "correction")
DialogueUpdateKind = Literal["option_set", "same_referent", "correction"]
MAX_DIALOGUE_UPDATES = 4
MAX_SPAN_CHARACTERS = 200
# The fold keeps this many most recent records; older relationships age out.
MAX_FOLDED_RECORDS = 32


class InterpretedSpan(FrozenModel):
    """An exact excerpt of one persisted user message."""

    message_ref: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=MAX_SPAN_CHARACTERS)

    @model_validator(mode="after")
    def valid(self) -> InterpretedSpan:
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("an interpreted span's bounds must match its text")
        return self

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.message_ref, self.start, self.end)


class InterpretedUpdate(FrozenModel):
    """One relationship. Span order carries the meaning:

    * ``option_set``: the options, in the order the user gave them (2-4);
    * ``same_referent``: ``(mention, referent)``, e.g. ("the loud one", "Tofu");
    * ``correction``: ``(replaced, replacement)``, e.g. ("painting", "hiking").
    """

    kind: DialogueUpdateKind
    spans: tuple[InterpretedSpan, ...] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def valid(self) -> InterpretedUpdate:
        if self.kind != "option_set" and len(self.spans) != 2:
            raise ValueError("same_referent and correction relate exactly two spans")
        if len({span.key for span in self.spans}) != len(self.spans):
            raise ValueError("an update relates distinct spans")
        if len({span.text.casefold() for span in self.spans}) != len(self.spans):
            raise ValueError("an update relates spans with distinct wording")
        return self


class DialogueInterpretationRecord(FrozenModel):
    """The validated updates one turn's Call 1 proposed for its own user message."""

    schema_version: Literal["dialogue-interpretation-v1"] = DIALOGUE_UPDATE_VERSION
    source_message_ref: str = Field(min_length=1)
    source_conversation_sequence: int = Field(ge=1)
    updates: tuple[InterpretedUpdate, ...] = Field(max_length=MAX_DIALOGUE_UPDATES)

    @model_validator(mode="after")
    def valid(self) -> DialogueInterpretationRecord:
        for update in self.updates:
            if not any(span.message_ref == self.source_message_ref for span in update.spans):
                raise ValueError("every update cites its own source message")
        return self

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


class FoldedSpan(FrozenModel):
    span: InterpretedSpan
    # A later correction replaced this value. It stays visible, labelled.
    superseded: bool


class FoldedOptionSet(FrozenModel):
    source_message_ref: str
    options: tuple[FoldedSpan, ...] = Field(min_length=2, max_length=4)


class FoldedReferentLink(FrozenModel):
    source_message_ref: str
    mention: FoldedSpan
    referent: FoldedSpan


class FoldedCorrection(FrozenModel):
    source_message_ref: str
    replaced: InterpretedSpan
    replacement: InterpretedSpan


class InterpretedDialogueState(FrozenModel):
    """Every relationship interpreted before the focus message, in message order."""

    version: Literal["interpreted-dialogue-fold-v1"] = INTERPRETED_FOLD_VERSION
    basis: Literal["interpreted"] = "interpreted"
    record_digests: tuple[str, ...]
    option_sets: tuple[FoldedOptionSet, ...]
    referent_links: tuple[FoldedReferentLink, ...]
    corrections: tuple[FoldedCorrection, ...]
    # Earlier user messages whose reading was not finished when this turn was captured.
    pending_message_refs: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.option_sets or self.referent_links or self.corrections)


__all__ = [
    "DIALOGUE_UPDATE_KINDS", "DIALOGUE_UPDATE_VERSION", "DialogueInterpretationRecord", "DialogueUpdateKind",
    "FoldedCorrection", "FoldedOptionSet", "FoldedReferentLink", "FoldedSpan", "INTERPRETED_FOLD_VERSION",
    "InterpretedDialogueState", "InterpretedSpan", "InterpretedUpdate", "MAX_DIALOGUE_UPDATES", "MAX_FOLDED_RECORDS",
    "MAX_SPAN_CHARACTERS",
]
