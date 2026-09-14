"""MacroVision as an MCP toolset: photo → macros, and depth on its own.

The model itself lives in ``app.ai.MacroVision``; this package is the MCP
wrapper around it, so the chat agent reaches nutrition inference through the
same protocol it reaches Gmail.

Unlike Gmail this server is mounted in the API process rather than spawned per
turn — see :mod:`app.ai.tools.macrovision.mount` for why, and for the two-step
wiring the app has to do.
"""
from app.ai.tools.macrovision.mount import (
    macrovision_mcp_lifespan,
    macrovision_mcp_url,
    mount_macrovision_mcp,
)
from app.ai.tools.macrovision.scope import mint_scope_token

__all__ = [
    "mount_macrovision_mcp",
    "macrovision_mcp_lifespan",
    "macrovision_mcp_url",
    "mint_scope_token",
]
