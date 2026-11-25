"""Per-stage timing marks for voice turns.

The question this answers is "where do the seconds between the user going quiet
and Jophiel's voice starting actually go?" Each stage of a voice turn appends
one JSON line here, keyed by the turn's ``turn_id``; the browser, the API
process, and the chat worker all write to the same file, and
``scripts/voice_latency_report.py`` lines them up into a per-turn waterfall.

Timestamps are wall-clock epoch milliseconds, because the marks come from three
processes (browser, uvicorn, worker) and monotonic clocks can't be compared
across them. That's only valid while they share a clock, which is true for the
local single-machine setup this is built for.

Privacy: a mark carries a stage name, ids, and numeric/enum dimensions only.
Never pass transcript text, reply text, audio, or prompts as a dimension.

Instrumentation must never break a turn, so ``mark`` swallows every error.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_write_lock = threading.Lock()

# Stages the browser may report, in pipeline order. Anything else in a client
# report is dropped: the client is untrusted, and this keeps the file's
# vocabulary closed.
CLIENT_STAGES = (
    "speech_last_active",
    "vad_endpoint",
    "voice_end_sent",
    "browser_first_audio",
    "browser_playback_started",
)

# Derived segments: (name, from_stage, to_stage). A stage missing on a turn
# just leaves that segment blank. The order is the pipeline order.
SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("endpoint_wait", "speech_last_active", "vad_endpoint"),
    ("client_send", "voice_end_sent", "voice_end_received"),
    ("stop_prior_turn", "voice_end_received", "prior_turn_stopped"),
    ("stt", "prior_turn_stopped", "stt_final"),
    ("persist_enqueue", "stt_final", "job_enqueued"),
    ("queue_wait", "job_enqueued", "job_claimed"),
    ("context_prep", "job_claimed", "context_ready"),
    # Inside context_prep: history and tokens, her memory check (other reads run alongside it), the reads after it.
    ("prep_history", "job_claimed", "history_ready"),
    ("prep_mind", "history_ready", "mind_ready"),
    ("prep_after_mind", "mind_ready", "context_ready"),
    ("tools_setup", "context_ready", "tools_ready"),
    ("model_ttft", "tools_ready", "model_first_token"),
    ("first_clause", "model_first_token", "first_clause_emitted"),
    ("tts_queue", "first_clause_emitted", "tts_request_started"),
    ("tts_first_byte", "tts_request_started", "tts_first_byte"),
    ("publish", "tts_first_byte", "redis_first_audio"),
    ("server_relay", "redis_first_audio", "ws_first_audio"),
    ("network_to_browser", "ws_first_audio", "browser_first_audio"),
    ("playback_schedule", "browser_first_audio", "browser_playback_started"),
)

# Where the user's turn is considered to have ended, best evidence first.
# `speech_last_active` is only present for a VAD endpoint; a manual "I'm done"
# starts the clock when the button was pressed.
ORIGIN_STAGES = ("speech_last_active", "voice_end_sent", "voice_end_received")


def enabled() -> bool:
    return bool(get_settings().voice_latency_marks_enabled)


def log_path() -> Path:
    configured = Path(get_settings().voice_latency_log_path)
    return configured if configured.is_absolute() else _BACKEND_ROOT / configured


def _write(record: dict) -> None:
    line = json.dumps(record, separators=(",", ":"), default=str)
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # One short append per mark. Separate processes append to the same file;
    # O_APPEND keeps whole lines intact at this size.
    with _write_lock, path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def mark(
    stage: str,
    turn_id: uuid.UUID | str | None,
    *,
    source: str,
    wall_ms: float | None = None,
    **dims: str | int | float | bool | None,
) -> None:
    """Record that ``stage`` of voice turn ``turn_id`` happened now.

    ``dims`` are safe numeric/enum dimensions (byte counts, model names,
    outcomes). Never pass user or assistant text.
    """
    if turn_id is None:
        return
    try:
        if not enabled():
            return
        record = {
            "event": "voice_latency_mark",
            "stage": stage,
            "turn_id": str(turn_id),
            "source": source,
            "wall_ms": round(wall_ms if wall_ms is not None else time.time() * 1000, 1),
            "pid": os.getpid(),
        }
        record.update({k: v for k, v in dims.items() if v is not None})
        _write(record)
    except Exception:
        logger.debug("voice latency mark %s failed", stage, exc_info=True)


def record_client_report(
    turn_id: uuid.UUID,
    *,
    outcome: str,
    endpoint: str,
    marks: dict[str, float],
) -> None:
    """Write the browser's marks for one turn (one line per stage)."""
    for stage in CLIENT_STAGES:
        value = marks.get(stage)
        if value is None:
            continue
        mark(stage, turn_id, source="browser", wall_ms=value, endpoint=endpoint)
    mark("client_report", turn_id, source="browser", outcome=outcome, endpoint=endpoint)


# --------------------------------------------------------------------------- #
# Reading the log back (used by scripts/voice_latency_report.py)


def read_records(path: Path | None = None) -> list[dict]:
    path = path or log_path()
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a half-written line from a killed process
            if record.get("event") == "voice_latency_mark":
                records.append(record)
    return records


LOOP_PREFIX = "loop_"

# Her agent loop inside model_ttft (app/ai/loop_timing.py), summed per turn.
LOOP_METRICS = ("loop_first_call_wait", "loop_model_calls", "loop_model_ms", "loop_tools", "loop_tool_ms",
                "loop_first_input_tokens", "loop_first_cached_tokens")


