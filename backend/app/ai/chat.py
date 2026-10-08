"""Chat entrypoints: stream or await a chatbot response for a given user.

These are the functions route handlers call. They take a plain message history
(role/content dicts — easy to validate with Pydantic) plus the runtime context,
and handle conversion to LangChain messages and prompt assembly internally.

Shape of a turn
---------------
The agent the caller ends up talking to is an **orchestrator** holding one tool
per available specialist, not the toolset tools themselves. Assembling it is
this module's job because it depends on the *call*, not on the agent
definition: which MCP servers this user has, and sessions that must open before
the run and close after it.

    context ──► connections ──► MCP server config ──► tools, per server
                                                          │
                                    sub-agent tools ◄─────┘
                                            │
                                       orchestrator

Users with nothing available skip all of it and get the cached, tool-less agent.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.ai.agent import build_agent, get_agent
from app.ai.context import AgentRuntimeContext
from app.ai.mcp_registry import build_server_config
from app.ai.messages import ChatTurn, extract_text, to_lc_messages
from app.ai.prompts import build_system_prompt
from app.ai.subagents import SUBAGENT_TAG, build_subagent_tool, subagents_for
from app.ai.subagents.registry import SubAgentSpec
from app.ai.tools.macrovision import macrovision_mcp_url, mint_scope_token
from app.ai.tools.desktop import build_desktop_tools
from app.ai.tools.screen import build_look_at_screen
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _resolve_connections(context: AgentRuntimeContext) -> dict[str, dict]:
    """Map this turn's live integrations to the secrets each MCP server needs.

    One `if` per toolset — but it only produces data, so nothing here opens a
    session or can leave one dangling.
    """
    settings = get_settings()
    connections: dict[str, dict] = {}
    if settings.drive_tools_enabled and context.drive_access_token:
        connections["drive"] = {
            "access_token": context.drive_access_token,
            "user_id": str(context.user_id),
            "openai_api_key": getattr(settings, "openai_api_key", None) or "",
            "image_model": getattr(settings, "slide_image_model", "gpt-image-2"),
            "public_image_base": getattr(settings, "slide_image_public_base_url", None) or "",
            "image_signing_key": getattr(settings, "google_token_encryption_key", None) or "",
            "asset_store_dir": getattr(settings, "asset_store_dir", "var/assets"),
            "image_store_dir": getattr(settings, "image_store_dir", "var/images"),
        }

    if settings.gmail_tools_enabled and context.gmail_connected:
        connections["gmail"] = {"access_token": context.gmail_access_token}

    if context.macrovision_enabled:
        # Mounted in this same process, so this is a loopback call; the scope
        # token is what tells the server whose images it may touch.
        connections["macrovision"] = {
            "url": macrovision_mcp_url(),
            "scope_token": mint_scope_token(context.user_id),
        }

    if context.blender_enabled:
        connections["blender"] = {
            "user_id": str(context.user_id),
            "host": settings.blender_bridge_host,
            "port": str(settings.blender_bridge_port),
        }

    # future integrations follow the same pattern:
    # if settings.salesforce_tools_enabled and context.salesforce_connected:
    #     connections["salesforce"] = {"access_token": ..., "instance_url": ...}

    return connections


async def _load_subagent_tools(
    server_config: dict[str, dict],
    specs: Sequence[SubAgentSpec],
    model: str | None,
    queue: asyncio.queue | None = None
) -> list[BaseTool]:
    """Load each server's tools and wrap the specs that own them.

    Tools are loaded **per server** rather than in one pool: a sub-agent must
    get its own toolset and nobody else's, which is the whole point of the
    split. A server that won't start drops just its sub-agent — the rest of the
    turn proceeds, matching how Gmail already degrades.
    """
    client = MultiServerMCPClient(server_config)
    tools_by_server: dict[str, list[BaseTool]] = {}
    for name in server_config:
        try:
            tools_by_server[name] = await client.get_tools(server_name=name)
        except Exception:
            logger.exception("Could not load MCP tools for %r; skipping it.", name)

    subagent_tools: list[BaseTool] = []
    for spec in specs:
        tools = [tool for server in spec.servers for tool in tools_by_server.get(server, [])]
        if not tools:
            logger.warning("No tools loaded for the %s sub-agent; skipping it.", spec.name)
            continue
        subagent_tools.append(build_subagent_tool(spec, tools, model=model, queue=queue))
    return subagent_tools


def _build_inputs(
    messages: Sequence[ChatTurn],
    context: AgentRuntimeContext,
    specs: Sequence[SubAgentSpec],
) -> dict[str, Any]:
    lc_messages: list[BaseMessage] = [
        SystemMessage(build_system_prompt(context, list(specs))),
        *to_lc_messages(messages),
    ]
    return {"messages": lc_messages}


@asynccontextmanager
async def _orchestrator_for(context: AgentRuntimeContext, queue: asyncio.Queue | None = None):
    """Yield ``(agent, specs)`` for this user's turn.

    The specs come back too so the prompt can describe exactly the specialists
    that were actually wired up — if a server failed to start, the model is
    told about it by omission rather than being offered a tool that isn't there.

    Tools may hold live MCP sessions opened for this call only; ``get_tools()``
    opens and closes per server internally, so there's nothing to leak past
    this block.

    ``queue``, if given, lets subagent tools push their own live token chunks
    back to the caller's stream as they're generated — see build_subagent_tool.
    """
    settings = get_settings()
    # Voice turns run on a faster model: the user is waiting to *hear* the
    # answer, so time-to-first-token dominates perceived latency. An explicit
    # override wins over both (nudges pin themselves to a cheap model).
    model = context.model_override or (
        settings.voice_chat_model if context.voice else None
    )

    # The eye is the one tool the orchestrator holds itself rather than
    # delegating — see app/ai/tools/screen. Built per turn because it closes
    # over the newest frame, and only when sharing is on.
    direct_tools: list[BaseTool] = []
    if context.vision_enabled:
        direct_tools.append(
            build_look_at_screen(context.user_id, context.screen_image_id)
        )
    # The game tools: same exception as the eye (few, cheap, latency-sensitive),
    # and only for a user with a game VM.
    if context.game is not None:
        direct_tools.extend(build_desktop_tools(context.user_id))

    connections = _resolve_connections(context)
    server_config = build_server_config(connections)
    specs = [
        spec
        for spec in subagents_for(set(server_config))
        if all(flag in context.capabilities() for flag in spec.requires)
    ]

    if not server_config or not specs:
        # No specialists, but possibly still the eye. `get_agent` is the cached
        # tool-less agent and can only serve the genuinely tool-less case.
        yield (build_agent(direct_tools, model=model) if direct_tools else get_agent(model)), []
        return

    subagent_tools = await _load_subagent_tools(server_config, specs, model, queue=queue)
    if not subagent_tools:
        yield (build_agent(direct_tools, model=model) if direct_tools else get_agent(model)), []
        return

    # Keep specs in step with the tools that actually built, so the prompt
    # never advertises a specialist whose server didn't come up.
    live = {tool.name for tool in subagent_tools}
    specs = [spec for spec in specs if spec.tool_name in live]
    yield build_agent([*direct_tools, *subagent_tools], model=model), specs

async def _speaker_from_tags(tags: Sequence[str] | None) -> str | None:
    """Return the specialist's name if this chunk came from a subagent run."""
    for tag in tags or ():
        if tag.startswith("subagent:"):
            return tag.removeprefix("subagent:")
    return None

