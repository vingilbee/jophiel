"""Reflexes: fast, plain-code reactions inside the VM (plan D3).

The driver decides from a picture that is ~2 s old by the time its move
starts. Reflexes close that gap for the simple cases. A sampler thread grabs
the game window ~15 times a second (kept in the VM: never encoded, never
sent), and two things read each sample:

- the move's own ``until`` stop rules, which the driver chose (``until``);
- the always-on reflexes below, configured by the trusted profile.

A reflex can only ever let go of keys: in ``act`` mode it ends the move
(``ended_by="reflex:<name>"``), in ``watch`` mode it only notes on the move's
report that it would have, so thresholds can be tuned on real play first.

``RegionMeter`` turns pictures into per-region change ratios; ``ReflexWatch``
turns those, plus what the scheduler is doing, into at most one reflex name.
Both are deterministic and run against scripted pictures in tests.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops

from .profile import BUILTIN_REGIONS, Profile, Reflexes
from .until import STATIC_EPSILON, RegionSample

SAMPLE_HZ = 15
# Samples are this share of the observation size (regions are in observation pixels).
SAMPLE_SCALE = 0.5
# A pixel "changed" when its grey level moved by more than this (as the driver's screen_change).
PIXEL_DELTA = 16
_CHANGED = [0] * (PIXEL_DELTA + 1) + [255] * (255 - PIXEL_DELTA)
# The scheduler's own camera motion or a key/click this recent explains a screen change.
OWN_MOTION_GRACE_MS = 400
# Samples further apart than this are a gap: stillness must be seen, not assumed.
SAMPLE_GAP_MS = 250


@dataclass(frozen=True)
class ReflexHit:
    name: str  # status_changed | jolt | blocked
    at_ms: int  # since the move started
    acted: bool  # False in watch mode: it would have stopped the move


def sample_size(profile: Profile) -> tuple[int, int]:
    w, h = profile.observation.size
    return (max(1, round(w * SAMPLE_SCALE)), max(1, round(h * SAMPLE_SCALE)))


class RegionMeter:
    """How much of each region changed since the move began and since the
    previous sample (shares of pixels, 0..1)."""

    def __init__(self, profile: Profile):
        self.size = sample_size(profile)
        names = set(BUILTIN_REGIONS) | set(profile.regions)
        self.boxes = {name: self._scaled(profile.region_rect(name)) for name in names}
        self._ref: Image.Image | None = None
        self._ref_id: str | None = None
        self._prev: Image.Image | None = None

    def _scaled(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = (round(v * SAMPLE_SCALE) for v in rect)
        return (x0, y0, max(x1, x0 + 1), max(y1, y0 + 1))

    def measure(self, image: Image.Image, decision_id: str) -> dict[str, RegionSample]:
        if image.mode != "L" or image.size != self.size:
            image = image.convert("L").resize(self.size, Image.Resampling.BILINEAR)
        if decision_id != self._ref_id:
            # A new move: its first sample is the reference for "since start",
            # and "since previous" starts fresh (no jump against an old picture).
            self._ref, self._ref_id, self._prev = image, decision_id, image
        assert self._ref is not None and self._prev is not None
        since_start = ImageChops.difference(self._ref, image).point(_CHANGED)
        since_prev = ImageChops.difference(self._prev, image).point(_CHANGED)
        self._prev = image
        return {name: RegionSample(_share(since_start, box), _share(since_prev, box))
                for name, box in self.boxes.items()}


def _share(mask: Image.Image, box: tuple[int, int, int, int]) -> float:
    crop = mask.crop(box)
    total = crop.width * crop.height
    return crop.histogram()[255] / total if total else 0.0


class ReflexWatch:
    """The always-on reflexes. One per scheduler; ``check`` is called on every
    sample while a move runs and returns the reflex that fires, if any."""

    def __init__(self, config: Reflexes):
        self.config = config
        self._ahead_moved_at: int | None = None
        self._last_sample_ms: int | None = None

    def check(
        self, now_ms: int, samples: dict[str, RegionSample], *, context: str, own_motion: bool,
        move_held_since: int | None,
    ) -> str | None:
        cfg = self.config
        if cfg.mode == "off" or (cfg.contexts is not None and context not in cfg.contexts):
            self._ahead_moved_at = None
            return None
        gap = self._last_sample_ms is None or now_ms - self._last_sample_ms > SAMPLE_GAP_MS
        self._last_sample_ms = now_ms
        ahead = samples.get(cfg.ahead_region)
        if ahead is not None and (gap or self._ahead_moved_at is None or own_motion
                                  or ahead.changed_since_prev > STATIC_EPSILON):
            self._ahead_moved_at = now_ms
        if cfg.status_region is not None:
            status = samples.get(cfg.status_region)
            if status is not None and status.changed_since_start > cfg.status_change:
                return "status_changed"
        whole = samples.get("full")
        if whole is not None and not own_motion and whole.changed_since_prev > cfg.jolt_change:
            return "jolt"
        if (move_held_since is not None and now_ms - move_held_since >= cfg.blocked_ms
                and self._ahead_moved_at is not None and now_ms - self._ahead_moved_at >= cfg.blocked_ms):
            return "blocked"
        return None
