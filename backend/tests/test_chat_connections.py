"""What keeps her first word quick: specialists' tool lists read once per process, OpenAI connections kept per
event loop, and the chat worker's one loop cleaning up after each job as ``asyncio.run`` did."""
from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import httpx
from mcp.types import Tool as MCPTool

from app.ai import chat as ai_chat, openai_http
from app.workers import chat as chat_worker


def test_a_servers_tools_are_listed_once_and_rebuilt_over_each_turns_connection(monkeypatch):
    opened = []

    @contextlib.asynccontextmanager
    async def session_for(connection, **_):
        opened.append(connection["env"]["TOKEN"])

        class Session:
            async def initialize(self):
                return None

            async def list_tools(self, cursor=None):
                return SimpleNamespace(
                    tools=[MCPTool(name="search", description="find", inputSchema={"type": "object"})],
                    nextCursor=None)
        yield Session()

    monkeypatch.setattr(ai_chat, "create_session", session_for)
    monkeypatch.setattr(ai_chat, "_MCP_TOOL_DEFINITIONS", {})

    def connection(token):
        return {"transport": "stdio", "command": "x", "args": [], "env": {"TOKEN": token}}

    first = asyncio.run(ai_chat._server_tools("mail", connection("token-1")))
    second = asyncio.run(ai_chat._server_tools("mail", connection("token-2")))

    assert opened == ["token-1"]  # the second turn started no server
    assert [t.name for t in first] == [t.name for t in second] == ["search"]
    # Each turn's tools call over that turn's connection (its fresh token), never a cached one.
    assert second[0].coroutine is not first[0].coroutine


def test_a_server_that_cannot_list_drops_only_its_own_tools(monkeypatch):
    async def tools(name, connection):
        if name == "broken":
            raise RuntimeError("would not start")
        return []

    monkeypatch.setattr(ai_chat, "_server_tools", tools)
    assert asyncio.run(ai_chat._load_subagent_tools({"broken": {}, "fine": {}}, [], None)) == []


def test_each_event_loop_gets_its_own_connection_pool():
    transport = openai_http._PerLoopTransport()

    async def pool():
        return transport._transport()

    loop = asyncio.new_event_loop()
    try:
        a1 = loop.run_until_complete(pool())
        a2 = loop.run_until_complete(pool())
    finally:
        loop.close()
    b = asyncio.run(pool())
    assert a1 is a2  # one loop keeps its connections between turns
    assert b is not a1  # another loop never borrows them
    assert isinstance(a1, httpx.AsyncHTTPTransport)
    assert openai_http.LIMITS.keepalive_expiry == openai_http.KEEPALIVE_SECONDS > 5


def test_a_job_on_the_long_lived_loop_leaves_nothing_running_but_the_keep_warm(monkeypatch):
    seen = {}

    async def generate(job):
        seen["straggler"] = asyncio.create_task(asyncio.sleep(60))

    async def run():
        warm = asyncio.create_task(asyncio.sleep(60), name=chat_worker._WARM_TASK)
        late_warm = None

        async def generate_with_ping(job):
            nonlocal late_warm
            await generate(job)
            late_warm = asyncio.create_task(asyncio.sleep(60), name=chat_worker._WARM_TASK)

        monkeypatch.setattr(chat_worker, "_generate", generate_with_ping)
        await chat_worker._run_job(object())
        result = (seen["straggler"].cancelled(), warm.done(), late_warm.done())
        warm.cancel()
        late_warm.cancel()
        return result

    straggler_cancelled, warm_done, late_warm_done = asyncio.run(run())
    assert straggler_cancelled
    assert not warm_done and not late_warm_done


class _Body(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.read, self.closed = chunks, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def test_a_finished_stream_reads_its_tail_before_closing_so_its_connection_is_kept():
    body = _Body([b"data: hi\n\n", b"data: [DONE]\n\n", b""])  # the last: the body's end the SDK never reads
    stream = openai_http._ReadTailOnClose(body)

    async def consume_like_the_sdk():
        async for chunk in stream:
            if b"[DONE]" in chunk:
                break
        await stream.aclose()

    asyncio.run(consume_like_the_sdk())
    assert body.read == 3 and body.closed


def test_a_stream_stopped_early_is_closed_without_reading_it_all(monkeypatch):
    monkeypatch.setattr(openai_http._ReadTailOnClose, "TAIL_BYTES", 10)
    body = _Body([b"x" * 8] * 1000)
    stream = openai_http._ReadTailOnClose(body)

    async def stop_after_one():
        async for _ in stream:
            break
        await stream.aclose()

    asyncio.run(stop_after_one())
    assert body.read < 10 and body.closed
