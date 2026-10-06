"""Canonical semantic and source digests (dialogue-working-memory-digest-v0.1).

Two digests keep two meanings separate:

- ``semantic_digest`` covers the whole snapshot except its own id and digest
  (owned by ``contracts.dialogue_memory.snapshot_semantic_digest``);
- ``source_prefix_digest`` is a hash chain over the content-free fingerprints
  of every event the segment consumed, so deleting, mutating, or reordering an
  already-consumed message is detectable from PostgreSQL alone.

Exposure is not part of a fingerprint: a delivery completing later changes
what may be remembered, not which messages were consumed.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts.dialogue_memory import (
    DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION,
    DialogueMemorySnapshot,
    DialogueSourceEvent,
    sha256_json,
    snapshot_semantic_digest,
    turn_context_digest,
)
from .models import SourceFingerprint


# Lineage bookkeeping that legitimately differs between an eager per-turn
# chain and a one-shot bootstrap or catch-up over the same source prefix.
LINEAGE_FIELDS = ("snapshot_id", "semantic_digest", "revision", "predecessor_snapshot_ref", "segment_id",
                  "reset_reason")


def dialogue_state_digest(snapshot: DialogueMemorySnapshot) -> str:
    """The remembered dialogue state alone: items, indexes, omissions, watermark, and source prefix.

    Two snapshots with equal state digests remember exactly the same thing,
    however many revisions it took to get there.
    """

    payload = snapshot.model_dump(mode="json")
    return sha256_json({
        "digest_version": DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION, "kind": "dialogue-state",
        "state": {key: value for key, value in payload.items() if key not in LINEAGE_FIELDS},
    })


def fingerprint_of(event: DialogueSourceEvent) -> SourceFingerprint:
    return SourceFingerprint(
        conversation_sequence=event.conversation_sequence, message_ref=event.message_ref, role=event.role.value,
        status=event.status.value, turn_ref=event.turn_ref, source_channel=event.source_channel,
        content_digest=event.content_digest,
    )


def genesis_prefix_digest(conversation_ref: str, segment_start_sequence: int) -> str:
    return sha256_json({
        "digest_version": DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION, "kind": "segment-genesis",
        "conversation_ref": conversation_ref, "segment_start_sequence": segment_start_sequence,
    })


def extend_prefix_digest(previous: str, fingerprint: SourceFingerprint) -> str:
    return sha256_json({
        "digest_version": DIALOGUE_MEMORY_SEMANTIC_DIGEST_VERSION, "kind": "segment-event", "previous": previous,
        "event": {
            "conversation_sequence": fingerprint.conversation_sequence, "message_ref": fingerprint.message_ref,
            "role": fingerprint.role, "status": fingerprint.status, "turn_ref": fingerprint.turn_ref,
            "source_channel": fingerprint.source_channel, "content_digest": fingerprint.content_digest,
        },
    })


def prefix_digest_for(conversation_ref: str, segment_start_sequence: int,
                      fingerprints: Iterable[SourceFingerprint]) -> str:
    digest = genesis_prefix_digest(conversation_ref, segment_start_sequence)
    for fingerprint in fingerprints:
        digest = extend_prefix_digest(digest, fingerprint)
    return digest


__all__ = [
    "LINEAGE_FIELDS", "dialogue_state_digest", "extend_prefix_digest", "fingerprint_of", "genesis_prefix_digest", "prefix_digest_for",
    "snapshot_semantic_digest", "turn_context_digest",
]
