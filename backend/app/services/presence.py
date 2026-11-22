"""Whether the user is looking at her right now, on any of their apps (frontend-phone/PLAN_PHASE_0-3.md, phase 2).

Each open chat socket reports what it knows about itself (``sense`` frames): which app it is, whether it is on
screen, when the user last touched it (web: mouse/keyboard), and whether a live voice session is open. Kept in
Redis per socket with a short life, refreshed by the socket's heartbeat, removed when it closes.

"Present" means some socket of the user is visible and, for the web app, used in the last two minutes (an open
tab nobody is at doesn't count); for the phone, visible is enough. The push worker reads this to decide whether
a message of hers still needs to reach the phone. Facts about the apps only: nothing here changes what she says.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from functools import lru_cache

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# A web app counts as in use this long after the last mouse/keyboard input.
WEB_ACTIVE_SECONDS = 120
# A socket's entry lives this long without a refresh (the socket heartbeat is 30 s).
ENTRY_SECONDS = 75


@lru_cache(maxsize=4)
def _client_for(url: str) -> redis.Redis:
    return redis.from_url(url)


def _client() -> redis.Redis | None:
    url = get_settings().redis_url
    return _client_for(url) if url else None


def _key(user_id: uuid.UUID | str) -> str:
    return f"presence:{user_id}"


def entry_present(entry: dict, now: float) -> bool:
    """One socket's entry: is the user at it?"""
    if entry.get("expires", 0) < now or not entry.get("visible"):
        return False
    if entry.get("client") == "phone":
        return True
    active = entry.get("last_active_at")
    return active is not None and now - float(active) <= WEB_ACTIVE_SECONDS


def _expired(raw, now: float) -> bool:
    try:
        return json.loads(raw).get("expires", 0) < now
    except (TypeError, ValueError):
        return True


def _entries(client: redis.Redis, user_id) -> list[dict]:
    out = []
    for raw in (client.hgetall(_key(user_id)) or {}).values():
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out


def present(user_id: uuid.UUID | str, *, now: float | None = None) -> bool:
    """Whether the user is at one of their apps now. Unknown (no Redis) counts as not present: better a push
    they didn't need than a message they never hear about."""
    client = _client()
    if client is None:
        return False
    now = time.time() if now is None else now
    try:
        return any(entry_present(entry, now) for entry in _entries(client, user_id))
    except redis.RedisError:
        logger.warning("presence: unavailable for user %s", user_id)
        return False


def voice_live(user_id: uuid.UUID | str, *, now: float | None = None) -> bool:
    """Whether some open socket of the user has a live voice session (she may be about to say it aloud)."""
    client = _client()
    if client is None:
        return False
    now = time.time() if now is None else now
    try:
        return any(entry.get("expires", 0) >= now and entry.get("voice_live")
                   for entry in _entries(client, user_id))
    except redis.RedisError:
        return False


def phone_app(user_id: uuid.UUID | str, *, now: float | None = None) -> dict | None:
    """The phone app as its sockets say: ``app`` open (on screen) / background (connected, not on screen) /
    closed (no socket), and ``voice`` (a live voice session on the phone). None when Redis can't be read: unknown,
    never "closed"."""
    client = _client()
    if client is None:
        return None
    now = time.time() if now is None else now
    try:
        entries = [e for e in _entries(client, user_id) if e.get("client") == "phone" and e.get("expires", 0) >= now]
    except redis.RedisError:
        return None
    app = "open" if any(e.get("visible") for e in entries) else ("background" if entries else "closed")
    return {"app": app, "voice": any(e.get("voice_live") for e in entries)}


def report(user_id: uuid.UUID | str, socket_id: uuid.UUID | str, *, client: str, visible: bool | None,
           last_active_at: float | None, voice_live: bool, now: float | None = None) -> bool:
    """Record this socket's state. True when this made the user present where they weren't before (time to
    take the phone's notifications down)."""
    redis_client = _client()
    if redis_client is None:
        return False
    now = time.time() if now is None else now
    entry = {"client": client, "visible": bool(visible), "last_active_at": last_active_at,
             "voice_live": bool(voice_live), "expires": now + ENTRY_SECONDS}
    try:
        key = _key(user_id)
        stored = redis_client.hgetall(key) or {}
        stale = [field for field, raw in stored.items() if _expired(raw, now)]
        if stale:
            redis_client.hdel(key, *stale)
        before = any(entry_present(e, now) for e in _entries(redis_client, user_id))
        redis_client.hset(key, str(socket_id), json.dumps(entry))
        redis_client.expire(key, ENTRY_SECONDS * 2)
        return not before and entry_present(entry, now)
    except redis.RedisError:
        logger.warning("presence: could not record socket %s", socket_id)
        return False


def leave(user_id: uuid.UUID | str, socket_id: uuid.UUID | str) -> None:
    redis_client = _client()
    if redis_client is None:
        return
    try:
        redis_client.hdel(_key(user_id), str(socket_id))
    except redis.RedisError:
        pass
