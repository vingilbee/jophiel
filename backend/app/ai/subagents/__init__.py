"""Per-toolset specialist agents the orchestrator delegates to.

``registry.py`` describes them as data (one entry per MCP toolset);
``builder.py`` turns an entry plus its live MCP tools into a single tool the
orchestrator can call. See ``registry.py`` for why the toolsets are fronted by
agents instead of being handed to the orchestrator directly.
"""
from app.ai.subagents.builder import SUBAGENT_TAG, build_subagent_tool
from app.ai.subagents.registry import SUBAGENTS, SubAgentSpec, subagents_for

__all__ = [
    "SUBAGENTS",
    "SUBAGENT_TAG",
    "SubAgentSpec",
    "build_subagent_tool",
    "subagents_for",
]
