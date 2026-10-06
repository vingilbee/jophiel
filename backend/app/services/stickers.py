"""Sticker markers in streamed assistant text.

The model sends a sticker by writing ``[[sticker:<name>]]`` inline (see the
sticker section of ``app/ai/prompts.py``). Anything that shows or speaks the
reply has to split those markers out of the text: shown, they become a sticker
image; spoken, they must not reach the TTS voice at all.

Shared by the WebSocket route (local path and nudges) and the chat worker
(which strips them from what it speaks).
"""

from __future__ import annotations

import re

STICKER_NAMES = frozenset({
    "confused", "question", "love", "thinking", "good-job", "sleepy",
    "yum", "tea", "hmm", "shy", "sorry", "idea", "encouragement", "silent",
    "scribble", "grateful", "surprised", "peeking", "embarrassed", "praying",
    "determined", "hungry", "see-you",
})
_STICKER_MARKER = re.compile(r"\[\[sticker:([a-z0-9-]+)\]\]")
# Public so other text mappings (see services/heard.py) agree on what a marker is.
MARKER = _STICKER_MARKER
_STICKER_PREFIX = "[[sticker:"


def consume_text(buffer: str, text: str, *, final: bool = False) -> tuple[list[dict], str]:
    """Turn complete sticker markers into protocol events without leaking them as text.

    Returns ``(events, buffer)``: ``token``/``sticker`` events for everything
    decidable so far, and the held-back tail that may be the start of a marker
    split across tokens. Pass ``final=True`` once the stream ends to release it.
    """
    pending = buffer + text
    events: list[dict] = []
    cursor = 0
    for match in _STICKER_MARKER.finditer(pending):
        if match.start() > cursor:
            events.append({"type": "token", "content": pending[cursor:match.start()]})
        name = match.group(1)
        if name in STICKER_NAMES:
            events.append({"type": "sticker", "sticker": name})
        # Unsupported markers are intentionally dropped. The model must never
        # leak an implementation marker into the visible conversation.
        cursor = match.end()
    tail = pending[cursor:]
    if final:
        if tail:
            events.append({"type": "token", "content": tail})
        return events, ""
    possible_start = tail.rfind(_STICKER_PREFIX)
    if possible_start < 0:
        # The opening itself can be split across tokens ("[[stick" +
        # "er:yum]]"), so also hold back a tail that could still grow into it.
        possible_start = _partial_prefix_start(tail)
    if possible_start >= 0:
        if possible_start:
            events.append({"type": "token", "content": tail[:possible_start]})
        return events, tail[possible_start:]
    if tail:
        events.append({"type": "token", "content": tail})
    return events, ""


def _partial_prefix_start(tail: str) -> int:
    """Index where ``tail`` ends in a proper prefix of the marker opening, or -1."""
    for start in range(max(0, len(tail) - len(_STICKER_PREFIX) + 1), len(tail)):
        if _STICKER_PREFIX.startswith(tail[start:]):
            return start
    return -1


def spoken_text(events: list[dict]) -> str:
    """The speakable part of ``consume_text`` events (stickers dropped)."""
    return "".join(event["content"] for event in events if event["type"] == "token")
