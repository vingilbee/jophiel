"""Connect the chat agent to the Gmail MCP server.

Bridges MCP → LangChain: spawns ``server.py`` over stdio, hands it the user's
Google access token, and adapts the server's tools into LangChain ``BaseTool``
objects the LangGraph ReAct agent can call.

Session lifetime
----------------
:func:`gmail_tools` is a context manager because the returned tools hold a live
MCP session — they stop working the moment it closes, so the session must stay
open for the whole agent run, not just tool loading.

We open one session per chat turn rather than pooling them per user. A pooled
session would have to be built around a token captured at pool time, and Google
access tokens expire after ~an hour; spawning fresh means every turn runs with
a freshly refreshed token and a crashed subprocess can't wedge a user's chat.
The cost is the subprocess start (~a few hundred ms), paid only by users who
have actually connected Gmail.
"""
from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from app.ai.tools.gmail.gmail_api import ACCESS_TOKEN_ENV

logger = logging.getLogger(__name__)

SERVER_MODULE = "app.ai.tools.gmail.server"

# The backend package root (the directory containing `app/`), so the subprocess
# can import `app.*` regardless of the parent's working directory.
_BACKEND_ROOT = Path(__file__).resolve().parents[4]


def _server_parameters(access_token: str) -> StdioServerParameters:
    # `sys.executable` is the uv-managed venv interpreter, so the child gets the
    # same dependencies without needing uv on PATH.
    env = get_default_environment()
    env[ACCESS_TOKEN_ENV] = access_token
    env["PYTHONPATH"] = str(_BACKEND_ROOT)
    # Unbuffered: stdout is the JSON-RPC channel and must not sit in a buffer.
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", SERVER_MODULE],
        env=env,
        cwd=str(_BACKEND_ROOT),
    )


@asynccontextmanager
async def gmail_tools(access_token: str) -> AsyncIterator[list[BaseTool]]:
    """Yield the Gmail MCP tools, bound to a session that lives for the block.

    Usage::

        async with gmail_tools(token) as tools:
            agent = build_agent(tools)
            ...   # tools are only callable inside this block
    """
    async with stdio_client(_server_parameters(access_token)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            logger.debug(
                "Loaded %d Gmail MCP tools: %s",
                len(tools),
                ", ".join(tool.name for tool in tools),
            )
            yield tools


@asynccontextmanager
async def gmail_tools_or_none(
    access_token: str | None,
) -> AsyncIterator[list[BaseTool]]:
    """Like :func:`gmail_tools`, but yields ``[]`` instead of failing.

    Used on the chat path: a user without a connected Google account, or a
    Gmail server that won't start, should get a working chatbot with no Gmail
    tools — not a broken conversation.
    """
    if not access_token:
        yield []
        return

    # Enter the session by hand rather than with `async with`, so the guard
    # covers *starting* the server only. Wrapping the yield too would swallow
    # errors raised by the caller's block (and re-yield, which is illegal).
    stack = AsyncExitStack()
    try:
        tools = await stack.enter_async_context(gmail_tools(access_token))
    except Exception:
        logger.exception("Could not start the Gmail MCP server; continuing without it.")
        await stack.aclose()
        yield []
        return

    try:
        yield tools
    finally:
        try:
            await stack.aclose()
        except Exception:
            # The turn is already done; a subprocess that misbehaves on
            # shutdown shouldn't surface as a failed chat response.
            logger.exception("Error shutting down the Gmail MCP server.")
