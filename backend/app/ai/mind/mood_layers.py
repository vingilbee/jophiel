"""Her inner state in three layers (docs/COLUMBINA_INNER_STATE_PLAN.md, step 5.1d, offline).

``inner_state.py`` adds every felt shift to her mood in full and keeps it on the mood's own
half-life, so a pleasant chat with many small happy moments pushes her to the top. A brief reaction
and a lasting change were the same thing. Here they are separate:

- **reaction**: the brief response to something that happened, fading in minutes
  (``reaction_half_life_minutes``). Kept per incident, so relief from one incident only calms that
  incident's reaction. It's how strongly she's reacting now, not a running total: within an
  incident the stronger reaction in the same direction wins, and across incidents the strongest
  each way counts, the two directions offsetting;
- **incident**: what stays emotionally relevant about one situation. Only a share of a shift lasts
  (``keep``, by size), optionally capped per incident (``incident_cap``: repeats within one concern
  add less and less). ``eased`` takes back from its own incident; ``sustained`` holds it where it was
  last touched (never raises it); ``lingering`` adds nothing. ``negative_keep_scale`` multiplies the
  retained share of hurt, annoyance and unease (capped at 1; their half-lives are unchanged): a
  character parameter this calibration chose, not a universal law;
- **mood**: the slow aggregate. A value is its baseline plus the sum of the reaction and incident
  layers, passed through a soft limit (``squash``). Near baseline that's one to one; near the top each
  extra unit counts less, but a second big event still shows.

A shift of unclear size is never quietly made small or clear: ``unclear_amount`` says what to assume
(None skips it), so results can be shown under several assumptions. Each event counts once; fading
is closed form (independent of how often anyone looks); a late event is aged on each layer's own
clock: its brief reaction on the reaction's, what lasts on the incident's. Pure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.ai.mind.inner_state import EFFECTS, KEYS, SIGNED

SIZES = {"small": 0.25, "clear": 0.5, "big": 0.75}
NEGATIVE = frozenset({"hurt", "annoyed", "uneasy", "unsure"})
LATE_DROP_HALF_LIVES = 2.0
APPLIED_KEPT = 500


@dataclass(frozen=True)
class Config:
    baseline: dict[str, float]
    half_life_minutes: dict[str, float]           # incident layer, per value
    reaction_half_life_minutes: float = 3.0
    keep: dict[str, float] = field(default_factory=lambda: {"small": 0.3, "clear": 0.6, "big": 0.9, "unclear": 0.3})
    unclear_amount: float | None = 0.25
    incident_cap: float | None = None             # per incident and value, in value units; None = no cap
    squash: bool = True
    # The retained share of hurt, annoyance and unease is this many times larger (capped at 1; the
    # half-lives don't change); 1.0 treats both alike. A character parameter, chosen by calibration.
    negative_keep_scale: float = 1.0

    def factor(self, key: str, seconds: float) -> float:
        return 0.5 ** (max(0.0, seconds) / 60.0 / self.half_life_minutes[key])

    def reaction_factor(self, seconds: float) -> float:
        return 0.5 ** (max(0.0, seconds) / 60.0 / self.reaction_half_life_minutes)


@dataclass(frozen=True)
class Shift:
    """One effect the reader proposed, for one event and one incident."""

    source: str
    activity: str
    event: str
    incident: str
    feeling: str
    status: str  # opened / escalated / eased / sustained / lingering
    size: str  # small / clear / big / unclear / -
    at: float

    @property
    def key(self) -> str:
        return f"{self.source}:{self.activity}:{self.event}:{self.incident}:{self.feeling}:{self.status}"

    @property
    def incident_key(self) -> str:
        return f"{self.source}:{self.activity}:{self.incident}"


@dataclass
class State:
    at: float
    # incident -> value -> its brief reaction (kept per incident, so repair can only calm its own)
    reactions: dict[str, dict[str, float]] = field(default_factory=dict)
    incidents: dict[str, dict[str, float]] = field(default_factory=dict)
    touched: dict[str, dict[str, float]] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    version: int = 0

    def copy(self) -> "State":
        return State(self.at, {k: dict(v) for k, v in self.reactions.items()},
                     {k: dict(v) for k, v in self.incidents.items()},
                     {k: dict(v) for k, v in self.touched.items()}, list(self.applied), self.version)

    def to_dict(self) -> dict:
        return dict(at=self.at, reactions=self.reactions, incidents=self.incidents, touched=self.touched,
                    applied=self.applied, version=self.version)

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        return cls(data["at"], {k: dict(v) for k, v in data.get("reactions", {}).items()},
                   {k: dict(v) for k, v in data.get("incidents", {}).items()},
                   {k: dict(v) for k, v in data.get("touched", {}).items()}, list(data.get("applied", [])),
                   data.get("version", 0))


@dataclass
class Outcome:
    state: State
    applied: bool
    reason: str = ""


def start(at: float) -> State:
    return State(at)


def _soft(cfg: Config, key: str, x: float) -> float:
    b = cfg.baseline[key]
    low, high = (-1.0 if key in SIGNED else 0.0), 1.0
    if not cfg.squash:
        return max(low, min(high, b + x))
    room = (high - b) if x >= 0 else (b - low)
    return b + room * math.tanh(x / room) if room > 0 else b


def reaction(state: State, key: str) -> float:
    """How strongly she's reacting now, on one value: the strongest reaction each way across
    incidents (not a running total), the two directions offsetting."""
    rs = [r.get(key, 0.0) for r in state.reactions.values()]
    return max([0.0, *rs]) + min([0.0, *rs])


def values(state: State, cfg: Config) -> dict[str, float]:
    # Incident shares are stored raw; the per-incident cap applies when they're read.
    return {k: _soft(cfg, k, reaction(state, k) + sum(_cap(cfg, s.get(k, 0.0)) for s in state.incidents.values()))
            for k in KEYS}


def fade(state: State, cfg: Config, now: float) -> State:
    out = state.copy()
    if now <= state.at:
        return out
    seconds = now - state.at
    rf = cfg.reaction_factor(seconds)
    out.reactions = {i: {k: v * rf for k, v in r.items()} for i, r in out.reactions.items()}
    out.reactions = {i: r for i, r in out.reactions.items() if any(abs(v) > 1e-4 for v in r.values())}
    for shares in out.incidents.values():
        for k in shares:
            shares[k] *= cfg.factor(k, seconds)
    out.at = now
    return out


def _cap(cfg: Config, x: float) -> float:
    c = cfg.incident_cap
    return x if c is None else c * math.tanh(x / c)


def _done(state: State, shift: Shift) -> None:
    state.applied = (state.applied + [shift.key])[-APPLIED_KEPT:]


def apply(state: State, shift: Shift, cfg: Config, now: float | None = None) -> Outcome:
    now = max(state.at, shift.at) if now is None else now
    cur = fade(state, cfg, now)
    if shift.key in cur.applied:
        return Outcome(cur, False, "duplicate")
    if shift.feeling not in EFFECTS:
        return Outcome(cur, False, "unknown_feeling")
    if shift.status == "lingering":
        _done(cur, shift)
        return Outcome(cur, False, "lingering")
    shares = cur.incidents.setdefault(shift.incident_key, {})
    if shift.status == "sustained":
        held = cur.touched.get(shift.incident_key, {})
        moved = False
        for k in EFFECTS[shift.feeling]:
            if k in held and abs(held[k]) > abs(shares.get(k, 0.0)) + 1e-9:
                shares[k] = held[k]
                moved = True
        _done(cur, shift)
        if not moved:
            return Outcome(cur, False, "nothing_to_hold")
        cur.version += 1
        return Outcome(cur, True)
    amount = SIZES.get(shift.size)
    if amount is None:
        amount = cfg.unclear_amount
        if amount is None:
            _done(cur, shift)
            return Outcome(cur, False, "unclear_skipped")
    keep = cfg.keep.get(shift.size if shift.size in SIZES else "unclear", 0.0)
    if shift.feeling in NEGATIVE:
        keep = min(1.0, keep * cfg.negative_keep_scale)
    age = max(0.0, now - shift.at)
    own = cur.reactions.setdefault(shift.incident_key, {})
    moved = False
    for k, per_unit in EFFECTS[shift.feeling].items():
        if age and age / 60.0 > LATE_DROP_HALF_LIVES * cfg.half_life_minutes[k]:
            continue
        # A late event: its brief reaction has faded on the reaction's clock, what lasts on the incident's.
        brief = amount * per_unit * cfg.reaction_factor(age)
        lasting = amount * per_unit * cfg.factor(k, age)
        if shift.status == "eased":
            # Take back from this incident (never past zero), and calm this incident's reaction only.
            have = shares.get(k, 0.0)
            take = min(abs(have), abs(lasting)) * (1 if have > 0 else -1)
            if abs(take) > 1e-9:
                shares[k] = have - take
                r = own.get(k, 0.0)
                if r * have > 0:
                    own[k] = r - min(abs(r), abs(brief)) * (1 if r > 0 else -1)
                moved = True
        else:  # opened / escalated (anything else adds too)
            # Within an incident the stronger reaction in the same direction wins; an opposite one offsets.
            r = own.get(k, 0.0)
            own[k] = (max(abs(r), abs(brief)) * (1 if brief > 0 else -1)) if r * brief > 0 else r + brief
            shares[k] = shares.get(k, 0.0) + keep * lasting
            moved = True
    _done(cur, shift)
    if not moved:
        return Outcome(cur, False, "nothing_to_take_back" if shift.status == "eased" else "too_late")
    cur.touched[shift.incident_key] = dict(shares)
    cur.version += 1
    return Outcome(cur, True)
