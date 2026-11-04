"""Predictions and bounded game trials (pure): docs/learning-system/04-predictions-and-trials.md (decisions) and
docs/learning-system/04-implementation.md (this implementation).

No database, no app state. ``services/learning_trials.py`` gathers the facts; this module holds:

- **Scenarios.** What kinds of experiment exist, with their measurable effects. The first (and only) one is
  ``climb_obstacle``: getting up a small obstacle with her movement and block placing.
- **The proposal contract.** One structured call (``Proposer``) reads the problem, her game state, her skills and
  relevant experience, and proposes a hypothesis, a small set of alternatives (each with predicted observations,
  qualitative uncertainty, prerequisites and costs), an observation window, limits and a recommendation; or says
  there is nothing to test. ``check`` keeps what is valid, drops the rest with a reason, and never repairs: an
  alternative without a prediction of whether she gets up, or a destination that isn't a small obstacle near her,
  is dropped. Limits can only be lowered. Untried alternatives keep *predicted* results: nothing here observes.
- **For cognition.** ``proposal_views``: each alternative as one proposal [P*] in Student 2's trained shape
  (producer, goal, purpose), with its prediction, cost, uncertainty and whether it is recommended in the purpose.
- **Measurement (code).** ``measure``: what the game counted (did she reach the destination, how high she got,
  blocks placed, attempts, test time, health lost), each with where it came from, or ``None`` when nothing counted
  it. ``evaluate``: each prediction ``met`` / ``not_met`` / ``unknown``. An interruption or a missing measurement
  never makes a prediction ``not_met``: only a measured value that already contradicts it does.
- **The assessment contract.** One structured call (``Assessor``) interprets: whether the hypothesis is
  supported, contradicted or unresolved, competing explanations, where the finding applies and its exceptions,
  whether a failure was the technique's or the preparation's, and an optional lesson. ``check_assessment`` keeps
  it only as far as the measurements allow (supported needs every prediction of the tried alternative met and the
  required evidence present; contradicted needs one measured as not met; a test that never started is
  unresolved), and drops a lesson from anything inconclusive.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

PROPOSAL_VERSION = "trial-proposal-v1"
ASSESSMENT_VERSION = "trial-assessment-v1"

MEASURES = ("reached", "climbed_m", "blocks_placed", "attempts_used", "test_seconds", "health_lost")
BOOLEAN_MEASURES = frozenset({"reached"})
EXPECT = ("true", "false", "at_most", "at_least", "between")
UNCERTAINTY = ("low", "medium", "high")
CHECKS = ("met", "not_met", "unknown")
HYPOTHESIS = ("supported", "contradicted", "unresolved")
EXPLANATION = ("supported", "plausible", "unresolved", "contradicted")
FAILURE_OF = ("technique", "preparation", "interrupted", "none", "unclear")
LESSON_LEVELS = ("skill", "plan")
# How an execution ended. "complete": ran until it worked or used its attempts (its observations are whole).
ENDINGS = ("completed", "limit", "stopped", "interrupted", "not_started", "approach_changed", "failed")
COMPLETE = frozenset({"completed", "limit"})

MAX_ALTERNATIVES = 4
MIN_ALTERNATIVES = 2
MAX_PREDICTIONS = 6
MAX_PREREQUISITES = 4
ACTION_CHARS = 200
APPROACH_CHARS = 500
TEXT_CHARS = 600
_ITEM = re.compile(r"^[a-z0-9_]{2,60}$")
_CITE = re.compile(r"^[a-z]+\d+$")


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    # How the destination must lie from where she is: risen at least / at most this many blocks, and no farther.
    min_rise: int
    max_rise: int


SCENARIOS: dict[str, Scenario] = {
    "climb_obstacle": Scenario(
        "climb_obstacle",
        "get up onto a small obstacle (a wall, a ledge, a pillar top, the side of a pit) using her movement and "
        "block placing; done when she stands at the destination",
        min_rise=1, max_rise=5),
}


@dataclass(frozen=True)
class Limits:
    attempts: int
    seconds: int
    blocks: int

    def to_dict(self) -> dict:
        return {"attempts": self.attempts, "seconds": self.seconds, "blocks": self.blocks}

    @classmethod
    def of(cls, raw: dict | None, default: "Limits") -> "Limits":
        raw = raw or {}

        def lowered(key: str, low: int) -> int:
            value = raw.get(key)
            top = getattr(default, key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return top
            return max(low, min(top, int(value)))

        return cls(lowered("attempts", 1), lowered("seconds", 30), lowered("blocks", 0))


def _cut(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _obj(**properties: Any) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_STR = {"type": "string"}
_NUM = {"type": "number"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_CITES = {"type": "array", "items": _STR}


def _enum(values) -> dict:
    return {"type": "string", "enum": list(values)}


# --------------------------------------------------------------------------- #
# The proposal: what learning hands cognition
# --------------------------------------------------------------------------- #


@dataclass
class Line:
    ref: str
    text: str
    source: str = ""  # the stored record ("experience:12", "trial:<id>", "lesson:<id>", "item:<id>")


@dataclass
class Bundle:
    """What the proposer reads. ``position``: her feet now (the destination is checked against it)."""

    scenario: Scenario
    limits: Limits
    window_seconds: int
    near_m: float
    problem: str
    position: dict | None
    state_text: str
    skills: list[str] = field(default_factory=list)
    evidence: list[Line] = field(default_factory=list)  # x1..: what triggered it (failures, a hypothesis)
    lessons: list[Line] = field(default_factory=list)  # g1..: her game lessons
    trials: list[Line] = field(default_factory=list)  # t1..: earlier trials and what their assessment found
    pending: Line | None = None  # p1: the pending proposal for this problem, when revising
    approach: str | None = None  # a changed approach that needs its own prediction

    def refs(self) -> dict[str, Line]:
        out = {line.ref: line for line in (*self.evidence, *self.lessons, *self.trials)}
        if self.pending is not None:
            out[self.pending.ref] = self.pending
        return out


PROPOSAL_INSTRUCTIONS = """You are Columbina's learning, in a game she plays with the user. Something keeps going wrong, \
or she has a hunch she hasn't tested. You design one small, bounded experiment that would tell her something: not \
an order, a proposal. Her cognition decides whether and when to run it (or puts it off, or turns it down); you never \
start anything.

