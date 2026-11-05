"""Retained game evidence adapter regressions. Shared learning: test_learning_consolidation.py."""
from dataclasses import replace
from tests.test_skill_driver import Harness, ScriptedModel, _decide


def _recording_harness(model, lessons=()):
    h = Harness(model)
    h.experiences = []
    h.run.deps.experience = lambda sid, kind, payload: h.experiences.append((kind, payload))
    h.run.deps.lessons = lambda sid: list(lessons)
    return h


def test_outcomes_and_notices_are_recorded_with_their_step_but_waits_are_not():
    model = ScriptedModel(_decide("mine", {"block": "oak_log", "count": 3}), _decide("wait", {"seconds": 2}),
                          *[_decide(status="step_done")] * 5)
    h = _recording_harness(model)
    h.steps(4)  # decide, run, finish
    h.pack.notice("village", "heads_up", "a village, 80 m north")
    h.steps(3)
    outcomes = [p for kind, p in h.experiences if kind == "skill_outcome"]
    assert [(o["skill"], o["status"], o["step_id"]) for o in outcomes] == [("mine", "done", "s1")]
    assert outcomes[0]["data"] == {"collected": 3}
    notices = [p for kind, p in h.experiences if kind == "notice"]
    assert [{k: v for k, v in p.items() if k not in ("seq", "pack_at")} for p in notices] == [
        {"kind": "village", "urgency": "heads_up", "text": "a village, 80 m north", "data": {}}]
    assert isinstance(notices[0]["seq"], int)  # the link to the same notice in her trajectory


def test_the_plans_rules_reach_the_hands_every_decision():
    h = _recording_harness(ScriptedModel(_decide("mine", {"block": "oak_log"})))
    h.view = replace(h.view, plan={**h.view.plan, "constraints": ["keep 5 torches"], "if_stuck": "go back up"})
    h.steps(1)
    assert "Rules for this goal (keep them the whole time): keep 5 torches" in h.run.deps.model.prompts[0]
    assert "If stuck: go back up" in h.run.deps.model.prompts[0]


def test_learning_failures_never_stop_play():
    h = _recording_harness(ScriptedModel(_decide("mine", {"block": "oak_log"}), *[_decide(status="step_done")] * 3))

    def broken(*_):
        raise RuntimeError("db down")

    h.run.deps.experience = broken
    h.run.deps.lessons = broken
    assert all(h.steps(4))
    assert "earlier sessions" not in h.run._system
