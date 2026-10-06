"""Provider-neutral optional cache for committed snapshots (dialogue-working-memory-cache-v0.1).

A cache is only a disposable acceleration layer.  It holds the canonical
snapshot payload (never ORM objects), is published only after the database
commit, and every read is re-verified against the expected user,
conversation, revision, snapshot id, and semantic digest.  A miss, a
mismatch, or an unavailable backend changes nothing semantically: the caller
takes the PostgreSQL path.  Keys carry ids, revisions, and digests only --
never message text.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from ..contracts.dialogue_memory import (
    DIALOGUE_MEMORY_CACHE_VERSION,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueMemorySnapshot,
    canonical_json,
)
from .errors import fail

CACHE_VERSION = DIALOGUE_MEMORY_CACHE_VERSION
_KEY_PREFIX = "cognitive:dialogue-memory:v0.1"


class DialogueMemoryCache(Protocol):
    def get(self, conversation_ref: str, revision: int, digest: str) -> bytes | None: ...

    def put(self, snapshot: DialogueMemorySnapshot, ttl_seconds: int) -> None: ...

    def delete_conversation(self, conversation_ref: str) -> None: ...


def snapshot_key(conversation_ref: str, revision: int, digest: str) -> str:
    return f"{_KEY_PREFIX}:snapshot:{conversation_ref}:{revision}:{digest}"


def head_key(conversation_ref: str) -> str:
    return f"{_KEY_PREFIX}:head:{conversation_ref}"


def conversation_key_pattern(conversation_ref: str) -> str:
    return f"{_KEY_PREFIX}:snapshot:{conversation_ref}:*"


def encode(snapshot: DialogueMemorySnapshot) -> bytes:
    return canonical_json(snapshot.model_dump(mode="json")).encode("utf-8")


def decode_verified(
    raw: bytes, *, user_ref: str, conversation_ref: str, revision: int, snapshot_id: str, digest: str,
) -> DialogueMemorySnapshot:
    """Validate a cached payload and require it to be exactly the expected snapshot.

    Raises a recoverable ``cache_digest_mismatch`` on any deviation; the
    caller discards the entry and reads PostgreSQL.
    """

    try:
        snapshot = DialogueMemorySnapshot.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise fail(DialogueMemoryFailurePhase.CACHE, DialogueMemoryFailureCode.CACHE_DIGEST_MISMATCH,
                   "A cached dialogue snapshot failed contract validation.") from exc
    if (snapshot.user_ref, snapshot.conversation_ref, snapshot.revision, snapshot.snapshot_id,
            snapshot.semantic_digest) != (user_ref, conversation_ref, revision, snapshot_id, digest):
        raise fail(DialogueMemoryFailurePhase.CACHE, DialogueMemoryFailureCode.CACHE_DIGEST_MISMATCH,
                   "A cached dialogue snapshot is not the expected snapshot.")
    return snapshot


class NullDialogueMemoryCache:
    """The disabled cache: always a miss, never an error."""

    def get(self, conversation_ref: str, revision: int, digest: str) -> bytes | None:
        return None

    def put(self, snapshot: DialogueMemorySnapshot, ttl_seconds: int) -> None:
        return None

    def delete_conversation(self, conversation_ref: str) -> None:
        return None


__all__ = [
    "CACHE_VERSION", "DialogueMemoryCache", "NullDialogueMemoryCache", "conversation_key_pattern",
    "decode_verified", "encode", "head_key", "snapshot_key",
]
