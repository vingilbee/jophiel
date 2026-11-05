"""Signals between her parts: one small record each time a part hands work to another (the System page's pulses).

``emit(user_id, "cognition", "mouth", "decision")`` appends ``{from, to, kind, ref, at}`` to that user's Redis
stream (``system:signals:<user>``, the last ``KEEP`` kept), so the owner's page can draw a pulse along the wire
and replay what happened while it was closed. Signals carry ids and part names only, never text. They are a
picture of the system, not a record anything reads back: no other code depends on them.

``emit`` never raises and never waits long: the same hand-off repeated within ``REPEAT_SECONDS`` is one signal,
and after Redis fails it stays quiet for ``BACKOFF_SECONDS`` instead of slowing every caller.

The wires a signal may travel are the System page's (``frontend/src/components/system/anatomy.ts`` ``WIRES``);
``WIRES`` here is the same list, and anything else is dropped.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import redis

from app.core.config import get_settings

KEEP = 400
TTL_SECONDS = 3 * 24 * 3600  # a user who stops using her leaves no stream behind
REPEAT_SECONDS = 1.5
BACKOFF_SECONDS = 10.0

WIRES = frozenset({
    ("memory", "cognition"), ("cognition", "mouth"), ("cognition", "mind"), ("mind", "mouth"), ("mind", "memory"),
    ("mind", "hands"), ("mouth", "memory"), ("mouth", "observation"), ("hands", "observation"),
    ("observation", "memory"), ("observation", "learning"), ("hands", "learning"), ("learning", "memory"),
    ("memory", "hands"), ("memory", "mind"),
})

_local = threading.local()
_lock = threading.Lock()
_recent: dict[tuple[str, str, str, str], float] = {}
_down_until = 0.0


def key(user_id: uuid.UUID | str) -> str:
    return f"system:signals:{user_id}"


def _client() -> redis.Redis | None:
    url = get_settings().redis_url
    if not url:
        return None
    client = getattr(_local, "client", None)
    if client is None:
        client = redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.3)
        _local.client = client
    return client


def emit(user_id: uuid.UUID | str | None, src: str, dst: str, kind: str, ref: Any = None) -> None:
    """Record one hand-off from part ``src`` to part ``dst`` for ``user_id``. Never raises."""
    global _down_until
    if user_id is None or (src, dst) not in WIRES:
        return
    now = time.time()
    dedupe = (str(user_id), src, dst, kind)
    with _lock:
        if now < _down_until or now - _recent.get(dedupe, 0.0) < REPEAT_SECONDS:
            return
        _recent[dedupe] = now
        if len(_recent) > 2000:
            for k in [k for k, at in _recent.items() if now - at > REPEAT_SECONDS]:
                _recent.pop(k, None)
    try:
        client = _client()
        if client is None:
            return
        fields = {"from": src, "to": dst, "kind": kind, "at": f"{now:.3f}"}
        if ref is not None:
            fields["ref"] = str(ref)[:80]
        pipe = client.pipeline(transaction=False)
        pipe.xadd(key(user_id), fields, maxlen=KEEP, approximate=True)
        pipe.expire(key(user_id), TTL_SECONDS)
        pipe.execute()
    except Exception:
        with _lock:
            _down_until = time.time() + BACKOFF_SECONDS


def _decode(entry_id: Any, fields: dict) -> dict[str, Any]:
    d = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
         for k, v in fields.items()}
    return {"id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id, "from": d.get("from"),
            "to": d.get("to"), "kind": d.get("kind"), "ref": d.get("ref"), "at": float(d.get("at") or 0)}


def recent(user_id: uuid.UUID, *, limit: int = 100) -> list[dict[str, Any]]:
    """The newest signals, oldest first ([] when Redis can't be read)."""
    try:
        client = _client()
        rows = client.xrevrange(key(user_id), count=limit) if client is not None else []
    except Exception:
        return []
    return [_decode(i, f) for i, f in reversed(rows)]


__all__ = ["emit", "recent", "key", "WIRES", "_decode"]
