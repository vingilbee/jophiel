"""Bounded first-use reconstruction.

A bootstrap is exactly a segment genesis followed by the ordinary updater over
the most recent bounded, contiguous run of messages ending at the boundary.
Bootstrap therefore equals incremental replay of the same bounded source
prefix by construction.  Nothing is summarized or invented: older history is
an explicit ``outside_bootstrap_window`` omission, a deleted position is an
explicit ``source_deleted`` omission, and the message limit is never presented
as semantic completeness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..contracts.dialogue_memory import (
    DialogueHistoryOmission,
    DialogueMemorySnapshot,
    DialogueOmissionReason,
    DialogueResetReason,
    DialogueSourceEvent,
)
from .models import DialogueMemoryLimits, DialogueMessageRecord
from .updater import DialogueMemoryUpdaterV0


@dataclass(frozen=True)
class BootstrapWindow:
    records: tuple[DialogueMessageRecord, ...]
    window_start: int
    omissions: tuple[DialogueHistoryOmission, ...]


def select_window(records: Sequence[DialogueMessageRecord], *, boundary: int, limit: int) -> BootstrapWindow:
    """The newest contiguous run of at most ``limit`` messages ending exactly at ``boundary``.

    ``records`` are the newest persisted messages at or before the boundary,
    ascending, as loaded (at most ``limit + 1`` of them is enough).
    """

    ordered = sorted(records, key=lambda item: item.conversation_sequence)
    if any(item.conversation_sequence > boundary for item in ordered):
        raise ValueError("bootstrap records must not pass the boundary")
    run: list[DialogueMessageRecord] = []
    expected = boundary
    older: DialogueMessageRecord | None = None
    for record in reversed(ordered):
        if len(run) == limit:
            break
        if record.conversation_sequence != expected:
            older = record
            break
        run.append(record)
        expected -= 1
    run.reverse()
    window_start = expected + 1
    omissions: list[DialogueHistoryOmission] = []
    if window_start > 1:
        if len(run) == limit:
            omissions.append(DialogueHistoryOmission(
                reason=DialogueOmissionReason.OUTSIDE_BOOTSTRAP_WINDOW, sequence_start=1,
                sequence_end=window_start - 1, recoverable=True,
            ))
        else:
            # The run stopped at a missing position: those messages were deleted.
            gap_start = older.conversation_sequence + 1 if older is not None else 1
            omissions.append(DialogueHistoryOmission(
                reason=DialogueOmissionReason.SOURCE_DELETED, sequence_start=gap_start,
                sequence_end=window_start - 1, recoverable=False,
            ))
            if gap_start > 1:
                omissions.append(DialogueHistoryOmission(
                    reason=DialogueOmissionReason.OUTSIDE_BOOTSTRAP_WINDOW, sequence_start=1,
                    sequence_end=gap_start - 1, recoverable=True,
                ))
    return BootstrapWindow(tuple(run), window_start, tuple(sorted(omissions, key=lambda item: item.sort_key)))


def bootstrap(
    *,
    user_ref: str,
    conversation_ref: str,
    events: Sequence[DialogueSourceEvent],
    window_start: int,
    revision: int,
    reset_reason: DialogueResetReason,
    omissions: Sequence[DialogueHistoryOmission] = (),
    limits: DialogueMemoryLimits | None = None,
) -> DialogueMemorySnapshot:
    """Genesis at ``window_start`` plus the ordinary transition over ``events``."""

    updater = DialogueMemoryUpdaterV0(limits)
    genesis = updater.genesis(
        user_ref=user_ref, conversation_ref=conversation_ref, revision=revision,
        segment_start_sequence=window_start, reset_reason=reset_reason, omitted_history=omissions,
    )
    return updater.apply(genesis, events, continue_lineage=False)


__all__ = ["BootstrapWindow", "bootstrap", "select_window"]
