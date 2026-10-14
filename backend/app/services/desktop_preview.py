"""The game preview: the latest frame per environment, briefly, in Redis.

The relay (which receives frames) and the API (which serves the card) are
separate processes, so the one latest frame lives in Redis with a short TTL:
a sense, not a recording (plan §23). Nothing here touches the image store,
the database, or memory. Without Redis there is simply no preview.

A stored frame records the capture epoch and controller generation it came
from. The API serves it only if both are still current, so a frame that was
in flight when the owner switched capture off is never shown, even if it
lands after the switch.
"""
from __future__ import annotations

import io
import json
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

import redis
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from desktop_controller.link import FrameHeader

FRAME_TTL_SECONDS = 10
# The last few seconds of frames, so Columbina can look at the moment the user
# was talking about rather than the newest picture (~15 s at 2 fps). Short-lived
# like the frames themselves: a sense, not a recording.
HISTORY_KEEP = 30
HISTORY_TTL_SECONDS = 20
# A frame older than this is "stale" on the card, not live.
STALE_AFTER_SECONDS = 5.0


@dataclass(frozen=True)
class StoredFrame:
    jpeg: bytes
    seq: int
    width: int
    height: int
    epoch: int
    generation: int
    received_at: float  # Unix seconds, on the Mac
    captured_ms: int = 0  # controller's monotonic clock: only compare with the same controller
    context: str | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.received_at)


@lru_cache
def _client(url: str) -> redis.Redis:
    return redis.from_url(url)


def redis_client() -> redis.Redis | None:
    """The shared desktop Redis client (preview frames, commands), or None."""
    url = get_settings().redis_url
    return _client(url) if url else None


def _key(environment_id: uuid.UUID, kind: str = "preview") -> str:
    return f"desktop:{kind}:{environment_id}"


def _history_key(environment_id: uuid.UUID, kind: str = "preview") -> str:
    return f"desktop:history:{kind}:{environment_id}"


def available() -> bool:
    return redis_client() is not None


def check_jpeg(header: FrameHeader, jpeg: bytes) -> bool:
    """Frames come from the guest: untrusted. Accept only a JPEG whose real
    size matches its header (and so the header's bounds)."""
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.format != "JPEG" or image.size != (header.width, header.height):
                return False
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return False
    return True


def store(environment_id: uuid.UUID, header: FrameHeader, jpeg: bytes, *, generation: int) -> None:
    """Keep the latest frame of its kind ("preview" for the card,
    "observation" for the driver model)."""
    client = redis_client()
    if client is None:
        return
    meta = {
        "seq": header.seq, "width": header.width, "height": header.height,
        "epoch": header.gate_epoch, "generation": generation, "received_at": time.time(),
        "captured_ms": header.captured_ms, "context": header.context,
    }
    key = _key(environment_id, header.kind)
    history = _history_key(environment_id, header.kind)
    with client.pipeline() as pipe:
        pipe.hset(key, mapping={"meta": json.dumps(meta), "jpeg": jpeg})
        pipe.expire(key, FRAME_TTL_SECONDS)
        pipe.lpush(history, json.dumps(meta).encode() + b"\n" + jpeg)
        pipe.ltrim(history, 0, HISTORY_KEEP - 1)
        pipe.expire(history, HISTORY_TTL_SECONDS)
        pipe.execute()


def load(environment_id: uuid.UUID, kind: str = "preview") -> StoredFrame | None:
    client = redis_client()
    if client is None:
        return None
    raw = client.hgetall(_key(environment_id, kind))
    if not raw or b"meta" not in raw or b"jpeg" not in raw:
        return None
    meta = json.loads(raw[b"meta"])
    return StoredFrame(jpeg=raw[b"jpeg"], **meta)


def frame_at(environment_id: uuid.UUID, at: float, kind: str = "preview") -> StoredFrame | None:
    """The frame showing the moment ``at`` (Unix seconds): the newest one
    received by then, else the oldest kept. None when there is no history."""
    client = redis_client()
    if client is None:
        return None
    frames = []
    for item in client.lrange(_history_key(environment_id, kind), 0, -1):  # newest first
        meta, _, jpeg = item.partition(b"\n")
        frames.append(StoredFrame(jpeg=jpeg, **json.loads(meta)))
    if not frames:
        return None
    return next((f for f in frames if f.received_at <= at), frames[-1])


def clear(environment_id: uuid.UUID) -> None:
    """Drop every stored frame (preview and observation) for the environment."""
    client = redis_client()
    if client is not None:
        try:
            client.delete(_key(environment_id, "preview"))
            client.delete(_key(environment_id, "observation"))
            client.delete(_history_key(environment_id, "preview"))
            client.delete(_history_key(environment_id, "observation"))
        except redis.RedisError:
            pass  # the epoch check still keeps a leftover frame from being used
