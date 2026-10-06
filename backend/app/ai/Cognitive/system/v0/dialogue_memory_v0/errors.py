"""Typed Dialogue Working Memory failures.

Every failure carries a closed phase and a stable code, never message text,
snapshot payloads, quotes, or provider bodies.  A deterministic contract
failure leaves the predecessor head unchanged: there is no partial snapshot.
"""

from __future__ import annotations

from ..contracts.dialogue_memory import (
    RECOVERABLE_FAILURE_CODES,
    DialogueMemoryFailure,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
)


class DialogueMemoryError(RuntimeError):
    """A typed, content-free Dialogue Working Memory failure."""

    def __init__(
        self,
        phase: DialogueMemoryFailurePhase | str,
        code: DialogueMemoryFailureCode | str,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.failure = DialogueMemoryFailure(
            phase=DialogueMemoryFailurePhase(phase), code=DialogueMemoryFailureCode(code),
            safe_message=safe_message, retryable=retryable,
        )
        super().__init__(f"{self.failure.phase.value}.{self.failure.code.value}")

    @property
    def phase(self) -> DialogueMemoryFailurePhase:
        return self.failure.phase

    @property
    def code(self) -> DialogueMemoryFailureCode:
        return self.failure.code

    @property
    def recoverable(self) -> bool:
        return self.failure.code in RECOVERABLE_FAILURE_CODES


def fail(
    phase: DialogueMemoryFailurePhase | str, code: DialogueMemoryFailureCode | str, safe_message: str,
    *, retryable: bool = False,
) -> DialogueMemoryError:
    return DialogueMemoryError(phase, code, safe_message, retryable=retryable)


__all__ = ["DialogueMemoryError", "fail"]
