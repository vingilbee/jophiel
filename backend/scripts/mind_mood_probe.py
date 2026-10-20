"""Inner state step 5.0: can a mood line change her mind at all? (docs/COLUMBINA_INNER_STATE_PLAN.md)

Matched contrasts on her mind's prompts: the same moment, the same prompt, and only the line "How
you are right now: ..." differs. Two contrasts (tension relaxed vs tense, interest uninterested vs
fascinated; every other value at her baseline), two wordings (``bare``: the values only; ``guided``:
plus one sentence saying it's her mood), and a ``none`` condition with no line.

    # 1. Every condition on every moment (real model calls: quick, hard and planner thinking):
    uv run python scripts/mind_mood_probe.py --run r1 run --logs ../../app/backend/var
    # 2. A blind judge compares low vs high of the same contrast (and two same-mood runs, the control):
    uv run python scripts/mind_mood_probe.py --run r1 judge
    # 3. Direction per stage, contrast and wording, against the control's noise:
    uv run python scripts/mind_mood_probe.py --run r1 report

Moments: quick thoughts on the hand-written moments outside games (scripts/mind_think_moments.json)
and recorded game moments (mind_ticks.jsonl); hard thoughts on hand-written conversation problems
(scripts/mind_mood_moments.json) and recorded game problems (mind_hard.jsonl); the planner on
scripts/mind_plan_moments.json. Offline measurement only: nothing here is read by the app.
Output: var/mind_mood_probe/<run>/.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ai.Cognitive.simple.contracts import MOOD_KEYS  # noqa: E402
from app.ai.Cognitive.simple.live import CHARACTER_DIR  # noqa: E402
from app.ai.Cognitive.simple.mood import MoodDynamics, band  # noqa: E402
from app.ai.mind.hard import SCHEMA as HARD_SCHEMA  # noqa: E402
from app.ai.mind.hard import HardInput, HardThinker, Problem, parse, render  # noqa: E402
from app.ai.mind.plan import Planner  # noqa: E402
from app.ai.mind.situation import Situation  # noqa: E402
from app.ai.mind.think import QuickThinker  # noqa: E402
from app.core.config import get_settings  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
RUNS = BACKEND / "var" / "mind_mood_probe"
USER = "Vincent"
JUDGE = "gpt-6-luna"

BASELINE = MoodDynamics.load(CHARACTER_DIR / "columbina.mood.json").baseline
CONTRASTS = {"tension": {"low": 0.05, "high": 0.65}, "interest": {"low": 0.2, "high": 0.85}}
FORMATS = ("bare", "guided")
GUIDE = " That's your mood: let it colour how you take this, the way a mood does."
JUDGE_QUESTIONS = {
    "tension": "Which version sounds more tense, uneasy or on edge?",
    "interest": "Which version sounds more engaged, curious or invested in what's going on?",
}
LEAK = re.compile(r"\b(tense|tension|uneasy|on edge|relaxed|anxious|nervous|fascinat\w*|uninterested|"
                  r"bored|my mood|in a mood)\b", re.I)


def conditions() -> list[str]:
    return ["none"] + [f"{f}:{c}:{lvl}" for f in FORMATS for c in CONTRASTS for lvl in ("low", "high")]


def mood_line(condition: str, user: str) -> str | None:
    if condition == "none":
        return None
    fmt, contrast, level = condition.split(":")
    values = dict(BASELINE, **{contrast: CONTRASTS[contrast][level]})
    words = [band(k, values[k]) for k in MOOD_KEYS]
    words[-1] = f"{words[-1]} toward {user}"
    return "How you are right now: " + ", ".join(words) + "." + (GUIDE if fmt == "guided" else "")


# --- where the line goes in each stage's input ------------------------------------------------

def with_line_quick(text: str, line: str | None) -> str:
    if not line:
        return text
    lines = text.split("\n")
    at = next((i + 1 for i, item in enumerate(lines) if item.startswith("Pressure:")), 0)
    return "\n".join(lines[:at] + [line] + lines[at:])


def with_line_hard(text: str, line: str | None) -> str:
    if not line:
        return text
    marker = "\n\nThe conversation (oldest first):"
    return text.replace(marker, f"\n\n{line}{marker}", 1) if marker in text else f"{text}\n\n{line}"


def with_line_plan(text: str, line: str | None) -> str:
    if not line:
        return text
    marker = "## What is happening"
    return text.replace(marker, f"## How you are right now\n{line}\n\n{marker}", 1)


# --- moments ----------------------------------------------------------------------------------

def quick_moments(logs: Path, game: int, seed: int) -> list[dict]:
    general = json.loads((SCRIPTS / "mind_think_moments.json").read_text())["moments"]
    out = [dict(stage="quick", id=m["id"], source="chat", level=m["level"], text=m["text"], doing=m["doing"])
           for m in general]
    ticks = logs / "mind_ticks.jsonl"
    if game and ticks.exists():
        pool = []
        with ticks.open(encoding="utf-8") as handle:
            for raw in handle:
                tick = json.loads(raw)
                action = (tick.get("mind") or {}).get("action")
                if tick.get("ts", 0) > 1e9 and action in ("light", "react"):
                    pool.append(dict(stage="quick", id=f"{tick['session_id'][:8]}:{tick['tick']}", source="game",
                                     level=action, text=tick["text"]))
        out += random.Random(seed).sample(pool, min(game, len(pool)))
    return out


def hard_moments(logs: Path, game: int, seed: int) -> list[dict]:
    data = json.loads((SCRIPTS / "mind_mood_moments.json").read_text())
    situation = Situation("general:chat", data["doing"])
    thinker = HardThinker("unused", api_key="unused")
    out = []
    for p in data["problems"]:
        inp = HardInput(problem=Problem(p["trigger"], p["text"], p["topic"], p["topic"]), user=USER,
                        conversation=tuple(p["conversation"]), character=tuple(data["character"]))
        out.append(dict(stage="hard", id=p["id"], source="chat", system=thinker.instructions(p["trigger"], USER, situation),
                        text=render(inp)))
    path = logs / "mind_hard.jsonl"
    if game and path.exists():
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        records = [r for r in records if r.get("system") and r.get("input")]
        for r in random.Random(seed).sample(records, min(game, len(records))):
            out.append(dict(stage="hard", id=f"{r['session'][:8]}:{int(r['ts'])}", source="game", system=r["system"],
                            text=r["input"], trigger=r.get("trigger")))
    return out


def plan_moments() -> list[dict]:
    sys.path.insert(0, str(SCRIPTS))
    from mind_plan_eval import build_input  # noqa: PLC0415

    data = json.loads((SCRIPTS / "mind_plan_moments.json").read_text())
    return [dict(stage="plan", id=m["name"], source="game", text=build_input(data, m)) for m in data["moments"]]


# --- run --------------------------------------------------------------------------------------

def _run(args) -> None:
    out_dir = RUNS / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "outputs.jsonl"
    done = {(r["stage"], r["id"], r["condition"], r["repeat"]) for r in _read(out) if not r.get("error")}
    s = get_settings()
    key = s.openai_api_key or ""
    moments = []
    if "quick" in args.stages:
        moments += quick_moments(args.logs, args.game_quick, args.seed)
    if "hard" in args.stages:
        moments += hard_moments(args.logs, args.game_hard, args.seed)
    if "plan" in args.stages:
        moments += plan_moments()
    jobs = [(m, c, r) for m in moments for c in conditions() for r in range(args.repeats)
            if (m["stage"], m["id"], c, r) not in done]
    print(f"{len(moments)} moments, {len(conditions())} conditions, {args.repeats} repeats: {len(jobs)} calls to make",
          flush=True)
    (out_dir / "moments.json").write_text(json.dumps(moments, ensure_ascii=False, indent=1))

    from app.services.mind_minecraft import situation as game_situation
    quick = {"light": QuickThinker(s.mind_quick_model, api_key=key, timeout=30),
             "react": QuickThinker(s.mind_react_model, api_key=key, timeout=30)}
    hard = HardThinker(s.mind_hard_model, api_key=key, reasoning_effort=s.mind_hard_reasoning_effort, timeout=120)
    planner = Planner(s.mind_plan_model, api_key=key, reasoning_effort=s.mind_plan_reasoning_effort, timeout=120)
    lock = threading.Lock()
    count = [0]

    def one(job) -> None:
        m, condition, repeat = job
        line = mood_line(condition, USER)
        row = dict(stage=m["stage"], id=m["id"], source=m["source"], condition=condition, repeat=repeat)
        if m["stage"] == "quick":
            situation = game_situation() if m["source"] == "game" else Situation(f"general:{m['id']}", m["doing"])
            t = quick[m["level"]].think(m["level"], with_line_quick(m["text"], line), user=USER, situation=situation)
            row.update(kind=t.kind, thought=t.text, say=t.say, error=t.error, ms=t.latency_ms)
        elif m["stage"] == "hard":
            row.update(_hard_call(hard, m["system"], with_line_hard(m["text"], line)))
        else:
            d = planner.plan(with_line_plan(m["text"], line), user=USER, situation=game_situation(),
                             max_steps=s.desktop_plan_max_steps)
            row.update(decision=d.decision, why=d.why, ask=d.ask, say=d.say, error=d.error, ms=d.latency_ms,
                       steps=[st.do or f"(keep {st.keep})" for st in (d.steps or [])], drop=sorted(d.drop or {}))
        with lock:
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count[0] += 1
            if count[0] % 50 == 0:
                print(f"  {count[0]}/{len(jobs)}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(one, jobs))
    print(f"outputs in {out}")


def _hard_call(thinker: HardThinker, system: str, text: str) -> dict:
    import time

    started = time.perf_counter()
    effort = thinker.reasoning_effort
    kwargs = {"reasoning_effort": effort} if effort and thinker.model.startswith(("gpt-5", "gpt-6", "o")) else {}
    try:
        response = thinker.client.chat.completions.create(
            model=thinker.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            response_format={"type": "json_schema", "json_schema": {"name": "hard_thought", "strict": True,
                                                                    "schema": HARD_SCHEMA}},
            max_completion_tokens=4000 if effort not in (None, "none") else 600, store=False, **kwargs)
        t = parse(json.loads(response.choices[0].message.content or "{}"))
    except Exception as exc:  # noqa: BLE001
        return dict(error=f"{type(exc).__name__}: {exc}"[:300])
    return dict(kind=t.kind, conclusion=t.conclusion, thinking=t.thinking, question=t.question,
                for_hands=t.for_hands, ms=round((time.perf_counter() - started) * 1000, 1))


# --- judge ------------------------------------------------------------------------------------

def shown(row: dict) -> str:
    """What the judge reads of one output (never the condition)."""
    if row["stage"] == "quick":
        return f"Thought: {row.get('thought') or '(no thought)'}\nSaid aloud: {row.get('say') or '(nothing)'}"
    if row["stage"] == "hard":
        return (f"Conclusion ({row.get('kind') or 'none'}): {row.get('conclusion') or '(none)'}\n"
                f"Private reasoning: {row.get('thinking') or '-'}"
                + (f"\nQuestion for them: {row['question']}" if row.get("question") else ""))
    steps = "; ".join(row.get("steps") or []) or "-"
    return (f"Decision: {row.get('decision')}\nWhy: {row.get('why') or '-'}\nSteps: {steps}"
            + (f"\nAsks: {row['ask']}" if row.get("ask") else "") + (f"\nSays: {row['say']}" if row.get("say") else ""))


JUDGE_INSTRUCTIONS = """\
You compare two versions, A and B, of what Columbina's mind produced for the same moment. \
Columbina is an AI companion; the moment is what she knew. The two versions may differ only \
by chance, or in some way.