Give:
- decision: "propose", or "none" when there is nothing worth testing here (say why in why). An experiment only fits \
the scenario named below; anything else is "none".
- problem: the unresolved problem, in one sentence.
- hypothesis: what you expect to be true and want to find out, in her words ("Jumping and placing a block under \
me gets me up a three-block wall"). One hypothesis, testable by what the game counts.
- destination: the block she should end up standing on (whole-number x, y, z of the block her feet are in once \
there), read from her state and surroundings, never guessed; radius: how close counts (blocks, 0.5 to 3).
- alternatives: 2 to 4 different ways to try it, each a real option with her skills and single actions (walk around \
it, jump-place a pillar, dig steps into it, ...). For each: key (a1, a2, ...), action (what she does, short), \
approach (how, concretely enough for her to write a program from it), predictions (what the game will count if she \
tries it: each a measure with what you expect), expected (the predicted result in a sentence), uncertainty (low / \
medium / high: how sure you are), seconds and blocks (what it will likely cost), risks (falling, getting hurt, \
breaking things), prerequisites (items she must carry before starting, with counts; empty if none).
  Measures: reached (true/false: she stands at the destination), climbed_m (how much higher her feet end than \
where she started), blocks_placed, attempts_used (out of the limit), test_seconds, health_lost. Expect: "true" / \
"false" for reached; "at_most", "at_least" or "between" (value, and high for between) for numbers. Every \
alternative predicts reached. These are predictions: you haven't seen any of them tried here.
- recommendation: the key of the alternative you'd try first, and recommendation_why.
- window_seconds: how long after it ends to keep watching (she may fall back down, take damage); \
required_evidence: the measures without which the result can't be judged (always reached).
- limits: attempts, seconds (including getting ready) and blocks placed; at most the limits given, less if less is \
enough.
- cites: the refs (x1, g2, t1, p1, ...) your proposal rests on. Earlier trials (t*) say what was found before: \
use them, and don't propose what was already settled unless the situation differs (say how).
- materially_different: when revising a pending proposal (p1), true if this is a different test of the same \
question rather than the same test with new evidence.

Rules: use only what you were shown. Real coordinates only. Keep each text short. Don't pick an alternative because \
it is novel: pick the one most likely to tell her something useful at a cost she can afford."""

_PREDICTION = _obj(measure=_enum(MEASURES), expect=_enum(EXPECT), value=_NUM, high=_NUM)
_PREREQ = _obj(item=_STR, count=_INT)
_ALTERNATIVE = _obj(key=_STR, action=_STR, approach=_STR, predictions={"type": "array", "items": _PREDICTION},
                    expected=_STR, uncertainty=_enum(UNCERTAINTY), seconds=_INT, blocks=_INT, risks=_STR,
                    prerequisites={"type": "array", "items": _PREREQ})
PROPOSAL_SCHEMA = _obj(
    decision=_enum(("propose", "none")), why=_STR, problem=_STR, hypothesis=_STR, materially_different=_BOOL,
    destination=_obj(x=_INT, y=_INT, z=_INT), radius=_NUM,
    alternatives={"type": "array", "items": _ALTERNATIVE}, recommendation=_STR, recommendation_why=_STR,
    window_seconds=_INT, required_evidence={"type": "array", "items": _enum(MEASURES)},
    limits=_obj(attempts=_INT, seconds=_INT, blocks=_INT), cites=_CITES)


