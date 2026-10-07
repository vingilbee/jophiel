from __future__ import annotations

import pytest

from desktop_controller.protocol import Rejection, RejectionCode, StepOutput
from desktop_controller.validate import ValidatedDecision, validate_decision

C = RejectionCode


def ok(raw, profile, context="playing") -> ValidatedDecision:
    result = validate_decision(raw, profile, context)
    assert isinstance(result, ValidatedDecision), result
    return result


def rejected(raw, profile, context="playing") -> Rejection:
    result = validate_decision(raw, profile, context)
    assert isinstance(result, Rejection), result
    return result


def test_empty_decision_is_valid_release_everything(minecraft):
    v = ok({}, minecraft)
    assert v.decision.hold == [] and v.decision.axes == {} and v.decision.events == []
    assert v.valid_for_ms == minecraft.limits.default_valid_for_ms


def test_typical_decision(minecraft):
    v = ok({"intent": "walk to tree", "hold": ["forward"], "axes": {"look_yaw": -20}}, minecraft)
    assert v.valid_for_ms == 1500 and v.clamped == ()


def test_examples_from_the_plan(minecraft):
    ok({"intent": "walk to oak tree", "hold": ["forward"], "axes": {"look_yaw": 20},
        "events": [{"at_ms": 300, "type": "tap", "button": "jump", "hold_ms": 50}], "valid_for_ms": 1500},
       minecraft)
    ok({"intent": "aim at log", "events": [{"type": "look_at", "point": [352, 160], "duration_ms": 150}]},
       minecraft)
    ok({"intent": "break log", "hold": ["attack"],
        "until": {"any": [{"region": "crosshair", "condition": "changed", "threshold": 0.4}]},
        "valid_for_ms": 6000}, minecraft)
    ok({"intent": "walk to river", "hold": ["forward", "sprint"],
        "until": {"any": [{"region": "full", "condition": "static", "for_ms": 500}]}, "valid_for_ms": 5000},
       minecraft)


@pytest.mark.parametrize(
    "raw, context, code, field",
    [
        ({"hold": ["fly"]}, "playing", C.UNKNOWN_BUTTON, "hold[0]"),
        ({"axes": {"roll": 5}}, "playing", C.UNKNOWN_AXIS, "axes.roll"),
        ({"events": [{"type": "click", "button": "attack", "point": [10, 10]}]}, "playing", C.CONTEXT_MISMATCH, "events[0]"),
        ({"axes": {"look_yaw": 30}}, "menu", C.CONTEXT_MISMATCH, "axes.look_yaw"),
        ({"hold": ["forward"]}, "menu", C.CONTEXT_MISMATCH, "hold[0]"),
        ({"hold": ["forward"]}, "unknown", C.CONTEXT_MISMATCH, "hold[0]"),
        # a pointer-targeted tap would move the pointer, which mouse-look forbids
        ({"events": [{"type": "tap", "button": "jump", "point": [10, 10]}]}, "playing", C.CONTEXT_MISMATCH, "events[0].point"),
        ({"events": [{"type": "click", "button": "menu_click", "point": [640, 10]}]}, "menu", C.POINT_OUTSIDE_VIEWPORT, "events[0].point"),
        ({"events": [{"type": "click", "button": "menu_click", "point": {"in": "minimap", "xy": [1, 1]}}]}, "menu", C.UNKNOWN_CROP, "events[0].point"),
        ({"events": [{"type": "click", "button": "menu_click", "point": {"in": "hotbar", "xy": [400, 10]}}]}, "menu", C.POINT_OUTSIDE_VIEWPORT, "events[0].point"),
        ({"axes": {"look_yaw": 400}}, "playing", C.LIMIT_EXCEEDED, "axes.look_yaw"),
        ({"hold": ["attack"], "events": [{"type": "tap", "button": "attack"}]}, "playing", C.CONFLICTING_INPUT, "events[0].button"),
        ({"events": [{"type": "tap", "button": "jump", "at_ms": 1500}]}, "playing", C.LIMIT_EXCEEDED, "events[0].at_ms"),
        ({"events": [{"type": "tap", "button": "jump", "at_ms": 1480, "hold_ms": 50}]}, "playing", C.LIMIT_EXCEEDED, "events[0]"),
        ({"events": [{"type": "tap", "button": "jump", "hold_ms": 900}]}, "playing", C.LIMIT_EXCEEDED, "events[0].hold_ms"),
        ({"events": [{"type": "macro", "name": "hadouken"}]}, "playing", C.CONTEXT_MISMATCH, "events[0]"),
        ({"until": {"any": [{"region": "sky", "condition": "changed"}]}}, "playing", C.UNKNOWN_REGION, "until.any[0].region"),
        ({"until": {"any": [{"region": "full", "condition": "static", "for_ms": 2000}]}, "valid_for_ms": 1500}, "playing", C.LIMIT_EXCEEDED, "until.any[0].for_ms"),
        ({"hold": ["forward", "forward"]}, "playing", C.INVALID_SCHEMA, "hold"),
        ({"hold": ["forward"], "speed": 3}, "playing", C.INVALID_SCHEMA, "speed"),
        ({"events": [{"type": "text", "text": "hello"}]}, "playing", C.INVALID_SCHEMA, "events[0]"),
        ({"axes": {"look_yaw": "fast"}}, "playing", C.INVALID_SCHEMA, "axes.look_yaw"),
    ],
)
def test_rejections(minecraft, raw, context, code, field):
    rej = rejected(raw, minecraft, context)
    assert rej.code == code, rej
    assert rej.field == field, rej


