"""Tools the chat agent can call, one MCP server per subpackage.

Exposing capabilities over MCP rather than as plain ``@tool`` functions keeps
them usable outside this app (Claude Desktop, other MCP clients) and keeps the
implementations isolated from the web process.

Each subpackage carries a ``config.json`` naming its transport; ``chat.py``
fills in the per-user secrets and ``mcp_registry.py`` assembles the client
config, so adding a toolset needs no change to the chat path. The credentials
tools need come from ``app.ai.integrations``.

Toolsets are not handed to the chat agent directly — each is fronted by a
specialist sub-agent (``app.ai.subagents``) that the orchestrator delegates to.

Current tool sets:
    - gmail       : search / read / send / draft / label a user's Gmail.
                    Spawned per turn over stdio.
    - macrovision : macros and depth from a food photo. Mounted in the API
                    process over HTTP instead of spawned — it would otherwise
                    reload 3.6GB of weights every turn. See its ``mount.py``.
"""
