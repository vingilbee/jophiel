"""Coordinate mapping, look_at angles, and axis unit conversion (plan §7.2, §7.8).

Three coordinate spaces:

- **frame**: pixels in the observation frame the driver saw (e.g. 640x360);
- **crop**: pixels inside a named crop, which is a profile region scaled up;
- **viewport**: pixels in the game window at execution time.

The driver only ever speaks frame or crop pixels; the controller maps them.
"""

from __future__ import annotations

import math

from .profile import AxisSpec, Profile
from .protocol import CropPoint, Point


def point_to_frame(point: Point, profile: Profile) -> tuple[float, float]:
    """Map a decision point to frame pixels. The caller checks crop/region names."""
    if isinstance(point, CropPoint):
        x0, y0, _, _ = profile.regions[point.crop]
        scale = profile.crop_scale(point.crop) or 1
        return (x0 + point.xy[0] / scale, y0 + point.xy[1] / scale)
    return (float(point[0]), float(point[1]))


def crop_size(profile: Profile, region: str) -> tuple[int, int]:
    x0, y0, x1, y1 = profile.regions[region]
    scale = profile.crop_scale(region) or 1
    return ((x1 - x0) * scale, (y1 - y0) * scale)


def frame_to_viewport(
    xy: tuple[float, float], frame_size: tuple[int, int], viewport_size: tuple[int, int]
) -> tuple[int, int]:
    fx = xy[0] * viewport_size[0] / frame_size[0]
    fy = xy[1] * viewport_size[1] / frame_size[1]
    vx = min(max(round(fx), 0), viewport_size[0] - 1)
    vy = min(max(round(fy), 0), viewport_size[1] - 1)
    return (vx, vy)


def look_at_degrees(
    xy: tuple[float, float], frame_size: tuple[int, int], fov_deg: tuple[float, float]
) -> tuple[float, float]:
    """Yaw and pitch that bring frame point ``xy`` to the screen centre.

    Perspective projection: a pixel at normalised offset ``n`` from the centre
    (-1 at the left edge, +1 at the right) sits at ``atan(n * tan(fov / 2))``.
    Positive yaw turns right, positive pitch looks down.
    """
    w, h = frame_size
    nx = (xy[0] - w / 2) / (w / 2)
    ny = (xy[1] - h / 2) / (h / 2)
    yaw = math.degrees(math.atan(nx * math.tan(math.radians(fov_deg[0]) / 2)))
    pitch = math.degrees(math.atan(ny * math.tan(math.radians(fov_deg[1]) / 2)))
    return (yaw, pitch)


def axis_counts_per_s(spec: AxisSpec, value: float) -> float:
    """Convert an axis value in its profile unit to mouse counts per second."""
    if spec.unit == "deg_per_s":
        assert spec.counts_per_degree is not None
        return value * spec.counts_per_degree
    return value


MIN_TURN_MS = 100


def turn_duration_ms(yaw: float, pitch: float, axes, requested_ms: int | None) -> int:
    """How long a turn takes: never faster than the look axes' max speed."""
    yaw_axis, pitch_axis = axes
    needed = max(abs(yaw) / yaw_axis.max, abs(pitch) / pitch_axis.max) * 1000
    return max(MIN_TURN_MS, int(-(-needed // 1)), requested_ms or 0)
