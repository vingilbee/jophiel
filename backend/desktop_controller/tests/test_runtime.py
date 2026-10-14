"""The guest session runtime: lease, fencing, the game-window guard, watchdog (plan §19, stage C)."""

from __future__ import annotations

import pytest

from desktop_controller import safety
from desktop_controller.fake import FakeInput, ManualClock
from desktop_controller.input_windows import MOUSE_BUTTON_FLAGS, SCANCODES, absolute_coords, relative_steps
from desktop_controller.runtime import WATCHDOG_STALL_MS, SessionRuntime
from desktop_controller.win32 import Rect

WALK = {"intent": "walk", "hold": ["forward"], "valid_for_ms": 2000}


@pytest.fixture
def setup(minecraft):
    clock = ManualClock(1_000)
    backend = FakeInput(clock=clock)
    runtime = SessionRuntime(minecraft, backend, clock=clock, threads=False)
    return runtime, backend, clock


def _down(backend: FakeInput) -> set[str]:
    return set(backend.down)


def test_no_input_without_a_lease(setup):
    runtime, backend, _ = setup
    record = runtime.apply("d1", WALK, generation=1)
    assert record.rejection and record.rejection.code.value == "no_lease"
    assert _down(backend) == set()


def test_a_lease_allows_input_and_lapses_without_renewal(setup):
    runtime, backend, clock = setup
    runtime.grant(1, 1500)
    runtime.apply("d1", {"hold": ["forward"], "valid_for_ms": 3000}, generation=1)
    assert _down(backend) == {"W"}
    clock.advance(1000)
    runtime.grant(1, 1500)  # renewed by a heartbeat reply
    clock.advance(1000)
    runtime.tick()
    assert _down(backend) == {"W"}  # 2 s in: still walking thanks to the renewal
    clock.advance(600)  # 1.6 s since the last renewal: the lease ran out
    runtime.tick()
    assert _down(backend) == set()


def test_a_new_generation_fences_the_old_decision(setup):
    runtime, backend, _ = setup
    runtime.grant(1, 1500)
    runtime.apply("d1", WALK, generation=1)
    runtime.grant(2, 1500)  # e.g. the session was paused and resumed meanwhile
    assert _down(backend) == set()
    late = runtime.apply("d2", WALK, generation=1)
    assert late.rejection and late.rejection.code.value == "stale_generation"
    runtime.apply("d3", WALK, generation=2)
    assert _down(backend) == {"W"}


def test_revoke_releases_at_once(setup):
    runtime, backend, _ = setup
    runtime.grant(1, 1500)
    runtime.apply("d1", {"hold": ["forward", "sprint"], "valid_for_ms": 2000}, generation=1)
    assert _down(backend) == {"W", "CTRL"}
    runtime.revoke("user_pause")
    assert _down(backend) == set()
    after = runtime.apply("d2", WALK, generation=1)
    assert after.rejection and after.rejection.code.value == "no_lease"


def test_losing_the_game_window_releases_and_asks_to_pause(setup):
    runtime, backend, clock = setup
    runtime.grant(1, 1500)
    runtime.apply("d1", {"hold": ["forward"], "axes": {"look_yaw": 90}, "valid_for_ms": 2000}, generation=1)
    clock.advance(100)
    runtime.tick()
    assert backend.relative_total()[0] > 0
    backend.lost = "not_foreground"  # e.g. the user clicked another window in the VM
    moved = backend.relative_total()
    clock.advance(100)
    runtime.tick()
    assert _down(backend) == set() and backend.relative_total() == moved
    assert [r.reason for r in runtime.drain() if r.kind == "pause"] == ["not_foreground"]


def test_a_decision_into_the_wrong_window_is_not_sent(setup):
    runtime, backend, _ = setup
    runtime.grant(1, 1500)
    backend.lost = "not_foreground"
    runtime.apply("d1", WALK, generation=1)
    assert _down(backend) == set()
    assert any(r.kind == "pause" for r in runtime.drain())


def test_the_watchdog_releases_when_ticking_stalls(setup):
    runtime, backend, clock = setup
    runtime.grant(1, 5000)
    runtime.apply("d1", WALK, generation=1)
    clock.advance(WATCHDOG_STALL_MS + 1)
    # What the watchdog thread does when the tick thread hasn't run:
    runtime.release_everything()
    runtime._stalled = True
    assert _down(backend) == set()
    runtime.tick()  # when ticking resumes, it pauses instead of carrying on
    assert [r.reason for r in runtime.drain() if r.kind == "pause"] == ["watchdog"]
    again = runtime.apply("d2", WALK, generation=1)
    assert again.rejection and again.rejection.code.value == "no_lease"


def test_context_follows_cursor_visibility(setup):
    runtime, backend, _ = setup
    assert runtime.context() == "playing"
    backend.cursor_shown = True
    assert runtime.context() == "menu"
    runtime.grant(1, 1500)
    record = runtime.apply("d1", {"axes": {"look_yaw": 90}}, generation=1)
    assert record.rejection and record.rejection.code.value == "context_mismatch"  # no mouse-look in a menu


def test_outcomes_are_reported_once(setup):
    runtime, _, clock = setup
    runtime.grant(1, 5000)
    runtime.apply("d1", {"hold": ["forward"], "valid_for_ms": 500}, generation=1)
    clock.advance(600)
    runtime.tick()
    runtime.tick()
    reports = [r for r in runtime.drain() if r.kind == "decision"]
    assert [(r.decision_id, r.state, r.ended_by, r.ran_ms) for r in reports] == [("d1", "expired", "valid_for_ms", 500)]


# --- the Windows driver's pure parts (no Windows needed) -------------------


def test_every_allowed_key_has_a_windows_code():
    assert safety.ALLOWED_KEYS <= set(SCANCODES) | set(MOUSE_BUTTON_FLAGS)
    assert SCANCODES["W"] == 0x11 and SCANCODES["SPACE"] == 0x39 and SCANCODES["UP"] == 0xE048


def test_big_relative_moves_are_split_without_losing_counts():
    steps = relative_steps(600, -45)
    assert sum(x for x, _ in steps) == 600 and sum(y for _, y in steps) == -45
    assert all(abs(x) <= 127 and abs(y) <= 127 for x, y in steps)
    assert relative_steps(0, 0) == [(0, 0)]


def test_pointer_positions_map_to_the_virtual_desktop():
    client = Rect(100, 50, 1280, 720)
    desktop = Rect(0, 0, 1920, 1080)
    assert absolute_coords(0, 0, client, desktop) == (round(100 * 65535 / 1919), round(50 * 65535 / 1079))
    assert absolute_coords(1819, 1029, client, desktop) == (65535, 65535)  # the desktop's last pixel


def test_a_reused_id_with_a_different_move_is_refused_out_loud(setup):
    """After a resume the driver once restarted its move ids; the VM refused
    every move and, since the refusal wasn't remembered, never said so."""
    runtime, backend, _ = setup
    runtime.grant(1, 1500)
    runtime.apply("drv1-abc", WALK, generation=1)
    runtime.drain()
    record = runtime.apply("drv1-abc", {"intent": "turn", "events": [{"type": "turn", "yaw": 30}]}, generation=1)
    assert record.rejection.code.value == "action_duplicate_conflict"
    reports = runtime.drain()
    assert [(r.decision_id, r.state, r.rejection_code) for r in reports] == [
        ("drv1-abc", "rejected", "action_duplicate_conflict")]
    assert runtime.scheduler.records["drv1-abc"].state.value == "running"  # the original move is untouched
