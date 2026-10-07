"""API -> relay -> controller commands, and the controller's decision reports.

The API and the relay are separate processes. Commands ride a short Redis
stream per environment (``desktop:commands:<env>``), read by the relay
connection that currently serves that environment. They are deliberately not
durable: the relay reads only entries that arrive while it is connected, so a
decision sent during a disconnect is dropped, never replayed later (plan §6:
no durable raw-input replay). Stops don't depend on this channel either: the
lease in every heartbeat reply is the backstop.

Reports (what happened to each decision) are a capped Redis list per session,
for the card's activity view; high-rate decision events never touch Postgres
(plan §17 Events).
"""
from __future__ import annotations

import json
import logging
import time
import uuid

import redis

from app.core.config import get_settings
from app.services import desktop_preview

log = logging.getLogger(__name__)

STREAM_MAXLEN = 200
REPORTS_KEEP = 30
REPORTS_TTL_SECONDS = 3600


def stream_key(environment_id: uuid.UUID | str) -> str:
    return f"desktop:commands:{environment_id}"


def reports_key(session_id: uuid.UUID | str) -> str:
    return f"desktop:decisions:{session_id}"


def available() -> bool:
    return desktop_preview.redis_client() is not None


def publish_decision(
    environment_id: uuid.UUID, session_id: uuid.UUID, decision_id: str, generation: int, decision: dict
) -> None:
    client = desktop_preview.redis_client()
    if client is None:
        raise RuntimeError("Redis is not configured")
    client.xadd(stream_key(environment_id), {
        "kind": "decision", "session_id": str(session_id), "decision_id": decision_id,
        "generation": str(generation), "decision": json.dumps(decision),
    }, maxlen=STREAM_MAXLEN, approximate=True)
    record_report(session_id, {"decision_id": decision_id, "state": "sent", "intent": decision.get("intent", "")})


def publish_cancel(environment_id: uuid.UUID, session_id: uuid.UUID, reason: str) -> None:
    """Best effort: a lost cancel only means waiting for the lease to lapse."""
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.xadd(stream_key(environment_id), {
            "kind": "cancel", "session_id": str(session_id), "reason": reason,
        }, maxlen=STREAM_MAXLEN, approximate=True)
    except redis.RedisError:
        log.warning("desktop: could not publish cancel for session %s", session_id)


def publish_refresh(environment_id: uuid.UUID) -> None:
    """Ask the relay to send the lease now (e.g. right after a resume)."""
    client = desktop_preview.redis_client()
    if client is None:
        return
    try:
        client.xadd(stream_key(environment_id), {"kind": "refresh"}, maxlen=STREAM_MAXLEN, approximate=True)
    except redis.RedisError:
        pass  # the next heartbeat carries it anyway


def record_report(session_id: uuid.UUID | str, report: dict) -> None:
    client = desktop_preview.redis_client()
    if client is None:
        return
    entry = json.dumps({**report, "at": time.time()})
    with client.pipeline() as pipe:
        pipe.lpush(reports_key(session_id), entry)
        pipe.ltrim(reports_key(session_id), 0, REPORTS_KEEP - 1)
        pipe.expire(reports_key(session_id), REPORTS_TTL_SECONDS)
        pipe.execute()


def recent_reports(session_id: uuid.UUID) -> list[dict]:
    """Newest first. A decision appears as "sent", then again with its outcome."""
    client = desktop_preview.redis_client()
    if client is None:
        return []
    return [json.loads(item) for item in client.lrange(reports_key(session_id), 0, REPORTS_KEEP - 1)]


def async_client():
    """For the relay's command reader (redis.asyncio), or None without Redis."""
    url = get_settings().redis_url
    if not url:
        return None
    import redis.asyncio as aioredis

    return aioredis.from_url(url)
