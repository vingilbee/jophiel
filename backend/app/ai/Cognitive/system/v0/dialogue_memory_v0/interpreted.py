"""Fold interpreted dialogue records into the state a turn sees (``interpreted-dialogue-fold-v1``).

Pure and deterministic: the same records always give the same state, whatever
order they were written in, because they are folded in conversation order.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts.dialogue_interpretation import (
    MAX_FOLDED_RECORDS, DialogueInterpretationRecord, FoldedCorrection, FoldedOptionSet, FoldedReferentLink,
    FoldedSpan, InterpretedDialogueState,
)


def fold(records: Iterable[DialogueInterpretationRecord], *, before_sequence: int,
         pending_message_refs: Iterable[str] = ()) -> InterpretedDialogueState:
    """Relationships interpreted from user messages strictly before ``before_sequence``."""

    ordered = sorted((record for record in records if record.source_conversation_sequence < before_sequence),
                     key=lambda record: record.source_conversation_sequence)
    if len({record.source_conversation_sequence for record in ordered}) != len(ordered):
        raise ValueError("one interpretation record per user message")
    ordered = ordered[-MAX_FOLDED_RECORDS:]
    # A value is superseded when a later message's correction replaced it.
    replaced = {update.spans[0].key for record in ordered for update in record.updates if update.kind == "correction"}

    def folded(span) -> FoldedSpan:
        return FoldedSpan(span=span, superseded=span.key in replaced)

    option_sets, links, corrections = [], [], []
    for record in ordered:
        source = record.source_message_ref
        for update in record.updates:
            if update.kind == "option_set":
                option_sets.append(FoldedOptionSet(
                    source_message_ref=source, options=tuple(folded(span) for span in update.spans)))
            elif update.kind == "same_referent":
                mention, referent = update.spans
                links.append(FoldedReferentLink(source_message_ref=source, mention=folded(mention),
                                                referent=folded(referent)))
            else:
                old, new = update.spans
                corrections.append(FoldedCorrection(source_message_ref=source, replaced=old, replacement=new))
    return InterpretedDialogueState(
        record_digests=tuple(record.digest for record in ordered), option_sets=tuple(option_sets),
        referent_links=tuple(links), corrections=tuple(corrections),
        pending_message_refs=tuple(dict.fromkeys(pending_message_refs)),
    )


__all__ = ["fold"]
