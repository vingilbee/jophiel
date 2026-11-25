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
import base64
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.sessions import create_session
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import Tool as MCPTool

from app.ai.agent import build_agent, get_agent
from app.ai.context import AgentRuntimeContext
from app.ai.mcp_registry import build_server_config
from app.ai.messages import ChatTurn, extract_text, to_lc_messages
from app.ai.prompts import build_system_prompt
from app.ai.subagents import SUBAGENT_TAG, build_subagent_tool, subagents_for
from app.ai.subagents.registry import SubAgentSpec
from app.ai.tools.macrovision import macrovision_mcp_url, mint_scope_token
from app.ai.tools.attachments import build_attachment_tools
from app.ai.tools.desktop import build_desktop_tools
from app.ai.tools.desktop_control import build_desktop_control_tools, screenshot_hook
from app.ai.tools.desktop_control.tools import RECURSION_LIMIT as DESKTOP_RECURSION_LIMIT
from app.ai.tools.security import build_security_tools
from app.ai.tools.memory import build_memory_tools
from app.ai.tools.memory.recall import build_recall_tools
from app.ai.tools.mind import build_mind_tools
from app.ai.tools.phone import build_exchange_tools, build_people_tools, build_phone_tools, build_reply_tools
from app.ai.tools.phone_control import SCREEN_TOOLS as PHONE_SCREEN_TOOLS
from app.ai.tools.phone_control import build_phone_control_tools
from app.ai.tools.screen import build_look_at_screen
from app.core.config import get_settings
from app.services.phone_control import record_line as phone_control_record_line

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


# What each MCP server offers, listed once per process. A server's tool list is fixed in its code, but listing it
# means starting it (a stdio server is a new process: ~0.7-0.9 s each) — on every turn, before her first word, even
# when she never delegates. The tools are rebuilt per turn over this turn's connection (fresh tokens), and each
# call opens its own session over it, exactly as before.
_MCP_TOOL_DEFINITIONS: dict[str, list[MCPTool]] = {}


async def _server_tools(name: str, connection: dict) -> list[BaseTool]:
    definitions = _MCP_TOOL_DEFINITIONS.get(name)
    if definitions is None:
        async with create_session(connection) as session:
            await session.initialize()
            definitions, cursor = [], None
            for _ in range(100):  # pages
                page = await session.list_tools(cursor=cursor)
                definitions.extend(page.tools or [])
                cursor = page.nextCursor
                if not cursor:
                    break
        _MCP_TOOL_DEFINITIONS[name] = definitions
    return [convert_mcp_tool_to_langchain_tool(None, definition, connection=connection, server_name=name)
            for definition in definitions]


async def prime_tool_definitions() -> None:
    """List every enabled specialist server's tools once, before the first turn needs them (the chat worker's
    warm-up). Placeholder credentials: listing reads no account. A server that won't list now is listed on the
    first turn that has it, as before."""
    import uuid

    settings = get_settings()
    connections: dict[str, dict] = {}
    if settings.macrovision_tools_enabled:
        connections["macrovision"] = {"url": macrovision_mcp_url(), "scope_token": mint_scope_token(uuid.uuid4())}
    if settings.gmail_tools_enabled:
        connections["gmail"] = {"access_token": "listing-only"}
    if settings.drive_tools_enabled:
        connections["drive"] = {key: "" for key in ("openai_api_key", "image_model", "public_image_base",
                                                    "image_signing_key")} | {
            "access_token": "listing-only", "user_id": "listing-only",
            "asset_store_dir": getattr(settings, "asset_store_dir", "var/assets"),
            "image_store_dir": getattr(settings, "image_store_dir", "var/images")}
    config = build_server_config(connections)
    results = await asyncio.gather(*(_server_tools(name, config[name]) for name in config), return_exceptions=True)
    for name, result in zip(config, results):
        if isinstance(result, BaseException):
            logger.info("Specialist %r not listed at start (%s); its first turn lists it.", name, result)


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
    tools_by_server: dict[str, list[BaseTool]] = {}
    loaded = await asyncio.gather(*(_server_tools(name, server_config[name]) for name in server_config),
                                  return_exceptions=True)
    for name, result in zip(server_config, loaded):
        if isinstance(result, BaseException):
            logger.error("Could not load MCP tools for %r; skipping it.", name, exc_info=result)
        else:
            tools_by_server[name] = result

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
    system = build_system_prompt(context, list(specs))
    if context.mind is not None:
        context.mind.prompt = system  # the evaluation's record keeps exactly what she read
    lc_messages: list[BaseMessage] = [
        SystemMessage(system),
        *to_lc_messages(messages),
    ]
    if context.game_picture is not None and lc_messages and isinstance(lc_messages[-1], HumanMessage):
        # Her game as the user saw it when they spoke, on the in-flight message
        # only (never persisted): she sees it without a tool round trip.
        last = lc_messages[-1]
        text = last.content if isinstance(last.content, str) else extract_text(last.content)
        lc_messages[-1] = HumanMessage(content=[
            {"type": "text", "text": text},
            {"type": "text", "text": context.game_picture_note or "[Your game when the user spoke.]"},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(context.game_picture).decode(),
                "detail": "low"}},
        ])
    return {"messages": lc_messages}