def render_bundle(b: Bundle) -> str:
    limits = b.limits
    lines = [f"Scenario: {b.scenario.name}: {b.scenario.description}. The destination must be "
             f"{b.scenario.min_rise} to {b.scenario.max_rise} blocks above her feet and within {b.near_m:g} m.",
             f"Limits (at most): {limits.attempts} attempts, {limits.seconds} s including getting ready, "
             f"{limits.blocks} blocks placed. Watch window: up to {b.window_seconds} s after it ends.",
             "", f"The problem: {b.problem}"]
    if b.approach:
        lines += ["", "Her body changed approach during a trial and needs a fresh prediction for it before it can "
                  f"count as an experiment: \"{b.approach}\". Include it as one alternative."]
    lines += ["", "What showed it (x*):"] + ([f"{x.ref}: {x.text}" for x in b.evidence] or ["(nothing)"])
    if b.pending is not None:
        lines += ["", f"The pending proposal for this problem ({b.pending.ref}): {b.pending.text}"]
    lines += ["", "Earlier trials and what was found (t*):"] + ([f"{t.ref}: {t.text}" for t in b.trials] or ["(none)"])
    lines += ["", "Her lessons from playing (g*, trust them as stated):"] + (
        [f"{g.ref}: {g.text}" for g in b.lessons] or ["(none)"])
    position = (f"x {b.position.get('x'):.1f}, y {b.position.get('y'):.1f}, z {b.position.get('z'):.1f}"
                if b.position else "unknown")
    lines += ["", f"Her feet now: {position}", "Her state (from the game):", b.state_text.strip() or "(none)"]
    lines += ["", "Her skills:"] + (b.skills or ["(unknown)"])
    return "\n".join(lines)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Checked:
    proposal: dict | None
    dropped: list[dict]
    why_none: str | None = None
    cites: list[str] = field(default_factory=list)


def _prediction(raw: dict) -> tuple[dict | None, str | None]:
    measure, expect = raw.get("measure"), raw.get("expect")
    if measure not in MEASURES or expect not in EXPECT:
        return None, "unknown measure or expectation"
    if measure in BOOLEAN_MEASURES:
        if expect not in ("true", "false"):
            return None, f"{measure} is true or false"
        return {"measure": measure, "expect": expect}, None
    if expect in ("true", "false"):
        return None, f"{measure} is a number"
    value, high = raw.get("value"), raw.get("high")
    if not isinstance(value, (int, float)) or value < 0:
        return None, "a number prediction needs a value of 0 or more"
    out = {"measure": measure, "expect": expect, "value": float(value)}
    if expect == "between":
        if not isinstance(high, (int, float)) or high < value:
            return None, "between needs high >= value"
        out["high"] = float(high)
    return out, None


def _alternative(raw: dict, key: str) -> tuple[dict | None, str | None]:
    action, approach = _cut(raw.get("action"), 10_000), _cut(raw.get("approach"), 10_000)
    if not action or not approach:
        return None, "an alternative needs an action and an approach"
    if len(action) > ACTION_CHARS or len(approach) > APPROACH_CHARS:
        return None, "too long (nothing is cut to fit)"
    predictions, dropped = [], []
    for p in (raw.get("predictions") or [])[:MAX_PREDICTIONS]:
        kept, why = _prediction(p if isinstance(p, dict) else {})
        if kept is None:
            dropped.append(why)
        elif kept["measure"] not in {q["measure"] for q in predictions}:
            predictions.append(kept)
    if not any(p["measure"] == "reached" for p in predictions):
        return None, "no prediction of whether she gets up (reached)"
    if raw.get("uncertainty") not in UNCERTAINTY:
        return None, "no uncertainty"
    prerequisites = []
    for p in (raw.get("prerequisites") or []):
        item = str((p or {}).get("item") or "").strip().lower().removeprefix("minecraft:").replace(" ", "_")
        count = (p or {}).get("count")
        if not _ITEM.match(item) or not isinstance(count, int) or not 1 <= count <= 64:
            return None, f"prerequisite {item or '?'} isn't an item with a count of 1-64"
        prerequisites.append({"item": item, "count": count})
    if len(prerequisites) > MAX_PREREQUISITES:
        return None, "too many prerequisites"
    seconds, blocks = raw.get("seconds"), raw.get("blocks")
    return {
        "key": key, "action": action, "approach": approach, "predictions": predictions,
        "expected": _cut(raw.get("expected"), TEXT_CHARS), "uncertainty": raw["uncertainty"],
        "seconds": max(0, int(seconds)) if isinstance(seconds, (int, float)) else None,
        "blocks": max(0, int(blocks)) if isinstance(blocks, (int, float)) else None,
        "risks": _cut(raw.get("risks"), 300), "prerequisites": prerequisites,
        **({"dropped_predictions": dropped} if dropped else {}),
    }, None


