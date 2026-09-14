"""Fast deterministic extraction of observable message features."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .schemas import Perception, PerceptionSignal

_WORD = re.compile(r"[\w']+", re.UNICODE)
_QUESTION = re.compile(r"(?:\?|^\s*(?:who|what|when|where|why|how|is|are|can|could|would|should|do|does|did)\b)", re.I)
_REQUEST = re.compile(
    r"(?:^\s*(?:please\s+)?(?:create|make|build|add|remove|delete|change|update|send|show|find|check|run|open|analy[sz]e|explain)\b|\b(?:can|could|would|will)\s+you\b|\bI\s+(?:want|need)\s+you\s+to\b)",
    re.I,
)
_GREETING = re.compile(r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))\b", re.I)
_VAGUE = re.compile(r"\b(?:it|this|that|these|those|thing|stuff|someone|somewhere|him|her|them)\b", re.I)
_CLARIFY = re.compile(r"\b(?:I mean|what I meant|to clarify|in other words|specifically)\b", re.I)


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _WORD.finditer(text)}


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class PerceptionEngine:
    """Extract stable fields plus optional signals without model or network I/O."""

    def perceive(
        self,
        message: str,
        recent_messages: Sequence[str] = (),
        *,
        directed_at_agent: bool = True,
        context_signals: dict[str, PerceptionSignal] | None = None,
    ) -> Perception:
        text = message.strip()
        word_count = len(_WORD.findall(text))
        is_question = bool(_QUESTION.search(text))
        is_request = bool(_REQUEST.search(text))

        similarities = [_similarity(text, prior) for prior in recent_messages if prior.strip()]
        novelty = 1.0 - max(similarities, default=0.0)
        if not text:
            novelty = 0.0

        vague_count = len(_VAGUE.findall(text))
        ambiguity = 0.0
        if not text:
            ambiguity = 1.0
        elif word_count <= 2 and not _GREETING.search(text):
            ambiguity = 0.75
        elif vague_count:
            ambiguity = min(0.85, 0.25 + (vague_count / max(word_count, 1)))
        if is_question or is_request:
            ambiguity = max(0.0, ambiguity - 0.1)

        signals = dict(context_signals or {})
        reserved_signals = {"word_count", "is_greeting", "is_clarification"}
        collisions = reserved_signals & signals.keys()
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(f"context_signals cannot replace reserved signals: {names}")
        signals.update(
            {
                "word_count": PerceptionSignal(value=word_count, confidence=1.0, source="text_rules"),
                "is_greeting": PerceptionSignal(value=bool(_GREETING.search(text)), confidence=1.0, source="text_rules"),
                "is_clarification": PerceptionSignal(value=bool(_CLARIFY.search(text)), confidence=0.9, source="text_rules"),
            }
        )
        return Perception(
            is_question=is_question,
            is_request=is_request,
            is_directed_at_agent=directed_at_agent,
            novelty=round(novelty, 6),
            ambiguity=round(ambiguity, 6),
            signals=signals,
        )