def group_turns(records: Iterable[dict]) -> dict[str, dict]:
    """Fold marks into one dict per turn: stage -> first wall_ms, plus dims.

    Keeps the *first* occurrence of each stage, so a retried worker job reports
    the attempt the user actually waited on. Her loop's marks (``loop_*``) repeat
    within a turn, so they are kept in order under ``loop`` instead.
    """
    turns: dict[str, dict] = {}
    for record in records:
        turn = turns.setdefault(record["turn_id"], {"stages": {}, "dims": {}, "loop": []})
        stage = record["stage"]
        if stage.startswith(LOOP_PREFIX):
            turn["loop"].append(record)
            continue
        if stage not in turn["stages"]:
            turn["stages"][stage] = float(record["wall_ms"])
        for key, value in record.items():
            if key in {"event", "stage", "turn_id", "wall_ms", "pid", "source"}:
                continue
            turn["dims"].setdefault(key, value)
    return turns


def origin(stages: dict[str, float]) -> float | None:
    for stage in ORIGIN_STAGES:
        if stage in stages:
            return stages[stage]
    return None


def segments(stages: dict[str, float]) -> dict[str, float]:
    """Duration in ms of each pipeline segment present on this turn.

    The STT segment falls back to starting at ``voice_end_received`` for turns
    with no ``prior_turn_stopped`` mark.
    """
    out: dict[str, float] = {}
    for name, start, end in SEGMENTS:
        if name == "stt" and start not in stages:
            start = "voice_end_received"
        if start in stages and end in stages:
            out[name] = stages[end] - stages[start]
    begin = origin(stages)
    if begin is not None and "browser_playback_started" in stages:
        out["TOTAL_first_audio"] = stages["browser_playback_started"] - begin
    return out


def loop_steps(turn: dict) -> list[dict]:
    """Her loop on this turn as steps in order: each model call and each tool, with its duration in ms.

    Only the first worker attempt's loop (marks of a retried job's later attempts are dropped)."""
    loop = sorted(turn.get("loop") or [], key=lambda r: r["wall_ms"])
    pid = loop[0].get("pid") if loop else None
    open_calls: dict[object, dict] = {}
    open_tools: dict[object, list[dict]] = {}
    steps: list[dict] = []
    for record in loop:
        if record.get("pid") != pid:
            continue
        stage, at = record["stage"], float(record["wall_ms"])
        if stage == "loop_model_start":
            step = {"kind": "model", "call": record.get("call"), "start": at}
            open_calls[record.get("call")] = step
            steps.append(step)
        elif stage == "loop_model_first_chunk":
            step = open_calls.get(record.get("call"))
            if step is not None:
                step["first_chunk_ms"] = at - step["start"]
        elif stage == "loop_model_end":
            step = open_calls.pop(record.get("call"), None)
            if step is not None:
                step["ms"] = at - step["start"]
                for key in ("tool_calls", "input_tokens", "cached_tokens", "output_tokens", "failed"):
                    if key in record:
                        step[key] = record[key]
        elif stage == "loop_tool_start":
            step = {"kind": "tool", "tool": record.get("tool"), "start": at}
            open_tools.setdefault(record.get("tool"), []).append(step)
            steps.append(step)
        elif stage == "loop_tool_end":
            pending = open_tools.get(record.get("tool")) or []
            if pending:
                step = pending.pop(0)
                step["ms"] = at - step["start"]
                step["ok"] = record.get("ok")
    return steps


def loop_metrics(turn: dict) -> dict[str, float]:
    """Her loop summed for one turn (LOOP_METRICS); empty when the turn has no loop marks."""
    steps = loop_steps(turn)
    models = [s for s in steps if s["kind"] == "model"]
    tools = [s for s in steps if s["kind"] == "tool"]
    if not models:
        return {}
    out: dict[str, float] = {
        "loop_model_calls": len(models),
        "loop_model_ms": sum(s.get("ms", 0) for s in models),
        "loop_tools": len(tools),
        "loop_tool_ms": sum(s.get("ms", 0) for s in tools),
    }
    first = models[0]
    ready = turn["stages"].get("tools_ready")
    if ready is not None and "first_chunk_ms" in first:
        out["loop_first_call_wait"] = first["start"] + first["first_chunk_ms"] - ready
    if first.get("input_tokens") is not None:
        out["loop_first_input_tokens"] = first["input_tokens"]
        out["loop_first_cached_tokens"] = first.get("cached_tokens") or 0
    return out


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; fine for the tens of turns this is used on."""
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    rank = max(1, min(len(ordered), math.ceil(pct / 100 * len(ordered))))
    return ordered[rank - 1]


def summarize(turns: dict[str, dict]) -> dict[str, dict[str, float]]:
    """p50/p95/max per segment across turns that have it."""
    by_segment: dict[str, list[float]] = {}
    for turn in turns.values():
        for name, value in segments(turn["stages"]).items():
            by_segment.setdefault(name, []).append(value)
        for name, value in loop_metrics(turn).items():
            by_segment.setdefault(name, []).append(value)
    order = [name for name, _, _ in SEGMENTS] + list(LOOP_METRICS) + ["TOTAL_first_audio"]
    return {
        name: {
            "n": len(by_segment[name]),
            "p50": statistics.median(by_segment[name]),
            "p95": percentile(by_segment[name], 95),
            "max": max(by_segment[name]),
        }
        for name in order
        if name in by_segment
    }