def check(raw: dict | None, b: Bundle) -> Checked:
    """The proposal as far as it is valid, or None with why. Never repaired."""
    dropped: list[dict] = []
    if not isinstance(raw, dict):
        return Checked(None, dropped, "no answer")
    if raw.get("decision") != "propose":
        return Checked(None, dropped, _cut(raw.get("why"), 300) or "nothing worth testing")
    refs = b.refs()
    cites = [c for c in dict.fromkeys(str(c).strip() for c in raw.get("cites") or []) if c]
    for c in cites:
        if c not in refs:
            dropped.append({"what": f"cite {c}", "why": "not shown"})
    cites = [c for c in cites if c in refs]
    if not any(c.startswith(("x", "t", "p")) for c in cites):
        return Checked(None, dropped, "it rests on nothing it was shown (cite the failures, a trial or the proposal)",
                       cites)
    hypothesis, problem = _cut(raw.get("hypothesis"), TEXT_CHARS), _cut(raw.get("problem"), TEXT_CHARS)
    if not hypothesis:
        return Checked(None, dropped, "no hypothesis", cites)
    dest = raw.get("destination") or {}
    if b.position is None or not all(isinstance(dest.get(k), int) for k in ("x", "y", "z")):
        return Checked(None, dropped, "no destination, or her position is unknown", cites)
    rise = dest["y"] - math.floor(b.position["y"])
    far = math.dist((dest["x"] + 0.5, dest["z"] + 0.5), (b.position["x"], b.position["z"]))
    if not b.scenario.min_rise <= rise <= b.scenario.max_rise or far > b.near_m:
        return Checked(None, dropped, f"the destination isn't a small obstacle near her (rise {rise}, {far:.0f} m)",
                       cites)
    radius = raw.get("radius")
    radius = min(3.0, max(0.5, float(radius))) if isinstance(radius, (int, float)) else 1.0
    alternatives = []
    for i, a in enumerate((raw.get("alternatives") or [])[:MAX_ALTERNATIVES]):
        given = _cut((a or {}).get("key"), 8) or f"a{i + 1}"
        kept, why = _alternative(a if isinstance(a, dict) else {}, f"a{len(alternatives) + 1}")
        if kept is None:
            dropped.append({"what": f"alternative {given}", "why": why})
            continue
        kept["given_key"] = given
        alternatives.append(kept)
    if len(alternatives) < MIN_ALTERNATIVES:
        return Checked(None, dropped, f"fewer than {MIN_ALTERNATIVES} valid alternatives", cites)
    recommended = next((a["key"] for a in alternatives if a["given_key"] == _cut(raw.get("recommendation"), 8)), None)
    if recommended is None:
        return Checked(None, dropped, "the recommendation isn't one of the valid alternatives", cites)
    for a in alternatives:
        a.pop("given_key")
    required = [m for m in dict.fromkeys(raw.get("required_evidence") or []) if m in MEASURES]
    if "reached" not in required:
        required.insert(0, "reached")
    window = raw.get("window_seconds")
    window = max(0, min(b.window_seconds, int(window))) if isinstance(window, (int, float)) else b.window_seconds
    limits = Limits.of(raw.get("limits"), b.limits)
    for a in alternatives:
        if a["blocks"] is not None and a["blocks"] > limits.blocks:
            a["over_limit"] = f"expects {a['blocks']} blocks; the limit is {limits.blocks}"
    proposal = {
        "version": PROPOSAL_VERSION, "scenario": b.scenario.name, "problem": problem or b.problem,
        "hypothesis": hypothesis, "destination": {k: dest[k] for k in ("x", "y", "z")}, "radius": radius,
        "start": {k: round(float(b.position[k]), 2) for k in ("x", "y", "z")}, "alternatives": alternatives,
        "recommendation": recommended, "recommendation_why": _cut(raw.get("recommendation_why"), TEXT_CHARS),
        "window_seconds": window, "required_evidence": required, "limits": limits.to_dict(),
        "materially_different": bool(raw.get("materially_different")), "cites": cites,
    }
    if b.approach:
        proposal["changed_approach"] = b.approach
    return Checked(proposal, dropped, None, cites)


def alternative(proposal: dict, key: str | None) -> dict | None:
    return next((a for a in proposal.get("alternatives") or [] if a["key"] == key), None)


def prediction_words(p: dict) -> str:
    m = p["measure"].replace("_", " ")
    if p["expect"] in ("true", "false"):
        return f"{'reaches' if p['expect'] == 'true' else 'does not reach'} the destination" if p["measure"] == "reached" \
            else f"{m} {p['expect']}"
    if p["expect"] == "between":
        return f"{m} {p['value']:g}-{p['high']:g}"
    return f"{m} {'<=' if p['expect'] == 'at_most' else '>='} {p['value']:g}"


def proposal_views(proposal: dict, *, deferred_because: list[str] = ()) -> list[dict]:
    """Each alternative as one proposal [P*] in Student 2's shape: pursuing one is choosing it; declining or
    deferring any of them is declining or deferring the whole proposal."""
    limits = proposal["limits"]
    shared = (f"Test of a hunch: {proposal['hypothesis']} Limits: {limits['attempts']} tries, {limits['seconds'] // 60} "
              f"min {limits['seconds'] % 60} s with getting ready, {limits['blocks']} blocks.")
    if deferred_because:
        shared += " Put off before: " + "; ".join(deferred_because[-2:])
    out = []
    for a in proposal["alternatives"]:
        cost = ", ".join(filter(None, (f"~{a['seconds']} s" if a.get("seconds") is not None else "",
                                       f"~{a['blocks']} blocks" if a.get("blocks") is not None else "",
                                       "needs " + ", ".join(f"{p['count']} {p['item']}" for p in a["prerequisites"])
                                       if a["prerequisites"] else "")))
        predicted = "; ".join(prediction_words(p) for p in a["predictions"])
        purpose = (f"{shared} If she tries it: {a['expected'] or predicted} (predicted: {predicted}; "
                   f"{a['uncertainty']} uncertainty" + (f"; costs {cost}" if cost else "") + ")"
                   + (f" Risks: {a['risks']}." if a.get("risks") else "")
                   + (" Learning recommends this one: " + proposal["recommendation_why"]
                      if a["key"] == proposal["recommendation"] else ""))
        out.append({"key": a["key"], "goal": f"try {a['action']}"[:160], "purpose": purpose[:900]})
    return out


