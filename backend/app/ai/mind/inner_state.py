"""Her inner state: five values that events change and time fades back (docs/COLUMBINA_INNER_STATE_PLAN.md).

Pure and not wired into anything yet (offline, steps 5.1c-5.1d). The reader proposes what one
**event** (a message, an outcome) did to her, in terms of an **incident** (what's going on that the
feeling is about: this pleasant chat, their news, they keep brushing her off). This module owns the
resulting state; the reader never states it.

What a change can do to its incident (``status``):
- ``opened``: new evidence starts it; adds;
- ``escalated``: new evidence makes it more (another put-down in a pattern); adds;
- ``eased``: new evidence makes it less (an apology, it got resolved); takes back what that incident
  still leaves, never more and never another incident's share;
- ``sustained``: the cause is still present (the warm chat goes on, the threat is still there); holds
  the incident where it was when last touched, so it doesn't fade, but never raises it;
- ``lingering``: she still feels it, but nothing new happened and the cause isn't present; adds
  nothing and lets it fade.

Each event counts once (keyed by event, incident, feeling, status and direction: duplicate delivery
is ignored). Time fades every value toward her baseline in closed form, so the result doesn't depend
on how often anyone looks; a late event is weakened by its age. Only the values a feeling maps to
move (``EFFECTS``).

The `appraisal-v1` statuses map on: new -> opened, continuing -> lingering, reinterpreted -> eased
(down) or escalated (up).
"""
from __future__ import annotations

from dataclasses import dataclass, field

KEYS = ("mood", "energy", "interest", "tension", "closeness")
SIGNED = frozenset({"mood", "closeness"})
AMOUNTS = {"a_little": 0.25, "somewhat": 0.5, "strong": 0.75, "very": 1.0}
# Sizes of a shift (appraisal-v2). Provisional until step 5.1d calibrates them; "unclear" (the
# thought doesn't say how big) is applied as small and counted.
SIZES = {"small": 0.25, "clear": 0.5, "big": 0.75, "unclear": 0.25}
# How one unit of a felt change moves each value. Only listed values move.
EFFECTS: dict[str, dict[str, float]] = {
    "happy": {"mood": 0.5},
    "amused": {"mood": 0.3, "energy": 0.1},
    "touched": {"mood": 0.3, "closeness": 0.2},
    "curious": {"interest": 0.5},
    "unsure": {"tension": 0.3},      # appraisal-v1 only: mixed unease with factual uncertainty
    "uneasy": {"tension": 0.4},
    "hurt": {"tension": 0.4, "mood": -0.4, "closeness": -0.1},
    "annoyed": {"tension": 0.5, "mood": -0.3},
    "tired": {"energy": -0.5},
}
STATUSES = ("opened", "escalated", "eased", "sustained", "lingering")
ALIASES = {"new": "opened", "continuing": "lingering"}
LATE_DROP_HALF_LIVES = 2.0
APPLIED_KEPT = 500


@dataclass(frozen=True)
class Change:
    """What one event did to her about one incident. ``at``: when the event happened (seconds)."""

    source: str
    activity: str
    event: str
    incident: str
    feeling: str
    direction: int  # +1 or -1: which way the feeling moved
    amount: float  # 0..1
    status: str
    at: float
    evidence: str = ""

    @property
    def kind(self) -> str:
        if self.status == "reinterpreted":
            return "eased" if self.direction < 0 else "escalated"
        return ALIASES.get(self.status, self.status)

    @property
    def key(self) -> str:
        return (f"{self.source}:{self.activity}:{self.event}:{self.incident}:{self.feeling}:{self.kind}:"
                f"{self.direction}")

    @property
    def incident_key(self) -> str:
        return f"{self.source}:{self.activity}:{self.incident}"


