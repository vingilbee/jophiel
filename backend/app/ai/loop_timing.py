"""Timing of her agent loop inside one turn: each model call and each tool, as stage marks.

``model_ttft`` in ``services/voice_latency.py`` runs from the model request to the first word she speaks, so on a
turn where she reads the phone screen, taps, or searches memory first, it also holds every one of those steps. This
splits it: a callback on the turn's run reports, through the caller's ``on_stage(stage, **dims)``:

- ``loop_model_start`` / ``loop_model_first_chunk`` / ``loop_model_end`` for each of her own model calls
  (``call`` = 1, 2, ...; at the end: tool calls asked for, input tokens, how many of them OpenAI's prompt cache
  served, output tokens);
- ``loop_tool_start`` / ``loop_tool_end`` for each of her tools (``tool`` = its name, ``ok``).

Only her own calls count: a model or tool run inside one of her tools (a specialist sub-agent, a check model) is
part of that tool's time. Dimensions are names and numbers only, never text. Never raises into the run.
"""
from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

OnStage = Callable[..., None]


class LoopTimer(BaseCallbackHandler):
    run_inline = True  # marks are stamped when the event happens, not when an executor gets to them
    raise_error = False

    def __init__(self, on_stage: OnStage) -> None:
        self._on_stage = on_stage
        self._parents: dict[uuid.UUID, uuid.UUID | None] = {}
        self._tools: dict[uuid.UUID, str] = {}
        self._models: dict[uuid.UUID, int] = {}
        self._streamed: set[uuid.UUID] = set()
        self._calls = 0

    # -- bookkeeping ------------------------------------------------------------------------------------------- #

    def _mark(self, stage: str, **dims: Any) -> None:
        with contextlib.suppress(Exception):
            self._on_stage(stage, **dims)

    def _seen(self, run_id: uuid.UUID, parent_run_id: uuid.UUID | None) -> None:
        self._parents[run_id] = parent_run_id

    def _inside_tool(self, parent_run_id: uuid.UUID | None) -> bool:
        hops = 0
        while parent_run_id is not None and hops < 64:
            if parent_run_id in self._tools:
                return True
            parent_run_id = self._parents.get(parent_run_id)
            hops += 1
        return False

    # -- chains (only to know who is inside whom) ----------------------------------------------------------- #

    def on_chain_start(self, serialized: Any, inputs: Any, *, run_id: uuid.UUID,
                       parent_run_id: uuid.UUID | None = None, **kwargs: Any) -> None:
        self._seen(run_id, parent_run_id)

    # -- her model calls ----------------------------------------------------------------------------------------- #

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: uuid.UUID,
                            parent_run_id: uuid.UUID | None = None, **kwargs: Any) -> None:
        self._seen(run_id, parent_run_id)
        if self._inside_tool(parent_run_id):
            return
        self._calls += 1
        self._models[run_id] = self._calls
        self._mark("loop_model_start", call=self._calls)

    def on_llm_new_token(self, token: str, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        call = self._models.get(run_id)
        if call is None or run_id in self._streamed:
            return
        self._streamed.add(run_id)
        self._mark("loop_model_first_chunk", call=call)

    def on_llm_end(self, response: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        call = self._models.pop(run_id, None)
        if call is None:
            return
        dims: dict[str, Any] = {"call": call}
        with contextlib.suppress(Exception):
            message = response.generations[0][0].message
            dims["tool_calls"] = len(getattr(message, "tool_calls", None) or [])
            usage = getattr(message, "usage_metadata", None) or {}
            if usage:
                dims["input_tokens"] = usage.get("input_tokens")
                dims["cached_tokens"] = (usage.get("input_token_details") or {}).get("cache_read")
                dims["output_tokens"] = usage.get("output_tokens")
        self._mark("loop_model_end", **dims)

    def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        call = self._models.pop(run_id, None)
        if call is not None:
            self._mark("loop_model_end", call=call, failed=True)

    # -- her tools ------------------------------------------------------------------------------------------------ #

    def on_tool_start(self, serialized: Any, input_str: str, *, run_id: uuid.UUID,
                      parent_run_id: uuid.UUID | None = None, **kwargs: Any) -> None:
        self._seen(run_id, parent_run_id)
        if self._inside_tool(parent_run_id):
            return
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._tools[run_id] = name
        self._mark("loop_tool_start", tool=name, call=self._calls)

    def on_tool_end(self, output: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        name = self._tools.get(run_id)
        if name is not None:
            self._mark("loop_tool_end", tool=name, ok=True)

    def on_tool_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        name = self._tools.get(run_id)
        if name is not None:
            self._mark("loop_tool_end", tool=name, ok=False)
