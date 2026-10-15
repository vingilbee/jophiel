"""Recording what her mind would see each second, while a game pack session is live.

One JSON line per tick in ``MIND_TICK_PATH``: the rendered tick text a gate reads,
its pressure and changes, and the raw inputs (pack state, notices, plan, recent
talk) so a tick can be re-rendered later. Read-only towards the game: it polls
the pack's state and notices (with its own notice cursor, so her hands' cursor is
untouched), and nothing reads the file back into play.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.desktop.pack_client import PackClient, PackError
from app.ai.mind.tick import TALK_KEPT, TICK_SCHEMA, TickInput, render
from app.core.config import get_settings
from app.db.models import ChatMessage, Conversation, DesktopSession
from app.db.session import SessionLocal
from app.services import desktop_commands, game_pack

log = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LIVE_STATES = ("running", "paused")
TALK_WINDOW = timedelta(minutes=10)

_lock = threading.Lock()
_previous: dict[uuid.UUID, TickInput] = {}
_notice_seq: dict[uuid.UUID, int] = {}
_tick_no: dict[uuid.UUID, int] = {}


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


def record_ticks(*, pack: PackClient | None = None, now: float | None = None) -> int:
    """One tick per live pack session; returns how many were written."""
    settings = get_settings()
    if not (settings.mind_tick_recording_enabled and settings.desktop_agent_enabled):
        return 0
    with SessionLocal() as db:
        sessions = live_sessions(db)
        if not sessions:
            _previous.clear()
            return 0
        pack = pack or PackClient.from_settings()
        try:
            state = pack.state()
        except PackError:
            return 0  # the pack is down: no tick, and the next one starts fresh
        at = now if now is not None else time.time()
        written = 0
        for session in sessions:
            try:
                written += _record(db, pack, session, state, at)
            except Exception:  # noqa: BLE001 - recording must never get in the way
                log.exception("mind tick for session %s failed", session.id)
        return written


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
    driver = desktop_commands.load_driver_state(session.id) or {}
    return TickInput(
        at=at, state=state, notices=notices, plan=session.plan, session_state=session.state,
        talk=recent_talk(db, session.owner_user_id, datetime.fromtimestamp(at, timezone.utc)),
        hands_note=driver.get("notes") or None, thoughts=thoughts or [], thinking=thinking,
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
                  "thoughts": tick.thoughts, "thinking": tick.thinking},
        **(extra or {}),
    }
    path = tick_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def _record(db: Session, pack: PackClient, session: DesktopSession, state: dict, at: float) -> int:
    tick = build_tick(db, session, state, next_notices(pack, _notice_seq, session.id), at)
    previous = fresh_previous(_previous.get(session.id), at)
    text, pressure, changed, why = render(tick, previous)
    number = _tick_no.get(session.id, 0) + 1
    append_tick(session.id, number, tick, text, pressure, changed, why)
    _previous[session.id] = tick
    _tick_no[session.id] = number
    return 1
