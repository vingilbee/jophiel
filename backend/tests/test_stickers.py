from app.services import stickers
from app.services.tts import clause_chunker


def _speak(tokens: list[str]) -> list[str]:
    """What the worker would hand to TTS for this token stream."""
    feed, flush = clause_chunker()
    buffer = ""
    clauses: list[str] = []
    for token in tokens:
        frames, buffer = stickers.consume_text(buffer, token)
        clauses.extend(feed(stickers.spoken_text(frames)))
    frames, buffer = stickers.consume_text(buffer, "", final=True)
    clauses.extend(feed(stickers.spoken_text(frames)))
    clauses.extend(flush())
    return clauses


def test_marker_becomes_sticker_event_not_text():
    events, buffer = stickers.consume_text("", "Nice work! [[sticker:good-job]] Keep going.")
    assert buffer == ""
    assert {"type": "sticker", "sticker": "good-job"} in events
    assert stickers.spoken_text(events) == "Nice work!  Keep going."


def test_marker_split_across_tokens_is_never_spoken():
    tokens = ["That's about five hundred calories. ", "[[stick", "er:yum", "]]", " Enjoy it."]
    spoken = " ".join(_speak(tokens))
    assert "sticker" not in spoken and "[[" not in spoken
    assert "five hundred calories" in spoken and "Enjoy it." in spoken


def test_unknown_sticker_is_dropped_silently():
    events, _ = stickers.consume_text("", "Hi [[sticker:not-a-real-one]] there", final=True)
    assert all(event["type"] == "token" for event in events)
    assert stickers.spoken_text(events) == "Hi  there"


def test_partial_opening_is_held_then_released_if_not_a_marker():
    events, buffer = stickers.consume_text("", "Costs [[")
    assert stickers.spoken_text(events) == "Costs " and buffer == "[["
    events, buffer = stickers.consume_text(buffer, "b]] more")
    assert stickers.spoken_text(events) == "[[b]] more" and buffer == ""
