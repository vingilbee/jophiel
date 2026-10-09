"""Reflexes and screen-sampled stop rules (plan D3), with scripted pictures."""

from __future__ import annotations

import copy

import pytest
from PIL import Image, ImageDraw

from desktop_controller.capture import FakeCapture
from desktop_controller.fake import FakeInput, ManualClock
from desktop_controller.profile import ProfileError, parse_profile
from desktop_controller.reflexes import RegionMeter, sample_size
from desktop_controller.runtime import SessionRuntime

WALK = {"intent": "walk", "hold": ["forward"], "valid_for_ms": 3000}
STATUS = (228, 318, 412, 338)  # Minecraft's status_bars region


def picture(shade: int = 60, status: int = 200, centre: int | None = None, seed: int = 0) -> Image.Image:
    """A 640x360 frame: background, a status bar, and an optional centre patch."""
    image = Image.new("L", (640, 360), shade)
    draw = ImageDraw.Draw(image)
    for x in range(0, 640, 40):  # some texture, so a shifted picture really differs
        draw.line([(x + seed, 0), (x + seed, 300)], fill=shade + 50, width=6)
    draw.rectangle(STATUS, fill=status)
    if centre is not None:
        draw.rectangle((240, 135, 400, 225), fill=centre)
    return image


def runtime_for(raw: dict, mode: str = "act", **reflexes):
    raw = copy.deepcopy(raw)
    raw["reflexes"] = {**raw["reflexes"], "mode": mode, **reflexes}
    profile = parse_profile(raw, "test")
    clock = ManualClock(1_000)
    backend = FakeInput(clock=clock)
    runtime = SessionRuntime(profile, backend, clock=clock, threads=False)
    runtime.grant(1, 5000)
    return runtime, backend, clock


def feed(runtime, clock, frames, every_ms: int = 66) -> None:
    for frame in frames:
        clock.advance(every_ms)
        runtime.observe(frame)


def test_the_meter_measures_regions_against_the_move_start_and_the_last_sample(minecraft):
    meter = RegionMeter(minecraft)
    assert sample_size(minecraft) == (320, 180)
    first = meter.measure(picture(), "d1")
    assert first["full"].changed_since_start == 0 and first["full"].changed_since_prev == 0
    hurt = meter.measure(picture(status=40), "d1")
    assert hurt["status_bars"].changed_since_start > 0.9 and hurt["full"].changed_since_start < 0.15
    again = meter.measure(picture(status=40), "d1")
    assert again["status_bars"].changed_since_prev == 0 and again["status_bars"].changed_since_start > 0.9
    # A new move starts fresh: no jump measured against the old move's last picture.
    fresh = meter.measure(picture(shade=180), "d2")
    assert fresh["full"].changed_since_prev == 0


def test_watch_mode_notes_a_reflex_but_keeps_walking(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw, mode="watch")
    runtime.apply("d1", WALK, generation=1)
    feed(runtime, clock, [picture(), picture(), picture(status=40), picture(status=40)])
    assert backend.down == {"W"}
    record = runtime.scheduler.active
    assert [(h.name, h.acted) for h in record.reflexes] == [("status_changed", False)]  # once per move
    clock.advance(3000)
    runtime.tick()
    report = next(r for r in runtime.drain() if r.decision_id == "d1")
    assert report.ended_by == "valid_for_ms" and report.reflexes[0].name == "status_changed"


def test_act_mode_lets_go_at_once_when_the_status_bar_changes(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw)
    runtime.apply("d1", WALK, generation=1)
    feed(runtime, clock, [picture(), picture(status=40)])
    assert backend.down == set()
    report = runtime.drain()[-1]
    assert report.state == "completed" and report.ended_by == "reflex:status_changed"
    assert report.reflexes[0].acted and 0 < report.reflexes[0].at_ms <= 200


def test_a_jolt_stops_a_walk_but_not_her_own_turn(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw)
    runtime.apply("t1", {"intent": "look around", "events": [{"type": "turn", "yaw": 90}], "valid_for_ms": 2000},
                  generation=1)
    feed(runtime, clock, [picture(), picture(shade=200), picture(shade=60)])
    assert runtime.scheduler.active is not None  # the picture moved because she turned

    runtime.apply("d1", WALK, generation=1)
    clock.advance(1000)  # the turn is long over
    feed(runtime, clock, [picture(shade=60), picture(shade=200, seed=20)])
    assert backend.down == set()
    assert runtime.drain()[-1].ended_by == "reflex:jolt"


def test_walking_into_a_wall_is_blocked_after_the_set_time(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw)
    runtime.apply("d1", WALK, generation=1)
    feed(runtime, clock, [picture(centre=100)] * 10, every_ms=66)  # 660 ms: not yet
    assert backend.down == {"W"}
    feed(runtime, clock, [picture(centre=100)] * 6, every_ms=66)
    assert backend.down == set() and runtime.drain()[-1].ended_by == "reflex:blocked"


def test_walking_through_a_moving_view_is_not_blocked(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw)
    runtime.apply("d1", WALK, generation=1)
    feed(runtime, clock, [picture(centre=100 + 10 * (i % 2) * 5) for i in range(30)])
    assert backend.down == {"W"}


def test_reflexes_stay_out_of_menus(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw)
    backend.cursor_shown = True  # the inventory is open
    runtime.apply("m1", {"intent": "sneak", "hold": ["sneak"], "valid_for_ms": 2000}, generation=1)
    feed(runtime, clock, [picture(), picture(status=40, shade=200)])
    assert backend.down == {"SHIFT"} and runtime.scheduler.active.reflexes == []


def test_the_moves_own_stop_rule_fires_on_the_screen(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw, mode="off")
    runtime.apply("d1", {**WALK, "until": {"any": [{"region": "center", "condition": "changed", "threshold": 0.3}]}},
                  generation=1)
    feed(runtime, clock, [picture(centre=100), picture(centre=100)])
    assert backend.down == {"W"}
    feed(runtime, clock, [picture(centre=230)])
    assert backend.down == set() and runtime.drain()[-1].ended_by == "until:0"


def test_no_samples_without_a_move(minecraft_raw):
    runtime, _, clock = runtime_for(minecraft_raw)
    feed(runtime, clock, [picture(), picture(status=40)])
    assert runtime.drain() == []


def test_profiles_with_bad_reflex_settings_are_refused(minecraft_raw):
    raw = copy.deepcopy(minecraft_raw)
    raw["reflexes"] = {"status_region": "hearts", "move_buttons": ["fly"], "contexts": ["swimming"]}
    raw["careful"] = {"hold": ["crouch"]}
    with pytest.raises(ProfileError) as excinfo:
        parse_profile(raw, "bad")
    issues = " ".join(excinfo.value.issues)
    for part in ("unknown region 'hearts'", "unknown button 'fly'", "unknown context 'swimming'",
                 "careful.hold: unknown button 'crouch'"):
        assert part in issues


def test_the_fake_capture_gives_small_grey_samples():
    image = FakeCapture().sample("javaw.exe", (320, 180))
    assert image.size == (320, 180) and image.mode == "L"
    assert FakeCapture(state="not_foreground").sample("javaw.exe", (320, 180)) is None


def test_a_gap_in_samples_is_not_taken_for_a_still_view(minecraft_raw):
    runtime, backend, clock = runtime_for(minecraft_raw)
    runtime.apply("d1", WALK, generation=1)
    feed(runtime, clock, [picture(centre=100)])
    clock.advance(1500)  # no pictures for a while (e.g. the game lost focus)
    feed(runtime, clock, [picture(centre=100)] * 3)
    assert backend.down == {"W"}
