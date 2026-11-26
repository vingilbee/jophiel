"""Print a per-turn latency waterfall for recent voice turns.

    uv run python scripts/voice_latency_report.py              # last 10 turns + summary
    uv run python scripts/voice_latency_report.py --last 30
    uv run python scripts/voice_latency_report.py --summary    # p50/p95 only
    uv run python scripts/voice_latency_report.py --clear      # start a fresh baseline

Reads the JSON lines written by `app.services.voice_latency` (browser, API, and
worker marks for each voice turn) from VOICE_LATENCY_LOG_PATH.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import voice_latency  # noqa: E402


def _fmt(ms: float | None) -> str:
    return "" if ms is None else f"{ms:8.0f}"


def _print_turn(turn_id: str, turn: dict) -> None:
    stages = turn["stages"]
    begin = voice_latency.origin(stages)
    dims = turn["dims"]
    extras = ", ".join(
        f"{key}={dims[key]}"
        for key in ("endpoint", "outcome", "audio_ms", "stt_model", "model",
                    "agent", "clause_chars", "attempt")
        if key in dims
    )
    print(f"\nturn {turn_id}  {extras}")
    print(f"  {'stage':<26}{'t (ms)':>8}")
    for stage, at in sorted(stages.items(), key=lambda item: item[1]):
        if stage == "client_report":
            continue
        offset = None if begin is None else at - begin
        print(f"  {stage:<26}{_fmt(offset)}")
    segs = voice_latency.segments(stages)
    if segs:
        print(f"  {'segment':<26}{'ms':>8}")
        for name, value in segs.items():
            print(f"  {name:<26}{_fmt(value)}")
    steps = voice_latency.loop_steps(turn)
    if steps:
        print(f"  {'her loop':<26}{'ms':>8}")
        for step in steps:
            if step["kind"] == "model":
                bits = [f"first chunk {step['first_chunk_ms']:.0f} ms" if "first_chunk_ms" in step else None,
                        f"{step['tool_calls']} tool call(s)" if step.get("tool_calls") else None,
                        f"in {step['input_tokens']} / cached {step.get('cached_tokens') or 0} tok"
                        if step.get("input_tokens") is not None else None,
                        "failed" if step.get("failed") else None]
                label = f"model call {step.get('call')}"
            else:
                bits = [None if step.get("ok", True) else "failed"]
                label = f"  tool {step.get('tool')}"
            note = ", ".join(b for b in bits if b)
            print(f"  {label:<26}{_fmt(step.get('ms'))}  {note}")
    if "TOTAL_first_audio" not in segs:
        print("  (no first-audio total: browser report missing, or no audio played)")


def _print_summary(turns: dict[str, dict]) -> None:
    summary = voice_latency.summarize(turns)
    print(f"\nSummary over {len(turns)} turn(s)")
    print(f"  {'segment':<26}{'n':>4}{'p50':>9}{'p95':>9}{'max':>9}")
    for name, row in summary.items():
        print(f"  {name:<26}{row['n']:>4}{row['p50']:>9.0f}{row['p95']:>9.0f}{row['max']:>9.0f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, default=None, help="override the log path")
    parser.add_argument("--last", type=int, default=10, help="turns to show (default 10)")
    parser.add_argument("--summary", action="store_true", help="summary table only")
    parser.add_argument("--clear", action="store_true", help="delete the log and exit")
    args = parser.parse_args()

    path = args.path or voice_latency.log_path()
    if args.clear:
        path.unlink(missing_ok=True)
        print(f"Cleared {path}")
        return 0

    records = voice_latency.read_records(path)
    if not records:
        print(f"No voice latency marks in {path}")
        return 1
    turns = voice_latency.group_turns(records)
    # Most recent turns last, ordered by each turn's earliest mark.
    ordered = sorted(turns.items(), key=lambda item: min(item[1]["stages"].values()))
    recent = dict(ordered[-args.last:]) if args.last > 0 else dict(ordered)

    if not args.summary:
        for turn_id, turn in recent.items():
            _print_turn(turn_id, turn)
    _print_summary(recent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
