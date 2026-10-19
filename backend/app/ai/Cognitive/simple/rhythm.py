"""Her own recent rhythm, counted by code: what a person notices about their own side of a chat.

Not a check: nothing is blocked. It's a fact she reads (and the student gets) so a pattern that
only exists across turns, like question after question, is visible in the turn that decides.
"""
from __future__ import annotations

from typing import Any

from app.services.long_term_memory import _content_words

from .contracts import Message

RECENT = 5


def _asks(text: str) -> bool:
    return '?' in text


def _mentions(reply: str, item: str) -> bool:
    words = _content_words(item) - {'she', 'interested', 'earlier', 'conversation', 'been', 'wondering', 'thinking'}
    return bool(words) and len(words & _content_words(reply)) / len(words) >= 0.5


def own_rhythm(history: list[Message], world: list[str]) -> dict[str, Any]:
    replies = [m.text for m in history if m.speaker == 'columbina']
    in_a_row = 0
    for text in reversed(replies):
        if not _asks(text):
            break
        in_a_row += 1
    recent = replies[-RECENT:]
    return dict(
        replies=len(replies),
        questions_in_a_row=in_a_row,
        questions_in_recent=sum(_asks(t) for t in recent),
        recent=len(recent),
        shared_world=[item for item in world if any(_mentions(t, item) for t in replies)],
    )


def rhythm_text(rhythm: dict[str, Any]) -> list[str]:
    if not rhythm or not rhythm['replies']:
        return ['Her own recent replies: none yet.']
    lines = [f'Her own recent replies: of her last {rhythm["recent"]}, {rhythm["questions_in_recent"]} asked a question; '
             f'the last {rhythm["questions_in_a_row"]} in a row ended with a question.' if rhythm['questions_in_a_row']
             else f'Her own recent replies: of her last {rhythm["recent"]}, {rhythm["questions_in_recent"]} asked a question; '
                  f'her last reply did not.']
    if rhythm['shared_world']:
        lines.append('From her world, she has already shared in this conversation:')
        lines.extend(f'- {item}' for item in rhythm['shared_world'])
    return lines
