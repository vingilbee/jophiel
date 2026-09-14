"""Mount the MacroVision MCP server inside the FastAPI app.

Gmail's MCP server is spawned as a subprocess per chat turn. MacroVision can't
be: it would reload ~3.6GB of ZoeDepth and ViT weights every single turn. So it
is mounted here instead, in the API process, where it shares the
:func:`app.ai.MacroVision.get_predictor` singleton and a tool call costs
inference and nothing else.

The chat agent then reaches it over loopback HTTP. That is a real network hop
for something in the same process, which looks wasteful until you consider the
alternative: a bespoke in-process transport that bypasses the MCP registry, is
invisible to external MCP clients, and needs its own tests. The hop is
sub-millisecond next to a multi-second VLM call, and in exchange MacroVision is
an ordinary entry in ``config.json`` like every other toolset.

Two things have to happen for a mounted MCP server to work, and forgetting the
second is the classic failure:

1. ``mount_macrovision_mcp(app)`` at app-construction time, and
2. ``macrovision_mcp_lifespan()`` entered in the app's lifespan.

FastAPI does not run a mounted sub-app's lifespan, and the session manager
lives there — skip step 2 and every request hangs.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.tools.macrovision.server import mcp
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Set by mount_macrovision_mcp; the session manager can't be touched before
# streamable_http_app() has been called (FastMCP creates it lazily).
_mounted = False


def mount_macrovision_mcp(app: FastAPI) -> None:
    """Attach the MacroVision MCP endpoint to ``app``."""
    global _mounted
    settings = get_settings()
    app.mount(settings.macrovision_mcp_path, mcp.streamable_http_app())
    _mounted = True
    logger.info("MacroVision MCP server mounted at %s", settings.macrovision_mcp_path)


@asynccontextmanager
async def macrovision_mcp_lifespan() -> AsyncIterator[None]:
    """Run the mounted server's session manager for the app's lifetime.

    A no-op if nothing was mounted, so a deployment that turns the tools off
    doesn't have to special-case its lifespan.
    """
    if not _mounted:
        yield
        return
    async with mcp.session_manager.run():
        yield


def macrovision_mcp_url() -> str:
    """The URL the chat agent dials to reach the mounted server."""
    settings = get_settings()
    return (
        f"{settings.internal_mcp_base_url.rstrip('/')}"
        f"/{settings.macrovision_mcp_path.strip('/')}/"
    )
