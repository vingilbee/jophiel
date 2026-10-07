"""Input-state scheduler: the controller core (plan §7.2, §7.7, §19).

Turns validated decisions into timed physical input with a monotonic local
clock. It owns the five rules of the action contract:

1. a decision is the complete desired state (anything unlisted is released);
2. it expires: at ``valid_for_ms`` everything is released;
3. a newer decision supersedes by diff (keys held in both stay down);
4. ``until`` conditions stop it early, checked on every tick;
5. ``look_at`` turns the camera by computed mouse counts.

It also enforces the lease (no input without a live lease), generation fencing
(cancel bumps the generation; older work is refused), and decision-id dedupe.

The clock and the input backend are injected. Production wires a real monotonic
clock and an OS input driver; ``fake.py`` wires a manual clock and a recorder,
so tests exercise this exact code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from . import safety
from .geometry import (
    axis_counts_per_s,
    frame_to_viewport,
    look_at_degrees,
    point_to_frame,
)
from .profile import Profile
from .protocol import (
    ClickEvent,
    Decision,
    DragEvent,
    LookAtEvent,
    MoveEvent,
    Point,
    Rejection,
    RejectionCode,
    ScrollEvent,
    TapEvent,
)
from .until import RegionSample, UntilHit, UntilTracker
from .validate import ValidatedDecision, parse_decision, validate_decision

# Pointer moves inside a drag or timed move are emitted at this spacing.
POINTER_STEP_MS = 16
# Decision ids remembered for dedupe (tombstones outlive any retry window).
MAX_RECORDS = 1024


class Clock(Protocol):
    def now_ms(self) -> int: ...


class InputBackend(Protocol):
    """OS input driver. Keys are physical names from ``safety.ALLOWED_KEYS``."""

    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def mouse_move_relative(self, dx: int, dy: int) -> None: ...
    def mouse_move_to(self, x: int, y: int) -> None: ...
    def scroll(self, amount: int) -> None: ...


class DecisionState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"  # an until condition fired
    EXPIRED = "expired"  # ran its full valid_for_ms
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class DecisionRecord:
    decision_id: str
    payload_hash: str
    state: DecisionState
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    ended_by: str | None = None
    rejection: Rejection | None = None
    clamped: tuple[str, ...] = ()

    @property
    def ran_ms(self) -> int | None:
        if self.started_at_ms is None or self.ended_at_ms is None:
            return None
        return self.ended_at_ms - self.started_at_ms


class SafeInput:
    """Wraps the backend: tracks what is physically down and refuses anything the
    global safety rules forbid, even if a bug upstream asked for it."""

    def __init__(self, backend: InputBackend):
        self._backend = backend
        self.down: set[str] = set()
        self.refused: list[str] = []

    def press(self, key: str) -> bool:
        if key in self.down:
            return True
        if key in safety.BLOCKED_KEYS or key not in safety.ALLOWED_KEYS:
            self.refused.append(key)
            return False
        if safety.blocked_combination(self.down | {key}) is not None:
            self.refused.append(key)
            return False
        self._backend.key_down(key)
        self.down.add(key)
        return True

    def release(self, key: str) -> None:
        if key in self.down:
            self._backend.key_up(key)
            self.down.discard(key)

    def release_all(self) -> None:
        for key in sorted(self.down):
            self._backend.key_up(key)
        self.down.clear()

    def move_relative(self, dx: int, dy: int) -> None:
        if dx or dy:
            self._backend.mouse_move_relative(dx, dy)

    def move_to(self, x: int, y: int) -> None:
        self._backend.mouse_move_to(x, y)

    def scroll(self, amount: int) -> None:
        self._backend.scroll(amount)


@dataclass(order=True)
class _Op:
    at_ms: int
    seq: int
    kind: str = field(compare=False)  # down | up | move_to | scroll
    arg: object = field(compare=False, default=None)


@dataclass
class _Rate:
    """A relative mouse motion spread over time (an axis or a look_at)."""

    dx_per_ms: float
    dy_per_ms: float
    start_ms: int
    end_ms: int | None  # None: until the decision ends
    acc_x: float = 0.0
    acc_y: float = 0.0
    last_ms: int = 0


@dataclass
class _Active:
    record: DecisionRecord
    validated: ValidatedDecision
    started_ms: int
    expires_ms: int
    hold_keys: frozenset[str]
    ops: list[_Op]
    rates: list[_Rate]
    until: UntilTracker | None


def _payload_hash(raw: object) -> str:
    if isinstance(raw, Decision):
        raw = raw.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    data = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode()).hexdigest()


class Scheduler:
    def __init__(
        self,
        profile: Profile,
        clock: Clock,
        backend: InputBackend,
        viewport_size: tuple[int, int],
    ):
        self.profile = profile
        self.clock = clock
        self.input = SafeInput(backend)
        self.viewport_size = viewport_size
        self.generation = 1
        self.lease_expires_ms: int | None = None
        self.records: dict[str, DecisionRecord] = {}
        self._active: _Active | None = None
        self._seq = 0
        self._pointer = (viewport_size[0] // 2, viewport_size[1] // 2)
        self._startup_release(backend)

    # -- lifecycle ----------------------------------------------------------

    def _startup_release(self, backend: InputBackend) -> None:
        """Release every key this profile can press, in case a previous run died
        holding one (plan §19 startup step 1)."""
        for key in sorted({spec.key.upper() for spec in self.profile.buttons.values()}):
            if key in safety.ALLOWED_KEYS:
                backend.key_up(key)

    def grant_lease(self, duration_ms: int, generation: int) -> bool:
        """Grant or renew the control lease (heartbeat). Refused for an old generation."""
        if generation != self.generation:
            return False
        self.lease_expires_ms = self.clock.now_ms() + duration_ms
        return True

    def cancel(self, reason: str = "cancelled") -> int:
        """Priority stop: release everything and fence all earlier work."""
        self._finish(DecisionState.CANCELLED, reason)
        self.input.release_all()
        self.generation += 1
        self.lease_expires_ms = None
        return self.generation

    @property
    def active(self) -> DecisionRecord | None:
        return self._active.record if self._active else None

    # -- submitting decisions -----------------------------------------------

    def apply(
        self, decision_id: str, raw: object, context: str, generation: int
    ) -> DecisionRecord:
        now = self.clock.now_ms()
        self.tick()
        payload_hash = _payload_hash(raw)

        existing = self.records.get(decision_id)
        if existing is not None:
            if existing.payload_hash == payload_hash:
                return existing
            return self._reject_unrecorded(
                decision_id, payload_hash, RejectionCode.DUPLICATE_CONFLICT,
                "decision_id", "this id was already used for a different decision",
            )
        if generation != self.generation:
            return self._reject(
                decision_id, payload_hash, RejectionCode.STALE_GENERATION,
                "generation", f"generation {generation} is not current ({self.generation})",
            )
        if self.lease_expires_ms is None or now >= self.lease_expires_ms:
            return self._reject(
                decision_id, payload_hash, RejectionCode.NO_LEASE, "lease", "no live control lease"
            )

        result = validate_decision(raw, self.profile, context)
        if isinstance(result, Rejection):
            record = DecisionRecord(
                decision_id=decision_id, payload_hash=payload_hash,
                state=DecisionState.REJECTED, rejection=result,
            )
            self._remember(record)
            return record

        record = DecisionRecord(
            decision_id=decision_id, payload_hash=payload_hash,
            state=DecisionState.RUNNING, started_at_ms=now, clamped=result.clamped,
        )
        self._remember(record)
        self._start(record, result, now)
        return record

    def _reject(self, decision_id, payload_hash, code, field_path, reason) -> DecisionRecord:
        record = self._reject_unrecorded(decision_id, payload_hash, code, field_path, reason)
        self._remember(record)
        return record

    def _remember(self, record: DecisionRecord) -> None:
        self.records[record.decision_id] = record
        while len(self.records) > MAX_RECORDS:
            del self.records[next(iter(self.records))]

    @staticmethod
    def _reject_unrecorded(decision_id, payload_hash, code, field_path, reason) -> DecisionRecord:
        return DecisionRecord(
            decision_id=decision_id, payload_hash=payload_hash, state=DecisionState.REJECTED,
            rejection=Rejection(code=code, field=field_path, reason=reason),
        )

    def _start(self, record: DecisionRecord, v: ValidatedDecision, now: int) -> None:
        d = v.decision
        hold_keys = frozenset(self.profile.key_for(b) for b in d.hold)

        # Supersede by diff: release what the new state does not hold (including
        # an old decision's in-flight taps), keep shared keys down, press new ones.
        if self._active is not None:
            self._active.record.state = DecisionState.SUPERSEDED
            self._active.record.ended_at_ms = now
            self._active.record.ended_by = "superseded"
            self._active = None
        for key in sorted(self.input.down - hold_keys):
            self.input.release(key)
        for key in sorted(hold_keys):
            self.input.press(key)

        rates: list[_Rate] = []
        for name, value in d.axes.items():
            if value == 0:
                continue
            spec = self.profile.axes[name]
            per_ms = axis_counts_per_s(spec, value) / 1000.0
            dx = per_ms if spec.device == "mouse_relative_x" else 0.0
            dy = per_ms if spec.device == "mouse_relative_y" else 0.0
            rates.append(_Rate(dx, dy, start_ms=now, end_ms=None, last_ms=now))

        ops: list[_Op] = []
        for ev in d.events:
            self._compile_event(ev, v, now, ops, rates)
        ops.sort()

        until = UntilTracker(d.until, now) if d.until is not None else None
        self._active = _Active(
            record=record, validated=v, started_ms=now,
            expires_ms=now + v.valid_for_ms, hold_keys=hold_keys,
            ops=ops, rates=rates, until=until,
        )
        self._run_until(now, None)

    def _op(self, ops: list[_Op], at: int, kind: str, arg: object = None) -> None:
        self._seq += 1
        ops.append(_Op(at, self._seq, kind, arg))

    def _viewport(self, point: Point) -> tuple[int, int]:
        frame_xy = point_to_frame(point, self.profile)
        return frame_to_viewport(frame_xy, self.profile.observation.size, self.viewport_size)

    def _compile_event(self, ev, v: ValidatedDecision, now: int, ops: list[_Op], rates: list[_Rate]) -> None:
        t = now + ev.at_ms
        if isinstance(ev, TapEvent):
            key = self.profile.key_for(ev.button)
            if ev.point is not None:
                self._op(ops, t, "move_to", self._viewport(ev.point))
            self._op(ops, t, "down", key)
            self._op(ops, t + v.tap_ms(ev, self.profile), "up", key)
        elif isinstance(ev, ClickEvent):
            key = self.profile.key_for(ev.button)
            self._op(ops, t, "move_to", self._viewport(ev.point))
            self._op(ops, t, "down", key)
            self._op(ops, t + self.profile.limits.default_tap_ms, "up", key)
        elif isinstance(ev, DragEvent):
            key = self.profile.key_for(ev.button)
            points = [self._viewport(p) for p in ev.path]
            self._op(ops, t, "move_to", points[0])
            self._op(ops, t, "down", key)
            for at, xy in _interpolate(points, t, ev.duration_ms):
                self._op(ops, at, "move_to", xy)
            self._op(ops, t + ev.duration_ms, "up", key)
        elif isinstance(ev, MoveEvent):
            target = self._viewport(ev.point)
            if ev.duration_ms == 0:
                self._op(ops, t, "move_to", target)
            else:
                for at, xy in _interpolate([self._pointer, target], t, ev.duration_ms):
                    self._op(ops, at, "move_to", xy)
        elif isinstance(ev, ScrollEvent):
            if ev.point is not None:
                self._op(ops, t, "move_to", self._viewport(ev.point))
            self._op(ops, t, "scroll", ev.amount)
        elif isinstance(ev, LookAtEvent):
            fov = self.profile.fov_deg()
            axes = self.profile.look_axes()
            assert fov is not None and axes is not None  # validated
            frame_xy = point_to_frame(ev.point, self.profile)
            yaw, pitch = look_at_degrees(frame_xy, self.profile.observation.size, fov)
            dx = yaw * axes[0].counts_per_degree  # type: ignore[operator]
            dy = pitch * axes[1].counts_per_degree  # type: ignore[operator]
            duration = max(ev.duration_ms, 1)
            rates.append(_Rate(dx / duration, dy / duration, start_ms=t, end_ms=t + duration, last_ms=t))

    # -- running ------------------------------------------------------------

    def tick(self, samples: dict[str, RegionSample] | None = None) -> None:
        """Advance to the clock's current time. Call every frame (30-60 Hz)."""
        now = self.clock.now_ms()
        lease = self.lease_expires_ms
        if lease is not None and now >= lease:
            # Whatever was due before the lease ran out still happens; nothing after.
            self._run_until(lease, samples)
            self._finish(DecisionState.CANCELLED, "lease_expired", at=lease)
            self.input.release_all()
            self.lease_expires_ms = None
            return
        self._run_until(now, samples)

    def _run_until(self, now: int, samples: dict[str, RegionSample] | None) -> None:
        active = self._active
        if active is None:
            return
        end = min(now, active.expires_ms)
        self._advance(end)
        if active.until is not None and samples:
            hit: UntilHit | None = active.until.update(end, samples)
            if hit is not None:
                self._finish(DecisionState.COMPLETED, f"until:{hit.index}", at=end)
                return
        if now >= active.expires_ms:
            self._finish(DecisionState.EXPIRED, "valid_for_ms", at=active.expires_ms)

    def _advance(self, to_ms: int) -> None:
        active = self._active
        if active is None:
            return
        while active.ops and active.ops[0].at_ms <= to_ms:
            op = active.ops.pop(0)
            self._integrate(active, op.at_ms)
            self._do(op, active)
        self._integrate(active, to_ms)

    def _integrate(self, active: _Active, to_ms: int) -> None:
        total_dx = total_dy = 0
        for rate in active.rates:
            start = max(rate.last_ms, rate.start_ms)
            stop = to_ms if rate.end_ms is None else min(to_ms, rate.end_ms)
            if stop <= start:
                continue
            dt = stop - start
            rate.acc_x += rate.dx_per_ms * dt
            rate.acc_y += rate.dy_per_ms * dt
            step_x, step_y = int(rate.acc_x), int(rate.acc_y)
            rate.acc_x -= step_x
            rate.acc_y -= step_y
            total_dx += step_x
            total_dy += step_y
            rate.last_ms = stop
        self.input.move_relative(total_dx, total_dy)

    def _do(self, op: _Op, active: _Active) -> None:
        if op.kind == "down":
            self.input.press(op.arg)  # type: ignore[arg-type]
        elif op.kind == "up":
            # A tap on a key the decision also holds would be a conflict and is
            # rejected by validation; never release a held key from an event.
            if op.arg not in active.hold_keys:
                self.input.release(op.arg)  # type: ignore[arg-type]
        elif op.kind == "move_to":
            x, y = op.arg  # type: ignore[misc]
            self._pointer = (x, y)
            self.input.move_to(x, y)
        elif op.kind == "scroll":
            self.input.scroll(op.arg)  # type: ignore[arg-type]

    def _finish(self, state: DecisionState, ended_by: str, at: int | None = None) -> None:
        active = self._active
        if active is None:
            return
        self._active = None
        active.record.state = state
        active.record.ended_at_ms = at if at is not None else self.clock.now_ms()
        active.record.ended_by = ended_by
        self.input.release_all()


def _interpolate(
    points: list[tuple[int, int]], start_ms: int, duration_ms: int
) -> list[tuple[int, tuple[int, int]]]:
    """Pointer positions along a polyline, one every POINTER_STEP_MS."""
    if duration_ms <= 0 or len(points) < 2:
        return [(start_ms, points[-1])]
    lengths = [
        ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(points, points[1:])
    ]
    total = sum(lengths) or 1.0
    out: list[tuple[int, tuple[int, int]]] = []
    steps = max(1, duration_ms // POINTER_STEP_MS)
    for i in range(1, steps + 1):
        frac = i / steps
        dist = frac * total
        seg = 0
        while seg < len(lengths) - 1 and dist > lengths[seg]:
            dist -= lengths[seg]
            seg += 1
        a, b = points[seg], points[seg + 1]
        f = 0.0 if lengths[seg] == 0 else min(dist / lengths[seg], 1.0)
        xy = (round(a[0] + (b[0] - a[0]) * f), round(a[1] + (b[1] - a[1]) * f))
        out.append((start_ms + round(duration_ms * frac), xy))
    return out
