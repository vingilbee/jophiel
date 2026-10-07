"""Game profiles (plan §7.3-7.4): trusted, versioned YAML naming a game's inputs.

A profile is the agent's permission list for one game. ``load_profile`` parses
it and runs every check in ``profile_issues``; a profile with any issue is
refused as a whole, never partially loaded.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import safety

FORMAT_VERSION = 1
UNKNOWN_CONTEXT = "unknown"
BUILTIN_REGIONS = frozenset({"full", "center"})

# Input kinds a context can allow. "text" (typing) exists in the plan but is
# off in v1, so a profile that allows it is refused.
INPUT_KINDS = frozenset(
    {"hold", "axes", "tap", "click", "drag", "move", "scroll", "macro", "look_at", "turn"}
)

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_FACT_KEY = re.compile(r"^[a-z0-9_]+(\.([a-z0-9_]+|\*))*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Target(_Strict):
    process: Annotated[str, Field(min_length=1, max_length=64)]
    window_title_hint: Annotated[str, Field(max_length=64)] | None = None


class ButtonSpec(_Strict):
    key: Annotated[str, Field(min_length=1, max_length=24)]
    description: Annotated[str, Field(min_length=1, max_length=120)]


class AxisSpec(_Strict):
    device: Literal["mouse_relative_x", "mouse_relative_y"]
    unit: Literal["deg_per_s", "counts_per_s"]
    counts_per_degree: Annotated[float, Field(gt=0)] | None = None
    max: Annotated[float, Field(gt=0)]
    description: Annotated[str, Field(min_length=1, max_length=120)]


class Camera(_Strict):
    fov_h_deg: Annotated[float, Field(gt=1, lt=179)] | None = None
    fov_v_deg: Annotated[float, Field(gt=1, lt=179)] | None = None


class Crop(_Strict):
    region: str
    scale: Annotated[int, Field(ge=1, le=4)] = 2


class Observation(_Strict):
    size: tuple[Annotated[int, Field(ge=160, le=1920)], Annotated[int, Field(ge=90, le=1080)]]
    frames: Annotated[int, Field(ge=1, le=3)] = 1
    crops: list[Crop] = Field(default_factory=list, max_length=4)


class ContextDetect(_Strict):
    method: Literal["cursor_visibility", "none"] = "none"
    cursor_hidden: str | None = None
    cursor_visible: str | None = None


class ContextMode(_Strict):
    allow: list[str] = Field(min_length=1)
    # Restricts which buttons this context accepts; None means all buttons.
    buttons: list[str] | None = None


class Contexts(_Strict):
    detect: ContextDetect = ContextDetect()
    modes: dict[str, ContextMode] = Field(min_length=1)


class Limits(_Strict):
    default_valid_for_ms: Annotated[int, Field(gt=0)] = 1_500
    max_valid_for_ms: Annotated[int, Field(gt=0)] = 3_000
    max_until_ms: Annotated[int, Field(gt=0)] = 8_000
    max_wait_ms: Annotated[int, Field(ge=0)] = 2_000
    default_tap_ms: Annotated[int, Field(gt=0)] = 50
    max_tap_hold_ms: Annotated[int, Field(gt=0)] = 500
    max_events: Annotated[int, Field(gt=0)] = 12
    max_taps_per_s: Annotated[int, Field(gt=0)] = 10
    max_drag_points: Annotated[int, Field(ge=2)] = 32
    max_until_conditions: Annotated[int, Field(gt=0)] = 3
    max_hold_buttons: Annotated[int, Field(gt=0)] = 6


class Profile(_Strict):
    format_version: int
    id: str
    revision: Annotated[int, Field(ge=1)]
    display_name: Annotated[str, Field(min_length=1, max_length=64)]
    target: Target
    online_allowed: bool = False
    buttons: dict[str, ButtonSpec] = Field(min_length=1)
    axes: dict[str, AxisSpec] = Field(default_factory=dict)
    fact_keys: list[str] = Field(default_factory=list, max_length=24)
    game_notes: Annotated[str, Field(max_length=600)] = ""
    camera: Camera | None = None
    observation: Observation
    regions: dict[str, tuple[int, int, int, int]] = Field(default_factory=dict)
    macros: dict[str, list[dict]] = Field(default_factory=dict)
    contexts: Contexts
    limits: Limits = Limits()

    # -- derived helpers ---------------------------------------------------

    def key_for(self, button: str) -> str:
        return self.buttons[button].key.upper()

    def region_rect(self, name: str) -> tuple[int, int, int, int]:
        w, h = self.observation.size
        if name == "full":
            return (0, 0, w, h)
        if name == "center":
            return (w * 3 // 8, h * 3 // 8, w * 5 // 8, h * 5 // 8)
        return self.regions[name]

    def crop_scale(self, region: str) -> int | None:
        for crop in self.observation.crops:
            if crop.region == region:
                return crop.scale
        return None

    def fov_deg(self) -> tuple[float, float] | None:
        """(horizontal, vertical) field of view for the observation aspect ratio."""
        if self.camera is None:
            return None
        w, h = self.observation.size
        return resolve_fov(self.camera, w / h)

    def look_axes(self) -> tuple[AxisSpec, AxisSpec] | None:
        """The calibrated deg_per_s axes on mouse x and y, if both exist."""
        by_device = {a.device: a for a in self.axes.values()}
        x, y = by_device.get("mouse_relative_x"), by_device.get("mouse_relative_y")
        if x is None or y is None:
            return None
        if x.unit != "deg_per_s" or y.unit != "deg_per_s":
            return None
        if x.counts_per_degree is None or y.counts_per_degree is None:
            return None
        return (x, y)

    def rules_for(self, context: str) -> "ContextRules":
        """Allowed input kinds and buttons in ``context``.

        An undetected or unrecognised context only allows what every context
        allows (plan §7.3).
        """
        modes = self.contexts.modes
        if context in modes:
            return _rules(self, modes[context])
        each = [_rules(self, m) for m in modes.values()]
        kinds = frozenset.intersection(*(r.kinds for r in each))
        buttons = frozenset.intersection(*(r.buttons for r in each))
        return ContextRules(kinds=kinds, buttons=buttons)


@dataclass(frozen=True)
class ContextRules:
    kinds: frozenset[str]
    buttons: frozenset[str]


def _rules(profile: Profile, mode: ContextMode) -> ContextRules:
    buttons = frozenset(profile.buttons) if mode.buttons is None else frozenset(mode.buttons)
    return ContextRules(kinds=frozenset(mode.allow), buttons=buttons)


def resolve_fov(camera: Camera, aspect: float) -> tuple[float, float]:
    """Fill in the missing FOV from the other one and the aspect ratio."""
    h, v = camera.fov_h_deg, camera.fov_v_deg
    if h is None and v is not None:
        h = math.degrees(2 * math.atan(math.tan(math.radians(v) / 2) * aspect))
    elif v is None and h is not None:
        v = math.degrees(2 * math.atan(math.tan(math.radians(h) / 2) / aspect))
    assert h is not None and v is not None
    return (h, v)


class ProfileError(Exception):
    def __init__(self, source: str, issues: list[str]):
        self.source = source
        self.issues = issues
        super().__init__(f"{source}: " + "; ".join(issues))


def profile_issues(p: Profile) -> list[str]:
    """Every reason this profile must be refused. Empty means it is valid."""
    issues: list[str] = []
    add = issues.append

    if p.format_version != FORMAT_VERSION:
        add(f"format_version {p.format_version} is not supported (expected {FORMAT_VERSION})")
    if not _NAME.match(p.id):
        add(f"id {p.id!r} must be lowercase letters, digits and underscores")
    if p.target.process.upper().startswith("REPLACE_ME"):
        add("target.process: this is a template; set the game's executable name")
    if p.online_allowed:
        add("online_allowed: online play is not supported in v1")
    if p.macros:
        add("macros: macros are not supported yet; leave this empty")

    for name, spec in p.buttons.items():
        if not _NAME.match(name):
            add(f"buttons.{name}: invalid name")
        key = spec.key.upper()
        if key in safety.BLOCKED_KEYS:
            add(f"buttons.{name}: blocked_os_key: {key} reaches the operating system")
        elif key not in safety.ALLOWED_KEYS:
            add(f"buttons.{name}: unknown key {spec.key!r}")

    devices: dict[str, str] = {}
    for name, axis in p.axes.items():
        if not _NAME.match(name):
            add(f"axes.{name}: invalid name")
        if axis.device in devices:
            add(f"axes.{name}: device {axis.device} is already used by axes.{devices[axis.device]}")
        devices[axis.device] = name
        if axis.unit == "deg_per_s":
            if axis.counts_per_degree is None:
                add(f"axes.{name}: deg_per_s needs counts_per_degree (calibrate it)")
            if axis.max > safety.GLOBAL_MAX_AXIS_DEG_PER_S:
                add(f"axes.{name}: max {axis.max} exceeds {safety.GLOBAL_MAX_AXIS_DEG_PER_S} deg/s")
        elif axis.max > safety.GLOBAL_MAX_AXIS_COUNTS_PER_S:
            add(f"axes.{name}: max {axis.max} exceeds {safety.GLOBAL_MAX_AXIS_COUNTS_PER_S} counts/s")

    for key in p.fact_keys:
        if not _FACT_KEY.match(key):
            add(f"fact_keys: {key!r} must be dotted lowercase, optionally ending in .*")

    w, h = p.observation.size
    for name, (x0, y0, x1, y1) in p.regions.items():
        if not _NAME.match(name):
            add(f"regions.{name}: invalid name")
        if name in BUILTIN_REGIONS:
            add(f"regions.{name}: {name!r} is a built-in region name")
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            add(f"regions.{name}: must be inside the {w}x{h} observation frame with x0<x1, y0<y1")
    seen_crops: set[str] = set()
    for crop in p.observation.crops:
        if crop.region not in p.regions:
            add(f"observation.crops: unknown region {crop.region!r}")
        if crop.region in seen_crops:
            add(f"observation.crops: region {crop.region!r} listed twice")
        seen_crops.add(crop.region)

    if p.camera is not None:
        cam = p.camera
        if cam.fov_h_deg is None and cam.fov_v_deg is None:
            add("camera: needs fov_h_deg or fov_v_deg")
        elif cam.fov_h_deg is not None and cam.fov_v_deg is not None:
            derived_h, _ = resolve_fov(Camera(fov_v_deg=cam.fov_v_deg), w / h)
            if abs(derived_h - cam.fov_h_deg) > 2.0:
                add(
                    f"camera: fov_h_deg {cam.fov_h_deg} and fov_v_deg {cam.fov_v_deg} disagree for "
                    f"a {w}x{h} frame (expected about {derived_h:.1f}); give only one"
                )

    modes = p.contexts.modes
    for mode_name, mode in modes.items():
        if not _NAME.match(mode_name) or mode_name == UNKNOWN_CONTEXT:
            add(f"contexts.modes.{mode_name}: invalid name")
        for kind in mode.allow:
            if kind == "text":
                add(f"contexts.modes.{mode_name}: typing (text) is off in v1")
            elif kind not in INPUT_KINDS:
                add(f"contexts.modes.{mode_name}: unknown input kind {kind!r}")
        if "turn" in mode.allow and p.look_axes() is None:
            add(f"contexts.modes.{mode_name}: turn needs calibrated deg_per_s axes on mouse x and y")
        if "look_at" in mode.allow and (p.camera is None or p.look_axes() is None):
            add(
                f"contexts.modes.{mode_name}: look_at needs a camera block and calibrated "
                "deg_per_s axes on mouse x and y"
            )
        for button in mode.buttons or []:
            if button not in p.buttons:
                add(f"contexts.modes.{mode_name}: unknown button {button!r}")
    detect = p.contexts.detect
    if detect.method == "cursor_visibility":
        for field_name in ("cursor_hidden", "cursor_visible"):
            target = getattr(detect, field_name)
            if target is None or target not in modes:
                add(f"contexts.detect.{field_name}: must name one of {sorted(modes)}")

    lim = p.limits
    ceilings = [
        ("max_valid_for_ms", lim.max_valid_for_ms, safety.GLOBAL_MAX_VALID_FOR_MS),
        ("max_until_ms", lim.max_until_ms, safety.GLOBAL_MAX_UNTIL_MS),
        ("max_events", lim.max_events, safety.GLOBAL_MAX_EVENTS),
        ("max_taps_per_s", lim.max_taps_per_s, safety.GLOBAL_MAX_TAPS_PER_S),
        ("max_drag_points", lim.max_drag_points, safety.GLOBAL_MAX_DRAG_POINTS),
        ("max_until_conditions", lim.max_until_conditions, safety.GLOBAL_MAX_UNTIL_CONDITIONS),
        ("max_hold_buttons", lim.max_hold_buttons, safety.GLOBAL_MAX_HOLD_BUTTONS),
        ("max_tap_hold_ms", lim.max_tap_hold_ms, safety.GLOBAL_MAX_TAP_HOLD_MS),
    ]
    for field_name, value, ceiling in ceilings:
        if value > ceiling:
            add(f"limits.{field_name}: {value} exceeds the global ceiling {ceiling}")
    if not lim.default_valid_for_ms <= lim.max_valid_for_ms <= lim.max_until_ms:
        add("limits: need default_valid_for_ms <= max_valid_for_ms <= max_until_ms")
    if lim.max_wait_ms > lim.max_valid_for_ms:
        add("limits: max_wait_ms must not exceed max_valid_for_ms")
    if lim.default_tap_ms > lim.max_tap_hold_ms:
        add("limits: default_tap_ms must not exceed max_tap_hold_ms")

    return issues


def parse_profile(data: object, source: str = "<profile>") -> Profile:
    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        issues = [
            f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise ProfileError(source, issues) from None
    issues = profile_issues(profile)
    if issues:
        raise ProfileError(source, issues)
    return profile


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_profile(data, source=str(path))
