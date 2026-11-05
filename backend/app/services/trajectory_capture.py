"""Collecting her trajectory from the game pack (docs/learning-system/POST-AUDIT-01-implementation.md, "Collection").

The pack records every client tick and keeps the chunks in a ring (``game_packs/minecraft/src/trajectory.js``).
This pass, on its own worker thread in desktop mode, pulls what is new (``GET /trajectory`` after the last chunk
it holds) and hands each chunk, stream header and reported drop to the capture writer
(``learning_capture.trajectory_chunk`` / ``trajectory_stream`` / ``trajectory_dropped``). Nothing here decodes a
chunk, makes a memory, or calls a model: routine ticks are evidence, not experiences.

- **Whose.** The pack is one Minecraft account on this machine: its environment's owner owns the trajectory. A
  chunk is filed under the session live when it was collected, if that session had started by the chunk's end;
  otherwise under none (she was in the world outside a session).
- **Cursor.** The last chunk queued. After a restart it starts from the newest chunk saved, and the pack serves
  what it still holds after that; a chunk saved twice is one row.
- **Backpressure.** Pulling stops while the writer's chunk queue has no room for another pull: the pack keeps
  buffering (its ring is bounded; what leaves it unread comes back as ``dropped``, a gap). The game never waits.
- **Integrity.** A chunk whose bytes don't match the pack's hash isn't saved; it's a gap.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import DESKTOP_TERMINAL_STATES, DesktopEnvironment, DesktopSession, TrajectoryChunk
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

_lock = threading.Lock()
_cursor: dict[str, Any] = {"stream": None, "seq": None, "loaded": False}
_headers: dict[str, str] = {}  # stream id -> digest of the header last queued (re-queued only when it changes)


def reset() -> None:
    """Forget the cursor (tests; a fresh process starts like this)."""
    with _lock:
        _cursor.update(stream=None, seq=None, loaded=False)
        _headers.clear()


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def _owner_and_session() -> tuple[uuid.UUID, DesktopSession | None] | None:
    with SessionLocal() as db:
        env = db.scalar(select(DesktopEnvironment).where(
            DesktopEnvironment.hosting_kind == "game_pack", DesktopEnvironment.revoked_at.is_(None)).limit(1))
        if env is None:
            return None
        session = db.scalar(select(DesktopSession).where(
            DesktopSession.environment_id == env.id, DesktopSession.state.not_in(DESKTOP_TERMINAL_STATES))
            .order_by(DesktopSession.started_at.desc()).limit(1))
        if session is not None:
            db.expunge(session)
        return env.owner_user_id, session


def _resume(user_id: uuid.UUID) -> None:
    """The newest chunk saved: where to start after a restart."""
    with SessionLocal() as db:
        row = db.execute(select(TrajectoryChunk.stream_id, TrajectoryChunk.seq).where(
            TrajectoryChunk.user_id == user_id).order_by(TrajectoryChunk.captured_at.desc(),
                                                         TrajectoryChunk.seq.desc()).limit(1)).first()
    with _lock:
        if row is not None:
            _cursor.update(stream=str(row.stream_id), seq=row.seq)
        _cursor["loaded"] = True


def _session_for(meta: dict[str, Any], session: DesktopSession | None) -> uuid.UUID | None:
    if session is None:
        return None
    try:
        ended = datetime.fromisoformat(str(meta["ended_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return session.id
    started = _aware(session.started_at)
    return session.id if started is None or ended >= started else None


def _header_digest(stream: dict[str, Any]) -> str:
    return hashlib.sha256(repr(sorted(stream.items())).encode()).hexdigest()


def run_once(client=None) -> bool:
    """One pass: pull what's new, hand it to the writer. True when something was collected (the worker loops
    again at once to catch up)."""
    from app.ai.desktop.pack_client import PackClient, PackError
    from app.services import learning_capture

    settings = get_settings()
    if not settings.desktop_agent_enabled:
        return False
    found = _owner_and_session()
    if found is None:
        return False
    user_id, session = found
    if not _cursor["loaded"]:
        _resume(user_id)
    client = client or PackClient.from_settings()
    svc = learning_capture.service()
    worked = False
    for _ in range(settings.trajectory_pull_calls):
        if not svc.room_for_chunks(settings.trajectory_pull_chunks):
            break  # the writer is behind: the pack keeps buffering meanwhile
        with _lock:
            stream, seq = _cursor["stream"], _cursor["seq"]
        try:
            body = client.trajectory(stream=stream, seq=seq, limit=settings.trajectory_pull_chunks)
        except PackError as exc:
            if exc.status not in (None, 404):
                log.warning("trajectory: the pack refused a pull: %s", exc)
            break
        run = body.get("run")
        coverage = body.get("coverage")
        for s in body.get("streams") or []:
            digest = _header_digest(s)
            if _headers.get(str(s.get("id"))) == digest:
                continue
            _headers[str(s.get("id"))] = digest
            learning_capture.trajectory_stream(user_id=user_id, stream=s, run_id=run, coverage=coverage)
        for drop in body.get("dropped") or []:
            learning_capture.trajectory_dropped(user_id=user_id, drop=drop)
        last = None
        for chunk in body.get("chunks") or []:
            last = chunk
            try:
                data = base64.b64decode(chunk.get("data") or "", validate=True)
            except ValueError:
                data = b""
            if not data or hashlib.sha256(data).hexdigest() != chunk.get("sha256"):
                svc.gap(user_id=user_id, record_kind="chunk",
                        record_id=learning_capture.chunk_id_for(chunk["stream"], chunk["seq"]), label="trajectory",
                        reason="unserializable", detail=f"stream {chunk.get('stream')} seq {chunk.get('seq')}: "
                                                        "bytes don't match the pack's hash; not saved")
                continue
            learning_capture.trajectory_chunk(user_id=user_id, game_session_id=_session_for(chunk, session),
                                              meta=chunk, data=data)
            worked = True
        if last is not None:
            with _lock:
                _cursor.update(stream=str(last["stream"]), seq=int(last["seq"]))
        if not body.get("more"):
            break
    return worked


__all__ = ["run_once", "reset"]