def summary(proposal: dict) -> str:
    """One line about a proposal, for a later proposer (p1) and the Learning page."""
    rec = alternative(proposal, proposal.get("recommendation")) or {}
    return (f"hypothesis: {proposal.get('hypothesis')}; recommended: {rec.get('action')} ("
            + "; ".join(prediction_words(p) for p in rec.get("predictions") or []) + ")")


# --------------------------------------------------------------------------- #
# Measurement: what the game counted
# --------------------------------------------------------------------------- #


def _pos(value) -> tuple[float, float, float] | None:
    if isinstance(value, dict) and all(isinstance(value.get(k), (int, float)) for k in ("x", "y", "z")):
        return float(value["x"]), float(value["y"]), float(value["z"])
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(isinstance(v, (int, float)) for v in value):
        return float(value[0]), float(value[1]), float(value[2])
    return None


def at_destination(position, destination: dict, radius: float) -> bool | None:
    p = _pos(position)
    if p is None:
        return None
    dx, dz = p[0] - (destination["x"] + 0.5), p[2] - (destination["z"] + 0.5)
    return math.hypot(dx, dz) <= radius + 0.5 and math.floor(p[1] + 0.01) >= destination["y"]


def _placed(program: dict | None) -> int | None:
    """Blocks placed by one program, as the pack counted them: successful single place actions plus place
    skills that ended done. None for a pack that doesn't count successes."""
    if not isinstance(program, dict):
        return None
    if isinstance(program.get("placed"), int) and not isinstance(program.get("placed"), bool):
        return program["placed"]  # the pack's own count
    actions = program.get("actions") or {}
    done = actions.get("done") if isinstance(actions, dict) else None
    if not isinstance(done, dict):
        if not actions:  # no single actions at all: only skills could have placed
            done = {}
        else:
            return None
    skill_places = sum(1 for c in program.get("calls") or [] if c.get("skill") == "place" and c.get("status") == "done")
    if program.get("calls_total", 0) > len(program.get("calls") or []):
        return None  # skill calls cut from the record: not countable
    return int(done.get("place", 0)) + skill_places


@dataclass
class Measured:
    values: dict[str, Any]
    sources: dict[str, str]
    notes: list[str]


def measure(proposal: dict, *, start: dict | None, tries: list[dict], end: dict | None, test_seconds: float | None,
            started: bool) -> Measured:
    """What happened, counted by code from the states and each try's counted facts. ``start``/``end``: her state
    when the test began and when its window ended (None when not read)."""
    values: dict[str, Any] = {m: None for m in MEASURES}
    sources: dict[str, str] = {}
    notes: list[str] = []
    if not started:
        notes.append("the test never started: nothing about the technique was observed")
        return Measured(values, sources, notes)
    dest, radius = proposal["destination"], proposal["radius"]
    ran = [t for t in tries if t.get("program")]
    values["attempts_used"] = len(ran)
    sources["attempts_used"] = "tries that ran a program"
    if test_seconds is not None:
        values["test_seconds"] = round(float(test_seconds), 1)
        sources["test_seconds"] = "clock: execution start to end"
    positions = [((t.get("facts") or {}).get("to"), f"try {t.get('n')} end") for t in ran]
    if end is not None:
        positions.append((end.get("position"), "after the window"))
    reached = [(at_destination(p, dest, radius), where) for p, where in positions]
    known = [(r, where) for r, where in reached if r is not None]
    if known:
        hit = next((where for r, where in known if r), None)
        values["reached"] = hit is not None
        sources["reached"] = (f"at the destination at {hit}" if hit else
                              f"not at the destination at {', '.join(w for _, w in known)}")
    start_y = (_pos((start or {}).get("position")) or (None, None, None))[1]
    if start_y is None and ran:
        start_y = (_pos((ran[0].get("facts") or {}).get("from")) or (None, None, None))[1]
    heights = [p[1] for p, _ in ((_pos(p), w) for p, w in positions) if p is not None]
    if start_y is not None and heights:
        values["climbed_m"] = round(max(0.0, max(heights) - start_y), 1)
        sources["climbed_m"] = "highest counted position minus the start"
    placed = [_placed(t.get("program")) for t in ran]
    if ran and all(p is not None for p in placed):
        values["blocks_placed"] = sum(placed)
        sources["blocks_placed"] = "the pack's count of successful place actions and place skills"
    elif ran:
        notes.append("blocks placed couldn't be counted for every try")
    if not ran:
        values["blocks_placed"] = 0
        sources["blocks_placed"] = "no program ran"
    health_start = (start or {}).get("health")
    lows = [h for t in ran for h in ((t.get("facts") or {}).get("lowest_health"),
                                     ((t.get("facts") or {}).get("health") or [None, None])[-1])
            if isinstance(h, (int, float))]
    if end is not None and isinstance(end.get("health"), (int, float)):
        lows.append(end["health"])
    if isinstance(health_start, (int, float)) and lows:
        values["health_lost"] = round(max(0.0, float(health_start) - min(lows)), 1)
        sources["health_lost"] = "health at the start minus the lowest counted"
    if end is None:
        notes.append("her state after the window wasn't read")
    return Measured(values, sources, notes)


