"""Message visibility/exposure policy (dialogue-message-eligibility-v0.1).

Exposure is decided from persisted facts only -- a message's own status and
content, and its explicit delivery records -- never from the assumption that
generating text means the user saw it.  One reviewed mapping per channel:

User messages (any known channel)
    persisted and owned                                   -> durably_visible

Assistant messages with explicit delivery records (the WhatsApp outbox;
also the ``system`` link confirmation, which is delivered through it)
    every part ``delivered`` or ``read``                   -> durably_visible
    any part ``failed``                                    -> delivery_failed
    otherwise (pending/retry/sending/accepted/sent)        -> delivery_pending
    ``accepted``/``sent`` are not final: the provider can still report a
    failure after them, so they never count as exposure.

Assistant messages on ``web``/``voice`` (streamed, then committed to the
history the client reloads; no outbox)
    completed, non-empty                                   -> durably_visible
    partial, non-empty                                     -> partially_visible
    failed, non-empty (shown in durable history)           -> partially_visible
    cancelled/failed/partial, empty                        -> not_visible
    completed, empty                                       -> not_visible

Anything else -- an unknown channel, or a delivery channel with no delivery
record -- fails closed as ``visibility_unknown``.  Shadow Realization
candidates are not chat messages and are never loaded at all.
"""

from __future__ import annotations

from ..contracts.dialogue_memory import (
    DIALOGUE_MESSAGE_ELIGIBILITY_VERSION,
    DialogueExposureState,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueMessageStatus,
    DialogueRole,
    DialogueSourceEvent,
    sha256_text,
    source_event_id_for,
)
from .errors import fail
from .models import DialogueMessageRecord

ELIGIBILITY_VERSION = DIALOGUE_MESSAGE_ELIGIBILITY_VERSION

# Channels whose user messages are authored in-app or received from a linked identity.
USER_CHANNELS = frozenset({"web", "voice", "whatsapp", "system"})
# Channels whose assistant output is streamed and committed to reloadable history.
STREAMED_CHANNELS = frozenset({"web", "voice"})
# Channels whose assistant output is only exposed through an explicit delivery outbox.
DELIVERY_CHANNELS = frozenset({"whatsapp", "system"})

DELIVERED_STATUSES = frozenset({"delivered", "read"})
FAILED_STATUSES = frozenset({"failed"})
KNOWN_DELIVERY_STATUSES = frozenset({"pending", "retry", "sending", "accepted", "sent", "delivered", "read", "failed"})


def resolve_exposure(record: DialogueMessageRecord) -> tuple[DialogueExposureState, tuple[str, ...]]:
    """The exposure state of one persisted message and the delivery evidence it rests on."""

    try:
        role = DialogueRole(record.role)
        status = DialogueMessageStatus(record.status)
    except ValueError as exc:
        raise fail(DialogueMemoryFailurePhase.EXPOSURE_RESOLUTION, DialogueMemoryFailureCode.EXPOSURE_UNKNOWN,
                   "A source message has an unsupported role or status.") from exc

    if role == DialogueRole.USER:
        if record.source_channel in USER_CHANNELS:
            return DialogueExposureState.DURABLY_VISIBLE, ()
        return DialogueExposureState.VISIBILITY_UNKNOWN, ()

    evidence = tuple(sorted(f"message-delivery:{item.delivery_ref}" for item in record.deliveries))
    if record.deliveries:
        statuses = {item.status for item in record.deliveries}
        if not statuses <= KNOWN_DELIVERY_STATUSES:
            return DialogueExposureState.VISIBILITY_UNKNOWN, evidence
        parts = sorted(item.part_index for item in record.deliveries)
        if parts != list(range(len(parts))):
            # A delivery outbox with missing parts cannot prove the whole text was sent.
            return DialogueExposureState.VISIBILITY_UNKNOWN, evidence
        if statuses & FAILED_STATUSES:
            return DialogueExposureState.DELIVERY_FAILED, evidence
        if statuses <= DELIVERED_STATUSES and record.content and status == DialogueMessageStatus.COMPLETED:
            return DialogueExposureState.DURABLY_VISIBLE, evidence
        if statuses <= DELIVERED_STATUSES:
            # Delivered, but the persisted outcome is not a completed utterance.
            return (DialogueExposureState.PARTIALLY_VISIBLE if record.content and status in {
                DialogueMessageStatus.PARTIAL, DialogueMessageStatus.FAILED,
            } else DialogueExposureState.VISIBILITY_UNKNOWN), evidence
        return DialogueExposureState.DELIVERY_PENDING, evidence

    if record.source_channel in DELIVERY_CHANNELS:
        # Output on a delivery channel with no delivery record was never sent.
        return DialogueExposureState.VISIBILITY_UNKNOWN, ()
    if record.source_channel not in STREAMED_CHANNELS:
        return DialogueExposureState.VISIBILITY_UNKNOWN, ()
    if not record.content:
        return DialogueExposureState.NOT_VISIBLE, ()
    if status == DialogueMessageStatus.COMPLETED:
        return DialogueExposureState.DURABLY_VISIBLE, ()
    if status in {DialogueMessageStatus.PARTIAL, DialogueMessageStatus.FAILED}:
        return DialogueExposureState.PARTIALLY_VISIBLE, ()
    # A cancelled outcome with text is not a state the chat paths persist.
    return DialogueExposureState.VISIBILITY_UNKNOWN, ()


def source_event(record: DialogueMessageRecord, *, user_ref: str) -> DialogueSourceEvent:
    """Project one persisted message through the policy into the updater's event."""

    exposure, evidence = resolve_exposure(record)
    try:
        return DialogueSourceEvent(
            event_id=source_event_id_for(record.conversation_ref, record.message_ref, record.conversation_sequence,
                                         exposure),
            conversation_ref=record.conversation_ref, user_ref=user_ref,
            conversation_sequence=record.conversation_sequence, message_ref=record.message_ref,
            turn_ref=record.turn_ref, role=record.role, status=record.status,
            source_channel=record.source_channel, exposure_state=exposure,
            content_digest=sha256_text(record.content), content=record.content,
            attachment_refs=record.attachment_refs, created_at=record.created_at,
            exposure_evidence_refs=evidence,
        )
    except ValueError as exc:
        raise fail(DialogueMemoryFailurePhase.EXPOSURE_RESOLUTION, DialogueMemoryFailureCode.EXPOSURE_UNKNOWN,
                   "A source message could not be projected under the eligibility policy.") from exc


__all__ = [
    "DELIVERED_STATUSES", "DELIVERY_CHANNELS", "ELIGIBILITY_VERSION", "STREAMED_CHANNELS", "USER_CHANNELS",
    "resolve_exposure", "source_event",
]
