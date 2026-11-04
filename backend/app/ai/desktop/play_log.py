"""What the driver has actually done, counted by code (no model).

Two things a one-step-at-a-time model can't keep track of by itself:

- **View angle (up/down).** Each turn is relative, so small tilts add up
  unseen: in the cave session every pitch was a few degrees "up" and the view
  ended on the ceiling. The game stops the view at straight up/down, so the
  angle is tracked as an interval of where it could be: it starts unknown
  ([-90, 90]) and both ends move and clamp with every turn. Turning 180° down
  pins it at straight down from anywhere, after which it is known exactly;
  ``level_view_turns`` is that move plus 90° back up.
- **Recent play.** How many turns, how much turning, how long keys were held,
  over the last minute. "Turned 16 times, held no keys" shows spinning on the
  spot, which the screen-change check can't (turning changes the whole
  screen). These are facts only: what to make of them is taught in the
  driver's instructions, not added as warnings here.

A decision's turns count once its outcome is known: rejected ones not at all,
one replaced or stopped part-way only for the part that ran. Until then the
view is shown as if it ran in full.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from desktop_controller.geometry import look_at_degrees, point_to_frame, turn_duration_ms
from desktop_controller.profile import Profile

PITCH_LIMIT = 90.0
# The angle counts as known when the interval is narrower than this.
KNOWN_WITHIN = 10.0
# Recent play covers this window (and at most this many decisions).
RECENT_SECONDS = 60.0
RECENT_MAX = 30
# A move with no outcome after this long (a lost report) counts as run in full.
OUTCOME_WAIT_SECONDS = 30.0
FINAL_STATES = frozenset({"completed", "expired", "superseded", "cancelled", "rejected"})


@dataclass(frozen=True)
class Turn:
    at_ms: int
    duration_ms: int
    yaw: float
    pitch: float


@dataclass
class Move:
    """One sent decision, as far as the view and the clock are concerned."""

    decision_id: str
    sent_at: float
    valid_for_ms: int
    hold: tuple[str, ...]
    taps: tuple[str, ...]
    turns: tuple[Turn, ...]
    state: str | None = None  # the controller's outcome, once known
    ran_ms: int | None = None
    ended_by: str | None = None
    reflexes: tuple[dict, ...] = ()  # {"name", "at_ms", "acted"} from the VM
    # Turned by the VM itself (a pursue): how far isn't known here.
    unknown_turns: bool = False

    @property
    def settled(self) -> bool:
        return self.state is not None

    def ran(self, turn: Turn) -> float:
        """Share of ``turn`` that actually happened (all of it while unknown)."""
        if self.state == "rejected":
            return 0.0
        if self.ran_ms is None:
            return 1.0
        if turn.duration_ms <= 0:
            return 1.0 if self.ran_ms >= turn.at_ms else 0.0
        return min(1.0, max(0.0, (self.ran_ms - turn.at_ms) / turn.duration_ms))

    def held_s(self, now: float) -> float:
        if not self.hold or self.state == "rejected":
            return 0.0
        ms = self.ran_ms if self.ran_ms is not None else min(self.valid_for_ms, (now - self.sent_at) * 1000)
        return max(0.0, ms) / 1000


def turns_of(decision: dict, profile: Profile) -> tuple[Turn, ...]:
    """The camera turns in a decision (``turn`` and ``look_at`` events), in degrees."""
    axes, fov = profile.look_axes(), profile.fov_deg()
    if axes is None:
        return ()
    out = []
    for ev in decision.get("events", []):
        if ev.get("type") == "turn":
            yaw, pitch = float(ev.get("yaw", 0.0)), float(ev.get("pitch", 0.0))
            out.append(Turn(ev.get("at_ms", 0), turn_duration_ms(yaw, pitch, axes, ev.get("duration_ms")), yaw, pitch))
        elif ev.get("type") == "look_at" and fov is not None and isinstance(ev.get("point"), list):
            yaw, pitch = look_at_degrees(point_to_frame(tuple(ev["point"]), profile), profile.observation.size, fov)
            out.append(Turn(ev.get("at_ms", 0), ev.get("duration_ms", 150), yaw, pitch))
    return tuple(out)


def level_view_turns(profile: Profile, at_ms: int = 0) -> list[dict]:
    """Turn events that leave the view level from any starting angle: 2 x 90°
    down (the view stops at straight down), then 90° up."""
    axes = profile.look_axes()
    if axes is None:
        return []
    step = turn_duration_ms(0.0, PITCH_LIMIT, axes, None) + 50
    return [{"type": "turn", "at_ms": at_ms + i * step, "yaw": 0.0, "pitch": pitch}
            for i, pitch in enumerate((PITCH_LIMIT, PITCH_LIMIT, -PITCH_LIMIT))]


def _clamp(value: float) -> float:
    return max(-PITCH_LIMIT, min(PITCH_LIMIT, value))


def _angle(degrees: float) -> str:
    if abs(degrees) < 5:
        return "level (straight ahead)"
    if degrees <= -PITCH_LIMIT + 2:
        return "straight up"
    if degrees >= PITCH_LIMIT - 2:
        return "straight down"
    return f"about {abs(degrees):.0f}° {'up' if degrees < 0 else 'down'}"


@dataclass
class PlayLog:
    # Where the view can be, after every settled move (degrees, + down / - up).
    low: float = -PITCH_LIMIT
    high: float = PITCH_LIMIT
    moves: list[Move] = field(default_factory=list)

    def record(self, move: Move) -> None:
        self.moves.append(move)

    def settle(self, outcomes: list[dict], now: float) -> list[Move]:
        """Apply outcomes that came in, oldest move first (a move's view change
        is applied once, when its outcome is known). Returns the moves settled now."""
        settled: list[Move] = []
        by_id = {o.get("decision_id"): o for o in outcomes if o.get("state") in FINAL_STATES}
        for move in self.moves:
            if move.settled:
                continue
            outcome = by_id.get(move.decision_id)
            if outcome is None and now - move.sent_at > OUTCOME_WAIT_SECONDS:
                outcome = {"state": "unknown"}
            if outcome is None:
                break  # keep the order: later moves wait for this one
            move.state, move.ran_ms = outcome["state"], outcome.get("ran_ms")
            move.ended_by, move.reflexes = outcome.get("ended_by"), tuple(outcome.get("reflexes") or ())
            self.low, self.high = self._apply(self.low, self.high, move)
            settled.append(move)
        return settled

    def prune(self, now: float) -> None:
        """Forget settled moves older than the window (their angle is already applied)."""
        keep = [m for m in self.moves if not m.settled or now - m.sent_at <= RECENT_SECONDS]
        self.moves = keep[-max(RECENT_MAX, sum(not m.settled for m in keep)):]

    @staticmethod
    def _apply(low: float, high: float, move: Move) -> tuple[float, float]:
        if move.unknown_turns and move.state != "rejected":
            return -PITCH_LIMIT, PITCH_LIMIT
        for turn in sorted(move.turns, key=lambda t: t.at_ms):
            delta = turn.pitch * move.ran(turn)
            low, high = _clamp(low + delta), _clamp(high + delta)
        return low, high

    def pitch_range(self) -> tuple[float, float]:
        """Where the view is now, counting moves still running as if they ran in full."""
        low, high = self.low, self.high
        for move in self.moves:
            if not move.settled:
                low, high = self._apply(low, high, move)
        return low, high

    def pitch(self) -> float | None:
        low, high = self.pitch_range()
        return (low + high) / 2 if high - low <= KNOWN_WITHIN else None

    # -- text ---------------------------------------------------------------------

    def view_text(self) -> str:
        pitch = self.pitch()
        if pitch is None:
            low, high = self.pitch_range()
            if high - low >= 2 * PITCH_LIMIT - 1:
                return "View up/down: not known yet."
            return f"View up/down: not known yet (between {_angle(low)} and {_angle(high)})."
        return f"View up/down: {_angle(pitch)}."

    def recent(self, now: float) -> dict:
        window = [m for m in self.moves if now - m.sent_at <= RECENT_SECONDS][-RECENT_MAX:]
        turning = [(t, m.ran(t)) for m in window for t in m.turns if m.state != "rejected"]
        held: dict[str, float] = {}
        for move in window:
            for button in move.hold:
                held[button] = held.get(button, 0.0) + move.held_s(now)
        taps: dict[str, int] = {}
        for move in window:
            if move.state != "rejected":
                for button in move.taps:
                    taps[button] = taps.get(button, 0) + 1
        stops: dict[str, int] = {}
        for move in window:
            reason = move.ended_by or ""
            if reason.startswith("reflex:"):
                stops[reason.removeprefix("reflex:")] = stops.get(reason.removeprefix("reflex:"), 0) + 1
            elif reason.startswith("until:"):
                stops["your stop rule"] = stops.get("your stop rule", 0) + 1
        return {
            "stops": stops,
            "seconds": round(now - window[0].sent_at) if window else 0,
            "decisions": len(window),
            "turns": len(turning),
            "yaw_total": round(sum(abs(t.yaw) * f for t, f in turning)),
            "yaw_net": round(sum(t.yaw * f for t, f in turning)),
            "held": {k: round(v, 1) for k, v in sorted(held.items(), key=lambda kv: -kv[1])},
            "taps": taps,
        }

    def recent_text(self, now: float) -> str | None:
        r = self.recent(now)
        if not r["decisions"]:
            return None
        parts = [f"turned {r['turns']} times"]
        if r["turns"]:
            side = "right" if r["yaw_net"] >= 0 else "left"
            parts[0] += f" ({r['yaw_total']}° in all, net {abs(r['yaw_net'])}° {side})"
        held = ", ".join(f"{k} {v:g} s" for k, v in r["held"].items() if v > 0)
        parts.append(f"held {held}" if held else "held no keys")
        if r["taps"]:
            parts.append("tapped " + ", ".join(f"{k} x{n}" for k, n in r["taps"].items()))
        if r["stops"]:
            parts.append("stopped early by " + ", ".join(
                f"{k.replace('_', ' ')} x{n}" if k == "your stop rule" else f"the {k.replace('_', ' ')} reflex x{n}"
                for k, n in r["stops"].items()))
        return f"Recent play (last {r['seconds']} s, {r['decisions']} decisions): " + "; ".join(parts) + "."

    def state(self, now: float) -> dict:
        """For the driver state Jophiel reads (counted by code, so trusted)."""
        return {"view": self.view_text(), "recent": self.recent_text(now)}
