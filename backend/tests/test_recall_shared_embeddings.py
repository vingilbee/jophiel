"""One evaluation embeds its message once: memory's search and the lessons read share it (pure, no network)."""
from __future__ import annotations

from app.services import long_term_memory, recall


def test_shared_embeddings_embed_a_text_once_within_the_block(monkeypatch):
    calls = []

    def fake(text):
        calls.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(long_term_memory, "_embedding", fake)
    with recall.shared_embeddings():
        assert recall.embed("hello") == recall.embed("hello") == [1.0, 0.0]
        recall.embed("other")
    assert calls == ["hello", "other"]
    recall.embed("hello")  # outside the block nothing is kept
    assert calls == ["hello", "other", "hello"]


def test_the_openai_client_is_made_once():
    long_term_memory._client.cache_clear()
    long_term_memory._embedding_client.cache_clear()
    assert long_term_memory._client() is long_term_memory._client()
    embedding = long_term_memory._embedding_client()
    assert embedding is long_term_memory._embedding_client()
    assert embedding.timeout == long_term_memory.EMBEDDING_TIMEOUT_SECONDS and embedding.max_retries == 1
