from app.services.tts import clause_chunker


def test_clause_chunker_emits_punctuation_early():
    feed, flush = clause_chunker()
    assert list(feed("This is a complete first sentence. More")) == [
        "This is a complete first sentence."
    ]
    assert list(flush()) == ["More"]


def test_clause_chunker_bounds_unpunctuated_first_audio():
    feed, _ = clause_chunker(max_chars=30)
    chunks = list(feed("This opening sentence keeps going without any punctuation at all"))
    assert chunks[0] == "This opening sentence keeps"
    assert all(len(chunk) <= 30 for chunk in chunks)
