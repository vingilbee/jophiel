"""Wrap a toolset's MCP tools into one delegable sub-agent tool.

Each :class:`SubAgentSpec` becomes a single LangChain tool. Calling it runs a
private ReAct loop over that toolset's tools and returns the final text, so from
the orchestrator's side delegation looks exactly like any other tool call — one
call in, one string back.

Stream isolation
----------------
The orchestrator's reply is streamed to the user token by token. A sub-agent's
internal chatter must not appear in that stream. As of langgraph 1.2 a nested
graph invoked inside a tool doesn't emit into the parent's
``astream(stream_mode="messages")``, even though the parent's callbacks are
propagated into it — verified, not assumed. That is behaviour we depend on but
don't control, so every sub-agent run is tagged with :data:`SUBAGENT_TAG` and
``chat.py`` drops tagged chunks. Tags do reach stream metadata, so if a future
version starts forwarding nested tokens the filter catches them instead of
leaking a sub-agent's working notes into the user's chat.

Failure policy
--------------
A sub-agent that raises returns an error *string* rather than propagating. The
orchestrator can then tell the user "I couldn't reach your mail just now" and
carry on, which matches how the rest of the AI layer degrades — a broken
toolset shouldn't take the whole reply down with it.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.ai.agent import get_chat_model
from app.ai.messages import extract_text
from app.ai.subagents.registry import SubAgentSpec
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Marks every run that happens *inside* a sub-agent. See "Stream isolation".
SUBAGENT_TAG = "macrovision:subagent"


class _TaskInput(BaseModel):
    """Arguments the orchestrator passes when delegating."""

    task: str = Field(
        description=(
            "What you need done, in plain language, as a self-contained "
            "instruction. The specialist cannot see the conversation, so "
            "include every detail it needs — ids, names, dates, and whether "
            "the user has approved anything."
        )
    )


def build_subagent_tool(
    spec: SubAgentSpec,
    tools: Sequence[BaseTool],
    *,
    model: str | None = None,
    queue: asyncio.Queue[tuple[str, str]] | None = None,
) -> BaseTool:
    """Build the orchestrator-facing tool that delegates to ``spec``."""
    settings = get_settings()
    # The graph is compiled per turn, not cached: these tools hold MCP sessions
    # scoped to this request, and a cached graph would close over dead ones.
    graph = create_react_agent(
        get_chat_model(model or settings.subagent_model or settings.chat_model),
        tools=list(tools),
    )

    async def run(task: str, run_manager: AsyncCallbackManagerForToolRun = None) -> str:
        logger.debug("Delegating to %s sub-agent: %s", spec.name, task)
        config = {
                "tags": [SUBAGENT_TAG, f"subagent:{spec.name}"],
                "recursion_limit": settings.subagent_recursion_limit,
        }
        if run_manager is not None:
            config["callbacks"] = run_manager.get_child()
            
        accum: dict[str, AIMessageChunk] = {}
        started = False
        try:
            async for chunk, metadata in graph.astream(
                {
                    "messages": [
                        SystemMessage(spec.system_prompt),
                        HumanMessage(task),
                    ]
                },
                stream_mode="messages",
                config=config,
            ):
                if not isinstance(chunk, AIMessageChunk) or chunk.tool_call_chunks:
                    continue
                text = extract_text(chunk.content)
                if not text:
                    continue
                if queue is not None:
                    if not started:
                        await queue.put({"kind": "subagent_start", "agent": spec.name})
                        started = True
                    print("run(%s): pushing %r", spec.name, text[:20])
                    await queue.put(
                        {"kind": "subagent_token", "agent": spec.name, "content": text}
                    )
                accum[chunk.id] = accum[chunk.id] + chunk if chunk.id in accum else chunk
        except GraphRecursionError:
            logger.warning("%s sub-agent hit its step limit on: %s", spec.name, task)
            return (
                f"The {spec.name} specialist used all its allowed steps without "
                "finishing. Try again with a narrower, more specific task."
            )
        except Exception as exc:
            logger.exception("%s sub-agent failed", spec.name)
            return (
                f"The {spec.name} specialist failed: {exc.__class__.__name__}. "
                "Tell the user this capability is unavailable right now; don't "
                "retry the same call."
            )
        finally:
            if queue is not None and started:
                await queue.put({"kind": "subagent_end", "agent": spec.name})

        last = next(reversed(accum.values()), None)
        reply = extract_text(last.content).strip() if last else ""
        return reply or f"The {spec.name} specialist returned nothing."

    return StructuredTool.from_function(
        coroutine=run,
        name=spec.tool_name,
        description=spec.description,
        args_schema=_TaskInput,
    )
