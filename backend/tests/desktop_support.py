"""Shared helpers for the desktop agent tests.

``isolated_redis`` keeps every desktop test off the developer's real Redis
(``REDIS_URL`` in ``.env``): the sync client becomes an in-memory stand-in and
the relay's async command reader is switched off. Tests that need the real
command path opt in with ``real_redis`` (``TEST_REDIS_URL``, a throwaway
server, never the dev one).
"""
from __future__ import annotations

import os
import threading

import pytest

from app.core.config import get_settings
from app.services import desktop_commands, desktop_preview


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.lists: dict[str, list[bytes]] = {}
        self.streams: dict[str, list[dict]] = {}
        self.lock = threading.Lock()

    def pipeline(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self):
        pass

    def expire(self, key, seconds):
        pass

    @staticmethod
    def _b(value) -> bytes:
        return value.encode() if isinstance(value, str) else value

    def hset(self, key, mapping):
        with self.lock:
            self.hashes[key] = {self._b(k): self._b(v) for k, v in mapping.items()}

    def hgetall(self, key):
        with self.lock:
            return dict(self.hashes.get(key, {}))

    def delete(self, key):
        with self.lock:
            self.hashes.pop(key, None)
            self.lists.pop(key, None)

    def lpush(self, key, value):
        with self.lock:
            self.lists.setdefault(key, []).insert(0, self._b(value))

    def ltrim(self, key, start, stop):
        with self.lock:
            self.lists[key] = self.lists.get(key, [])[start:stop + 1]

    def lrange(self, key, start, stop):
        with self.lock:
            return list(self.lists.get(key, [])[start:stop + 1])

    def xadd(self, key, fields, maxlen=None, approximate=True):
        with self.lock:
            self.streams.setdefault(key, []).append(dict(fields))


@pytest.fixture(autouse=True)
def isolated_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(desktop_preview, "redis_client", lambda: fake)
    monkeypatch.setattr(desktop_commands, "async_client", lambda: None)
    return fake


@pytest.fixture
def real_redis(monkeypatch):
    """A real, throwaway Redis for the command path (relay XREAD)."""
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL (a throwaway Redis) to run the input end-to-end tests")
    import redis

    client = redis.from_url(url)
    client.flushdb()
    settings = get_settings().model_copy(update={"redis_url": url})
    monkeypatch.setattr(desktop_preview, "redis_client", lambda: client)
    monkeypatch.setattr(desktop_commands, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop_commands, "async_client", _real_async(url))
    yield client
    client.flushdb()


def _real_async(url):
    def make():
        import redis.asyncio as aioredis

        return aioredis.from_url(url)
    return make
