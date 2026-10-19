"""Memory retrieval for the teacher environment, scored exactly like live recall.

Live recall (`long_term_memory.retrieve_for_prompt`) reads the database. The environment's
synthetic users have no rows there, so this keeps their memories in memory and applies the same
scoring and thresholds, imported from the live module so the two can't drift apart. Episodes and
recency don't apply to scenario facts.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.long_term_memory import (
    ALWAYS_IMPORTANCE, ALWAYS_KINDS, MIN_SIMILARITY, MIN_WORD_OVERLAP, _content_words, _cosine,
)

from .contracts import MAX_MEMORIES
from .llm import LLM


@dataclass
class MemoryItem:
    content: str
    kind: str = 'fact'
    importance: float = 0.6
    confidence: float = 0.9
    embedding: list[float] | None = None


class MemoryPool:
    def __init__(self, items: list[MemoryItem], llm: LLM, embedding_model: str):
        self.items = items
        self.llm = llm
        self.embedding_model = embedding_model

    async def prepare(self) -> None:
        missing = [item for item in self.items if item.embedding is None]
        if missing:
            vectors = await self.llm.embed([item.content for item in missing], self.embedding_model)
            for item, vector in zip(missing, vectors, strict=True):
                item.embedding = vector

    async def retrieve(self, query: str, limit: int = MAX_MEMORIES) -> list[str]:
        if not self.items:
            return []
        query_vector = (await self.llm.embed([query], self.embedding_model))[0]
        query_words = _content_words(query)
        ranked = []
        for item in self.items:
            lexical = len(query_words & _content_words(item.content)) / max(1, len(query_words))
            semantic = max(0.0, _cosine(query_vector, item.embedding))
            score = semantic * 0.55 + lexical * 0.25 + item.importance * 0.1 + item.confidence * 0.05
            always = item.kind in ALWAYS_KINDS and item.importance >= ALWAYS_IMPORTANCE
            if semantic >= MIN_SIMILARITY or (query_words and lexical >= MIN_WORD_OVERLAP) or always:
                ranked.append((score, item.content))
        return [content for _, content in sorted(ranked, key=lambda x: x[0], reverse=True)[:limit]]