def test_too_many_events(minecraft):
    events = [{"type": "tap", "button": "jump", "at_ms": i * 100} for i in range(13)]
    assert rejected({"events": events, "valid_for_ms": 3000}, minecraft).code == C.LIMIT_EXCEEDED


def test_tap_rate_limit(minecraft):
    events = [{"type": "tap", "button": f"hotbar_{(i % 9) + 1}", "at_ms": i * 50, "hold_ms": 20} for i in range(11)]
    rej = rejected({"events": events}, minecraft)
    assert rej.code == C.LIMIT_EXCEEDED and "per second" in rej.reason


def test_valid_for_is_clamped_not_rejected(minecraft):
    v = ok({"hold": ["forward"], "valid_for_ms": 9000}, minecraft)
    assert v.valid_for_ms == 3000 and v.clamped == ("valid_for_ms",)
    v = ok({"hold": ["attack"], "valid_for_ms": 9000,
            "until": {"any": [{"region": "crosshair", "condition": "changed"}]}}, minecraft)
    assert v.valid_for_ms == 8000  # until allows the longer cap, never more


def test_wait_is_clamped(minecraft):
    v = ok({"wait_ms": 5000}, minecraft)
    assert v.wait_ms == 1500 and "wait_ms" in v.clamped


def test_blocked_combination(alt_profile):
    rej = rejected({"hold": ["alt"], "events": [{"type": "tap", "button": "f4"}]}, alt_profile)
    assert rej.code == C.BLOCKED_COMBINATION and "ALT+F4" in rej.reason
    ok({"hold": ["alt"]}, alt_profile)  # each key alone is fine


def test_look_at_needs_calibrated_camera(generic):
    raw = {"events": [{"type": "look_at", "point": [10, 10]}]}
    # the generic profile allows no look_at at all, and has no camera
    assert rejected(raw, generic, "any").code == C.CONTEXT_MISMATCH


def test_menu_interactions(minecraft):
    ok({"events": [{"type": "click", "button": "menu_click", "point": {"in": "hotbar", "xy": [30, 20]}}]}, minecraft, "menu")
    ok({"hold": ["sneak"], "events": [{"type": "click", "button": "menu_click", "point": [300, 200]}]}, minecraft, "menu")
    ok({"events": [{"type": "drag", "button": "menu_click", "path": [[200, 200], [300, 250]], "duration_ms": 300}]}, minecraft, "menu")
    ok({"events": [{"type": "tap", "button": "inventory"}]}, minecraft, "unknown")


def test_rejection_render(minecraft):
    rej = rejected({"events": [{"type": "click", "button": "attack", "point": [10, 10]}]}, minecraft)
    assert rej.render() == 'last decision REJECTED: context_mismatch: "click" not allowed in context "playing" (events[0])'


def test_step_output_minimal_and_full():
    out = StepOutput.model_validate({"decision": {"intent": "walk to tree", "hold": ["forward"]}})
    assert out.status is None and out.facts == []
    out = StepOutput.model_validate({
        "decision": {}, "step_id": "s1", "status": "needs_human", "notes": "login screen",
        "facts": [{"key": "inventory.cobblestone", "value": "12", "source": "hotbar"}],
        "report": [{"kind": "notable", "text": "found iron ore"}],
        "answers": [{"question_id": "q1", "text": "12 in hotbar", "confidence": "seen"}],
    })
    assert out.status == "needs_human"
    with pytest.raises(ValueError):
        StepOutput.model_validate({"decision": {}, "notes": "x" * 201})
