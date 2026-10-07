"""The fake controller: the real Scheduler on a manual clock with a recorder."""

from __future__ import annotations

import pytest

from desktop_controller.fake import FakeController, ManualClock, RecordingBackend
from desktop_controller.geometry import look_at_degrees
from desktop_controller.protocol import RejectionCode
from desktop_controller.scheduler import DecisionState, SafeInput, Scheduler
from desktop_controller.until import RegionSample

S = DecisionState
C = RejectionCode


@pytest.fixture
def fc(minecraft) -> FakeController:
    fc = FakeController(minecraft)
    assert fc.scheduler.grant_lease(60_000, generation=1)
    return fc


def apply(fc, decision_id, raw, context="playing", generation=None):
    gen = fc.scheduler.generation if generation is None else generation
    return fc.scheduler.apply(decision_id, raw, context, gen)


# -- rule 2: expiry -----------------------------------------------------------

def test_hold_releases_at_valid_for(fc):
    rec = apply(fc, "d1", {"intent": "walk", "hold": ["forward"], "valid_for_ms": 1500})
    assert rec.state == S.RUNNING
    fc.step(2000)
    assert fc.backend.keys() == [(0, "down", "W"), (1500, "up", "W")]
    assert rec.state == S.EXPIRED and rec.ended_by == "valid_for_ms" and rec.ran_ms == 1500
    assert fc.scheduler.input.down == set()


def test_default_valid_for_from_profile(fc):
    apply(fc, "d1", {"hold": ["forward"]})
    fc.step(3000)
    assert fc.backend.keys()[-1] == (1500, "up", "W")


# -- rule 1 + 3: complete state, supersede by diff ------------------------------

def test_supersede_keeps_shared_keys_down(fc):
    first = apply(fc, "d1", {"hold": ["forward"]})
    fc.step(500)
    second = apply(fc, "d2", {"hold": ["forward", "jump"]})
    fc.step(100)
    assert fc.backend.keys() == [(0, "down", "W"), (500, "down", "SPACE")]  # W never re-pressed
    assert first.state == S.SUPERSEDED and second.state == S.RUNNING


def test_omitted_buttons_are_released(fc):
    apply(fc, "d1", {"hold": ["forward", "sprint"]})
    fc.step(200)
    apply(fc, "d2", {"hold": ["forward"]})
    assert (200, "up", "CTRL") in fc.backend.keys()
    assert fc.scheduler.input.down == {"W"}


def test_empty_decision_releases_everything(fc):
    apply(fc, "d1", {"hold": ["forward", "attack"], "axes": {"look_yaw": 90}})
    fc.step(300)
    apply(fc, "d2", {})
    moved = fc.backend.relative_total()
    fc.step(1000)
    assert fc.scheduler.input.down == set()
    assert fc.backend.relative_total() == moved  # turning stopped too


def test_supersede_cancels_old_pending_events_and_releases_mid_tap(fc):
    apply(fc, "d1", {"events": [{"type": "tap", "button": "jump", "at_ms": 0, "hold_ms": 300},
                                {"type": "tap", "button": "drop", "at_ms": 800}]})
    fc.step(100)
    apply(fc, "d2", {"hold": ["forward"]})
    fc.step(1500)
    keys = fc.backend.keys()
    assert (100, "up", "SPACE") in keys  # in-flight tap released at supersede
    assert all(k[2] != "Q" for k in keys)  # old future tap never fired


def test_rejected_decision_leaves_current_state_running(fc):
    rec = apply(fc, "d1", {"hold": ["forward"]})
    fc.step(200)
    bad = apply(fc, "d2", {"hold": ["fly"]})
    assert bad.state == S.REJECTED and bad.rejection.code == C.UNKNOWN_BUTTON
    assert rec.state == S.RUNNING and fc.scheduler.input.down == {"W"}


# -- events -------------------------------------------------------------------

def test_tap_timing(fc):
    apply(fc, "d1", {"hold": ["forward"], "events": [{"type": "tap", "button": "jump", "at_ms": 300, "hold_ms": 50}]})
    fc.step(1000)
    assert (300, "down", "SPACE") in fc.backend.keys()
    assert (350, "up", "SPACE") in fc.backend.keys()


def test_click_maps_frame_point_to_viewport(fc):
    apply(fc, "d1", {"events": [{"type": "click", "button": "menu_click", "point": [320, 180]}]}, context="menu")
    fc.step(200)
    log = fc.backend.log
    assert log[0] == (0, "move_to", 640, 360)  # 640x360 frame -> 1280x720 window
    assert log[1] == (0, "down", "MOUSE_LEFT") and log[2] == (50, "up", "MOUSE_LEFT")


