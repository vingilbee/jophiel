"""Intent interpretation with an optional trained local model and safe rules fallback."""

from __future__ import annotations

from pathlib import Path

from .classifier import IntentClassifier
from .schemas import Intent, IntentScore, Interpretation, Perception


class IntentInterpreter:
    def __init__(self, artifact_path: Path | None = None, *, clarification_threshold: float = 0.55) -> None:
        self.artifact_path = artifact_path
        self.clarification_threshold = clarification_threshold
        self._classifier: IntentClassifier | None = None
        self._load_attempted = False

    def _load_classifier(self) -> IntentClassifier | None:
        if self._load_attempted:
            return self._classifier
        self._load_attempted = True
        if self.artifact_path is None:
            return None
        try:
            self._classifier = IntentClassifier.load(self.artifact_path)
        except (OSError, ValueError, KeyError, TypeError):
            self._classifier = None
        return self._classifier

    def interpret(self, message: str, perception: Perception) -> Interpretation:
        if not message.strip():
            return Interpretation(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                needs_clarification=True,
                source="empty_input_guard",
            )
        classifier = self._load_classifier()
        if classifier is not None:
            prediction = classifier.predict(message)
            ranked = sorted(prediction.probabilities.items(), key=lambda item: item[1], reverse=True)
            intent, confidence = ranked[0]
            alternatives = [
                IntentScore(intent=label, confidence=round(score, 6))
                for label, score in ranked[1:3]
            ]
            source = "local_classifier"
        else:
            intent, confidence = self._fallback(perception)
            alternatives = []
            source = "rules_fallback"

        needs_clarification = (
            intent is Intent.UNKNOWN
            or confidence < self.clarification_threshold
            or perception.ambiguity >= 0.7
        )
        return Interpretation(
            intent=intent,
            confidence=round(confidence, 6),
            alternatives=alternatives,
            needs_clarification=needs_clarification,
            source=source,
        )

    @staticmethod
    def _fallback(perception: Perception) -> tuple[Intent, float]:
        greeting = perception.signals.get("is_greeting")
        clarification = perception.signals.get("is_clarification")
        word_count = perception.signals.get("word_count")
        if greeting and greeting.value is True:
            return Intent.GREETING, 0.95
        if clarification and clarification.value is True:
            return Intent.CLARIFY, 0.85
        if perception.is_request:
            return Intent.REQUEST, 0.8
        if perception.is_question:
            return Intent.QUESTION, 0.8
        if word_count and isinstance(word_count.value, int) and word_count.value > 0:
            return Intent.INFORM, max(0.5, 1.0 - perception.ambiguity)
        return Intent.UNKNOWN, 0.2
