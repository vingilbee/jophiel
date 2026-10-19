"""1. Notice: cheap facts about what the message literally does. Meaning comes from the encoder."""
from __future__ import annotations

import re
from typing import Any

_LAUGH = re.compile(r'\b(a?ha(ha)+h?|he(he)+|lol|lmao|rofl|xd)\b', re.I)
_EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
_GREETING = re.compile(r'^\s*(hi+|hello+|hey+|yo|hiya|good (morning|afternoon|evening)|morning|evening)\b', re.I)
_FAREWELL = re.compile(r'\b(bye+|good ?night|gn|see (you|ya)|later|gotta go|talk (to you )?(later|tomorrow)|ttyl)\b', re.I)
_REQUEST = re.compile(r'\b(can|could|would|will) you\b|\b(please|pls|plz)\b|^\s*(tell|give|show|help|write|make|find|remind|explain|send)\b', re.I)
_QUESTION_START = re.compile(r'^\s*(who|what|when|where|why|how|which|do|does|did|is|are|was|were|can|could|would|will|should|have|has)\b', re.I)
_NAME = re.compile(r'\b(columbina|you|your|you\'re|u|ur)\b', re.I)


def notice(text: str, *, seconds_since_reply: float | None, first_user_turn: bool) -> dict[str, Any]:
    stripped = text.strip()
    return dict(
        question='?' in stripped or bool(_QUESTION_START.match(stripped)),
        request=bool(_REQUEST.search(stripped)),
        mentions_her=bool(_NAME.search(stripped)),
        laughing=bool(_LAUGH.search(stripped)),
        emoji=bool(_EMOJI.search(stripped)),
        exclamation='!' in stripped,
        greeting=bool(_GREETING.match(stripped)),
        farewell=bool(_FAREWELL.search(stripped)),
        length=len(stripped.split()),
        seconds_since_reply=seconds_since_reply,
        first_user_turn=first_user_turn,
    )


def numeric(facts: dict[str, Any]) -> list[float]:
    """Fixed-order numbers for the student."""
    seconds = facts['seconds_since_reply']
    return [float(facts[k]) for k in ('question', 'request', 'mentions_her', 'laughing', 'emoji', 'exclamation',
                                       'greeting', 'farewell', 'first_user_turn')] + [
        min(facts['length'], 80) / 80.0,
        0.0 if seconds is None else min(seconds, 3600.0) / 3600.0,
    ]


NOTICE_WIDTH = 11
