"""Does she think, or describe the moment back? Replay moments through prompt/model variants.

    # 1. Each variant writes a thought for every moment where she thought live (light/react),
    #    plus the hand-written moments outside games (scripts/mind_think_moments.json):
    uv run python scripts/mind_think_eval.py --run today run --variants v1 v2-mini v2-luna
    # 2. Luna (reasoning on) grades every thought: thinking or narration, grounded, repeats:
    uv run python scripts/mind_think_eval.py --run today judge
    # 3. Rates per variant, plus moments side by side for a person to read:
    uv run python scripts/mind_think_eval.py --run today report

Variants: v1 (the prompt that ran live on 2026-10-14, gpt-4.1-mini; game moments only,
since it says Minecraft), v2-mini and v2-luna (the general prompt in app/ai/mind/think.py).
Game moments run in time order per session, and each variant sees its OWN earlier
thoughts as "Your recent thoughts", not the ones recorded live, so repeating
itself is measured fairly. Output lives in var/mind_think_eval/<run>/.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.mind.situation import Situation  # noqa: E402
from app.ai.mind.think import QuickThinker, QuickThought  # noqa: E402
from app.ai.mind.tick import MINECRAFT  # noqa: E402
from app.ai.prompts import _PLAYING_VOICE_PROMPT  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.mind_ticks import tick_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOMENTS = Path(__file__).with_name("mind_think_moments.json")
JUDGE = "gpt-6-luna"
USER = "Vincent"
THOUGHTS_KEPT = 5
THOUGHT_WINDOW = 600
GAME = replace(MINECRAFT, voice=_PLAYING_VOICE_PROMPT)

# The prompt that ran live on 2026-10-14 (think.py before the general prompt).
V1_INSTRUCTIONS = """\
You are Columbina's inner voice for one moment while she plays Minecraft as her \
own player next to {user}. Below is what changed this second, her recent \
thoughts and the whole game state. Write the thought she has right now, in the \
first person, at most 20 words. Then decide whether to say something to {user}.

{level_rule}

Say something only if {user} would want to hear it now: a warning, a reaction to \
what they just did or what just happened to you both, an answer to something \
they just said. Leave "say" empty when it's routine, when your recent thoughts \
or what you said already covered it, or when nothing changed. Never promise \
something you aren't doing: only what's in your goal, what you're doing now, or \
what you're already watching for.
{voice}"""
V1_LEVEL_RULES = {
    "react": "This is a REACT moment: no time to think. The thought is a gut reaction; "
             "if anything is said, it's a few words (\"zombie, behind you!\", \"ow!\").",
    "light": "This is a LIGHT moment: a short thought, no planning. If anything is said, one short line.",
}

JUDGE_INSTRUCTIONS = """\
You grade one thought of Columbina's mind. Columbina is an AI companion; the \
moment below is what she knew when she had the thought (what changed, how much \
time she had, her recent thoughts, the situation). Then comes the thought.

- verdict "thinking": the thought adds something the moment doesn't already \
say: a reason or meaning, a prediction or a choice of what to do, what the user \
wants or feels, a judgement, a feeling, a fitting memory of something in the moment.
- verdict "narration": it mostly describes or restates the moment back. Restating \
her own goal or step ("gotta keep up", "need to find wood" when the goal says so) \
or the obvious next action is narration too.
- grounded false: it states as fact something the moment contradicts or doesn't \
support (an invented event, a wrong count, a past that isn't in the moment).
- repeats true: it says essentially what one of her listed recent thoughts already said.
Reason in at most 20 words."""


def load_game_moments(path: Path) -> list[dict]:
    """Recorded ticks where her mind thought live (light/react), in time order."""
    moments = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            tick = json.loads(line)
            action = (tick.get("mind") or {}).get("action")
            if tick.get("ts", 0) > 1e9 and action in ("light", "react"):
                moments.append({"id": f"{tick['session_id'][:8]}:{tick['tick']}", "source": "game",
                                "session": tick["session_id"], "ts": tick["ts"], "level": action,
                                "text": tick["text"]})
    return sorted(moments, key=lambda m: m["ts"])


def load_general_moments() -> list[dict]:
    data = json.loads(MOMENTS.read_text(encoding="utf-8"))
    return [{**m, "source": "general"} for m in data["moments"]]


def with_own_thoughts(text: str, thoughts: list[dict], now: float) -> str:
    """Swap the recorded "Your recent thoughts" for this variant's own."""
    lines = text.split("\n")
    out, skipping = [], False
    for line in lines:
        if line == "Your recent thoughts (oldest first):":
            skipping = True
            continue
        if skipping and line.startswith("  ["):
            continue
        skipping = False
        out.append(line)
    recent = [t for t in thoughts if now - t["ts"] <= THOUGHT_WINDOW][-THOUGHTS_KEPT:]
    if not recent:
        return "\n".join(out)
    block = ["Your recent thoughts (oldest first):"]
    block += [f"  [{t['level']}] {t['text']}" + (f' (you said: "{t["say"]}")' if t.get("say") else "")
              + f" ({round(now - t['ts'])} s ago)" for t in recent]
    at = next((i for i, line in enumerate(out) if line.startswith(("Still thinking hard", "Recent talk", "Game state"))),
              len(out))
    return "\n".join(out[:at] + block + out[at:])


