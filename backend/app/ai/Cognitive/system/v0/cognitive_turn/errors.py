"""Typed failures for the shadow cognitive-turn spine."""

from __future__ import annotations

from typing import Literal

FailureStage = Literal[
    "validation",
    "appraisal",
    "dynamic_state",
    "action_handoff_validation",
    "action_selection",
    "trace_validation",
]


class CognitiveTurnExecutionError(RuntimeError):
    """A safe, stage-specific failure that preserves the original exception."""

    def __init__(
        self,
        *,
        cognitive_turn_id: str,
        failed_stage: FailureStage,
        safe_message: str,
        action_failure_phase: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.cognitive_turn_id = cognitive_turn_id
        self.failed_stage = failed_stage
        self.safe_message = safe_message
        self.action_failure_phase = action_failure_phase

