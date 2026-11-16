"""AI layer: Jophiel's agent loop (LangChain + OpenAI) and her mind's pure parts.

Public surface:
    - AgentRuntimeContext : per-request context (user_id + future integrations)
    - stream_chat         : async generator of response text chunks
    - complete_chat       : await the full response as a single string

Exports resolve lazily so importing a sibling package such as
``app.ai.mind`` or ``app.ai.learning`` does not load the chat agent.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.chat import complete_chat, stream_chat
    from app.ai.context import AgentRuntimeContext

__all__ = ["AgentRuntimeContext", "stream_chat", "complete_chat"]

_EXPORTS = {
    "AgentRuntimeContext": "app.ai.context",
    "stream_chat": "app.ai.chat",
    "complete_chat": "app.ai.chat",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