def evaluate(predictions: list[dict], values: dict[str, Any], *, complete: bool, started: bool) -> list[dict]:
    """Each prediction against what was measured. An incomplete run (stopped, interrupted) can confirm a value
    that can only grow past a bound (at_least, reached), and contradict one already past it (at_most), but never
    calls something not met for what it didn't get to see."""
    out = []
    for p in predictions:
        m, expect = p["measure"], p["expect"]
        got = values.get(m) if started else None
        status, why = "unknown", "not measured" if started else "the test never started"
        if got is not None:
            if m in BOOLEAN_MEASURES:
                want = expect == "true"
                if bool(got) == want and (want or complete):
                    status, why = "met", f"{m} {str(got).lower()}"
                elif bool(got) != want and (got or complete):
                    status, why = "not_met", f"{m} {str(got).lower()}"
                else:
                    status, why = "unknown", f"{m} {str(got).lower()} so far, and the run didn't finish"
            else:
                v, hi = p.get("value", 0.0), p.get("high", p.get("value", 0.0))
                if expect == "at_most":
                    status = "not_met" if got > v else ("met" if complete else "unknown")
                elif expect == "at_least":
                    status = "met" if got >= v else ("not_met" if complete else "unknown")
                else:
                    status = "not_met" if got > hi else (
                        ("met" if complete else "unknown") if got >= v else ("not_met" if complete else "unknown"))
                why = f"{m} {got:g}" + ("" if status != "unknown" else ", and the run didn't finish")
        out.append({"ref": f"c{len(out) + 1}", "prediction": prediction_words(p), "measure": m, "status": status,
                    "why": why})
    return out


def missing(proposal: dict, values: dict[str, Any]) -> list[str]:
    return [m for m in proposal.get("required_evidence") or [] if values.get(m) is None]


def allowed(checks: list[dict], missing_required: list[str], *, started: bool) -> set[str]:
    """What the hypothesis may be, from code's checks alone."""
    out = {"unresolved"}
    if not started:
        return out
    if any(c["status"] == "not_met" for c in checks):
        out.add("contradicted")
    if checks and not missing_required and all(c["status"] == "met" for c in checks):
        out.add("supported")
    return out


# --------------------------------------------------------------------------- #
# The assessment: what learning makes of it
# --------------------------------------------------------------------------- #


ASSESSMENT_INSTRUCTIONS = """You assess, after the fact, one bounded experiment Columbina ran in a game. Before it ran, \
learning wrote a hypothesis and predictions; her cognition chose one alternative; her body prepared and tried it. \
Code then counted what happened (m.*) and checked each prediction against it (c*: met, not met, or unknown). You \
interpret: you never change what was counted.

Give:
- hypothesis: supported / contradicted / unresolved. Allowed values are listed: only those. Unresolved is right \
whenever the evidence doesn't settle it, including when the run was interrupted or something wasn't measured: an \
interruption or a missing observation is never evidence against a prediction.
- why: one or two sentences citing the checks and measurements.
- explanations: the competing explanations of what happened (her technique, the spot, the materials, luck, the \
preparation, the game itself), each with its own status (supported / plausible / unresolved / contradicted) and \
cites. Keep the ones you can't rule out.
- failure_of: if something went wrong, whose it was: technique (the approach itself), preparation (she wasn't ready \
or the test never started), interrupted, none, or unclear.
- applicability: where the finding applies (what kind of obstacle, height, materials), and exceptions.
- lesson: only when the hypothesis is supported or contradicted: a short situation and advice her hands or planner \
could use later, level skill (how to do something) or plan (what to do when), and a short key; otherwise \
propose false. One trial is one occasion: the lesson starts as tentative.
- cites: the refs you rest on (c*, m.*, a*, e*)."""

ASSESSMENT_SCHEMA = _obj(
    hypothesis=_enum(HYPOTHESIS), why=_STR,
    explanations={"type": "array", "items": _obj(text=_STR, status=_enum(EXPLANATION), cites=_CITES)},
    failure_of=_enum(FAILURE_OF), applicability=_STR, exceptions={"type": "array", "items": _STR},
    lesson=_obj(propose=_BOOL, key=_STR, situation=_STR, advice=_STR, level=_enum(LESSON_LEVELS)), cites=_CITES)


@dataclass
class TrialEvidence:
    """What the assessor reads: the revision tried, cognition's choice, the tries, the code's counts and checks."""

    proposal: dict
    selected: dict  # the alternative tried
    selection: dict
    ending: str
    stop_reason: str | None
    costs: dict
    measured: Measured
    checks: list[dict]
    missing: list[str]
    started: bool
    attempts: list[Line] = field(default_factory=list)  # a*: each try
    events: list[Line] = field(default_factory=list)  # e*: preparation, readiness, stops, recovery

    def refs(self) -> set[str]:
        return ({c["ref"] for c in self.checks} | {f"m.{m}" for m in MEASURES}
                | {a.ref for a in self.attempts} | {e.ref for e in self.events})


