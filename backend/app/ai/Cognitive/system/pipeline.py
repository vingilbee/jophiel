"""Composition boundary for perception followed by interpretation."""

from __future__ import annotations

from collections.abc import Sequence

from .interpretation import IntentInterpreter
from .perception import PerceptionEngine
from .schemas import CognitiveInput, PerceptionSignal


class CognitiveInputPipeline:
    def __init__(self, perception: PerceptionEngine | None = None, interpretation: IntentInterpreter | None = None) -> None:
        self.perception = perception or PerceptionEngine()
        self.interpretation = interpretation or IntentInterpreter()

    def process(
        self,
        message: str,
        recent_messages: Sequence[str] = (),
        *,
        directed_at_agent: bool = True,
        context_signals: dict[str, PerceptionSignal] | None = None,
    ) -> CognitiveInput:
        perceived = self.perception.perceive(
            message,
            recent_messages,
            directed_at_agent=directed_at_agent,
            context_signals=context_signals,
        )
        interpreted = self.interpretation.interpret(message, perceived)
        return CognitiveInput(perception=perceived, interpretation=interpreted)
