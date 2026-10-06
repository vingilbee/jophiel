"""Canonical per-conversation source ordering (dialogue-message-order-v0.1).

``chat_messages.conversation_sequence`` is the durable ledger position.  It is
allocated under the conversation row lock by a database trigger and is never
reused, so a missing position is a deleted message, never a reordering.

Existing rows were backfilled (migration ``b8d2f4a6c1e3``) within each
conversation by, in order:

1. ``coalesce(ChatJob.created_at, ChatMessage.created_at)`` (the turn's time);
2. turn grouping when ``turn_id`` is present;
3. user before assistant within one turn;
4. ``ChatMessage.created_at``;
5. ``ChatMessage.id`` as the final stable tie-break.

``backfill_sort_key`` mirrors that SQL exactly so the ordering can be tested
without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..contracts.dialogue_memory import (
    DIALOGUE_MESSAGE_ORDER_VERSION,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueSourceEvent,
)
from .errors import fail
from .models import DialogueMessageRecord

if TYPE_CHECKING:  # annotations only: this package never reads a clock
    from datetime import datetime

ORDER_VERSION = DIALOGUE_MESSAGE_ORDER_VERSION


def backfill_sort_key(
    *, job_created_at: datetime | None, turn_id: str | None, role: str, created_at: datetime, message_id: str,
) -> tuple:
    """The documented backfill order for one pre-existing message.

    Rows sharing a turn share ``effective_at`` (their ChatJob's time), so the
    turn id groups them and the role puts the user prompt first.
    """

    effective_at = job_created_at if job_created_at is not None else created_at
    return (effective_at, turn_id or "", 0 if role == "user" else 1, created_at, message_id)


def validate_records(records: Sequence[DialogueMessageRecord], *, after_sequence: int, conversation_ref: str) -> None:
    """Require records to continue ``after_sequence`` contiguously, one conversation, no duplicates."""

    expected = after_sequence + 1
    seen: set[str] = set()
    for record in records:
        if record.conversation_ref != conversation_ref:
            raise fail(DialogueMemoryFailurePhase.SOURCE_ORDERING, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                       "A source message belongs to a different conversation.")
        if record.message_ref in seen:
            raise fail(DialogueMemoryFailurePhase.SOURCE_ORDERING, DialogueMemoryFailureCode.MESSAGE_ORDER_INVALID,
                       "A source message appears twice in one ordered batch.")
        seen.add(record.message_ref)
        if record.conversation_sequence < expected:
            raise fail(DialogueMemoryFailurePhase.SOURCE_ORDERING, DialogueMemoryFailureCode.MESSAGE_ORDER_INVALID,
                       "Source messages are duplicated or out of canonical order.")
        if record.conversation_sequence > expected:
            raise fail(DialogueMemoryFailurePhase.SOURCE_ORDERING, DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP,
                       "The conversation ledger has an unexplained sequence gap.")
        expected += 1


def validate_events(events: Sequence[DialogueSourceEvent], *, after_sequence: int, conversation_ref: str,
                    user_ref: str) -> None:
    """The same contiguity rule for already-projected events, plus ownership."""

    expected = after_sequence + 1
    seen: set[str] = set()
    for event in events:
        if event.conversation_ref != conversation_ref or event.user_ref != user_ref:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.CONVERSATION_NOT_OWNED,
                       "A source event belongs to a different user or conversation.")
        if event.message_ref in seen:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.MESSAGE_ORDER_INVALID,
                       "A source event appears twice in one transition.")
        seen.add(event.message_ref)
        if event.conversation_sequence < expected:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.MESSAGE_ORDER_INVALID,
                       "Source events are duplicated or out of canonical order.")
        if event.conversation_sequence > expected:
            raise fail(DialogueMemoryFailurePhase.TRANSITION, DialogueMemoryFailureCode.SOURCE_SEQUENCE_GAP,
                       "Source events do not continue the predecessor contiguously.")
        expected += 1


__all__ = ["ORDER_VERSION", "backfill_sort_key", "validate_events", "validate_records"]
