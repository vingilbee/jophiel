"""Which gate should decide how hard she thinks? Replay recorded ticks through each one.

    # 1. Play a pack session: her mind writes its ticks to var/mind_ticks.jsonl
    # 2. Ask each gate about every tick (or every Nth), timing each call:
    uv run python scripts/mind_gate_eval.py replay --gates chat:gpt-4.1-mini chat:gpt-4.1-nano decisions jev strands
    # 3. Have Luna (reasoning on) label a sample, the reference answers:
    uv run python scripts/mind_gate_eval.py judge --sample 300
    # 4. Compare: speed, cost per hour of play, agreement with the labels:
    uv run python scripts/mind_gate_eval.py report

Gates: chat:<model> (an OpenAI chat model), decisions[:<model>] (OpenAI Decisions
API, gpt-6-luna), jev[:<model>] (TypeSafe Jev: directly with TYPESAFE_API_KEY, jev-latest or
jev-preview; else via OpenRouter with OPENROUTER_API_KEY),
strands (Strands Decider 2B served locally:
`uvx --from strands-decider strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8765`).
Results are appended, so a run can be stopped and resumed: a tick a gate already
answered (without an error) is skipped. Output lives in var/mind_gate_eval/<run>/.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median, quantiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.mind import gates as g  # noqa: E402
from app.ai.mind.tick import MINECRAFT  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.mind_ticks import tick_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
JUDGE = "gpt-6-luna"
TICKS_PER_HOUR = 3600
# USD per million tokens (input, output). Chat prices are list prices to check
# before trusting a cost line; Decisions and Jev are from launch coverage
# (Jev also reports its cost per call, which wins when present).
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "openai-decisions:gpt-6-luna": (0.10, 0.0),
    "jev:typesafe/jev-1.13": (0.042, 0.0),
    "typesafe:jev-latest": (0.042, 0.0),
    "typesafe:jev-preview": (0.042, 0.0),
    "strands-decider-2b": (0.0, 0.0),
}


def load_ticks(path: Path, session: str | None = None) -> list[dict]:
    ticks = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                tick = json.loads(line)
                if session is None or tick["session_id"] == session:
                    ticks.append(tick)
    return ticks


def key(tick: dict) -> str:
    return f"{tick['session_id']}:{tick['tick']}"


def build_gate(spec: str):
    settings = get_settings()
    kind, _, arg = spec.partition(":")
    if kind == "chat":
        return g.ChatGate(arg or "gpt-4.1-mini", api_key=settings.openai_api_key)
    if kind == "decisions":
        return g.OpenAIDecisionsGate(arg or "gpt-6-luna", api_key=settings.openai_api_key)
    if kind == "jev":
        if settings.typesafe_api_key:
            return g.typesafe(settings.typesafe_api_key, arg or "jev-latest", settings.typesafe_base_url)
        if not settings.openrouter_api_key:
            raise SystemExit("jev needs TYPESAFE_API_KEY (or OPENROUTER_API_KEY) in backend/.env")
        return g.jev(settings.openrouter_api_key, arg or "typesafe/jev-1.13")
    if kind == "strands":
        return g.strands(arg or settings.strands_decider_url)
    raise SystemExit(f"unknown gate {spec!r}")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def replay(args) -> int:
    ticks = load_ticks(args.ticks, args.session)[:: args.every]
    if args.labelled:  # only what the judge labelled: enough to grade a slow gate
        labelled = {r["tick_key"] for r in read_jsonl(args.out / "labels.jsonl") if r.get("level")}
        ticks = [t for t in ticks if key(t) in labelled]
    if args.limit:
        ticks = ticks[: args.limit]
    out = args.out / "results.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["tick_key"], r["gate"]) for r in read_jsonl(out) if not r["result"].get("error")}
    lock = threading.Lock()

    def run(spec: str) -> None:
        gate = build_gate(spec)
        todo = [t for t in ticks if (key(t), gate.name) not in done]
        print(f"{gate.name}: {len(todo)} ticks to ask", flush=True)
        for i, tick in enumerate(todo, 1):
            result = gate.decide(tick["text"], MINECRAFT)
            row = {"tick_key": key(tick), "gate": gate.name, "pressure": tick["pressure"],
                   "unchanged": tick.get("unchanged", False), "result": result.to_dict(),
                   "capped": g.capped(result.level, tick["pressure"])}
            with lock, open(out, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            if result.error and i <= 3:
                print(f"  {gate.name} error: {result.error}", flush=True)
            if i % 50 == 0:
                print(f"  {gate.name}: {i}/{len(todo)}", flush=True)

    # One thread per gate, each asking in order: gates never wait on each other,
    # and each gate's latency is one call at a time, as it would be live.
    threads = [threading.Thread(target=run, args=(spec,)) for spec in args.gates]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"results in {out}")
    return 0


def judge(args) -> int:
    ticks = load_ticks(args.ticks, args.session)
    out = args.out / "labels.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    labelled = {r["tick_key"] for r in read_jsonl(out) if r.get("level")}
    # Most ticks change little: sample changed ticks more, but keep some quiet ones,
    # since answering "none" to a quiet second is half of what a gate must get right.
    rng = random.Random(args.seed)
    changed = [t for t in ticks if not t.get("unchanged") and key(t) not in labelled]
    quiet = [t for t in ticks if t.get("unchanged") and key(t) not in labelled]
    wanted = max(0, args.sample - len(labelled))  # --sample is the total, so a resumed run tops up
    n_quiet = min(len(quiet), wanted // 4)
    sample = rng.sample(changed, min(len(changed), wanted - n_quiet)) + rng.sample(quiet, n_quiet)
    gate = g.ChatGate(args.model, api_key=get_settings().openai_api_key, reasoning_effort=args.reasoning, judge=True,
                      timeout=120)
    print(f"judging {len(sample)} ticks with {gate.name}, {args.workers} at a time", flush=True)
    lock = threading.Lock()
    done = [0]

    def label(tick: dict) -> None:
        result = gate.decide(tick["text"], MINECRAFT)
        row = {"tick_key": key(tick), "judge": gate.name, "pressure": tick["pressure"], "level": result.level,
               "capped": g.capped(result.level, tick["pressure"]), "reason": result.reason, "error": result.error}
        with lock:
            with open(out, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"  {done[0]}/{len(sample)}", flush=True)

    # Labels aren't timed, so they can run side by side.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(label, sample))
    print(f"labels in {out}")
    return 0


def _pct(values: list[float], q: int) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return quantiles(values, n=100)[q - 1]


def report(args) -> int:
    results = read_jsonl(args.out / "results.jsonl")
    labels = {r["tick_key"]: r for r in read_jsonl(args.out / "labels.jsonl") if r.get("level")}
    if not results:
        raise SystemExit(f"no results in {args.out}; run replay first")
    by_gate: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_gate[r["gate"]].append(r)
    order = {level: i for i, level in enumerate(g.LEVELS)}
    lines = [f"# Mind gate comparison ({args.out.name})", "",
             f"{len({r['tick_key'] for r in results})} ticks asked; {len(labels)} labelled by the judge ({JUDGE}).",
             "Levels are compared after the combat cap (hard under combat counts as react).", "",
             "| gate | calls | errors | p50 ms | p90 ms | p99 ms | in tok | cached | $/hour at 1 tick/s | "
             "agree | thinks less | thinks more | quiet->none |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    details = []
    for name, rows in sorted(by_gate.items()):
        ok = [r for r in rows if not r["result"].get("error") and r["result"].get("level") in order]
        lat = [r["result"]["latency_ms"] for r in ok]
        tokens = [r["result"]["input_tokens"] for r in ok if r["result"].get("input_tokens")]
        cached = [r["result"].get("cached_tokens") or 0 for r in ok if r["result"].get("input_tokens")]
        out_tok = [r["result"].get("output_tokens") or 0 for r in ok]
        costs = [r["result"]["cost_usd"] for r in ok if r["result"].get("cost_usd") is not None]
        if costs:
            per_hour = sum(costs) / len(costs) * TICKS_PER_HOUR
        elif name in PRICES and tokens:
            price_in, price_out = PRICES[name]
            per_tick = (sum(tokens) / len(tokens) * price_in + sum(out_tok) / max(1, len(out_tok)) * price_out) / 1e6
            per_hour = per_tick * TICKS_PER_HOUR
        else:
            per_hour = None
        graded = [(r, labels[r["tick_key"]]) for r in ok if r["tick_key"] in labels]
        agree = sum(order[r["capped"]] == order[lab["capped"]] for r, lab in graded)
        less = sum(order[r["capped"]] < order[lab["capped"]] for r, lab in graded)
        more = sum(order[r["capped"]] > order[lab["capped"]] for r, lab in graded)
        quiet = [r for r in ok if r.get("unchanged")]
        quiet_none = sum(r["capped"] == "none" for r in quiet)
        frac = lambda n, d: f"{100 * n / d:.0f}%" if d else "-"  # noqa: E731
        lines.append(
            f"| {name} | {len(rows)} | {len(rows) - len(ok)} | {median(lat) if lat else 0:.0f} | {_pct(lat, 90):.0f} | "
            f"{_pct(lat, 99):.0f} | {sum(tokens) / len(tokens) if tokens else 0:.0f} | "
            f"{frac(sum(cached), sum(tokens))} | {'-' if per_hour is None else f'{per_hour:.3f}'} | "
            f"{frac(agree, len(graded))} | {frac(less, len(graded))} | {frac(more, len(graded))} | "
            f"{frac(quiet_none, len(quiet))} |")
        confusion = Counter((lab["capped"], r["capped"]) for r, lab in graded)
        details += ["", f"## {name}", "", f"Levels it chose: {dict(Counter(r['capped'] for r in ok))}",
                    f"By pressure: {dict(Counter((r['pressure'], r['capped']) for r in ok))}", "",
                    "| judge \\ gate | " + " | ".join(g.LEVELS) + " |", "|---|" + "---|" * len(g.LEVELS)]
        for want in g.LEVELS:
            details.append(f"| {want} | " + " | ".join(str(confusion[(want, got)]) for got in g.LEVELS) + " |")
        errors = Counter(r["result"]["error"][:80] for r in rows if r["result"].get("error"))
        if errors:
            details += ["", "Errors: " + "; ".join(f"{e} (x{n})" for e, n in errors.most_common(5))]
        misses = [(r, lab) for r, lab in graded if order[r["capped"]] < order[lab["capped"]]]
        if misses:
            details += ["", "Thought less than the judge (first 8):"]
            for r, lab in misses[:8]:
                details.append(f"- {r['tick_key']}: gate {r['capped']}, judge {lab['capped']} ({lab.get('reason')})")
    lines += ["", "*agree / thinks less / thinks more* are against the judge's labels; *quiet->none* is how often "
              "a tick where nothing changed got none."] + details
    text = "\n".join(lines) + "\n"
    (args.out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticks", type=Path, default=None, help="recorded ticks (default MIND_TICK_PATH)")
    parser.add_argument("--run", default="default", help="name of this comparison (output folder)")
    parser.add_argument("--session", help="only this session id")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("replay")
    p.add_argument("--gates", nargs="+", default=["chat:gpt-4.1-mini", "decisions"])
    p.add_argument("--every", type=int, default=1, help="ask every Nth tick")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--labelled", action="store_true", help="only ticks the judge labelled")
    p = sub.add_parser("judge")
    p.add_argument("--sample", type=int, default=300)
    p.add_argument("--model", default=JUDGE)
    p.add_argument("--reasoning", default="medium")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workers", type=int, default=6)
    sub.add_parser("report")
    args = parser.parse_args()
    args.ticks = args.ticks or tick_path()
    args.out = ROOT / "var" / "mind_gate_eval" / args.run
    return {"replay": replay, "judge": judge, "report": report}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
