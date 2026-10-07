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

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.received_at)


@lru_cache
def _client(url: str) -> redis.Redis:
    return redis.from_url(url)


def _redis() -> redis.Redis | None:
    url = get_settings().redis_url
    return _client(url) if url else None


def _key(environment_id: uuid.UUID) -> str:
    return f"desktop:preview:{environment_id}"


def available() -> bool:
    return _redis() is not None


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
    client = _redis()
    if client is None:
        return
    meta = {
        "seq": header.seq, "width": header.width, "height": header.height,
        "epoch": header.gate_epoch, "generation": generation, "received_at": time.time(),
    }
    with client.pipeline() as pipe:
        pipe.hset(_key(environment_id), mapping={"meta": json.dumps(meta), "jpeg": jpeg})
        pipe.expire(_key(environment_id), FRAME_TTL_SECONDS)
        pipe.execute()


def load(environment_id: uuid.UUID) -> StoredFrame | None:
    client = _redis()
    if client is None:
        return None
    raw = client.hgetall(_key(environment_id))
    if not raw or b"meta" not in raw or b"jpeg" not in raw:
        return None
    meta = json.loads(raw[b"meta"])
    return StoredFrame(jpeg=raw[b"jpeg"], **meta)


def clear(environment_id: uuid.UUID) -> None:
    client = _redis()
    if client is not None:
        try:
            client.delete(_key(environment_id))
        except redis.RedisError:
            pass  # the epoch check still keeps a leftover frame from being served
