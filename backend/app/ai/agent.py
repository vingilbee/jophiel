"""The chatbot agent: a LangGraph ReAct agent over OpenAI (via langchain-openai).

This builds the **orchestrator** — the agent the user actually talks to. It
holds no toolset tools of its own. Each MCP toolset is fronted by a specialist
sub-agent, and the orchestrator gets one tool per specialist (see
``app.ai.subagents``); those are assembled per turn in ``chat.py``, since they
close over MCP sessions scoped to a single request.

``BASE_TOOLS`` is for genuinely static tools — no per-user state, no session.
Add a ``@tool``-decorated function and the ReAct loop picks it up. Anything
needing credentials or a connection belongs in a toolset, not here.

The model client is cached, since the provider HTTP client is the expensive
part. The graph is only cached for the no-extra-tools case — compiling a small
ReAct graph is cheap, and caching graphs that close over a per-request MCP
session would hand later turns a dead connection.
"""
from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from app.core.config import get_settings

# Tools available to every user regardless of what they've connected.
BASE_TOOLS: list[Any] = []


@lru_cache
def get_chat_model(model: str | None = None) -> ChatOpenAI:
    """The OpenAI chat model. Streaming is driven by calling ``.astream``.

    ``model`` overrides ``settings.chat_model`` — voice turns pass a faster
    model, since time-to-first-token is invisible when you're reading a reply
    and very visible when you're waiting to hear one. Cached per model id.

    Sampling parameters are intentionally left at provider defaults.
    """
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "model": model or settings.chat_model,
        "max_tokens": settings.chat_max_tokens,
    }
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


def build_agent(extra_tools: Sequence[BaseTool] = (), model: str | None = None):
    """Compile a ReAct agent over the base tools plus any per-request ones.

    ``extra_tools`` is normally the set of sub-agent delegation tools built for
    this turn.
    """
    return create_react_agent(get_chat_model(model), tools=[*BASE_TOOLS, *extra_tools])


@lru_cache
def get_agent(model: str | None = None):
    """The agent for users with no specialists available. Cached per model."""
    return build_agent(model=model)