async def stream_chat(
    *,
    messages: Sequence[ChatTurn],
    context: AgentRuntimeContext,
    on_stage: Callable[[str], None] | None = None,
) -> AsyncIterator[dict]:
    """Stream the orchestrator's reply as token/subagent events.

    ``on_stage``, if given, is called with ``"tools_ready"`` once the
    specialists' MCP sessions are up and the model request is about to go out
    -- the caller's timing hook. It must not raise; errors are swallowed.
    """
    queue: asyncio.Queue[tuple[str | None, str] | dict | None] = asyncio.Queue()
    logger.debug("stream_chat: queue created id=%s", id(queue))
    async def _drain_orchestrator():
        try:
            async with _orchestrator_for(context, queue=queue) as (agent, specs):
                if on_stage is not None:
                    with contextlib.suppress(Exception):
                        on_stage("tools_ready")
                inputs = _build_inputs(messages, context, specs)
                async for chunk, metadata in agent.astream(inputs, stream_mode="messages"):
                    if not isinstance(chunk, AIMessageChunk) or chunk.tool_call_chunks:
                        continue
                    if SUBAGENT_TAG in (metadata.get("tags") or ()):
                        continue  # belongs to a subagent; it already pushed itself
                    text = extract_text(chunk.content)
                    if text:
                        await queue.put((None, text))
        finally:
            await queue.put(None)  # sentinel

    task = asyncio.create_task(_drain_orchestrator())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, tuple):
                _, text = item
                yield {"type": "token", "content": text}
            else:
                kind = item["kind"]
                if kind == "subagent_start":
                    yield {"type": "subagent_start", "agent": item["agent"]}
                elif kind == "subagent_token":
                    yield {"type": "subagent_token", "agent": item["agent"], "content": item["content"]}
                elif kind == "subagent_end":
                    yield {"type": "subagent_end", "agent": item["agent"]}
    finally:
        await task  

async def complete_chat(
    *, messages: Sequence[ChatTurn], context: AgentRuntimeContext
) -> str:
    """Await the full assistant reply as a single string (non-streaming)."""
    async with _orchestrator_for(context) as (agent, specs):
        result = await agent.ainvoke(_build_inputs(messages, context, specs))
    return extract_text(result["messages"][-1].content)
