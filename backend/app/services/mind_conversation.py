"""A conversation as a source of her mind, in shadow (docs/COLUMBINA_INNER_STATE_PLAN.md, step 3).

It proves the general mind needs no game: no ``DesktopSession``, no pack, no body. After her
reply, a question of theirs that is still open (the simple-cognition notes in
services/cognitive_live.py: ``their_open_question`` closes when a reply answers it) is a problem
worth revisiting. It goes through the same attention and hard thinking as a game problem, but
its attention is its own and its thought is only written to ``MIND_SHADOW_LOG_PATH``: never
saved, said, shown, or sent anywhere. Off unless ``MIND_CONVERSATION_SHADOW_ENABLED``.

Event rhythm: nothing polls it; ``observe`` is called once per reply.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.ai.mind.contracts import Moment, Rhythm, Scope
from app.ai.mind.hard import HardInput, Problem
from app.ai.mind.situation import Situation
from app.core.config import get_settings

log = logging.getLogger(__name__)
NAME = "conversation"
CONVERSATION = Situation(NAME, "talking with {user} in chat")


def enabled() -> bool:
    return bool(get_settings().mind_conversation_shadow_enabled)


def open_problem(conversation_id: uuid.UUID, notes: list[dict]) -> Problem | None:
    """Their newest question still open after her reply, as a problem (None if there's none)."""
    open_ = [n for n in notes if n.get("kind") == "their_open_question" and n.get("text")]
    if not open_:
        return None
    note = open_[-1]
    text = " ".join(str(note["text"]).split())[:300]
    return Problem("open", f'They asked: "{text}". Your reply left it open.',
                   topic=f"still open: {text}"[:200], query=text,
                   anchor={"note": note.get("id"), "event": f"{conversation_id}:{note.get('id')}"})


def _note_open(user_id: uuid.UUID, conversation_id: uuid.UUID, note_id: str | None) -> bool:
    from app.services import cognitive_live

    notes = cognitive_live.load_state(user_id, conversation_id).get("notes") or []
    return any(n.get("id") == note_id for n in notes)


class ConversationSource:
    name = NAME
    rhythm = Rhythm("event")
    shadow = True

    def enabled(self) -> bool:
        return enabled()

    def gate_situation(self) -> Situation:
        return CONVERSATION

    def situation(self) -> Situation:
        return CONVERSATION

    def new_state(self) -> None:
        return None

    def worse(self, now: Any, before: Any) -> bool:
        return False

    def problems(self, scope: Scope, state: Any, moment: Moment, *, gate_hard: bool) -> list[Problem]:
        return []

    def drop_waiting(self, scope: Scope, moment: Moment, problem: Problem) -> str | None:
        return None

    def quiet_topics(self, user: str) -> tuple[str, ...]:
        return ()

    def gather(self, db: Session, scope: Scope, problem: Problem, base: HardInput) -> tuple[HardInput, Any]:
        from app.services import mind_hard

        now = datetime.now(timezone.utc)
        return replace(base, conversation=mind_hard.conversation(db, scope.user_id, now, scope.activity)), None

    def dropped(self, db: Session, scope: Scope, problem: Problem, cancelled: str | None) -> str | None:
        if cancelled:
            return cancelled
        if problem.anchor.get("note") and not _note_open(scope.user_id, scope.activity, problem.anchor["note"]):
            return "it's been answered"
        return None

    def outdated(self, db: Session, scope: Scope, problem: Problem, token: Any) -> list[str]:
        return []

    def live(self, db: Session, scope: Scope) -> bool:
        return True

    def changed_since(self, db: Session, token: Any) -> list[str]:
        return []

    def current_goal(self, db: Session, scope: Scope) -> str | None:
        return None

    def speaking(self, db: Session, scope: Scope) -> dict:
        return {}

    def body(self, scope: Scope) -> None:
        return None

    def thought_session(self, scope: Scope) -> None:
        return None

    def handoff_scope(self, db: Session, user_id: uuid.UUID) -> None:
        return None


SOURCE = ConversationSource()


def observe(*, user_id: uuid.UUID, conversation_id: uuid.UUID, notes: list[dict]) -> str | None:
    """After a reply: offer the open question, if any, to her (shadow) slow thinking.
    Never raises; returns what happened."""
    if not enabled():
        return None
    try:
        from app.services import mind_hard

        problem = open_problem(conversation_id, notes)
        if problem is None:
            return None
        return mind_hard.offer(SOURCE, Scope(user_id, NAME, conversation_id), problem, time.time())
    except Exception:  # noqa: BLE001 - shadow must never touch the turn
        log.exception("mind_conversation.observe failed")
        return None
