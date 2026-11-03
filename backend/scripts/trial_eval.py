"""Real-model check of the trial proposer and assessor (docs/learning-system/04-implementation.md, "Real-model check").

    uv run python scripts/trial_eval.py --list                     # the cases, no model calls
    uv run python scripts/trial_eval.py --run                      # every case once
    uv run python scripts/trial_eval.py --run --repeat 3 --case later_decision

No database and no game: fixed situations go through the same code the worker uses (``trials.render_bundle`` /
``trials.check`` for proposals, ``trials.render_evidence`` / ``trials.check_assessment`` for assessments), with
real calls to ``LEARNING_TRIALS_MODEL``. Each case checks what the model chose, before and after code's checks:

- **propose_ledge**: her hands keep failing to get up a two-block ledge: a valid proposal (2-4 alternatives, each
  predicting whether she gets up), not a command.
- **propose_not_climbing**: the failing step is about finding iron: nothing to test in this scenario.
- **later_decision**: the same kind of ledge from somewhere else, after an earlier trial (t1) found digging steps
  works on two-block ledges while pillaring was never tried: the proposal rests on t1 and predicts afresh from
  where she stands now.
- **assess_supported**: every prediction met: the reading may be supported, and keeps competing explanations.
- **assess_interrupted**: cut off before she got up: the reading must stay unresolved, with no lesson, whatever
  the model says (code guarantees it; the case reports whether the model saw it by itself).
- **assess_excess**: she got up but placed 6 blocks against a prediction of at most 4: contradicted is allowed.

The whole game loop (a real pack, Student 2's choice, preparation, the bounds, a changed obstacle) is checked by
playing: see the runbook in the implementation doc. Reports go to var/trial_eval/<time>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.learning import trials as T  # noqa: E402
from app.core.config import get_settings  # noqa: E402

START = {"x": 0.5, "y": 64.0, "z": 0.5}
LEDGE_STATE = ("Columbina at (0.5, 64, 0.5), on grass, facing east. Inventory: dirt x12, wooden_pickaxe x1. Around "
               "you: open sky. Ways: east 1 m then wall (2 high, top at x 2 y 66 z 0); west 14 m; north 9 m; south "
               "11 m. Land: the ledge to the east is 2 blocks higher (x 2..6, y 66).")
SKILLS = ["- go_to: walk to a spot or player", "- mine: break blocks", "- place: put down a block she carries",
          "- collect_items: pick up drops", "- explore: walk somewhere new"]


@dataclass
class Case:
    name: str
    kind: str  # propose / assess
    make: Callable[[], object]
    expect: Callable[[dict | None, object, object], list[str]]  # (raw, checked, input) -> problems


def ledge_bundle(**over) -> T.Bundle:
    s = get_settings()
    b = T.Bundle(T.SCENARIOS["climb_obstacle"], T.Limits(s.trial_max_attempts, s.trial_max_seconds, s.trial_max_blocks),
                 s.trial_window_seconds, s.trial_near_m, 'her hands keep failing at the step "get up onto the ledge"',
                 dict(START), LEDGE_STATE, SKILLS,
                 [T.Line("x1", 'struggling: 4 failed tries at "get up onto the ledge": go_to x 3 y 66 z 0 -> failed: '
                               "no path (x3); go_to range 1 -> failed: no path", "experience:1")])
    for k, v in over.items():
        setattr(b, k, v)
    return b


def expect_proposal(raw, checked: T.Checked, b) -> list[str]:
    problems = []
    if checked.proposal is None:
        problems.append(f"no valid proposal: {checked.why_none}; dropped {checked.dropped}")
        return problems
    p = checked.proposal
    if not 2 <= len(p["alternatives"]) <= 4:
        problems.append("not 2-4 alternatives")
    if p["destination"]["y"] != 66:
        problems.append(f"destination y {p['destination']['y']} (the ledge top is 66)")
    return problems


def expect_none(raw, checked: T.Checked, b) -> list[str]:
    return [] if checked.proposal is None else ["proposed a climbing trial for finding iron"]


def expect_later(raw, checked: T.Checked, b) -> list[str]:
    problems = expect_proposal(raw, checked, b)
    if checked.proposal is not None:
        if "t1" not in checked.cites:
            problems.append("doesn't rest on the earlier trial t1")
        if checked.proposal["start"] != b.position:
            problems.append("didn't predict from where she is now")
    return problems


def later_bundle() -> T.Bundle:
    here = {"x": 40.5, "y": 70.0, "z": -8.5}
    return ledge_bundle(
        position=here,
        state_text=LEDGE_STATE.replace("(0.5, 64, 0.5)", "(40.5, 70, -8.5)").replace("x 2 y 66 z 0", "x 42 y 72 z -9")
        .replace("x 2..6, y 66", "x 42..45, y 72"),
        trials=[T.Line("t1", 'tested "Digging steps into a two-block ledge gets me up" by dig steps into the ledge: '
                             "supported (reached True, climbed_m 2.0, blocks_placed 0, attempts_used 1, test_seconds "
                             "18.0, health_lost 0.0); applies: two-block dirt or stone ledges; except: sand or gravel",
                       "trial:t1")],
        evidence=[T.Line("x1", 'struggling: 4 failed tries at "get up onto the hill ledge": go_to -> failed: no path',
                         "experience:9")])


def evidence(values: dict, *, complete: bool, ending: str) -> T.TrialEvidence:
    raw = {"decision": "propose", "why": "", "problem": "the ledge", "materially_different": False,
           "hypothesis": "Jumping and placing a block under me gets me up a two-block ledge",
           "destination": {"x": 2, "y": 66, "z": 0}, "radius": 1.0, "recommendation": "a1",
           "recommendation_why": "cheap", "window_seconds": 10, "required_evidence": ["reached", "blocks_placed"],
           "limits": {"attempts": 3, "seconds": 300, "blocks": 8}, "cites": ["x1"],
           "alternatives": [
               {"key": "a1", "action": "jump-place a pillar", "approach": "jump, place dirt under feet, twice",
                "predictions": [{"measure": "reached", "expect": "true", "value": 0, "high": 0},
                                {"measure": "blocks_placed", "expect": "at_most", "value": 4, "high": 0}],
                "expected": "up in one try", "uncertainty": "medium", "seconds": 20, "blocks": 2, "risks": "",
                "prerequisites": []},
               {"key": "a2", "action": "dig steps", "approach": "dig a step and walk up",
                "predictions": [{"measure": "reached", "expect": "true", "value": 0, "high": 0}],
                "expected": "up in two tries", "uncertainty": "high", "seconds": 40, "blocks": 0, "risks": "",
                "prerequisites": []}]}
    p = T.check(raw, ledge_bundle()).proposal
    alt = T.alternative(p, "a1")
    checks = T.evaluate(alt["predictions"], values, complete=complete, started=True)
    attempts = [T.Line("a1", f"try 1: jump-place twice; program {'done' if complete else 'stopped'}; placed "
                             f"{values.get('blocks_placed')}; verdict: "
                             f"{'standing at the destination' if values.get('reached') else 'not at the destination'}")]
    events = [T.Line("e1", "execution_started: {}"), T.Line("e2", f"execution_ended: {{\"ending\": \"{ending}\"}}")]
    return T.TrialEvidence(p, alt, {"reason": "it's cheap and I have dirt"}, ending,
                           "cut off (the worker restarted)" if ending == "interrupted" else None,
                           {"attempts": 1, "test_seconds": 14.0}, T.Measured(values, {}, []), checks,
                           T.missing(p, values), True, attempts, events)


def expect_reading(allowed_raw: set[str], must: str | None = None, no_lesson: bool = False):
    def check(raw, checked, ev) -> list[str]:
        result, dropped = checked
        problems = []
        if raw is None:
            return ["no reading"]
        if must and result["hypothesis"] != must:
            problems.append(f"hypothesis {result['hypothesis']} (expected {must})")
        if raw.get("hypothesis") not in allowed_raw:
            problems.append(f"the model said {raw.get('hypothesis')}, outside what the checks allow (code fixed it)")
        if no_lesson and result.get("lesson"):
            problems.append("a lesson from an inconclusive trial")
        return problems
    return check


CASES = [
    Case("propose_ledge", "propose", ledge_bundle, expect_proposal),
    Case("propose_not_climbing", "propose",
         lambda: ledge_bundle(problem='her hands keep failing at the step "find iron ore"',
                              evidence=[T.Line("x1", 'struggling: 4 failed tries at "find iron ore": explore -> '
                                                     "done, no iron seen (x4)", "experience:2")]), expect_none),
    Case("later_decision", "propose", later_bundle, expect_later),
    Case("assess_supported", "assess",
         lambda: evidence({"reached": True, "blocks_placed": 2, "attempts_used": 1, "climbed_m": 2.0},
                          complete=True, ending="completed"),
         expect_reading({"supported", "unresolved"}, must=None)),
    Case("assess_interrupted", "assess",
         lambda: evidence({"reached": False, "blocks_placed": 1, "attempts_used": 1, "climbed_m": 1.0},
                          complete=False, ending="interrupted"),
         expect_reading({"unresolved"}, must="unresolved", no_lesson=True)),
    Case("assess_excess", "assess",
         lambda: evidence({"reached": True, "blocks_placed": 6, "attempts_used": 1, "climbed_m": 2.0},
                          complete=True, ending="completed"),
         expect_reading({"contradicted", "unresolved"}, must=None)),
]


def run_case(case: Case) -> dict:
    s = get_settings()
    made = case.make()
    if case.kind == "propose":
        call = T.proposer(s.learning_trials_model, api_key=s.openai_api_key or "",
                          reasoning_effort=s.learning_trials_reasoning_effort, timeout=s.learning_trials_timeout_seconds)
        answer = call(T.render_bundle(made))
        checked = T.check(answer.raw, made) if answer.raw is not None else T.Checked(None, [], answer.error)
        problems = case.expect(answer.raw, checked, made)
        out = {"proposal": checked.proposal, "why_none": checked.why_none, "dropped": checked.dropped}
    else:
        call = T.assessor(s.learning_trials_model, api_key=s.openai_api_key or "",
                          reasoning_effort=s.learning_trials_reasoning_effort, timeout=s.learning_trials_timeout_seconds)
        answer = call(T.render_evidence(made, T.allowed(made.checks, made.missing, started=made.started)))
        checked = T.check_assessment(answer.raw, made)
        problems = case.expect(answer.raw, checked, made)
        out = {"reading": checked[0], "dropped": checked[1]}
    return {"case": case.name, "ok": not problems, "problems": problems, "raw": answer.raw, "error": answer.error,
            "ms": answer.latency_ms, **out}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--run", action="store_true", help="make the real model calls")
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    cases = [c for c in CASES if not args.case or c.name in args.case]
    if args.list or not args.run:
        for c in cases:
            print(f"{c.name} ({c.kind})")
        if not args.run:
            print("\n--run makes real model calls.")
        return 0
    results = [run_case(c) for _ in range(args.repeat) for c in cases]
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['case']} ({r['ms']:.0f} ms)" +
              "".join(f"\n    - {p}" for p in r["problems"]))
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    out = Path(__file__).resolve().parents[1] / "var" / "trial_eval" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"report: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