def test_drag_follows_path_and_cancel_releases(fc):
    apply(fc, "d1", {"events": [{"type": "drag", "button": "menu_click",
                                 "path": [[100, 100], [300, 100]], "duration_ms": 400}]}, context="menu")
    fc.step(200)
    moves = [e for e in fc.backend.log if e[1] == "move_to"]
    assert moves[0] == (0, "move_to", 200, 200) and 200 < moves[-1][2] < 600
    fc.scheduler.cancel("user_pause")
    n = len(fc.backend.log)
    fc.step(500)
    assert fc.backend.log[n - 1][1:] == ("up", "MOUSE_LEFT")
    assert len(fc.backend.log) == n  # rest of the path discarded


# -- axes and look_at ---------------------------------------------------------

def test_look_axis_turns_by_counts_per_degree(fc, minecraft):
    apply(fc, "d1", {"axes": {"look_yaw": 90}, "valid_for_ms": 1000})
    fc.step(1500)
    dx, dy = fc.backend.relative_total()
    assert dx == int(90 * 6.67)  # 90 deg/s for 1 s at 6.67 counts/deg
    assert dy == 0


def test_look_at_turns_exactly_to_target(fc, minecraft):
    apply(fc, "d1", {"events": [{"type": "look_at", "point": [480, 120], "duration_ms": 150}]})
    fc.step(500)
    yaw, pitch = look_at_degrees((480, 120), (640, 360), minecraft.fov_deg())
    dx, dy = fc.backend.relative_total()
    assert abs(dx - yaw * 6.67) <= 1 and abs(dy - pitch * 6.67) <= 1
    assert dx > 0 and dy < 0  # right and up
    # spread over 150 ms, not a single teleport
    rel_times = {e[0] for e in fc.backend.log if e[1] == "rel"}
    assert min(rel_times) < 50 and max(rel_times) >= 140


# -- rule 4: until ------------------------------------------------------------

def test_until_changed_stops_early(fc):
    rec = apply(fc, "d1", {"hold": ["attack"], "valid_for_ms": 6000,
                           "until": {"any": [{"region": "crosshair", "condition": "changed", "threshold": 0.4}]}})
    samples = lambda t: {"crosshair": RegionSample(0.6 if t >= 800 else 0.05, 0.02)}
    fc.step(2000, samples=samples)
    assert rec.state == S.COMPLETED and rec.ended_by == "until:0" and rec.ran_ms == 800
    assert fc.backend.keys() == [(0, "down", "MOUSE_LEFT"), (800, "up", "MOUSE_LEFT")]


def test_until_static_detects_blocked_walking(fc):
    rec = apply(fc, "d1", {"hold": ["forward", "sprint"], "valid_for_ms": 5000,
                           "until": {"any": [{"region": "full", "condition": "static", "for_ms": 500}]}})
    samples = lambda t: {"full": RegionSample(0.3, 0.05 if t < 1000 else 0.0)}
    fc.step(3000, samples=samples)
    assert rec.state == S.COMPLETED and rec.ran_ms == 1490  # last motion seen at 990 + 500 ms still
    assert fc.scheduler.input.down == set()


def test_until_never_met_stops_at_cap(fc):
    rec = apply(fc, "d1", {"hold": ["attack"], "valid_for_ms": 60000,
                           "until": {"any": [{"region": "crosshair", "condition": "changed"}]}})
    fc.step(10_000, samples={"crosshair": RegionSample(0.0, 0.0)})
    assert rec.state == S.EXPIRED and rec.ran_ms == 8000 and rec.clamped == ("valid_for_ms",)


# -- lease, generation, cancel --------------------------------------------------

def test_no_input_without_lease(minecraft):
    fc = FakeController(minecraft)
    rec = apply(fc, "d1", {"hold": ["forward"]})
    assert rec.state == S.REJECTED and rec.rejection.code == C.NO_LEASE
    assert fc.backend.log == []


def test_lease_expiry_releases_mid_hold(minecraft):
    fc = FakeController(minecraft)
    fc.scheduler.grant_lease(1000, generation=1)
    rec = apply(fc, "d1", {"hold": ["forward"], "valid_for_ms": 3000})
    fc.step(2000)
    assert rec.state == S.CANCELLED and rec.ended_by == "lease_expired"
    assert fc.backend.keys() == [(0, "down", "W"), (1000, "up", "W")]


