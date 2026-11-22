"""HTTP clients for OpenAI calls that sit in front of a reply (her chat model, memory's embedding).

Two things the defaults get wrong for a conversation:

- **Idle connections close after 5 s** (httpx's and the OpenAI SDK's default). People take longer than that to
  write back, so most turns paid a new TLS handshake to the embedding and the chat model (~100-350 ms each).
  ``KEEPALIVE_SECONDS`` keeps them; OpenAI itself drops idle connections after about a minute, which the chat
  worker covers by keeping its connections warm for a while after a turn (``workers/chat.py``).
- **An async connection belongs to the event loop that opened it.** The async client here keeps one connection
  pool per running loop, so one cached client is safe to share between the chat worker's long-lived loop, the
  API's loop and short-lived loops of other workers; each reuses only its own connections. A short-lived loop
  runs under ``run`` (below), which closes that loop's pools before the loop ends: an idle pooled connection
  holds its loop, so a pool left behind would keep its socket open for the worker's lifetime. Pools of loops
  closed some other way are dropped the next time any pool is looked up.
- **Proxies from the environment** (``HTTP(S)_PROXY``, ``ALL_PROXY``, ``NO_PROXY``): httpx reads them only when
  it builds its own transport. The sync client keeps httpx's transport (only the limits change); the async
  client mounts its per-loop pools along the same routes httpx would, read once when the client is built.
- **A streamed reply's connection was never reused**: the SDK closes a stream with its last bytes unread
  (``_ReadTailOnClose``).
"""
from __future__ import annotations

import asyncio
import weakref
from collections.abc import Coroutine
from typing import Any, TypeVar

import httpx
from httpx._utils import get_environment_proxies  # what httpx itself mounts when it builds its transport
# The SDK imports its resources lazily, on first use. The chat worker's warm-up touches them from its own threads
# while a turn may be doing the same: two threads importing one module at once can fail ("KeyError:
# 'openai.resources'", the embedding lost for that turn). Imported here, once, when this module loads.
import openai.resources.chat  # noqa: F401
import openai.resources.embeddings  # noqa: F401
import openai.resources.models  # noqa: F401
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

KEEPALIVE_SECONDS = 120.0
LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100, keepalive_expiry=KEEPALIVE_SECONDS)


T = TypeVar("T")
_ALL: weakref.WeakSet[_PerLoopTransport] = weakref.WeakSet()


class _PerLoopTransport(httpx.AsyncBaseTransport):
    """One pooled transport per running event loop (a pool can't be used from another loop), direct or via
    ``proxy``."""

    def __init__(self, proxy: httpx.Proxy | None = None) -> None:
        self._proxy = proxy
        # A plain dict, not weak keys: a pooled connection references its loop, so a weak key would never go.
        self._by_loop: dict[asyncio.AbstractEventLoop, httpx.AsyncHTTPTransport] = {}
        _ALL.add(self)

    def _transport(self) -> httpx.AsyncHTTPTransport:
        loop = asyncio.get_running_loop()
        transport = self._by_loop.get(loop)
        if transport is None:
            # Pools of loops already closed can't be closed from here; dropped, the collector closes their sockets.
            for closed in [other for other in self._by_loop if other.is_closed()]:
                del self._by_loop[closed]
            transport = self._by_loop[loop] = httpx.AsyncHTTPTransport(limits=LIMITS, proxy=self._proxy)
        return transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._transport().handle_async_request(request)
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            response.stream = _ReadTailOnClose(response.stream)
        return response

    async def aclose(self) -> None:
        transport = self._by_loop.pop(asyncio.get_running_loop(), None)
        if transport is not None:
            await transport.aclose()


async def close_loop_pools() -> None:
    """Close the running loop's pools in every async client here (before a short-lived loop ends)."""
    for per_loop in list(_ALL):
        try:
            await per_loop.aclose()
        except Exception:  # noqa: BLE001 - closing is best effort; the rest still close
            pass


def run(main: Coroutine[Any, Any, T]) -> T:
    """``asyncio.run`` for a short-lived loop that may call OpenAI: its connections are closed before it ends."""
    async def wrapped() -> T:
        try:
            return await main
        finally:
            await close_loop_pools()
    return asyncio.run(wrapped())


class _ReadTailOnClose(httpx.AsyncByteStream):
    """A streamed reply, closed so its connection can be used again.

    The SDK stops reading a stream at its ``[DONE]`` event and closes the response with the HTTP body's last bytes
    still unread; a connection with an unfinished body can't be reused, so every reply used to open a new one (a
    TLS handshake before her first word). On close the little that's left is read first. A stream closed early (a
    stopped reply) has more than a tail left: past ``TAIL_BYTES`` or ``TAIL_SECONDS`` it is closed unread, its
    connection dropped, as before.
    """

    TAIL_BYTES = 64 * 1024
    TAIL_SECONDS = 0.5

    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self._inner = inner
        self._chunks = None

    async def __aiter__(self):
        self._chunks = self._inner.__aiter__()
        async for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        if self._chunks is not None:
            try:
                async with asyncio.timeout(self.TAIL_SECONDS):
                    read = 0
                    async for chunk in self._chunks:
                        read += len(chunk)
                        if read > self.TAIL_BYTES:
                            break
            except Exception:  # noqa: BLE001 - the connection is dropped instead; nothing else depends on it
                pass
        await self._inner.aclose()


def sync_client() -> httpx.Client:
    # No transport of our own: httpx builds its transport and the environment's proxy routes with these limits.
    return DefaultHttpxClient(limits=LIMITS)


def async_client() -> httpx.AsyncClient:
    # An explicit transport turns httpx's environment proxies off, so the same routes are mounted here, each a
    # per-loop pool; ``NO_PROXY`` hosts map to None, which httpx sends through the default (direct) transport.
    mounts: dict[str, httpx.AsyncBaseTransport | None] = {
        pattern: None if url is None else _PerLoopTransport(httpx.Proxy(url=url))
        for pattern, url in get_environment_proxies().items()
    }
    return DefaultAsyncHttpxClient(transport=_PerLoopTransport(), mounts=mounts)