def render_evidence(ev: TrialEvidence, allowed_values: set[str]) -> str:
    p, a = ev.proposal, ev.selected
    lines = [f"Hypothesis (written before it ran): {p['hypothesis']}",
             f"Destination: {p['destination']} (within {p['radius']:g}); start: {p.get('start')}",
             f"Alternative chosen by cognition: {a['action']} — approach: {a['approach']}",
             f"Why cognition chose it: {ev.selection.get('reason') or '(not given)'}"
             + ("" if a["key"] == p["recommendation"] else " (not the one learning recommended)"),
             f"Predicted: {a['expected']} ({a['uncertainty']} uncertainty)",
             f"Limits: {p['limits']}; spent: {ev.costs}",
             f"How it ended: {ev.ending}" + (f" ({ev.stop_reason})" if ev.stop_reason else ""),
             f"The test {'started' if ev.started else 'never started'}.",
             "", "Counted by code (m.*):"]
    for m in MEASURES:
        value = ev.measured.values.get(m)
        lines.append(f"m.{m}: " + ("not measured" if value is None else f"{value}")
                     + (f" ({ev.measured.sources[m]})" if m in ev.measured.sources else ""))
    lines += [f"note: {n}" for n in ev.measured.notes]
    lines += ["", "Predictions checked (c*):"] + [f"{c['ref']}: {c['prediction']}: {c['status']} ({c['why']})"
                                                 for c in ev.checks]
    lines.append(f"Required evidence missing: {', '.join(ev.missing) or 'none'}")
    lines += ["", "Tries (a*):"] + ([f"{x.ref}: {x.text}" for x in ev.attempts] or ["(none ran)"])
    lines += ["", "What happened around it (e*):"] + ([f"{x.ref}: {x.text}" for x in ev.events] or ["(nothing)"])
    lines += ["", f"Allowed hypothesis values: {', '.join(sorted(allowed_values))}."]
    return "\n".join(lines)


def check_assessment(raw: dict | None, ev: TrialEvidence) -> tuple[dict, list[dict]]:
    """The reading as far as code allows, and what was dropped. With no reading, an unresolved one by code."""
    dropped: list[dict] = []
    allowed_values = allowed(ev.checks, ev.missing, started=ev.started)
    refs = ev.refs()
    if not isinstance(raw, dict):
        return {"hypothesis": "unresolved", "why": "no reading was made; code's checks stand alone",
                "explanations": [], "failure_of": "unclear" if ev.started else "preparation", "applicability": "",
                "exceptions": [], "lesson": None, "cites": [], "by": "code"}, dropped

    def kept_cites(values, what: str) -> list[str]:
        out = []
        for c in dict.fromkeys(str(v).strip() for v in values or []):
            if c in refs:
                out.append(c)
            elif c:
                dropped.append({"what": f"{what} cite {c}", "why": "not shown"})
        return out

    hypothesis = raw.get("hypothesis") if raw.get("hypothesis") in HYPOTHESIS else "unresolved"
    if hypothesis not in allowed_values:
        dropped.append({"what": f"hypothesis {hypothesis}", "why": "the checks don't allow it; unresolved"})
        hypothesis = "unresolved"
    measured_refs = {c["ref"] for c in ev.checks if c["status"] != "unknown"} | {
        f"m.{m}" for m, v in ev.measured.values.items() if v is not None}
    explanations = []
    for e in (raw.get("explanations") or [])[:6]:
        text = _cut((e or {}).get("text"), 300)
        if not text:
            continue
        cites = kept_cites((e or {}).get("cites"), "explanation")
        status = (e or {}).get("status") if (e or {}).get("status") in EXPLANATION else "unresolved"
        if status in ("supported", "contradicted") and not set(cites) & measured_refs:
            dropped.append({"what": f"explanation status {status}", "why": "no measured evidence cited; plausible"})
            status = "plausible" if status == "supported" else "unresolved"
        explanations.append({"text": text, "status": status, "cites": cites})
    failure = raw.get("failure_of") if raw.get("failure_of") in FAILURE_OF else "unclear"
    if not ev.started and failure != "preparation":
        dropped.append({"what": f"failure_of {failure}", "why": "the test never started: preparation"})
        failure = "preparation"
    if ev.started and ev.ending == "interrupted" and failure == "technique" and hypothesis != "contradicted":
        dropped.append({"what": "failure_of technique", "why": "an interruption isn't the technique's failure"})
        failure = "interrupted"
    lesson = None
    proposed = raw.get("lesson") or {}
    if proposed.get("propose"):
        situation, advice = _cut(proposed.get("situation"), 300), _cut(proposed.get("advice"), 300)
        key = re.sub(r"[^a-z0-9_]+", "_", str(proposed.get("key") or "").lower()).strip("_")[:60]
        if hypothesis == "unresolved":
            dropped.append({"what": "lesson", "why": "nothing was established: no lesson from an inconclusive trial"})
        elif not situation or not advice or not key or proposed.get("level") not in LESSON_LEVELS:
            dropped.append({"what": "lesson", "why": "needs a key, situation, advice and level"})
        else:
            lesson = {"key": key, "situation": situation, "advice": advice, "level": proposed["level"]}
    exceptions = [x for x in (_cut(x, 200) for x in (raw.get("exceptions") or [])[:4]) if x]
    return {"hypothesis": hypothesis, "why": _cut(raw.get("why"), TEXT_CHARS), "explanations": explanations,
            "failure_of": failure, "applicability": _cut(raw.get("applicability"), 400), "exceptions": exceptions,
            "lesson": lesson, "cites": kept_cites(raw.get("cites"), "reading"), "by": "model"}, dropped