@asynccontextmanager
async def _orchestrator_for(context: AgentRuntimeContext, queue: asyncio.Queue | None = None):
    """Yield ``(agent, specs)`` for this user's turn (``_assemble``); when the turn ends, however it ends, her use of
    the phone screen this turn is finished (control off, its one chat line: app/ai/tools/phone_control.py)."""
    try:
        async with _assemble(context, queue) as built:
            yield built
        if context.phone_control is not None:
            # Her run ended by itself (not stopped, not failed): only such a turn looks at the phone again.
            context.phone_control.answered = True
    finally:
        if context.phone_control is not None:
            context.phone_control.finish()


@asynccontextmanager
async def _assemble(context: AgentRuntimeContext, queue: asyncio.Queue | None = None):
    """Yield ``(agent, specs)`` for this user's turn.

    The specs come back too so the prompt can describe exactly the specialists
    that were actually wired up — if a server's tools couldn't be listed, the
    model is told about it by omission rather than being offered a tool that
    isn't there. (A server's list is read once per process, ``_server_tools``:
    one that breaks later fails its calls, said back to her as a tool error.)

    Each MCP tool call opens and closes its own session over this turn's
    connection, so there's nothing to leak past this block.

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
        direct_tools.extend(build_desktop_tools(context.user_id, context.game_event, spoke_at=context.spoke_at,
                                                planner=context.game_planner,
                                                since=context.game_moment, message_id=context.source_message_id,
                                                idle=context.game_idle,
                                                own_thought=getattr(context.mind, "reason", None)
                                                in ("thought", "proposal")))
    # Her own desktop's hands (general desktop control): same exception, and she looks after every few
    # actions, so an extra model hop per action would make it crawl. Replies only: a background turn
    # never starts using the desktop on its own.
    hook = None
    reasoning = None
    base_model = model  # specialists keep the turn's usual model (their calls run without reasoning)
    # In a game (a session on, or a game event woke her): her mind there thinks before it answers. An explicit
    # override wins; her desktop's own model below wins over this.
    if (context.game is not None and not context.game_idle) or context.game_event is not None:
        if not context.model_override and settings.game_mind_model:
            model = settings.game_mind_model
            reasoning = settings.game_mind_reasoning_effort
    # A wake (not a reply to the user's message): nobody is waiting, so she thinks before she decides and speaks.
    if context.wake is not None and reasoning is None and not context.model_override:
        reasoning = settings.wake_mind_reasoning_effort
    if context.desktop is not None and context.desktop_usable and context.game_event is None and context.nudge is None:
        direct_tools.extend(build_desktop_control_tools(context.user_id, turn_id=context.turn_id,
                                                        conversation_id=context.conversation_id,
                                                        message_id=context.source_message_id,
                                                        commands=context.desktop_commands))
        hook = screenshot_hook
        # Working on her desktop is many steps, each chosen from what the last showed: worth a reasoning model that
        # thinks before each (a reply turn without the desktop stays on the quick one). An explicit override wins.
        reasoning = settings.desktop_control_reasoning_effort
        if not context.model_override and settings.desktop_control_model:
            model = settings.desktop_control_model
    # Her phone (app/ai/tools/phone.py): every action on a reply to the user; the quiet ones on her own turns (a
    # wake); none at all on a turn someone else's words caused (a notification, or what such a wake started or
    # scheduled), however it was carried; never on game events (other players' chat) or nudges.
    phone_reply = context.source_message_id is not None and context.wake is None
    if (settings.redis_url and context.conversation_id is not None and not context.third_party
            and context.game_event is None and context.nudge is None and (phone_reply or context.wake is not None)):
        direct_tools.extend(build_phone_tools(context.user_id, context.conversation_id, own_turn=not phone_reply,
                                              message_id=context.source_message_id if phone_reply else None,
                                              evaluation=context.mind))
        # Who she knows on the phone (read only): the user's own reply turns, never a wake.
        if phone_reply:
            direct_tools.extend(build_people_tools(context.user_id, context.source_message_id))
        # Phase 9: her hands on their phone's screen (frontend-phone/PLAN_PHASE_9.md): the user's reply turns (typed
        # or spoken), and a wake to look again after one of them used the screen, for what the user's message asked
        # (services/phone_look.py; never a wake someone else's words caused: excluded above). Building them takes
        # the phone's control for this turn: an older turn still acting there is answered taken_over. Its
        # screenshots reach her through the picture hook. A turn whose hands acted schedules her next look.
        asked = context.source_message_id if phone_reply else context.phone_look
        if asked is not None and context.phone is not None and context.turn_id is not None:
            control_tools, context.phone_control = build_phone_control_tools(
                context.user_id, context.conversation_id, turn_id=context.turn_id, asked=asked, zone=context.zone)
            direct_tools.extend(control_tools)
            hook = hook or screenshot_hook
    # Her reading of what people on the phone said (affinity B2: shadow records, requests for space): only on her
    # mind's turns someone else's words there caused. Nothing in them sends or acts.
    if (settings.redis_url and context.mind is not None and context.wake is not None and context.third_party
            and context.game_event is None and context.nudge is None):
        direct_tools.extend(build_exchange_tools(context.user_id, getattr(context.mind, "phone_scope", None)))
        # Her own reply to someone there (affinity B3): only to an exchange this wake is bound to, the messages that
        # woke her or a follow-up she scheduled come due; every send checked (services/phone_replies.py).
        targets = getattr(context.mind, "reply_targets", None) or []
        follow_up = getattr(context.mind, "phone_follow_up", None)
        if context.conversation_id is not None and (targets or follow_up):
            direct_tools.extend(build_reply_tools(
                context.user_id, context.conversation_id, targets=None if follow_up else targets,
                follow_up=follow_up, wake_id=getattr(context.mind, "wake_work_id", None)))
    # Her desktop's security records: read only, so on follow-ups too (she checks before presenting a finding as
    # current); never on game-event turns.
    if context.security is not None and context.game_event is None:
        direct_tools.extend(build_security_tools(context.user_id, zone=context.zone))
    # Her reading of the user's files: one tool, its result back in her own loop (never on game-event turns).
    if context.files and context.game_event is None:
        direct_tools.extend(build_attachment_tools(context.user_id, conversation_id=context.conversation_id,
                                                   message_id=context.source_message_id))
    # Her working memory's tools: same exception (few, cheap, and the next turn must see the change).
    if context.memory_tools:
        direct_tools.extend(build_memory_tools(context.user_id, message_id=context.source_message_id,
                                               background=context.game_event is not None,
                                               wake=context.wake is not None, evaluation=context.mind))
    # Her read tools over what she remembers: their results come straight back into this same loop.
    if context.recall_turn is not None:
        direct_tools.extend(build_recall_tools(context.recall_turn))
    # Her mind's own tools (silence, background thinking, coming back later, a proposal's decision): on every
    # evaluation of her mind. A game's own think_it_over (with the game in view) takes that name while she plays.
    if context.mind is not None:
        taken = {tool.name for tool in direct_tools}
        direct_tools.extend(tool for tool in build_mind_tools(context.mind) if tool.name not in taken)
        context.mind.model = model or settings.chat_model

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
        yield (build_agent(direct_tools, model=model, pre_model_hook=hook, reasoning=reasoning) if direct_tools or reasoning else get_agent(model)), []
        return

    subagent_tools = await _load_subagent_tools(server_config, specs, base_model, queue=queue)
    if not subagent_tools:
        yield (build_agent(direct_tools, model=model, pre_model_hook=hook, reasoning=reasoning) if direct_tools or reasoning else get_agent(model)), []
        return

    # Keep specs in step with the tools that actually built, so the prompt
    # never advertises a specialist whose server didn't come up.
    live = {tool.name for tool in subagent_tools}
    specs = [spec for spec in specs if spec.tool_name in live]
    yield build_agent([*direct_tools, *subagent_tools], model=model, pre_model_hook=hook, reasoning=reasoning), specs

def _run_config(context: AgentRuntimeContext) -> dict | None:
    """A desktop or phone-control turn alternates looking and acting many times: more graph steps than a chat turn
    (both budgets are 80 actions)."""
    if context.desktop is not None or context.phone_control is not None:
        return {"recursion_limit": DESKTOP_RECURSION_LIMIT}
    return None


REPLY_NODE = "agent"  # create_react_agent's model node: the only model whose words are her reply


def _her_words(metadata: dict | None) -> bool:
    """Whether a streamed chunk is her own reply: from the agent's model node at the top of the graph, never from
    a model one of her tools invoked (the phone checks' verdicts, a verifier) nor a nested graph."""
    metadata = metadata or {}
    if metadata.get("langgraph_node") != REPLY_NODE:
        return False
    return "|" not in str(metadata.get("langgraph_checkpoint_ns") or "")


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
                async for chunk, metadata in agent.astream(inputs, _run_config(context), stream_mode="messages"):
                    if SUBAGENT_TAG in (metadata.get("tags") or ()):
                        continue  # belongs to a subagent; it already pushed itself
                    if isinstance(chunk, ToolMessage) and context.mind is not None:
                        # What each of her tools returned, in order: the evaluation's record of what she did.
                        result = extract_text(chunk.content)
                        if chunk.name in PHONE_SCREEN_TOOLS:
                            # A phone screen is never stored: the record keeps which screen and app, not what's on it.
                            result = phone_control_record_line(result)
                        context.mind.actions.append({"tool": chunk.name, "status": getattr(chunk, "status", None),
                                                     "result": result[:300]})
                        continue
                    if not isinstance(chunk, AIMessageChunk) or chunk.tool_call_chunks:
                        continue
                    if not _her_words(metadata):
                        continue  # a model a tool ran (a check's verdict): diagnostic, never her reply
                    text = extract_text(chunk.content)
                    if text:
                        await queue.put((None, text))
        finally:
            await queue.put(None)  # sentinel

    task = asyncio.create_task(_drain_orchestrator())
    finished = False
    try:
        while True:
            item = await queue.get()
            if item is None:
                finished = True
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
        # Closed before the run ended (the turn was stopped): stop the run itself, so no further model call or
        # tool goes out for a reply nobody will hear. Seen live 2026-11-24: a turn stopped by the user's next
        # words ran 8 more seconds, still set a game goal and resumed the game, and held up the next turn.
        if not finished and not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  

async def complete_chat(
    *, messages: Sequence[ChatTurn], context: AgentRuntimeContext
) -> str:
    """Await the full assistant reply as a single string (non-streaming)."""
    async with _orchestrator_for(context) as (agent, specs):
        result = await agent.ainvoke(_build_inputs(messages, context, specs), _run_config(context))
    return extract_text(result["messages"][-1].content)
