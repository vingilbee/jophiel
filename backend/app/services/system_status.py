"""Whether her parts are alive: worker heartbeats, grouped into the seven parts the System page shows.

Every worker thread runs the same loop (``workers/runner._work_loop``), which calls ``beat`` before and after
each ``run_once``. A beat is one Redis hash field per worker (``system:workers``): when it was last seen, whether
it's inside a job right now, and its last error. Beats are throttled, carry no user data, and never raise: a
missing Redis only means the page can't tell.

``status`` reads them back per part. A worker counts as seen while its last beat is younger than three of its
loop intervals (plus a margin), or while it's inside a job that started less than ``BUSY_LIMIT`` ago, since a
long model call beats only when it ends.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import redis

from app.core.config import get_settings

KEY = "system:workers"
BUSY_LIMIT = 15 * 60  # a job running longer than this no longer proves the worker is alive
ERROR_WINDOW = 120  # an error this recent makes the part "failing"
_MIN_GAP = 1.0  # at most one unchanged beat per worker per second


@dataclass(frozen=True)
class Part:
    id: str
    label: str
    workers: tuple[str, ...]
    desktop_only: tuple[str, ...] = ()  # workers that run only with DESKTOP_AGENT_ENABLED
    configured: tuple[tuple[str, str], ...] = ()  # (worker, setting): workers that run only when that setting is on


# The seven parts and the worker threads that are them (names from workers/runner.py). Her mind's evaluations run
# where the words come out (the chat worker: every wake is one evaluation there); its wakes and background thinking
# are the coordinator's, its background thoughts the mind worker's (the phone's ticks too). The phone's own thread
# is the push worker; the rest of the phone (its reports, her replies there and their check) runs in the request
# handlers and her mind's evaluations.
PARTS: tuple[Part, ...] = (
    Part("mouth", "Mouth", ("chat",), desktop_only=("agent-events",), configured=(("whatsapp", "whatsapp_enabled"),)),
    Part("mind", "Mind", ("coordinator", "mind")),
    Part("hands", "Hands", (), desktop_only=("desktop-driver", "game-practice")),
    Part("memory", "Memory", ("memory", "recall-embeddings", "file-deletion")),
    Part("observation", "Observation", ("observation",)),
    Part("learning", "Learning", ("learning", "practice"), desktop_only=("trajectory",)),
    Part("phone", "Phone", ("push",)),
)

_local = threading.local()
_state: dict[str, dict[str, Any]] = {}  # this process's view of each of its workers (one thread each)
_state_lock = threading.Lock()


def _client() -> redis.Redis | None:
    url = get_settings().redis_url
    if not url:
        return None
    client = getattr(_local, "client", None)
    if client is None:
        client = redis.from_url(url, socket_connect_timeout=0.3, socket_timeout=0.5)
        _local.client = client
    return client


def beat(name: str, *, interval: float, busy: bool = False, worked: bool = False, error: str | None = None) -> None:
    """Record that worker ``name`` is alive (``busy``: a job just started). Never raises."""
    now = time.time()
    with _state_lock:
        entry = _state.setdefault(name, {"seen": 0.0, "busy_since": None, "worked": None, "error_at": None,
                                         "error": None})
        changed = busy != (entry["busy_since"] is not None) or worked or error is not None
        if not changed and now - entry["seen"] < _MIN_GAP:
            return
        entry.update(seen=now, interval=interval, busy_since=now if busy else None)
        if worked:
            entry["worked"] = now
        if error is not None:
            entry.update(error_at=now, error=error[:300])
        payload = json.dumps(entry)
    try:
        client = _client()
        if client is not None:
            client.hset(KEY, name, payload)
    except Exception:
        pass


def _worker_view(name: str, entry: dict | None, now: float) -> dict[str, Any]:
    if not entry:
        return {"name": name, "state": "down", "seen_ago": None, "busy": False, "error": None}
    interval = float(entry.get("interval") or 1.0)
    seen_ago = now - float(entry.get("seen") or 0)
    busy_since = entry.get("busy_since")
    busy = busy_since is not None and now - float(busy_since) < BUSY_LIMIT
    alive = busy or seen_ago < 3 * interval + 5
    error_at = entry.get("error_at")
    failing = alive and error_at is not None and now - float(error_at) < ERROR_WINDOW
    state = "failing" if failing else ("busy" if alive and busy else ("alive" if alive else "down"))
    return {"name": name, "state": state, "seen_ago": round(seen_ago, 1), "busy": busy,
            "worked_ago": round(now - float(entry["worked"]), 1) if entry.get("worked") else None,
            "error": entry.get("error") if failing else None}


def _part_state(workers: list[dict[str, Any]]) -> str:
    if not workers:
        return "off"
    states = {w["state"] for w in workers}
    if "failing" in states:
        return "failing"
    if states == {"down"}:
        return "down"
    if "down" in states:
        return "partial"
    return "busy" if "busy" in states else "alive"


def status() -> dict[str, Any]:
    """Every part's state, from its workers' beats (``known: false`` when Redis can't be read)."""
    settings = get_settings()
    now = time.time()
    try:
        client = _client()
        raw = client.hgetall(KEY) if client is not None else None
    except Exception:
        raw = None
    entries = {k.decode() if isinstance(k, bytes) else k: json.loads(v) for k, v in (raw or {}).items()}
    parts = []
    for part in PARTS:
        names = list(part.workers) + (list(part.desktop_only) if settings.desktop_agent_enabled else [])
        names += [name for name, setting in part.configured if getattr(settings, setting, False)]
        workers = [_worker_view(n, entries.get(n), now) for n in names]
        parts.append({"id": part.id, "label": part.label, "state": _part_state(workers), "workers": workers})
    return {"known": raw is not None, "at": now, "desktop": settings.desktop_agent_enabled, "parts": parts}