def finding(proposal: dict, selected_key: str | None, result: dict, values: dict) -> str:
    """One line about an assessed trial, for a later proposer (t*) and her summary."""
    a = alternative(proposal, selected_key) or {}
    got = ", ".join(f"{m} {v}" for m, v in values.items() if v is not None) or "nothing measured"
    text = (f"tested \"{proposal.get('hypothesis')}\" by {a.get('action')}: {result.get('hypothesis')} ({got})")
    if result.get("applicability"):
        text += f"; applies: {result['applicability']}"
    if result.get("exceptions"):
        text += f"; except: {'; '.join(result['exceptions'])}"
    return text


# --------------------------------------------------------------------------- #
# The calls
# --------------------------------------------------------------------------- #


@dataclass
class Answer:
    raw: dict | None
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


class Caller:
    """One structured call (no tools, no temperature games: alternatives are asked for explicitly)."""

    def __init__(self, model: str, *, api_key: str, instructions: str, schema: dict, name: str, version: str,
                 reasoning_effort: str | None = "low", timeout: float = 90.0):
        from openai import OpenAI

        self.model, self.instructions, self.schema, self.schema_name = model, instructions, schema, name
        self.version, self.reasoning_effort = version, reasoning_effort
        self.client = OpenAI(api_key=api_key or None, timeout=timeout, max_retries=0)

    @property
    def name(self) -> str:
        return f"{self.version}:{self.model}"

    def __call__(self, text: str) -> Answer:
        started = time.perf_counter()
        kwargs: dict = {}
        if self.model.startswith(("gpt-5", "gpt-6", "o")) and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        reasoning = self.reasoning_effort not in (None, "none")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.instructions}, {"role": "user", "content": text}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": self.schema_name, "strict": True, "schema": self.schema}},
                max_completion_tokens=8000 if reasoning else 3000, store=False, **kwargs)
            raw = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001 - a failed call is reported, never guessed
            return Answer(None, round((time.perf_counter() - started) * 1000, 1), error=f"{type(exc).__name__}: {exc}"[:300])
        return Answer(raw, round((time.perf_counter() - started) * 1000, 1),
                      getattr(response.usage, "prompt_tokens", None), getattr(response.usage, "completion_tokens", None))


def proposer(model: str, *, api_key: str, reasoning_effort: str | None, timeout: float) -> Caller:
    return Caller(model, api_key=api_key, instructions=PROPOSAL_INSTRUCTIONS, schema=PROPOSAL_SCHEMA,
                  name="trial_proposal", version=PROPOSAL_VERSION, reasoning_effort=reasoning_effort, timeout=timeout)


def assessor(model: str, *, api_key: str, reasoning_effort: str | None, timeout: float) -> Caller:
    return Caller(model, api_key=api_key, instructions=ASSESSMENT_INSTRUCTIONS, schema=ASSESSMENT_SCHEMA,
                  name="trial_assessment", version=ASSESSMENT_VERSION, reasoning_effort=reasoning_effort,
                  timeout=timeout)


# --------------------------------------------------------------------------- #
# Her body: the rules a trial adds to the program writer
# --------------------------------------------------------------------------- #

WRITER_RULES = """

This is not open practice: it is a bounded experiment, and it tests one approach, chosen beforehand:
  {approach}
Destination: stand on the block at x {x}, y {y}, z {z} (within {radius:g}). It ends when you stand there.
Keep to that approach. Fix details between tries (where to stand, which block, timing), but never switch to another \
way of doing it: a different way needs its own prediction first. If this approach can't work here, say so: set \
keeps_approach false and describe the other way in other_approach (then nothing runs). Otherwise keeps_approach true \
and other_approach empty.
Limits for the whole experiment: {blocks} blocks placed (the game refuses more), {attempts} tries."""

TRIAL_WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string", "description": "one or two sentences: how the program does the task"},
        "code": {"type": "string", "description": "the body of an async function (JavaScript)"},
        "keeps_approach": {"type": "boolean"},
        "other_approach": {"type": "string"},
    },
    "required": ["approach", "code", "keeps_approach", "other_approach"],
    "additionalProperties": False,
}


def task_words(proposal: dict, key: str) -> str:
    a = alternative(proposal, key) or {}
    d = proposal["destination"]
    return f"get up to x {d['x']} y {d['y']} z {d['z']} by {a.get('action', 'the chosen way')}"[:200]


__all__ = [
    "SCENARIOS", "Scenario", "Limits", "Bundle", "Line", "Checked", "check", "render_bundle", "digest",
    "proposal_views", "summary", "alternative", "measure", "Measured", "evaluate", "missing", "allowed",
    "TrialEvidence", "render_evidence", "check_assessment", "finding", "Caller", "proposer", "assessor", "Answer",
    "WRITER_RULES", "TRIAL_WRITER_SCHEMA", "task_words", "MEASURES", "COMPLETE", "ENDINGS",
]
