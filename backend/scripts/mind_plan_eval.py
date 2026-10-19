"""Does her planner keep the chain? Replay the moments the 2026-10-19 session went wrong.

    uv run python scripts/mind_plan_eval.py                 # each moment 3 times, gpt-6-luna, reasoning low
    uv run python scripts/mind_plan_eval.py --repeats 5 --reasoning none --show

Moments (scripts/mind_plan_moments.json, game states from the recorded ticks):
- bare_hands_iron: at the iron with nothing. Pass: the plan gets wood, a wooden pickaxe,
  then a stone pickaxe, then iron, in that order.
- on_track: the plan is already stone -> stone pickaxe -> iron, and "mine stone" failed
  (no stone in sight from the surface). Live, a turn replaced it with "mine iron ore".
  Pass: the stone pickaxe still comes before iron.
- water_shore: walking north "for stone" along water, her hands want the user. Pass: she
  doesn't stop to wait for the user (stone is under her feet; she has a wooden pickaxe).

The input is built the way services/game_planning builds it live (same sections and plan
and needs rendering), from the recorded facts. Each run calls the model: a few cents.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.mind.plan import Planner  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.game_planning import merged_steps, needs_text, plan_lines  # noqa: E402

MOMENTS = Path(__file__).with_name("mind_plan_moments.json")
USER = "Vincent"


def build_input(data: dict, moment: dict) -> str:
    sections = [
        ("Why you're planning now", [f"- {r}" for r in moment["reasons"]]),
        ("The goal and the plan", [*plan_lines(moment["plan"]),
                                   "Keep playing is off: after this goal, she stops and tells the user.",
                                   f"Her session: {moment['session']}."]),
        ("What it takes", [needs_text(moment["needs"])]),
        ("What is happening", ["On your mind (your own thoughts while playing, newest last): "
                               + "; ".join(moment["thoughts"]) + ".",
                               "Your player right now (from the game): " + moment["state"]]),
        ("What she can do", data["skills"]),
        ("How steps are written here", [data["steps_rule"]]),
        ("What she learned playing", data["learned"]),
        ("Who is who", [f'In Minecraft you are "Piqeeee" (never target yourself). Other players: "tearDrop12". '
                        f'{USER} plays as "tearDrop12" (online).']),
        ("Recent talk (oldest first)", [f"  {line}" for line in moment["talk"]]),
    ]
    return "\n\n".join(f"## {title}\n" + "\n".join(lines) for title, lines in sections)


def _first(steps: list[dict], *words: str) -> int | None:
    for i, step in enumerate(steps):
        text = step["do"].lower().replace("_", " ")
        if any(word in text for word in words):
            return i
    return None


def final_steps(moment: dict, decision) -> list[dict]:
    """The open steps she'd be on after this decision (as game_planning.apply would set them)."""
    current = moment["plan"]["steps"]
    if decision.decision == "revise":
        return merged_steps(current, decision.steps, decision.drop)
    if decision.decision == "new_goal":
        return [{"do": s.do, "done_when": s.done_when} for s in decision.steps if s.do]
    return [{"do": s["do"], "done_when": s["done_when"]} for s in current if s["state"] in ("pending", "active")]


def judge(moment: dict, decision) -> tuple[bool, str]:
    steps = final_steps(moment, decision)
    stone_pick = _first(steps, "stone pickaxe")
    iron = _first(steps, "iron")
    if moment["expect"] == "chain":
        wood = _first(steps, "log", "wood", "tree")
        wooden = _first(steps, "wooden pickaxe")
        order = [wood, wooden, stone_pick, iron]
        ok = None not in order and order == sorted(order)
        return ok, f"wood {wood}, wooden pickaxe {wooden}, stone pickaxe {stone_pick}, iron {iron}"
    if moment["expect"] == "keep_order":
        ok = decision.decision != "wait_for_user" and stone_pick is not None and (iron is None or stone_pick < iron)
        return ok, f"stone pickaxe {stone_pick}, iron {iron}"
    if moment["expect"] == "no_wait":
        # Not waiting for the user, and not back on the step her hands are stuck on.
        stuck = next(s["do"] for s in moment["plan"]["steps"] if s["state"] == "active")
        same = bool(steps) and steps[0]["do"] == stuck
        ok = decision.decision != "wait_for_user" and not same
        return ok, ("stuck step kept first; " if same else "") + "asked: " + (decision.ask or "-")
    raise ValueError(moment["expect"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=None, help="default MIND_PLAN_MODEL")
    parser.add_argument("--reasoning", default=None, help="none|low|medium (default MIND_PLAN_REASONING_EFFORT)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", nargs="*", help="moment names")
    parser.add_argument("--show", action="store_true", help="print each decision's steps")
    parser.add_argument("--print-input", action="store_true", help="print the planning input and stop")
    args = parser.parse_args()

    settings = get_settings()
    data = json.loads(MOMENTS.read_text(encoding="utf-8"))
    moments = [m for m in data["moments"] if not args.only or m["name"] in args.only]
    if args.print_input:
        for moment in moments:
            print(f"===== {moment['name']}\n{build_input(data, moment)}\n")
        return 0
    from app.services.mind import situation

    planner = Planner(args.model or settings.mind_plan_model, api_key=settings.openai_api_key or "",
                      reasoning_effort=args.reasoning if args.reasoning is not None else settings.mind_plan_reasoning_effort)
    print(f"model {planner.model}, reasoning {planner.reasoning_effort}, {args.repeats} runs per moment\n")
    passed_all = 0
    total = 0
    for moment in moments:
        text = build_input(data, moment)
        passed = 0
        print(f"== {moment['name']}: {moment['note']}")
        for run in range(args.repeats):
            decision = planner.plan(text, user=USER, situation=situation(),
                                    max_steps=settings.desktop_plan_max_steps)
            if decision.decision is None:
                print(f"  run {run + 1}: ERROR {decision.error}")
                continue
            ok, detail = judge(moment, decision)
            passed += ok
            print(f"  run {run + 1}: {'PASS' if ok else 'FAIL'} {decision.decision:<13} {detail} "
                  f"({decision.latency_ms / 1000:.1f} s)")
            print(f"         why: {decision.why}")
            if decision.ask or decision.say:
                print(f"         {'ask' if decision.ask else 'say'}: {decision.ask or decision.say}")
            if args.show:
                for step in final_steps(moment, decision):
                    print(f"           - {step['do']}  [done when: {step['done_when']}]")
        print(f"  {passed}/{args.repeats} passed\n")
        passed_all += passed
        total += args.repeats
    print(f"total {passed_all}/{total}")
    return 0 if passed_all == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
