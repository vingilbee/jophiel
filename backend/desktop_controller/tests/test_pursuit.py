"""Chasing a moving target in the VM (plan D3, ``pursue``), with scripted pictures."""

from __future__ import annotations

import copy

from PIL import Image, ImageDraw

from desktop_controller.fake import FakeInput, ManualClock
from desktop_controller.profile import parse_profile
from desktop_controller.pursuit import Tracker, cut_template, template_pixels
from desktop_controller.protocol import PursueEvent
from desktop_controller.runtime import SessionRuntime
from desktop_controller.validate import validate_decision


def scene(sheep_at: tuple[int, int] | None, seed: int = 0) -> Image.Image:
    """A 640x360 field with some texture, and a patterned 'sheep' centred at ``sheep_at``."""
    image = Image.new("L", (640, 360), 90)
    draw = ImageDraw.Draw(image)
    for x in range(0, 640, 37):
        draw.line([(x + seed, 200), (x + seed + 20, 360)], fill=70, width=3)
    if sheep_at is not None:
        x, y = sheep_at
        draw.rectangle((x - 20, y - 14, x + 20, y + 14), fill=235)
        draw.rectangle((x + 8, y - 10, x + 18, y + 2), fill=40)  # a dark head
        draw.rectangle((x - 16, y + 8, x - 10, y + 14), fill=60)  # legs
        draw.rectangle((x + 4, y + 8, x + 10, y + 14), fill=60)
    return image


def pursue(point, attack="attack", **extra) -> dict:
    template = cut_template(scene(point), point, (48, 36))
    return {"intent": "chase the sheep", "hold": ["forward"], "valid_for_ms": 6000,
            "events": [{"type": "pursue", "point": list(point), "size": [48, 36], "attack": attack,
                        "template": template, **extra}]}


def make_runtime(minecraft_raw, mode="off"):
    raw = copy.deepcopy(minecraft_raw)
    raw["reflexes"]["mode"] = mode
    profile = parse_profile(raw, "test")
    clock = ManualClock(1_000)
    backend = FakeInput(clock=clock)
    runtime = SessionRuntime(profile, backend, clock=clock, threads=False)
    runtime.grant(1, 60_000)
    return runtime, backend, clock


def test_the_tracker_follows_a_moving_target(minecraft):
    start = (200, 180)
    event = PursueEvent(type="pursue", point=start, size=(48, 36), template=cut_template(scene(start), start, (48, 36)))
    tracker = Tracker(template_pixels(event, minecraft), start)
    for step in range(1, 12):
        truth = (200 + 12 * step, 180 + 2 * step)
        seen = tracker.locate(scene(truth, seed=step).resize((320, 180)))
        assert seen is not None and abs(seen.x - truth[0]) <= 6 and abs(seen.y - truth[1]) <= 6
    assert tracker.locate(scene(None).resize((320, 180))) is None


def test_the_first_look_finds_a_target_that_moved_since_the_drivers_picture(minecraft):
    event = PursueEvent(type="pursue", point=(150, 150), size=(48, 36),
                        template=cut_template(scene((150, 150)), (150, 150), (48, 36)))
    seen = Tracker(template_pixels(event, minecraft), (150, 150)).locate(scene((420, 170)).resize((320, 180)))
    assert seen is not None and abs(seen.x - 420) <= 6


def test_pursuit_turns_toward_the_target_and_attacks_once_centred(minecraft_raw):
    runtime, backend, clock = make_runtime(minecraft_raw)
    record = runtime.apply("p1", pursue((480, 180)), generation=1)
    assert record.state.value == "running"
    for _ in range(5):  # the sheep stays right of centre: she keeps turning right
        clock.advance(66)
        runtime.observe(scene((480, 180)))
    assert backend.relative_total()[0] > 0
    assert not [e for e in backend.log if e[1:] == ("down", "MOUSE_LEFT")]
    for _ in range(8):  # now centred: attack, but not every sample
        clock.advance(66)
        runtime.observe(scene((320, 180)))
    presses = [e for e in backend.log if e[1:] == ("down", "MOUSE_LEFT")]
    assert 1 <= len(presses) <= 2
    assert backend.down >= {"W"}
    assert runtime.scheduler.active.pursuit["attacks"] == len(presses)


def test_losing_the_target_ends_the_move_and_says_so(minecraft_raw):
    runtime, backend, clock = make_runtime(minecraft_raw)
    runtime.apply("p1", pursue((320, 180)), generation=1)
    clock.advance(66)
    runtime.observe(scene((320, 180)))
    for _ in range(16):
        clock.advance(66)
        runtime.observe(scene(None))
    assert backend.down == set()
    report = next(r for r in runtime.drain() if r.decision_id == "p1")
    assert report.ended_by == "pursue:lost" and report.pursuit["lost"] is True and report.pursuit["seen"] >= 1


def test_without_pictures_a_pursuit_gives_up_rather_than_walk_blind(minecraft_raw):
    runtime, backend, clock = make_runtime(minecraft_raw)
    runtime.apply("p1", pursue((320, 180)), generation=1)
    clock.advance(1000)
    runtime.tick()
    assert backend.down == set() and runtime.drain()[-1].ended_by == "pursue:lost"


def test_a_pursuit_needs_its_picture_and_a_place_to_aim(minecraft):
    no_picture = pursue((320, 180))
    no_picture["events"][0]["template"] = None
    rejection = validate_decision(no_picture, minecraft, "playing")
    assert rejection.code.value == "invalid_schema" and "template" in rejection.field
    assert validate_decision(pursue((320, 180)), minecraft, "menu").code.value == "context_mismatch"
    two = pursue((320, 180))
    two["events"].append(dict(two["events"][0]))
    assert validate_decision(two, minecraft, "playing").code.value == "limit_exceeded"
    ok = validate_decision(pursue((320, 180)), minecraft, "playing")
    assert ok.valid_for_ms == 6000  # a move that ends itself may run past the plain limit