@dataclass
class State:
    values: dict[str, float]
    at: float
    version: int = 0
    applied: list[str] = field(default_factory=list)
    # incident -> value -> what that incident still contributes (as of `at`)
    incidents: dict[str, dict[str, float]] = field(default_factory=dict)
    # incident -> value -> what it contributed when last touched (what `sustained` holds it at)
    touched: dict[str, dict[str, float]] = field(default_factory=dict)

    def copy(self) -> "State":
        return State(dict(self.values), self.at, self.version, list(self.applied),
                     {k: dict(v) for k, v in self.incidents.items()},
                     {k: dict(v) for k, v in self.touched.items()})


@dataclass(frozen=True)
class Dynamics:
    baseline: dict[str, float]
    half_life_minutes: dict[str, float]

    def factor(self, key: str, seconds: float) -> float:
        return 0.5 ** (max(0.0, seconds) / 60.0 / self.half_life_minutes[key])


@dataclass
class Outcome:
    state: State
    applied: bool
    reason: str = ""  # duplicate / lingering / too_late / nothing_to_take_back / nothing_to_hold / unknown_feeling
    deltas: dict[str, float] = field(default_factory=dict)


def start(dynamics: Dynamics, at: float) -> State:
    return State(dict(dynamics.baseline), at)


def _clip(key: str, value: float) -> float:
    return max(-1.0 if key in SIGNED else 0.0, min(1.0, value))


def fade(state: State, dynamics: Dynamics, now: float) -> State:
    """The state at ``now``: every value (and every incident's share) faded toward baseline."""
    out = state.copy()
    if now <= state.at:
        return out
    seconds = now - state.at
    for k in KEYS:
        b = dynamics.baseline[k]
        out.values[k] = b + (state.values[k] - b) * dynamics.factor(k, seconds)
    for shares in out.incidents.values():
        for k in shares:
            shares[k] *= dynamics.factor(k, seconds)
    out.at = now
    return out


def _done(state: State, change: Change) -> None:
    state.applied = (state.applied + [change.key])[-APPLIED_KEPT:]


def apply(state: State, change: Change, dynamics: Dynamics, now: float | None = None) -> Outcome:
    """Fade to ``now`` (default: the later of the state's time and the change's), then apply the
    change once."""
    now = max(state.at, change.at) if now is None else now
    current = fade(state, dynamics, now)
    if change.key in current.applied:
        return Outcome(current, False, "duplicate")
    if change.feeling not in EFFECTS:
        return Outcome(current, False, "unknown_feeling")
    kind = change.kind
    if kind == "lingering":
        _done(current, change)
        return Outcome(current, False, "lingering")
    shares = current.incidents.setdefault(change.incident_key, {})
    if kind == "sustained":
        held = current.touched.get(change.incident_key, {})
        deltas = {k: held[k] - shares.get(k, 0.0) for k in EFFECTS[change.feeling] if k in held}
        if not any(abs(d) > 1e-9 for d in deltas.values()):
            _done(current, change)
            return Outcome(current, False, "nothing_to_hold")
    else:
        age = max(0.0, now - change.at)
        deltas = {}
        for k, per_unit in EFFECTS[change.feeling].items():
            if age and age / 60.0 > LATE_DROP_HALF_LIVES * dynamics.half_life_minutes[k]:
                continue
            deltas[k] = change.direction * change.amount * per_unit * dynamics.factor(k, age)
        if not deltas:
            return Outcome(current, False, "too_late")
        if kind == "eased":
            # Take back what that incident still contributes, never past zero.
            for k, d in list(deltas.items()):
                have = shares.get(k, 0.0)
                deltas[k] = (-have if abs(d) > abs(have) else d) if have * d < 0 else 0.0
            if not any(abs(d) > 1e-9 for d in deltas.values()):
                _done(current, change)
                return Outcome(current, False, "nothing_to_take_back")
    for k, d in deltas.items():
        before = current.values[k]
        current.values[k] = _clip(k, before + d)
        shares[k] = shares.get(k, 0.0) + (current.values[k] - before)
    current.touched[change.incident_key] = dict(shares)
    current.version += 1
    _done(current, change)
    return Outcome(current, True, deltas={k: round(v, 4) for k, v in deltas.items()})
