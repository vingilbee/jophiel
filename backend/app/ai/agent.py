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
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import create_react_agent

from app.ai import openai_http
from app.core.config import get_settings

# Tools available to every user regardless of what they've connected.
BASE_TOOLS: list[Any] = []


def supports_reasoning_controls(model: str) -> bool:
    """Model families that accept ``reasoning_effort`` (others reject it)."""
    return model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


@lru_cache
def get_chat_model(model: str | None = None, reasoning: str | None = None,
                   max_tokens: int | None = None) -> ChatOpenAI:
    """The OpenAI chat model. Streaming is driven by calling ``.astream``.

    ``model`` overrides ``settings.chat_model`` — voice turns pass a faster
    model, since time-to-first-token is invisible when you're reading a reply
    and very visible when you're waiting to hear one. Cached per model id.

    ``reasoning``: an effort for a model that reasons while it uses tools (her desktop turns), on the Responses
    API; None: ``settings.chat_reasoning_effort`` on Chat Completions. Sampling parameters are intentionally left
    at provider defaults. ``max_tokens``: the output limit (reasoning included); None: ``settings.chat_max_tokens``.
    """
    settings = get_settings()
    model_id = model or settings.chat_model
    kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": max_tokens or settings.chat_max_tokens,
        # Token usage on streamed replies too (input, cached, output): app/ai/loop_timing.py reads it.
        "stream_usage": True,
    }
    # Reasoning-capable models (e.g. gpt-6-luna) would otherwise think for
    # seconds before the first word; conversation wants the quick reply.
    if reasoning and supports_reasoning_controls(model_id):
        # A model that thinks while it uses tools (her desktop turns): Chat Completions refuses function tools with
        # reasoning for these models, so this goes through the Responses API. Its reasoning comes back encrypted
        # and is passed back with the next call (nothing stored at OpenAI), so what she thought at one step is
        # still hers at the next.
        kwargs.update(use_responses_api=True, reasoning={"effort": reasoning},
                      include=["reasoning.encrypted_content"], store=False)
    elif settings.chat_reasoning_effort and supports_reasoning_controls(model_id):
        kwargs["reasoning_effort"] = settings.chat_reasoning_effort
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    # Connections kept between turns (and per event loop): app/ai/openai_http.py.
    kwargs["http_client"] = openai_http.sync_client()
    kwargs["http_async_client"] = openai_http.async_client()
    return ChatOpenAI(**kwargs)


PRIVATE_RUN = {"callbacks": [], "tags": ["nostream"]}


def invoke_private(runnable, messages):
    """Run a check model (a verdict, never her words) detached from the turn it is called in: no inherited
    callbacks, and tagged so LangGraph's message stream never carries its output to the user's transcript or
    speech. Its result reaches only the code that called it (and the diagnostic trace that code keeps)."""
    return runnable.invoke(messages, config=dict(PRIVATE_RUN))


def build_agent(extra_tools: Sequence[BaseTool] = (), model: str | None = None, pre_model_hook=None,
                reasoning: str | None = None, hands: frozenset[str] = frozenset(),
                hands_reasoning: str | None = None):
    """Compile a ReAct agent over the base tools plus any per-request ones.

    ``extra_tools`` is normally the set of sub-agent delegation tools built for
    this turn. ``pre_model_hook`` changes what each model call reads (her desktop
    tools use it to show her their screenshots).

    ``hands`` / ``hands_reasoning``: once a tool named in ``hands`` has answered in this turn (her hands on the phone
    screen), every later model call thinks at ``hands_reasoning`` first; before that, a turn that only talks stays
    on the quick model. Ignored when ``reasoning`` is already set for the whole turn.
    """
    tools = [*BASE_TOOLS, *extra_tools]
    if not (hands and hands_reasoning) or reasoning:
        return create_react_agent(get_chat_model(model, reasoning), tools=tools, pre_model_hook=pre_model_hook)
    quick = get_chat_model(model).bind_tools(tools)
    thinking = get_chat_model(model, hands_reasoning).bind_tools(tools)

    def choose(state, runtime):
        used = any(isinstance(m, ToolMessage) and m.name in hands for m in state["messages"])
        return thinking if used else quick

    return create_react_agent(choose, tools=tools, pre_model_hook=pre_model_hook)


@lru_cache
def get_agent(model: str | None = None):
    """The agent for users with no specialists available. Cached per model."""
    return build_agent(model=model)
