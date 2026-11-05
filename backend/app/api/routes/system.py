"""Her parts as one system (the System page; owner only).

    GET /api/system/status          every part's state, from its workers' heartbeats
    GET /api/system/parts/{part}    one part's recent activity (the owner's own)
    GET /api/system/watch           the watch log: what she thought, what memory captured, her hands' goal
    GET /api/system/errors          what went wrong lately, across her parts
    GET /api/system/signals         the newest hand-offs between her parts (the owner's own), oldest first
    GET /api/system/signals/stream  the same, live (server-sent events; resumes after Last-Event-ID)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_owner
from app.db.models import User
from app.db.session import get_db
from app.core.config import get_settings
from app.services import system_parts, system_signals, system_status

# The owner only (deps.require_owner): her insides are not for other users.
router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_owner)])


@router.get("/status")
def get_status() -> dict[str, Any]:
    return system_status.status()


@router.get("/watch")
def get_watch(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return system_parts.highlights(db, user.id)


@router.get("/errors")
def get_errors(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return system_parts.errors(db, user.id, system_status.status())


@router.get("/parts/{part}")
def get_part(part: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    feed = system_parts.feed(db, user.id, part)
    if feed is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"No feed for part {part!r}."})
    return feed


@router.get("/signals")
def get_signals(limit: int = Query(default=100, ge=1, le=system_signals.KEEP),
                user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return system_signals.recent(user.id, limit=limit)


@router.get("/signals/stream")
async def stream_signals(request: Request, user: User = Depends(get_current_user)) -> StreamingResponse:
    """New signals as they're emitted. A reconnect sends Last-Event-ID and misses nothing still kept."""
    url = get_settings().redis_url
    if not url:
        raise HTTPException(status_code=503, detail={"code": "no_redis", "message": "Signals need Redis."})
    stream = system_signals.key(user.id)
    last = request.headers.get("last-event-id") or "$"

    async def events():
        nonlocal last
        client = aioredis.from_url(url)
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    found = await client.xread({stream: last}, count=50, block=15_000)
                except Exception:  # noqa: BLE001 - Redis went away: tell the page, then try again
                    yield ": redis unavailable\n\n"
                    await asyncio.sleep(2)
                    continue
                if not found:
                    yield ": keep-alive\n\n"  # also lets a closed tab be noticed
                    continue
                for _, entries in found:
                    for entry_id, fields in entries:
                        signal = system_signals._decode(entry_id, fields)
                        last = signal["id"]
                        yield f"id: {signal['id']}\ndata: {json.dumps(signal)}\n\n"
        finally:
            await client.aclose()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