def test_heartbeat_renews_lease(minecraft):
    fc = FakeController(minecraft)
    fc.scheduler.grant_lease(1000, generation=1)
    rec = apply(fc, "d1", {"hold": ["forward"], "valid_for_ms": 2500})
    for _ in range(5):
        fc.step(500)
        fc.scheduler.grant_lease(1000, generation=1)
    assert rec.state == S.EXPIRED and rec.ran_ms == 2500


def test_cancel_fences_old_generation(fc):
    apply(fc, "d1", {"hold": ["forward"]})
    fc.step(100)
    new_gen = fc.scheduler.cancel("user_pause")
    assert new_gen == 2 and fc.scheduler.input.down == set()
    assert not fc.scheduler.grant_lease(5000, generation=1)  # a late old grant cannot revive it
    late = apply(fc, "d2", {"hold": ["forward"]}, generation=1)
    assert late.state == S.REJECTED and late.rejection.code == C.STALE_GENERATION
    no_lease = apply(fc, "d3", {"hold": ["forward"]}, generation=2)
    assert no_lease.rejection.code == C.NO_LEASE  # resuming needs a fresh lease


# -- dedupe -------------------------------------------------------------------

def test_duplicate_id_same_payload_does_not_repeat(fc):
    first = apply(fc, "d1", {"hold": ["forward"]})
    fc.step(100)
    again = apply(fc, "d1", {"hold": ["forward"]})
    assert again is first
    assert [k for k in fc.backend.keys() if k[1] == "down"] == [(0, "down", "W")]


def test_duplicate_id_different_payload_is_rejected(fc):
    apply(fc, "d1", {"hold": ["forward"]})
    conflict = apply(fc, "d1", {"hold": ["back"]})
    assert conflict.state == S.REJECTED and conflict.rejection.code == C.DUPLICATE_CONFLICT
    assert fc.scheduler.input.down == {"W"}
    assert fc.scheduler.records["d1"].state == S.RUNNING  # original untouched


# -- safety net and startup ---------------------------------------------------

def test_safe_input_refuses_blocked_keys_and_combinations():
    clock = ManualClock()
    backend = RecordingBackend(clock)
    safe = SafeInput(backend)
    assert not safe.press("LWIN")
    assert safe.press("ALT")
    assert not safe.press("F4")  # Alt+F4 never reaches the OS
    assert backend.keys() == [(0, "down", "ALT")]
    assert safe.refused == ["LWIN", "F4"]


def test_startup_releases_every_profile_key(minecraft):
    clock = ManualClock()
    backend = RecordingBackend(clock)
    Scheduler(minecraft, clock, backend, (1280, 720))
    released = {e[2] for e in backend.log if e[1] == "up"}
    assert {"W", "SPACE", "CTRL", "MOUSE_LEFT", "MOUSE_RIGHT", "E", "1", "9"} <= released
    assert all(e[1] == "up" for e in backend.log)


# --- turn: exact relative degrees (stage D) ----------------------------------

def test_turn_moves_exactly_the_requested_degrees(minecraft):
    from desktop_controller.fake import FakeController

    fc = FakeController(minecraft)
    fc.scheduler.grant_lease(5000, fc.scheduler.generation)
    fc.scheduler.apply("t", {"events": [{"type": "turn", "yaw": -30, "pitch": 10}], "valid_for_ms": 1000},
                       "playing", fc.scheduler.generation)
    fc.step(1000)
    assert fc.backend.relative_total() == (round(-30 * 6.67), round(10 * 6.67))


def test_turn_speed_is_capped_by_the_look_axes(minecraft):
    from desktop_controller.fake import FakeController

    fc = FakeController(minecraft)
    fc.scheduler.grant_lease(5000, fc.scheduler.generation)
    fc.scheduler.apply("t", {"events": [{"type": "turn", "yaw": 180, "pitch": 0}], "valid_for_ms": 1500},
                       "playing", fc.scheduler.generation)
    fc.step(250)
    assert abs(fc.backend.relative_total()[0] - 600) <= 2  # half of 180 deg at 360 deg/s after 250 ms
    fc.step(500)
    assert fc.backend.relative_total()[0] == round(180 * 6.67)


def test_a_turn_must_fit_its_decision_and_needs_a_calibrated_camera(minecraft, generic):
    from desktop_controller.protocol import Rejection
    from desktop_controller.validate import validate_decision

    too_long = validate_decision({"events": [{"type": "turn", "yaw": 180}], "valid_for_ms": 300}, minecraft, "playing")
    assert isinstance(too_long, Rejection) and too_long.code.value == "limit_exceeded"
    in_menu = validate_decision({"events": [{"type": "turn", "yaw": 10}]}, minecraft, "menu")
    assert isinstance(in_menu, Rejection) and in_menu.code.value == "context_mismatch"