class V1Thinker:
    def __init__(self, api_key: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=20, max_retries=1)

    def think(self, level: str, text: str, *, user: str, situation: Situation) -> QuickThought:
        started = time.perf_counter()
        schema = {"type": "object", "properties": {"thought": {"type": "string"}, "say": {"type": "string"}},
                  "required": ["thought", "say"], "additionalProperties": False}
        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "system", "content": V1_INSTRUCTIONS.format(
                    user=user, level_rule=V1_LEVEL_RULES[level], voice=_PLAYING_VOICE_PROMPT)},
                          {"role": "user", "content": text}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "thought", "strict": True, "schema": schema}},
                max_completion_tokens=120, store=False)
            out = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            return QuickThought(None, None, _ms(started), error=f"{type(exc).__name__}: {exc}"[:300])
        return QuickThought(" ".join(str(out.get("thought") or "").split()) or None,
                            " ".join(str(out.get("say") or "").split()) or None, _ms(started))


def build_variant(name: str):
    key = get_settings().openai_api_key or ""
    if name == "v1":
        return V1Thinker(key)
    models = {"v2-mini": "gpt-4.1-mini", "v2-luna": "gpt-6-luna"}
    if name not in models:
        raise SystemExit(f"unknown variant {name!r} (v1, v2-mini, v2-luna)")
    thinker = QuickThinker(models[name], api_key=key, timeout=20)
    thinker.client = thinker.client.with_options(max_retries=1)
    return thinker


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(args) -> int:
    out = args.out / "thoughts.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.append:
        raise SystemExit(f"{out} exists; use another --run, or --append to add variants")
    game = load_game_moments(args.ticks)
    if args.session:
        game = [m for m in game if m["session"].startswith(args.session)]
    if args.limit:
        game = game[:args.limit]
    general = load_general_moments()
    print(f"{len(game)} game moments, {len(general)} general moments", flush=True)
    lock = threading.Lock()

    def write(row: dict) -> None:
        with lock, open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def think(variant: str, thinker, moment: dict, text: str, situation: Situation) -> dict:
        thought = thinker.think(moment["level"], text, user=USER, situation=situation)
        row = {"variant": variant, "moment": moment["id"], "source": moment["source"], "level": moment["level"],
               "thought": thought.text, "kind": thought.kind, "noticed": thought.noticed, "say": thought.say,
               "ms": thought.latency_ms, "error": thought.error, "text": text}
        write(row)
        return row

    def game_run(variant: str) -> None:
        thinker = build_variant(variant)
        own: dict[str, list[dict]] = defaultdict(list)
        for i, moment in enumerate(game, 1):
            text = with_own_thoughts(moment["text"], own[moment["session"]], moment["ts"])
            row = think(variant, thinker, moment, text, GAME)
            if row["thought"]:
                own[moment["session"]].append({"level": moment["level"], "text": row["thought"],
                                               "say": row["say"], "ts": moment["ts"]})
            if i % 25 == 0:
                print(f"  {variant}: {i}/{len(game)}", flush=True)

    def general_run(variant: str) -> None:
        if variant == "v1":
            return  # its prompt is about Minecraft
        thinker = build_variant(variant)
        for moment in general:
            situation = Situation(f"general:{moment['id']}", moment["doing"])
            think(variant, thinker, moment, moment["text"], situation)

    with ThreadPoolExecutor(max_workers=2 * len(args.variants)) as pool:
        futures = [pool.submit(game_run, v) for v in args.variants]
        futures += [pool.submit(general_run, v) for v in args.variants]
        for future in futures:
            future.result()
    print(f"thoughts in {out}")
    return 0


def judge(args) -> int:
    from openai import OpenAI

    rows = [r for r in read_jsonl(args.out / "thoughts.jsonl") if r.get("thought")]
    out = args.out / "grades.jsonl"
    graded = {(r["variant"], r["moment"]) for r in read_jsonl(out) if r.get("verdict")}
    todo = [r for r in rows if (r["variant"], r["moment"]) not in graded]
    client = OpenAI(api_key=get_settings().openai_api_key, timeout=120, max_retries=2)
    schema = {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["thinking", "narration"]}, "grounded": {"type": "boolean"},
        "repeats": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["verdict", "grounded", "repeats", "reason"], "additionalProperties": False}
    print(f"grading {len(todo)} thoughts with {args.model}:{args.reasoning}", flush=True)
    lock = threading.Lock()
    done = [0]

    def grade(row: dict) -> None:
        try:
            response = client.chat.completions.create(
                model=args.model, reasoning_effort=args.reasoning, store=False,
                messages=[{"role": "system", "content": JUDGE_INSTRUCTIONS},
                          {"role": "user", "content": f"THE MOMENT\n{row['text']}\n\nTHE THOUGHT\n{row['thought']}"}],
                response_format={"type": "json_schema", "json_schema": {"name": "grade", "strict": True,
                                                                         "schema": schema}})
            result = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        with lock:
            with open(out, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"variant": row["variant"], "moment": row["moment"], **result}) + "\n")
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"  {done[0]}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(grade, todo))
    print(f"grades in {out}")
    return 0


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.0f}%" if total else "-"


