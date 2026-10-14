"""Decision validation against the active profile and context (plan §7.2-7.5, §7.9).

``validate_decision`` returns either a ``ValidatedDecision`` with every default
resolved, or the *first* ``Rejection`` found, with a stable code, the offending
field, and a short reason the driver can act on.

Two limits are clamped instead of rejected, because a shorter hold is always the
safe direction: ``valid_for_ms`` above the profile cap, and ``wait_ms`` above its
cap. Clamps are recorded so the action report can say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import safety
from .geometry import turn_duration_ms, crop_size
from .pursuit import template_pixels
from .profile import BUILTIN_REGIONS, Profile
from .protocol import (
    ClickEvent,
    CropPoint,
    Decision,
    DragEvent,
    LookAtEvent,
    TurnEvent,
    MacroEvent,
    MoveEvent,
    Point,
    PursueEvent,
    Rejection,
    RejectionCode,
    ScrollEvent,
    StaticCondition,
    TapEvent,
)

C = RejectionCode


@dataclass(frozen=True)
class ValidatedDecision:
    decision: Decision
    context: str
    valid_for_ms: int
    wait_ms: int | None
    clamped: tuple[str, ...] = field(default=())

    def tap_ms(self, event: TapEvent, profile: Profile) -> int:
        return event.hold_ms if event.hold_ms is not None else profile.limits.default_tap_ms


class _Reject(Exception):
    def __init__(self, code: RejectionCode, field_path: str, reason: str):
        self.rejection = Rejection(code=code, field=field_path, reason=reason)


def _fail(code: RejectionCode, field_path: str, reason: str) -> None:
    raise _Reject(code, field_path, reason)


def parse_decision(raw: object) -> Decision | Rejection:
    if isinstance(raw, Decision):
        return raw
    try:
        return Decision.model_validate(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = _format_loc(err["loc"])
        return Rejection(code=C.INVALID_SCHEMA, field=loc or "decision", reason=err["msg"])


def _format_loc(loc: tuple) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def validate_decision(
    raw: object, profile: Profile, context: str
) -> ValidatedDecision | Rejection:
    parsed = parse_decision(raw)
    if isinstance(parsed, Rejection):
        return parsed
    try:
        return _validate(parsed, profile, context)
    except _Reject as rej:
        return rej.rejection


def _validate(d: Decision, profile: Profile, context: str) -> ValidatedDecision:
    rules = profile.rules_for(context)
    lim = profile.limits
    clamped: list[str] = []

    # A move that stops itself (until, or a pursue that ends when the target is
    # lost) may run up to the until limit.
    stops_itself = d.until is not None or any(isinstance(ev, PursueEvent) for ev in d.events)
    cap = lim.max_until_ms if stops_itself else lim.max_valid_for_ms
    valid_for = d.valid_for_ms if d.valid_for_ms is not None else lim.default_valid_for_ms
    if valid_for > cap:
        valid_for = cap
        clamped.append("valid_for_ms")
    if valid_for <= 0:
        _fail(C.LIMIT_EXCEEDED, "valid_for_ms", "must be greater than 0")
    wait_ms = d.wait_ms
    if wait_ms is not None:
        wait_cap = min(lim.max_wait_ms, valid_for)
        if wait_ms > wait_cap:
            wait_ms = wait_cap
            clamped.append("wait_ms")

    def need_kind(kind: str, field_path: str) -> None:
        if kind not in rules.kinds:
            _fail(C.CONTEXT_MISMATCH, field_path, f'"{kind}" not allowed in context "{context}"')

    def need_button(name: str, field_path: str) -> None:
        if name not in profile.buttons:
            _fail(C.UNKNOWN_BUTTON, field_path, f'no button named "{name}"')
        if name not in rules.buttons:
            _fail(C.CONTEXT_MISMATCH, field_path, f'button "{name}" not allowed in context "{context}"')

    def need_point(point: Point, field_path: str, moves_pointer: bool = True) -> None:
        # Positioning the pointer is only meaningful where the cursor is free;
        # in mouse-look contexts it would jerk the camera instead.
        if moves_pointer:
            need_kind("move", field_path)
        if isinstance(point, CropPoint):
            if point.crop not in profile.regions or profile.crop_scale(point.crop) is None:
                _fail(C.UNKNOWN_CROP, field_path, f'no crop named "{point.crop}"')
            cw, ch = crop_size(profile, point.crop)
            x, y = point.xy
            if not (0 <= x < cw and 0 <= y < ch):
                _fail(C.POINT_OUTSIDE_VIEWPORT, field_path, f"{list(point.xy)} is outside the {cw}x{ch} crop")
            return
        w, h = profile.observation.size
        x, y = point
        if not (0 <= x < w and 0 <= y < h):
            _fail(C.POINT_OUTSIDE_VIEWPORT, field_path, f"{[x, y]} is outside the {w}x{h} frame")

    def need_within(start: int, length: int, field_path: str) -> None:
        if start + length > valid_for:
            _fail(
                C.LIMIT_EXCEEDED,
                field_path,
                f"ends at {start + length} ms, after valid_for_ms {valid_for}",
            )

    # -- held buttons -----------------------------------------------------
    if d.hold:
        need_kind("hold", "hold")
    if len(d.hold) > lim.max_hold_buttons:
        _fail(C.LIMIT_EXCEEDED, "hold", f"at most {lim.max_hold_buttons} held buttons")
    if len(set(d.hold)) != len(d.hold):
        _fail(C.INVALID_SCHEMA, "hold", "a button is listed twice")
    for i, name in enumerate(d.hold):
        need_button(name, f"hold[{i}]")

    # -- axes ---------------------------------------------------------------
    for name, value in d.axes.items():
        path = f"axes.{name}"
        if name not in profile.axes:
            _fail(C.UNKNOWN_AXIS, path, f'no axis named "{name}"')
        if not math.isfinite(value):
            _fail(C.INVALID_SCHEMA, path, "must be a finite number")
        if value != 0:
            need_kind("axes", path)
        spec = profile.axes[name]
        if abs(value) > spec.max:
            _fail(C.LIMIT_EXCEEDED, path, f"|{value}| exceeds max {spec.max}")

    # -- events -------------------------------------------------------------
    if len(d.events) > lim.max_events:
        _fail(C.LIMIT_EXCEEDED, "events", f"at most {lim.max_events} events")
    held = set(d.hold)
    press_times: list[int] = []
    pursuits = 0
    for i, ev in enumerate(d.events):
        path = f"events[{i}]"
        need_kind("look_at" if ev.type == "pursue" else ev.type, path)
        if ev.at_ms >= valid_for:
            _fail(C.LIMIT_EXCEEDED, f"{path}.at_ms", f"{ev.at_ms} is not before valid_for_ms {valid_for}")
        if isinstance(ev, TapEvent):
            need_button(ev.button, f"{path}.button")
            if ev.button in held:
                _fail(C.CONFLICTING_INPUT, f"{path}.button", f'"{ev.button}" is already held')
            hold_ms = ev.hold_ms if ev.hold_ms is not None else lim.default_tap_ms
            if not 1 <= hold_ms <= lim.max_tap_hold_ms:
                _fail(C.LIMIT_EXCEEDED, f"{path}.hold_ms", f"must be 1..{lim.max_tap_hold_ms}")
            if ev.point is not None:
                need_point(ev.point, f"{path}.point")
            need_within(ev.at_ms, hold_ms, path)
            press_times.append(ev.at_ms)
        elif isinstance(ev, ClickEvent):
            need_button(ev.button, f"{path}.button")
            if ev.button in held:
                _fail(C.CONFLICTING_INPUT, f"{path}.button", f'"{ev.button}" is already held')
            need_point(ev.point, f"{path}.point")
            need_within(ev.at_ms, lim.default_tap_ms, path)
            press_times.append(ev.at_ms)
        elif isinstance(ev, DragEvent):
            need_button(ev.button, f"{path}.button")
            if ev.button in held:
                _fail(C.CONFLICTING_INPUT, f"{path}.button", f'"{ev.button}" is already held')
            if len(ev.path) > lim.max_drag_points:
                _fail(C.LIMIT_EXCEEDED, f"{path}.path", f"at most {lim.max_drag_points} points")
            for j, pt in enumerate(ev.path):
                need_point(pt, f"{path}.path[{j}]")
            need_within(ev.at_ms, ev.duration_ms, path)
        elif isinstance(ev, MoveEvent):
            need_point(ev.point, f"{path}.point")
            need_within(ev.at_ms, ev.duration_ms, path)
        elif isinstance(ev, ScrollEvent):
            if ev.amount == 0 or abs(ev.amount) > safety.GLOBAL_MAX_SCROLL:
                _fail(C.LIMIT_EXCEEDED, f"{path}.amount", f"must be 1..{safety.GLOBAL_MAX_SCROLL} in size")
            if ev.point is not None:
                need_point(ev.point, f"{path}.point")
        elif isinstance(ev, MacroEvent):
            if ev.name not in profile.macros:
                _fail(C.UNKNOWN_MACRO, f"{path}.name", f'no macro named "{ev.name}"')
        elif isinstance(ev, LookAtEvent):
            if profile.fov_deg() is None or profile.look_axes() is None:
                _fail(C.LOOK_AT_UNCALIBRATED, path, "this profile has no calibrated camera")
            need_point(ev.point, f"{path}.point", moves_pointer=False)
            need_within(ev.at_ms, ev.duration_ms, path)
        elif isinstance(ev, TurnEvent):
            axes = profile.look_axes()
            if axes is None:
                _fail(C.LOOK_AT_UNCALIBRATED, path, "this profile has no calibrated look axes")
            need_within(ev.at_ms, turn_duration_ms(ev.yaw, ev.pitch, axes, ev.duration_ms), path)
        elif isinstance(ev, PursueEvent):
            if profile.fov_deg() is None or profile.look_axes() is None:
                _fail(C.LOOK_AT_UNCALIBRATED, path, "this profile has no calibrated camera")
            pursuits += 1
            if pursuits > 1:
                _fail(C.LIMIT_EXCEEDED, path, "at most one pursue per decision")
            need_point(ev.point, f"{path}.point", moves_pointer=False)
            if ev.attack is not None:
                need_button(ev.attack, f"{path}.attack")
                if ev.attack in held:
                    _fail(C.CONFLICTING_INPUT, f"{path}.attack", f'"{ev.attack}" is already held')
            if template_pixels(ev, profile) is None:
                _fail(C.INVALID_SCHEMA, f"{path}.template", "missing or not the target's picture at the sample scale")

    press_times.sort()
    for i, start in enumerate(press_times):
        in_window = sum(1 for t in press_times[i:] if t < start + 1000)
        if in_window > lim.max_taps_per_s:
            _fail(C.LIMIT_EXCEEDED, "events", f"more than {lim.max_taps_per_s} presses per second")

    # -- until --------------------------------------------------------------
    if d.until is not None:
        conditions = d.until.any
        if len(conditions) > lim.max_until_conditions:
            _fail(C.LIMIT_EXCEEDED, "until.any", f"at most {lim.max_until_conditions} conditions")
        for i, cond in enumerate(conditions):
            path = f"until.any[{i}]"
            if cond.region not in BUILTIN_REGIONS and cond.region not in profile.regions:
                _fail(C.UNKNOWN_REGION, f"{path}.region", f'no region named "{cond.region}"')
            if isinstance(cond, StaticCondition) and cond.for_ms >= valid_for:
                _fail(C.LIMIT_EXCEEDED, f"{path}.for_ms", f"must be shorter than valid_for_ms {valid_for}")

    # -- OS key combinations ------------------------------------------------
    keys = {profile.key_for(b) for b in d.hold}
    for ev in d.events:
        button = getattr(ev, "button", None)
        if button is not None:
            keys.add(profile.key_for(button))
    combo = safety.blocked_combination(keys)
    if combo is not None:
        _fail(C.BLOCKED_COMBINATION, "decision", "+".join(sorted(combo)) + " is blocked")

    return ValidatedDecision(
        decision=d,
        context=context,
        valid_for_ms=valid_for,
        wait_ms=wait_ms,
        clamped=tuple(clamped),
    )
