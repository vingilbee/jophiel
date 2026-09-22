"""Typed, content-safe failures for the canonical Cognitive Pipeline V0."""

from __future__ import annotations

from typing import Literal


PipelineFailureStage = Literal[
    "validation",
    "perception",
    "handoff_validation",
    "interpretation",
    "turn_request",
    "cognitive_turn",
    "trace_validation",
]


class CognitivePipelineExecutionError(RuntimeError):
    """A safe outer failure that retains the original exception as its cause."""

    def __init__(
        self,
        *,
        cognitive_turn_id: str,
        failed_stage: PipelineFailureStage,
        safe_message: str,
        nested_stage: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.cognitive_turn_id = cognitive_turn_id
        self.failed_stage = failed_stage
        self.nested_stage = nested_stage
        self.safe_message = safe_message

