"""Conversations and her mind's background side (docs/MIND_ARCHITECTURE.md).

A conversation is not a source of background thinking any more: every message wakes her mind directly
(``services/mind_turn.py``), what she noted is in her working memory, and what she chose to come back to wakes her
later (``coordinator.schedule_wake``). Nothing here decides when she should think about a promise, an intention
or a quiet stretch: those are her own choices, made with her tools.

What stays is ``SOURCE.sweep`` (the mind worker, every ``SWEEP_SECONDS``): something the user said that disagrees
with what she remembers (an open ``memory_conflicts`` row, once: services/memory_conflicts.py) wakes her mind in the
conversation it was said in. An event from memory, handed over unchanged: whether to ask, think it over or leave it
is hers.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai.mind.contracts import Rhythm
from app.ai.mind.situation import Situation
from app.db.models import MemoryConflict

log = logging.getLogger(__name__)
NAME = "conversation"
CONVERSATION = Situation(NAME, "talking with {user} in chat")

SWEEP_SECONDS = 30.0
WATCH_HOURS = 24  # an unresolved disagreement older than this is no longer handed over (recall still shows it)
CONFLICT_TTL_SECONDS = 15 * 60  # how long the wake about one may wait behind replies before it's stale


def conflict_text(row: MemoryConflict) -> str:
    since = row.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d") if row.created_at else "earlier"
    known = " ".join(row.known_content.split())[:300]
    said = " ".join(row.user_words.split())[:300]
    return (f'You remember: "{known}". On {since} they said: "{said}", which doesn\'t match it. Nothing has settled '
            "which holds: they may have misspoken or been misheard, it may have changed without them saying so, or "
            "both may be true (a visit, a trip). Your memory still says what it said.")


class ConversationEvents:
    """What the mind worker runs for conversations: no polling, no thoughts of its own, only memory's events
    handed to her mind."""

    name = NAME
    rhythm = Rhythm("event")
    shadow = False

    def __init__(self) -> None:
        self._last_sweep = 0.0

    def enabled(self) -> bool:
        return True

    def gate_situation(self) -> Situation:
        return CONVERSATION

    def situation(self) -> Situation:
        return CONVERSATION

    def thinks_when_quiet(self) -> bool:
        return False

    def handoff_scope(self, db: Session, user_id: uuid.UUID) -> None:
        return None

    def sweep(self, db: Session, at: float) -> list[str]:
        """Every SWEEP_SECONDS: each open disagreement not handed over yet wakes her mind once, in the
        conversation it was said in. Returns what happened."""
        from app.services import autonomous_speech, memory_conflicts

        if at - self._last_sweep < SWEEP_SECONDS:
            return []
        self._last_sweep = at
        now = datetime.fromtimestamp(at, timezone.utc)
        rows = memory_conflicts.due_for_mind(db, before=now, since=now - timedelta(hours=WATCH_HOURS))
        out = []
        for row in rows:
            conflict_id, user_id, conversation_id = row.id, row.user_id, row.conversation_id
            text, stated = conflict_text(row), row.stated
            if conversation_id is None:
                outcome = "no conversation to bring it to"
            else:
                outcome = autonomous_speech.submit(
                    db, user_id=user_id, activity_id=conversation_id, source=NAME,
                    event_id=f"conflict:{conflict_id}", text=text, reasons=[f"conflict: {stated}"[:300]],
                    commentary_mode="occasional", ttl=CONFLICT_TTL_SECONDS, premise={"conflict": conflict_id},
                    wake="conflict")
            # Handed over once: one that goes stale stays unresolved in memory, where recall shows it.
            memory_conflicts.mark_offered(db, conflict_id)
            db.commit()
            out.append(f"conflict {conflict_id.hex[:6]}: {outcome}")
        return out


SOURCE = ConversationEvents()
