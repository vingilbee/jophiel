"""Is her hard thinking concluding things, or describing the moment back? (docs/COLUMBINA_HARD_THINKING_PLAN.md)

Every live hard thought's full input and answer is in MIND_HARD_LOG_PATH (var/mind_hard.jsonl).

    # What she concluded live: per trigger, kinds, empty answers, status lines, latency, tokens
    uv run python scripts/mind_hard_eval.py report
    # Replay the same inputs through another model or reasoning effort, side by side
    uv run python scripts/mind_hard_eval.py replay --model gpt-6-luna --effort none --last 30

A "status line" is a conclusion that only says what she's doing or that she's stuck
("I'm heading...", "I'm still...", "I can't..."): the thing hard thinking used to produce
and shouldn't any more. Replays go to var/mind_hard_eval/<model>-<effort>.jsonl.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.mind.hard import SCHEMA, HardThinker, parse  # noqa: E402
from app.core.config import get_settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATUS_LINE = re.compile(r"^(i'?m (still |now |just )?(heading|going|on my way|paused|stuck|trying|working|moving|"
                         r"waiting|doing|making|chopping|mining|following)|i can'?t |still )", re.IGNORECASE)


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def is_status_line(conclusion: str | None) -> bool:
    return bool(conclusion and STATUS_LINE.match(conclusion.strip()))


def summarize(records: list[dict], title: str) -> None:
    print(f"\n{title}: {len(records)} thoughts")
    if not records:
        return
    by_trigger: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_trigger[r.get("trigger") or "?"].append(r)
    for trigger, rows in sorted(by_trigger.items()):
        kinds = Counter(r.get("kind") or "(none)" for r in rows)
        print(f"  {trigger:9} {len(rows):3}  " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    concluded = [r for r in records if r.get("kind")]
    errors = [r for r in records if r.get("error")]
    status = [r for r in concluded if is_status_line(r.get("conclusion"))]
    ms = [r["ms"] for r in records if r.get("ms")]
    tokens = [(r.get("input_tokens") or 0, r.get("output_tokens") or 0) for r in records]
    print(f"  concluded {len(concluded)}, nothing to conclude {len(records) - len(concluded) - len(errors)}, "
          f"errors {len(errors)}")
    print(f"  status lines {len(status)} of {len(concluded)}" + (f" ({100 * len(status) / len(concluded):.0f}%)"
                                                                if concluded else ""))
    if ms:
        print(f"  latency median {median(ms) / 1000:.1f} s, max {max(ms) / 1000:.1f} s")
    if tokens:
        print(f"  tokens per thought: in {median(t[0] for t in tokens):.0f}, out {median(t[1] for t in tokens):.0f}"
              " (median)")
    for r in status[:5]:
        print(f"    status line: {r.get('conclusion')}")


def report(args) -> None:
    summarize(load(_path(args.log)), "Live")


def replay(args) -> None:
    records = [r for r in load(_path(args.log)) if r.get("system") and r.get("input")][-args.last:]
    if not records:
        print("Nothing to replay yet: play a session with MIND_ENABLED=true first.")
        return
    settings = get_settings()
    effort = None if args.effort == "none" else args.effort
    thinker = HardThinker(args.model, api_key=settings.openai_api_key or "", reasoning_effort=effort or "none")

    def one(record: dict) -> dict:
        import time

        started = time.perf_counter()
        kwargs = {"reasoning_effort": effort} if effort and args.model.startswith(("gpt-5", "gpt-6", "o")) else {}
        try:
            response = thinker.client.chat.completions.create(
                model=args.model,
                messages=[{"role": "system", "content": record["system"]}, {"role": "user", "content": record["input"]}],
                response_format={"type": "json_schema", "json_schema": {"name": "hard_thought", "strict": True,
                                                                        "schema": SCHEMA}},
                max_completion_tokens=4000 if effort else 600, store=False, **kwargs)
            out = parse(json.loads(response.choices[0].message.content or "{}"))
            usage = response.usage
            return {"trigger": record.get("trigger"), "topic": record.get("topic"), "kind": out.kind,
                    "conclusion": out.conclusion, "for_hands": out.for_hands, "question": out.question,
                    "live_kind": record.get("kind"), "live_conclusion": record.get("conclusion"),
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "input_tokens": getattr(usage, "prompt_tokens", None),
                    "output_tokens": getattr(usage, "completion_tokens", None)}
        except Exception as exc:  # noqa: BLE001
            return {"trigger": record.get("trigger"), "error": f"{type(exc).__name__}: {exc}"[:200]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, records))
    out_dir = ROOT / "var" / "mind_hard_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.model}-{args.effort}.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    summarize(records, "Live (same inputs)")
    summarize(results, f"Replay {args.model} / {args.effort}")
    print("\nSide by side:")
    for r in results:
        print(f"\n  [{r.get('trigger')}] {r.get('topic')}")
        print(f"    live:   ({r.get('live_kind')}) {r.get('live_conclusion')}")
        print(f"    replay: ({r.get('kind')}) {r.get('conclusion') or r.get('error')}")
    print(f"\nWritten to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", default=get_settings().mind_hard_log_path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("report")
    rep = sub.add_parser("replay")
    rep.add_argument("--model", default=get_settings().mind_hard_model)
    rep.add_argument("--effort", default="none", choices=["none", "low", "medium", "high"])
    rep.add_argument("--last", type=int, default=30)
    args = parser.parse_args()
    {"report": report, "replay": replay}[args.command](args)


if __name__ == "__main__":
    main()
