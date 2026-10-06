"""Package-internal records for Dialogue Working Memory V0.

These are not cross-boundary contracts (those live in
``contracts/dialogue_memory.py``).  ``DialogueMessageRecord`` is the plain,
database-free shape the service copies each persisted chat row into before
the pure eligibility policy turns it into a ``DialogueSourceEvent``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.dialogue_memory import MAX_SNAPSHOT_ITEMS


@dataclass(frozen=True)
class DeliveryFact:
    """One persisted outbound delivery part (e.g. a WhatsApp outbox row)."""

    delivery_ref: str
    channel: str
    part_index: int
    status: str


@dataclass(frozen=True)
class DialogueMessageRecord:
    """Exactly the persisted facts of one chat message the policy may read.

    ``created_at`` is an ISO-8601 UTC string; ``deliveries`` are that message's
    explicit delivery records (empty for channels without an outbox).
    """

    message_ref: str
    conversation_ref: str
    conversation_sequence: int
    turn_ref: str | None
    role: str
    status: str
    source_channel: str
    content: str
    created_at: str
    attachment_refs: tuple[str, ...] = ()
    deliveries: tuple[DeliveryFact, ...] = field(default=())


@dataclass(frozen=True)
class SourceFingerprint:
    """The content-free identity of one consumed source event.

    The source-prefix digest chains these.  Exposure is deliberately excluded:
    a delivery that later completes changes what may be remembered, not the
    identity of the consumed prefix.
    """

    conversation_sequence: int
    message_ref: str
    role: str
    status: str
    turn_ref: str | None
    source_channel: str
    content_digest: str


@dataclass(frozen=True)
class DialogueMemoryLimits:
    """Configured bounds.  They may lower, never raise, the contract caps."""

    bootstrap_messages: int = 30
    max_items: int = MAX_SNAPSHOT_ITEMS

    def __post_init__(self) -> None:
        if not 1 <= self.bootstrap_messages <= 500:
            raise ValueError("bootstrap_messages must be between 1 and 500")
        if not 1 <= self.max_items <= MAX_SNAPSHOT_ITEMS:
            raise ValueError("max_items cannot exceed the contract cap")


__all__ = ["DeliveryFact", "DialogueMemoryLimits", "DialogueMessageRecord", "SourceFingerprint"]
