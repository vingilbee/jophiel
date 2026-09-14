"""Conversions between our plain role/content turns and LangChain messages.

Kept separate from ``chat.py`` because the sub-agent layer needs the same
conversions and importing them from ``chat.py`` would be a cycle.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

# A single chat turn from the caller: {"role": "user"|"assistant", "content": str}
ChatTurn = dict[str, str]


def to_lc_messages(history: Iterable[ChatTurn]) -> list[BaseMessage]:
    """Convert stored role/content turns into LangChain messages."""
    messages: list[BaseMessage] = []
    for turn in history:
        role, content = turn["role"], turn["content"]
        if role == "user":
            messages.append(HumanMessage(content))
        elif role == "assistant":
            messages.append(AIMessage(content))
        else:
            raise ValueError(f"Unsupported message role: {role!r}")
    return messages


def extract_text(content: Any) -> str:
    """Pull plain text out of a message's content.

    Providers may return either a string or a list of typed blocks; only plain
    text belongs in the UI and persisted assistant transcript.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""
