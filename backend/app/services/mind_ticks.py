"""What her mind sees each second while a game pack session is live, and its record.

``build_tick`` assembles a tick from the pack's state and notices (read with the mind's own
notice cursor, so her hands' cursor is untouched). Her mind appends every tick it handles to
``MIND_TICK_PATH``: the rendered tick text a gate reads, its pressure and changes, and the raw
inputs (pack state, notices, plan, recent talk) so a tick can be re-rendered later. Nothing reads
the file back into play.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.desktop.pack_client import PackClient
from app.ai.mind.tick import TALK_KEPT, TICK_SCHEMA, TickInput
from app.core.config import get_settings
from app.db.models import ChatMessage, Conversation, DesktopSession
from app.services import desktop_commands, game_pack

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LIVE_STATES = ("running", "paused")
TALK_WINDOW = timedelta(minutes=10)

_lock = threading.Lock()


def tick_path() -> Path:
    path = Path(get_settings().mind_tick_path)
    return path if path.is_absolute() else BACKEND_ROOT / path


def live_sessions(db: Session) -> list[DesktopSession]:
    return list(db.scalars(select(DesktopSession).where(
        DesktopSession.state.in_(LIVE_STATES), DesktopSession.profile_id == game_pack.PACK_PROFILE_ID)))


def recent_talk(db: Session, user_id: uuid.UUID, now: datetime) -> list[dict]:
    """The user's last few messages with her, oldest first, from the last few minutes."""
    since = (now - TALK_WINDOW).replace(tzinfo=None)  # TimestampMixin columns are naive UTC
    rows = db.execute(
        select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(Conversation.user_id == user_id, ChatMessage.created_at >= since, ChatMessage.content != "")
        .order_by(ChatMessage.created_at.desc()).limit(TALK_KEPT)).all()
    return [{"role": role, "content": content, "at": at.replace(tzinfo=timezone.utc).timestamp()}
            for role, content, at in reversed(rows)]


def next_notices(pack: PackClient, cursors: dict, session_id: uuid.UUID) -> list[dict]:
    """The pack notices since this reader's last call (its own cursor: starts from now)."""
    seq = cursors.get(session_id)
    if seq is None:
        seq = pack.notices(0).get("next", 0)  # older notices aren't this tick's news
    batch = pack.notices(seq)
    cursors[session_id] = batch.get("next", seq)
    return batch.get("notices", [])


def build_tick(db: Session, session: DesktopSession, state: dict, notices: list[dict], at: float, *,
               thoughts: list[dict] | None = None, thinking: str | None = None) -> TickInput:
    from app.services import game_challenges, game_practice

    driver = desktop_commands.load_driver_state(session.id) or {}
    return TickInput(
        at=at, state=state, notices=notices, plan=session.plan, session_state=session.state,
        talk=recent_talk(db, session.owner_user_id, datetime.fromtimestamp(at, timezone.utc)),
        hands_note=driver.get("notes") or None, thoughts=thoughts or [], thinking=thinking,
        learning=game_practice.tick_line(db, session.id),
        challenge=game_challenges.tick_line(db, session.id),
    )


def fresh_previous(previous: TickInput | None, at: float) -> TickInput | None:
    """A long gap (pack down, paused worker) makes the next tick a fresh start."""
    return None if previous is None or at - previous.at > 30 else previous


def append_tick(session_id: uuid.UUID, number: int, tick: TickInput, text: str, pressure: str,
                changed: list[str], why: str, extra: dict | None = None) -> None:
    record = {
        "schema": TICK_SCHEMA, "ts": tick.at, "session_id": str(session_id), "tick": number,
        "pressure": pressure, "why": why, "changed": changed, "unchanged": not changed,
        "text": text,
        "input": {"state": tick.state, "notices": tick.notices, "plan": tick.plan,
                  "session_state": tick.session_state, "talk": tick.talk, "hands_note": tick.hands_note,
                  "thoughts": tick.thoughts, "thinking": tick.thinking, "learning": tick.learning},
        **(extra or {}),
    }
    path = tick_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")