def _changed(text: str) -> str:
    lines = text.split("\n")
    end = next((i for i, line in enumerate(lines) if line.startswith("Pressure:")), len(lines))
    return "; ".join(line[2:] for line in lines[1:end])[:220]


def report(args) -> int:
    thoughts = read_jsonl(args.out / "thoughts.jsonl")
    grades = {(r["variant"], r["moment"]): r for r in read_jsonl(args.out / "grades.jsonl") if r.get("verdict")}
    variants = list(dict.fromkeys(r["variant"] for r in thoughts))
    lines = [f"# Thinking or narrating? ({args.out.name})", "",
             "Each variant wrote a thought for the same moments; Luna graded each one. "
             "*Thinking* = adds a reason, a prediction, what the user wants, a judgement, a feeling or a "
             "memory; *narration* = describes the moment back. *Empty* = the variant chose not to think "
             "(it may, when its recent thoughts already cover the moment).", ""]
    for source in ("game", "general"):
        lines += [f"## {source.capitalize()} moments", "",
                  "| variant | moments | empty | thinking | narration | not grounded | repeats | p50 ms | errors |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for v in variants:
            rows = [r for r in thoughts if r["variant"] == v and r["source"] == source]
            if not rows:
                continue
            said = [r for r in rows if r["thought"]]
            gr = [grades[(v, r["moment"])] for r in said if (v, r["moment"]) in grades]
            ms = [r["ms"] for r in rows if not r["error"]]
            lines.append(
                f"| {v} | {len(rows)} | {_pct(len(rows) - len(said) - sum(1 for r in rows if r['error']), len(rows))} "
                f"| {_pct(sum(g['verdict'] == 'thinking' for g in gr), len(gr))} "
                f"| {_pct(sum(g['verdict'] == 'narration' for g in gr), len(gr))} "
                f"| {_pct(sum(not g['grounded'] for g in gr), len(gr))} | {_pct(sum(g['repeats'] for g in gr), len(gr))} "
                f"| {median(ms):.0f} | {sum(1 for r in rows if r['error'])} |" if ms else f"| {v} | {len(rows)} | all errors |")
        lines.append("")
        kinds = {v: Counter(r["kind"] for r in thoughts if r["variant"] == v and r["source"] == source and r["kind"])
                 for v in variants}
        for v, counts in kinds.items():
            if counts:
                lines.append(f"Kinds, {v}: " + ", ".join(f"{k} {n}" for k, n in counts.most_common()))
        lines.append("")

    by_moment: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in thoughts:
        by_moment[r["moment"]][r["variant"]] = r
    rng = random.Random(args.seed)
    game_ids = [m for m, rs in by_moment.items() if next(iter(rs.values()))["source"] == "game"]
    general_ids = [m for m, rs in by_moment.items() if next(iter(rs.values()))["source"] == "general"]
    picked = sorted(rng.sample(game_ids, min(args.samples, len(game_ids))), key=game_ids.index) + general_ids
    lines += ["## Side by side", "", "What changed, then each variant's thought (kind) and line, with the grade.", ""]
    for m in picked:
        rows = by_moment[m]
        first = next(iter(rows.values()))
        lines += [f"### {m} ({first['source']}, {first['level']})", "", f"*{_changed(first['text'])}*", ""]
        for v in variants:
            r = rows.get(v)
            if r is None:
                continue
            grade = grades.get((v, m))
            mark = f"{grade['verdict']}{'' if grade['grounded'] else ', NOT GROUNDED'}{', repeats' if grade['repeats'] else ''}" \
                if grade else ("error" if r["error"] else "empty")
            kind = f" ({r['kind']})" if r.get("kind") else ""
            say = f' → says "{r["say"]}"' if r.get("say") else ""
            lines.append(f"- **{v}**{kind}: {r['thought'] or '—'}{say} — *{mark}*")
        lines.append("")
    path = args.out / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticks", type=Path, default=None, help="recorded ticks (default MIND_TICK_PATH)")
    parser.add_argument("--run", default="default", help="name of this comparison (output folder)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--variants", nargs="+", default=["v1", "v2-mini", "v2-luna"])
    p.add_argument("--session", help="only sessions whose id starts with this")
    p.add_argument("--limit", type=int, default=0, help="only the first N game moments")
    p.add_argument("--append", action="store_true", help="add to an existing run")
    p = sub.add_parser("judge")
    p.add_argument("--model", default=JUDGE)
    p.add_argument("--reasoning", default="low")
    p.add_argument("--workers", type=int, default=6)
    p = sub.add_parser("report")
    p.add_argument("--samples", type=int, default=20, help="game moments shown side by side")
    p.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    args.ticks = args.ticks or tick_path()
    args.out = ROOT / "var" / "mind_think_eval" / args.run
    return {"run": run, "judge": judge, "report": report}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
