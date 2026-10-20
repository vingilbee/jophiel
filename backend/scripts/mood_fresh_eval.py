"""Step 5.1d phase 3: the frozen mood dynamics on FRESH conversations (never used for tuning).

    uv run python scripts/mood_fresh_eval.py --run f1 scripted     # scripts/inner_state_fresh_scenarios.json, 3 runs each
    uv run python scripts/mood_fresh_eval.py --run f1 simulated    # new simulated users (seed 77, unused before)
    uv run python scripts/mood_fresh_eval.py --run f1 report       # needs docs/characters/columbina.inner_state.json

The scripted scenarios and their expectations were written before calibration. The simulated users
come from the scenario maker with a seed no earlier batch used. Labels are `appraisal-v3.1`; thought
and reply are the frozen teacher's. The report reads the frozen config and never changes it: if
anything here is used to revise the dynamics or labels, this set becomes development data and a
new fresh set is needed. Offline only. Output: var/mood_fresh/<run>/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))
ROOT = BACKEND.parent

import appraisal_v2_eval as v2  # noqa: E402
import mood_calibrate as cal  # noqa: E402
import reading_boundaries as rb  # noqa: E402

from app.ai.Cognitive.simple.appraisal import LABELS_V31, AppraisalTeacherV31  # noqa: E402
from app.ai.Cognitive.simple.contracts import Message  # noqa: E402
from app.ai.Cognitive.simple.scenario import make_scenarios  # noqa: E402
from app.ai.Cognitive.simple.simuser import DELAY_SECONDS, make_card, next_turn  # noqa: E402
from app.ai.mind import mood_layers as ml  # noqa: E402

RUNS = BACKEND / "var" / "mood_fresh"
FRESH = BACKEND / "scripts" / "inner_state_fresh_scenarios.json"
SEED = 77


def _read(path: Path) -> list[dict]:
    return rb._read(path)


async def scripted(out: Path, runs: int) -> None:
    data = json.loads(FRESH.read_text())
    gap = float(data["gap_seconds"])
    convs = {k: ([], [(m, gap) for m in v["messages"]]) for k, v in data["scenarios"].items()}
    await v2.run_set(out, convs, runs, AppraisalTeacherV31, LABELS_V31)


async def simulated(out: Path, count: int) -> None:
    llm = rb._llm()
    typed = rb.sample_typed_world(rb.WORLD, seed=0)
    done = {r["name"] for r in _read(out) if all("appraisal" in t for t in r["turns"])}
    scenarios = [s for s in make_scenarios(count, seed=SEED) if s.id not in done]
    print(f"simulated: {len(scenarios)} conversations", flush=True)

    async def one(scenario):
        teacher = AppraisalTeacherV31(llm, rb.PAGE, rb.DYNAMICS)
        card = await make_card(llm, scenario)
        history: list[Message] = []
        lines: list[tuple[str, str]] = []
        turns, previous, at = [], None, 0.0
        for i in range(scenario.turns):
            user = await next_turn(llm, card, scenario, history, turn_index=i, last_turn=i == scenario.turns - 1)
            message = user["message"].strip()
            gap = DELAY_SECONDS[user["delay"]] if i else 30.0
            at += gap
            bundle, hist = rb._bundle(lines, message, typed, previous)
            got = await teacher.samples(rb.View(rb.PAGE, bundle, hist, full=True), 1)
            if not got:
                turns.append(dict(message=message, at=at, error="no sample"))
                break
            s = got[0]
            turns.append(dict(message=message, at=at, number=bundle.turn, delay=user["delay"],
                              **v2._sample_row(s)))
            lines += [("user", message), ("columbina", s.extra["reply"])]
            history += [Message("user", message, bundle.turn), Message("columbina", s.extra["reply"], bundle.turn + 1)]
            previous = dict(understand=s.understand, decide=s.decide, intention=s.extra["intention"])
            if user["ending"] and i >= 3:
                break
        rb._append(out, dict(labels=LABELS_V31, name=scenario.id, scenario=scenario.to_dict(), turns=turns))
        print(f"  {scenario.id} ({scenario.user_type}): {len(turns)} turns", flush=True)

    rows = [r for r in _read(out) if r["name"] in done]
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    await asyncio.gather(*(one(s) for s in scenarios))


def frozen_config(unclear: float) -> ml.Config:
    if not cal.FROZEN.exists():
        raise SystemExit(f"no frozen config at {cal.FROZEN}: run scripts/mood_calibrate.py --write first")
    f = json.loads(cal.FROZEN.read_text())
    return ml.Config(f["baseline"], f["half_life_minutes"], reaction_half_life_minutes=f["reaction_half_life_minutes"],
                     keep=f["keep"], unclear_amount=unclear, incident_cap=f["incident_cap"], squash=f["squash"],
                     negative_keep_scale=f.get("negative_keep_scale", 1.0))


def report(run_dir: Path) -> str:
    frozen = json.loads(cal.FROZEN.read_text()) if cal.FROZEN.exists() else None
    if not frozen:
        raise SystemExit("freeze a config first")
    cfgs = {u: frozen_config(ua) for u, ua in cal.UNCLEAR.items()}
    L = [f"# Fresh evaluation {run_dir.name}", "", f"Frozen config: {frozen['chosen']} (from {frozen['labels']}). "
         "Mood / tension per message, mean over runs, shown as unclear-as-small | unclear-as-clear; "
         "the flat model (inner_state.py) for comparison.", ""]
    data = json.loads(FRESH.read_text())["scenarios"]
    rows = _read(run_dir / "scripted.jsonl")
    for name, spec in data.items():
        runs = [r for r in rows if r["name"] == name and all("appraisal" in t for t in r["turns"])]
        if not runs:
            continue
        lay = {u: cal.mean_traj([cal.trajectory(r["turns"], "layers", c, c.unclear_amount) for r in runs])
               for u, c in cfgs.items()}
        fl = cal.mean_traj([cal.trajectory(r["turns"], "flat", None, 0.25) for r in runs])
        L += [f"**{name}** ({len(runs)} runs): expected: {spec['expect']}", "",
              "| # | message | effects (run 0) | mood layered | tension layered | mood flat |", "|---|---|---|---|---|---|"]
        for i, m in enumerate(spec["messages"]):
            if i >= len(lay["as_small"]):
                break
            eff = ", ".join(f"{e['feeling']}:{e['status']}" + (f"/{e['size']}" if e['shift'] != 'none' else '')
                            for e in runs[0]["turns"][i]["appraisal"]["effects"]) or "-"
            L.append(f"| {i + 1} | {m[:40]} | {eff[:90]} | {lay['as_small'][i]['mood']:+.2f} / {lay['as_clear'][i]['mood']:+.2f} "
                     f"| {lay['as_small'][i]['tension']:.2f} / {lay['as_clear'][i]['tension']:.2f} | {fl[i]['mood']:+.2f} |")
        L.append("")

    sims = [r for r in _read(run_dir / "simulated.jsonl") if r["turns"] and all("appraisal" in t for t in r["turns"])]
    if sims:
        L += ["## Simulated conversations (fresh users)", "", f"{len(sims)} conversations, "
              f"{sum(len(r['turns']) for r in sims)} messages.", ""]
        for u, c in cfgs.items():
            n = top = 0
            quiet_moves, neg_up, eased_down, ends = [], [], [], defaultdict(list)
            for r in sims:
                traj = cal.trajectory(r["turns"], "layers", c, c.unclear_amount)
                prev = dict(cal.BASE)
                for t, v in zip(r["turns"], traj, strict=False):
                    n += 1
                    top += v["mood"] > 0.95 or v["interest"] > 0.95
                    effs = t["appraisal"]["effects"]
                    if not any(e["shift"] != "none" for e in effs):
                        quiet_moves.append(abs(v["mood"] - prev["mood"]))
                    if any(e["feeling"] in ("hurt", "annoyed", "uneasy") and e["shift"] == "up" for e in effs):
                        neg_up.append(v["tension"] - prev["tension"])
                    if any(e["status"] == "eased" and e["feeling"] in ("hurt", "annoyed", "uneasy") for e in effs):
                        eased_down.append(prev["tension"] - v["tension"])
                    prev = v
                ends[r["scenario"]["group"]].append(traj[-1]["mood"])

            def m(xs):
                return f"{sum(xs) / len(xs):+.3f} (n={len(xs)})" if xs else "-"
            L += [f"**unclear {u}**", "",
                  f"- mood or interest above .95: {top / n:.0%} of messages",
                  f"- mood change on messages with no shift (should be ~0, fading only): mean |change| {m(quiet_moves)}",
                  f"- tension change when a negative feeling shifts up (should be > 0): {m(neg_up)}",
                  f"- tension drop when a negative incident eases (should be > 0): {m(eased_down)}",
                  "- end mood by user group: " + ", ".join(f"{g} {sum(v) / len(v):+.2f} (n={len(v)})"
                                                          for g, v in sorted(ends.items())), ""]
        L += [f"User groups: {dict(Counter(r['scenario']['group'] for r in sims))}.", ""]
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scripted")
    p.add_argument("--runs", type=int, default=3)
    p = sub.add_parser("simulated")
    p.add_argument("--count", type=int, default=20)
    sub.add_parser("report")
    args = parser.parse_args()
    run_dir = RUNS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "report":
        text = report(run_dir)
        (run_dir / "report.md").write_text(text)
        print(text)
        return
    out = run_dir / f"{args.command}.jsonl"
    asyncio.run(scripted(out, args.runs) if args.command == "scripted" else simulated(out, args.count))
    print(f"done: {out}")


if __name__ == "__main__":
    main()
