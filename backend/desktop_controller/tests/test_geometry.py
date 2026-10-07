from __future__ import annotations

import math

import pytest

from desktop_controller.geometry import frame_to_viewport, look_at_degrees, point_to_frame
from desktop_controller.protocol import CropPoint


def test_look_at_centre_is_zero():
    assert look_at_degrees((320, 180), (640, 360), (100, 70)) == pytest.approx((0, 0))


def test_look_at_edge_is_half_fov():
    yaw, pitch = look_at_degrees((640, 360), (640, 360), (100, 70))
    assert yaw == pytest.approx(50) and pitch == pytest.approx(35)


def test_look_at_is_perspective_not_linear():
    # halfway to the edge is MORE than half the half-FOV: pixels near the
    # centre span more angle, so a linear guess would undershoot
    yaw, _ = look_at_degrees((480, 180), (640, 360), (100, 70))
    assert yaw == pytest.approx(math.degrees(math.atan(0.5 * math.tan(math.radians(50)))))
    assert yaw == pytest.approx(30.79, abs=0.01) and yaw > 25


def test_crop_point_maps_to_frame(minecraft):
    # hotbar region starts at (228, 338) and is sent at scale 2
    assert point_to_frame(CropPoint(crop="hotbar", xy=(20, 10)), minecraft) == (238.0, 343.0)
    assert point_to_frame((5, 6), minecraft) == (5.0, 6.0)


def test_frame_to_viewport_scales_and_clamps():
    assert frame_to_viewport((320, 180), (640, 360), (1280, 720)) == (640, 360)
    assert frame_to_viewport((639.9, 359.9), (640, 360), (1280, 720)) == (1279, 719)