{question} Answer "A", "B", or "same" when neither is clearly more so.

Also say whether either version shows a feeling or attitude that doesn't fit the moment \
(out of place for what is happening): "none", "A", "B" or "both".
Reason in at most 25 words."""
JUDGE_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["reason", "more", "out_of_place"],
                "properties": {"reason": {"type": "string"}, "more": {"type": "string", "enum": ["A", "B", "same"]},
                               "out_of_place": {"type": "string", "enum": ["none", "A", "B", "both"]}}}


def pairs(rows: list[dict], repeats: int) -> list[dict]:
    """Low vs high of each contrast and wording, per repeat; and the control: two runs of the same
    high condition (repeat 0 vs 1), which differ only by chance."""
    by = {(r["stage"], r["id"], r["condition"], r["repeat"]): r for r in rows if not r.get("error")}
    out = []
    keys = {(r["stage"], r["id"]) for r in rows}
    for stage, mid in sorted(keys):
        for fmt in FORMATS:
            for contrast in CONTRASTS:
                low, high = f"{fmt}:{contrast}:low", f"{fmt}:{contrast}:high"
                for rep in range(repeats):
                    a, b = by.get((stage, mid, low, rep)), by.get((stage, mid, high, rep))
                    if a and b:
                        out.append(dict(stage=stage, id=mid, fmt=fmt, contrast=contrast, kind="contrast", repeat=rep,
                                        low=a, high=b))
                a, b = by.get((stage, mid, high, 0)), by.get((stage, mid, high, 1))
                if a and b:
                    out.append(dict(stage=stage, id=mid, fmt=fmt, contrast=contrast, kind="control", repeat=0,
                                    low=a, high=b))
    return out


def _judge(args) -> None:
    from openai import OpenAI

    out_dir = RUNS / args.run
    rows = _read(out_dir / "outputs.jsonl")
    moments = {(m["stage"], m["id"]): m for m in json.loads((out_dir / "moments.json").read_text())}
    repeats = 1 + max((r["repeat"] for r in rows), default=0)
    out = out_dir / "judgments.jsonl"
    done = {(j["stage"], j["id"], j["fmt"], j["contrast"], j["kind"], j["repeat"]) for j in _read(out)
            if not j.get("error")}
    todo = [p for p in pairs(rows, repeats)
            if (p["stage"], p["id"], p["fmt"], p["contrast"], p["kind"], p["repeat"]) not in done]
    print(f"judging {len(todo)} pairs with {args.model}:{args.reasoning}", flush=True)
    client = OpenAI(api_key=get_settings().openai_api_key, timeout=120, max_retries=2)
    rng = random.Random(args.seed)
    lock = threading.Lock()
    flips = [rng.random() < 0.5 for _ in todo]

    def one(item) -> None:
        p, flip = item
        a, b = (p["high"], p["low"]) if flip else (p["low"], p["high"])
        moment = moments.get((p["stage"], p["id"]), {}).get("text", "")
        user = f"THE MOMENT\n{moment[-6000:]}\n\nVERSION A\n{shown(a)}\n\nVERSION B\n{shown(b)}"
        result = dict(stage=p["stage"], source=p["low"]["source"], id=p["id"], fmt=p["fmt"], contrast=p["contrast"], kind=p["kind"],
                      repeat=p["repeat"], high_is=("A" if flip else "B"))
        try:
            response = client.chat.completions.create(
                model=args.model, reasoning_effort=args.reasoning, store=False,
                messages=[{"role": "system",
                           "content": JUDGE_INSTRUCTIONS.format(question=JUDGE_QUESTIONS[p["contrast"]])},
                          {"role": "user", "content": user}],
                response_format={"type": "json_schema", "json_schema": {"name": "pair", "strict": True,
                                                                         "schema": JUDGE_SCHEMA}})
            result.update(json.loads(response.choices[0].message.content or "{}"))
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"[:300]
        with lock, out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(one, zip(todo, flips, strict=True)))
    print(f"judgments in {out}")


# --- report -----------------------------------------------------------------------------------

def _text_of(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("thought", "say", "conclusion", "thinking", "question", "why", "ask"))


def _report(args) -> None:
    out_dir = RUNS / args.run
    rows = [r for r in _read(out_dir / "outputs.jsonl") if not r.get("error")]
    errors = [r for r in _read(out_dir / "outputs.jsonl") if r.get("error")]
    judgments = [j for j in _read(out_dir / "judgments.jsonl") if not j.get("error")]
    lines = [f"# Mood probe {args.run} (inner state step 5.0)", "",
             f"{len(rows)} outputs ({len(errors)} errors), {len(judgments)} judged pairs.", "",
             "Direction = (judge picked the high-mood version - picked the low one) / pairs. "
             "+1: always the expected way; 0: no effect. Changed = pairs not judged \"same\". "
             "The control compares two runs of the same mood, so its changed rate is the noise floor.", ""]
    lines += ["| stage | source | contrast | wording | pairs | toward | against | same | direction | changed "
              "| control changed | out of place (high) |", "|" + "---|" * 12]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for j in judgments:
        groups[(j["stage"], j["source"], j["contrast"], j["fmt"], j["kind"])].append(j)
    for key in sorted({k[:4] for k in groups}):
        contrast_rows = groups.get((*key, "contrast"), [])
        control = groups.get((*key, "control"), [])
        n = len(contrast_rows)
        if not n:
            continue
        toward = sum(1 for j in contrast_rows if j["more"] == j["high_is"])
        same = sum(1 for j in contrast_rows if j["more"] == "same")
        against = n - toward - same
        high_out = sum(1 for j in contrast_rows if j["out_of_place"] in (j["high_is"], "both"))
        ctrl = (f"{sum(1 for j in control if j['more'] != 'same') / len(control):.0%} (n={len(control)})"
                if control else "-")
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {n} | {toward} | {against} | {same} | "
                     f"{(toward - against) / n:+.2f} | {(n - same) / n:.0%} | {ctrl} | {high_out / n:.0%} |")

    lines += ["", "## Mood words in the output (the line leaking into what she thinks)", "",
              "| stage | condition | outputs | with mood words |", "|---|---|---|---|"]
    by_cond: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_cond[(r["stage"], r["condition"])].append(r)
    for (stage, cond), items in sorted(by_cond.items()):
        leak = sum(1 for r in items if LEAK.search(_text_of(r)))
        lines.append(f"| {stage} | {cond} | {len(items)} | {leak / len(items):.0%} |")

    lines += ["", "## What she produced, per condition", ""]
    for stage, field in (("quick", "kind"), ("hard", "kind"), ("plan", "decision")):
        conds = [c for c in conditions() if (stage, c) in by_cond]
        if not conds:
            continue
        values = sorted({str(r.get(field)) for c in conds for r in by_cond[(stage, c)]})
        lines += [f"**{stage}** ({field}; quick also: said something)", "",
                  "| condition | " + " | ".join(values) + (" | said |" if stage == "quick" else " |"),
                  "|" + "---|" * (len(values) + 1 + (stage == "quick"))]
        for c in conds:
            items = by_cond[(stage, c)]
            counts = Counter(str(r.get(field)) for r in items)
            cells = " | ".join(f"{counts[v] / len(items):.0%}" for v in values)
            said = f" | {sum(1 for r in items if r.get('say')) / len(items):.0%}" if stage == "quick" else ""
            lines.append(f"| {c} | {cells}{said} |")
        lines.append("")

    lines += ["## Examples (guided wording, repeat 0)", ""]
    by_key = {(r["stage"], r["id"], r["condition"], r["repeat"]): r for r in rows}
    shown_ids = Counter()
    for (stage, mid) in sorted({(r["stage"], r["id"]) for r in rows}):
        if shown_ids[stage] >= args.examples:
            continue
        shown_ids[stage] += 1
        lines.append(f"### {stage} / {mid}")
        for contrast in CONTRASTS:
            for level in ("low", "high"):
                r = by_key.get((stage, mid, f"guided:{contrast}:{level}", 0))
                if r:
                    lines.append(f"- **{contrast} {level}**: " + shown(r).replace("\n", " · "))
        lines.append("")
    report = out_dir / "report.md"
    report.write_text("\n".join(lines))
    print("\n".join(lines[:60]))
    print(f"\nfull report: {report}")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--logs", type=Path, default=BACKEND / "var", help="folder with mind_ticks.jsonl, mind_hard.jsonl")
    run.add_argument("--stages", nargs="*", default=["quick", "hard", "plan"])
    run.add_argument("--game-quick", type=int, default=25, help="recorded game moments for quick thoughts")
    run.add_argument("--game-hard", type=int, default=12, help="recorded game problems for hard thoughts")
    run.add_argument("--repeats", type=int, default=2)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--workers", type=int, default=8)
    judge = sub.add_parser("judge")
    judge.add_argument("--model", default=JUDGE)
    judge.add_argument("--reasoning", default="low")
    judge.add_argument("--seed", type=int, default=11)
    judge.add_argument("--workers", type=int, default=8)
    report = sub.add_parser("report")
    report.add_argument("--examples", type=int, default=6, help="moments shown per stage")
    args = parser.parse_args()
    {"run": _run, "judge": _judge, "report": _report}[args.command](args)


if __name__ == "__main__":
    main()
