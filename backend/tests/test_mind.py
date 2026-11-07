"""Her mind's tick rules (services/mind.py), with a fake gate, thinker and pack: no network, no database."""
import uuid
from types import SimpleNamespace

import pytest

from app.ai.mind import gates as g
from app.ai.mind.contracts import Scope
from app.core.config import get_settings
from app.db.models import DesktopSession
from app.services import mind, mind_hard, mind_minecraft


def state(**over) -> dict:
    base = {"health": 20, "food": 20, "oxygen": 20, "position": {"x": 0, "y": 64, "z": 0}, "inventory": [],
            "nearby": [], "players": [], "skill": {"name": "follow_player"}, "time": {"phase": "day"},
            "weather": "clear", "text": "state"}
    return {**base, **over}


ZOMBIE = [{"label": "zombie", "kind": "hostile", "distance": 3, "side": "left"}]


class Gate:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.name = "fake"

    def decide(self, text, situation=None):
        answer = self.answers.pop(0) if self.answers else {"none": 1.0}
        if answer == "error":
            return g.GateResult(self.name, None, error="boom")
        return g.GateResult(self.name, max(answer, key=answer.get), probabilities=answer)


class Pack:
    def notices(self, since=0):
        return {"next": 1, "notices": []}


@pytest.fixture
def brain(monkeypatch, tmp_path):
    settings = get_settings()
    for name, value in (("desktop_agent_enabled", True), ("mind_think_threshold", 0.7),
                        ("mind_quick_gap_seconds", 4.0), ("mind_hard_gap_seconds", 20.0), ("mind_quiet_seconds", 0),
                        ("mind_tick_path", str(tmp_path / "ticks.jsonl")), ("mind_log_path", str(tmp_path / "mind.jsonl")),
                        ("mind_hard_quiet_seconds", 0)):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(mind, "recent_thoughts", lambda db, sid, now, limit=5: [])
    monkeypatch.setattr(mind_minecraft.mind_ticks, "recent_talk", lambda db, uid, now: [])
    monkeypatch.setattr(mind_minecraft.mind_ticks.desktop_commands, "load_driver_state", lambda sid: {})
    monkeypatch.setattr(mind.desktop_preview, "redis_client", lambda: None)
    hard, quick, driver = [], [], {}
    monkeypatch.setattr(mind.mind_hard, "start", lambda source, scope, att, problem, at: (
        hard.append(problem), setattr(att, "running", problem), setattr(att, "running_scope", scope),
        setattr(att, "last_started_at", at)))
    monkeypatch.setattr(mind_minecraft.desktop_commands, "load_driver_state", lambda sid: driver)
    monkeypatch.setattr(mind._pool, "submit", lambda fn, source, scope, level, text, at, thinker: (
        quick.append(level), setattr(mind._minds[scope.activity], "quick_busy", False)))
    mind._minds.clear()
    mind_hard.reset()
    mind_minecraft._notice_cursors.clear()
    session = SimpleNamespace(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), state="running", plan=None,
                              plan_revision=1, commentary_mode="occasional")
    scope = mind_minecraft.scope_of(session)

    picked = iter(range(1, 1000))

    def run(gate, fallback=None, at=100.0, fresh=True, **over):
        """One tick. ``fresh``: she picks up a dirt block, so the tick has news."""
        monkeypatch.setattr(mind, "_gates", lambda: (gate, fallback or gate, None))
        if fresh:
            over.setdefault("inventory", [{"name": "dirt", "count": next(picked)}])
        return mind.tick(None, mind_minecraft.SOURCE, scope, (session, state(**over), Pack()), at)

    def landed():
        """The running hard thought landed."""
        mind_hard.attention_for(session.owner_user_id).running = None

    return SimpleNamespace(run=run, hard=hard, quick=quick, session=session, driver=driver, landed=landed)


def test_threshold_decides_whether_she_thinks():
    def level(**p):
        return g.level_from(g.GateResult("x", None, probabilities=p), 0.7)

    assert level(none=0.4, light=0.5, hard=0.1) == "none"  # 0.6 sure: not enough
    assert level(none=0.2, light=0.6, hard=0.2) == "light"
    assert level(none=0.1, light=0.3, hard=0.6) == "hard"
    assert level(none=0.1, react=0.6, light=0.3) == "react"  # danger by top choice, whatever the threshold
    assert g.level_from(g.GateResult("x", "light"), 0.7) == "light"  # no probabilities: its own choice


def test_quiet_tick_does_nothing_and_light_respects_the_gap(brain):
    assert brain.run(Gate({"none": 0.9, "light": 0.1}))["action"] == "none"
    assert brain.run(Gate({"light": 0.9}), at=101)["action"] == "light"
    assert brain.run(Gate({"light": 0.9}), at=102)["action"] == "skipped: quick gap"
    assert brain.run(Gate({"react": 0.9}), at=103)["action"] == "react"  # react ignores the gap
    assert brain.quick == ["light", "react"]


def test_the_gates_hard_is_a_surprise_only_when_the_world_changed(brain, monkeypatch):
    assert brain.run(Gate({"hard": 0.9}), at=90)["hard"] is None  # the first tick: everything is "new"
    record = brain.run(Gate({"hard": 0.9}))  # she picked something up
    assert (record["level"], record["action"], record["hard"]) == ("light", "light", "hard: surprise")
    assert "you got dirt" in brain.hard[0].text
    brain.hard.clear()
    brain.landed()
    monkeypatch.setattr(mind_minecraft.mind_ticks, "recent_talk", lambda db, uid, now: [
        {"role": "user", "content": "this session is for harder skills", "at": 150.0}])
    record = brain.run(Gate({"hard": 0.9}), at=150, fresh=False)  # only the user talking: her mouth answers
    assert record["hard"] is None and brain.hard == []


def test_urgent_pressure_postpones_hard_thinking_until_it_is_off(brain):
    brain.run(Gate({"none": 0.9}), at=99)
    record = brain.run(Gate({"hard": 0.9}), nearby=ZOMBIE)
    assert (record["pressure"], record["level"], record["action"]) == ("urgent", "react", "react")
    assert record["hard"] == "hard postponed (surprise): urgent" and brain.hard == []
    record = brain.run(Gate({"none": 0.95}), at=101, fresh=False)  # the zombie is gone
    assert record["hard"] == "hard: surprise"
    assert "zombie appeared" in brain.hard[0].text  # what she postponed: the fight


def test_one_hard_thought_at_a_time_with_a_gap(brain):
    brain.run(Gate({"none": 0.9}), at=99)
    assert brain.run(Gate({"hard": 0.9}))["hard"] == "hard: surprise"
    assert brain.run(Gate({"hard": 0.9}), at=105)["hard"] == "hard waiting (surprise)"
    brain.landed()  # the thought landed
    assert brain.run(Gate({"none": 0.9}), at=110, fresh=False)["hard"] == "hard waiting (surprise): gap"
    assert brain.run(Gate({"none": 0.9}), at=121, fresh=False)["hard"] == "hard: surprise"
    assert len(brain.hard) == 2


def test_a_step_that_ends_is_a_moment_to_look_back_with_no_count_of_tries(brain):
    """Stage 2 (docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md): no number decides she's stuck; a step ending makes her
    look back over the whole effort, and her mind judges it. Many failed tries alone raise nothing."""
    steps = [{"id": "s1", "state": "active", "do": "find iron ore"}, {"id": "s2", "state": "pending", "do": "mine it"}]
    brain.session.plan = {"goal": "iron armor", "steps": steps}
    fails = [{"skill": "go_to", "params": {"target": "block:iron_ore"}, "status": "failed",
              "reason": "no iron_ore in sight within 64"}]
    brain.driver["step"] = {"id": "s1", "do": "find iron ore", "since": 90.0, "tries": 12, "failed": 11,
                            "failures": fails * 6}
    assert brain.run(Gate({"none": 0.9}))["hard"] is None  # failing a lot: hers to notice when she looks back
    assert brain.run(Gate({"none": 0.9}), at=101)["hard"] is None
    brain.session.plan = {"goal": "iron armor", "steps": [{**steps[0], "state": "done"}, {**steps[1], "state": "active"}]}
    assert brain.run(Gate({"none": 0.9}), at=102)["hard"] == "hard: review"
    problem = brain.hard[-1]
    assert problem.trigger == "review" and '"find iron ore" (done)' in problem.text
    assert "tried it 12 times; 11 failed" in problem.text and "whole effort" in problem.text
    brain.landed()
    assert brain.run(Gate({"none": 0.9}), at=130)["hard"] is None  # the next step just started: nothing ended


def test_her_hands_giving_up_is_looked_back_on_once(brain):
    brain.session.plan = {"goal": "iron armor", "steps": [{"id": "s1", "state": "active", "do": "find iron ore"}]}
    brain.driver["step"] = {"id": "s1", "do": "find iron ore", "since": 90.0, "tries": 3, "failed": 3, "failures": []}
    assert brain.run(Gate({"none": 0.9}))["hard"] is None
    brain.session.driver_status, brain.session.current_step_id = "stuck", "s1"
    brain.driver["notes"] = "no iron anywhere I can see"
    assert brain.run(Gate({"none": 0.9}), at=101)["hard"] == "hard: review"
    assert 'your hands gave up on it: "no iron anywhere I can see"' in brain.hard[-1].text
    brain.landed()
    assert brain.run(Gate({"none": 0.9}), at=130)["hard"] is None  # the same report: looked back on already


def test_a_plan_that_moves_on_is_a_step_ended_only_once_her_hands_tried_it(brain):
    brain.session.plan = {"goal": "iron armor", "steps": [{"id": "s1", "state": "active", "do": "follow Vincent"}]}
    assert brain.run(Gate({"none": 0.9}))["hard"] is None
    brain.session.plan_revision = 2
    brain.session.plan = {"goal": "iron armor", "steps": [{"id": "s1", "state": "active", "do": "explore the cave"}]}
    assert brain.run(Gate({"none": 0.9}), at=101)["hard"] is None  # replaced before anything was tried
    brain.driver["step"] = {"id": "s1", "do": "explore the cave", "since": 100.0, "tries": 2, "failed": 0,
                            "failures": []}
    assert brain.run(Gate({"none": 0.9}), at=102)["hard"] is None
    brain.session.plan_revision = 3
    brain.session.plan = {"goal": "iron armor", "steps": [{"id": "s1", "state": "active", "do": "dig down"}]}
    assert brain.run(Gate({"none": 0.9}), at=103)["hard"] == "hard: review"
    assert '"explore the cave" (the plan moved on before it was done)' in brain.hard[-1].text


def test_a_result_that_says_failed_while_she_got_the_loot_is_a_surprise(brain):
    brain.run(Gate({"none": 0.9}), fresh=False, inventory=[])
    brain.driver["outcomes"] = [{"skill": "learned_attack_passive_sheep", "params": {"times": 1}, "status": "failed",
                                 "reason": "the target is gone", "seconds": 20, "at": 125.0}]
    record = brain.run(Gate({"none": 0.9}), at=126, inventory=[{"name": "mutton", "count": 2}])
    assert record["hard"] == "hard: surprise"
    assert "But while it ran you got: mutton +2" in brain.hard[-1].text
    brain.landed()
    assert brain.run(Gate({"none": 0.9}), at=150, fresh=False,
                     inventory=[{"name": "mutton", "count": 2}])["hard"] is None  # seen once


def test_something_big_is_thought_over_before_it_starts(brain):
    brain.run(Gate({"none": 0.9}))
    brain.session.plan = {"goal": "build a shelter", "steps": [
        {"id": f"s{i}", "state": "pending", "do": f"step {i}"} for i in range(3)]}
    assert brain.run(Gate({"none": 0.9}), at=101)["hard"] == "hard: big"
    assert brain.hard[-1].anchor == {"goal": "build a shelter"}
    brain.landed()
    brain.driver["running"] = {"skill": "learned_build_up", "params": {"times": 10}}
    assert brain.run(Gate({"none": 0.9}), at=130)["hard"] == "hard: big"
    assert "learned_build_up 10 times in a row" in brain.hard[-1].text


def test_quiet_hard_thinking_rotates_topics(brain, monkeypatch):
    monkeypatch.setattr(get_settings(), "mind_hard_quiet_seconds", 180.0)
    monkeypatch.setattr(get_settings(), "mind_hard_quiet_every_seconds", 300.0)
    brain.run(Gate({"none": 0.9}))
    assert brain.run(Gate({"none": 0.9}), at=200, fresh=False)["hard"] is None
    assert brain.run(Gate({"none": 0.9}), at=400, fresh=False)["hard"] == "hard: quiet"
    brain.landed()
    assert brain.run(Gate({"none": 0.9}), at=600, fresh=False)["hard"] is None  # once per 5 min
    assert brain.run(Gate({"none": 0.9}), at=701, fresh=False)["hard"] == "hard: quiet"
    assert [p.topic for p in brain.hard] == ["quiet: the user: what they want from today, and how they're doing",
                                             "quiet: the session so far: what you two did, and what's unfinished"]


def test_nothing_new_means_no_thought_whatever_the_gate_says(brain):
    brain.run(Gate({"none": 0.9}), fresh=False)
    record = brain.run(Gate({"light": 0.9}), at=110, fresh=False,
                       position={"x": 5, "y": 64, "z": 0})  # she walked: background, not news
    assert (record["level"], record["note"]) == ("none", "nothing new since the last tick")
    assert brain.run(Gate({"light": 0.9}), at=111)["action"] == "light"  # she picked something up
    assert brain.quick == ["light"]


def test_same_danger_gets_one_reaction_until_it_gets_worse(brain):
    assert brain.run(Gate({"react": 0.9}), nearby=ZOMBIE)["action"] == "react"
    record = brain.run(Gate({"react": 0.9}), at=102, nearby=ZOMBIE)
    assert (record["action"], record["note"]) == ("none", "same danger, not worse")
    closer = [{**ZOMBIE[0], "distance": 1}]
    assert brain.run(Gate({"react": 0.9}), at=103, nearby=closer)["action"] == "react"
    assert brain.run(Gate({"react": 0.9}), at=104, nearby=closer, health=16)["action"] == "react"  # it hit her
    assert brain.run(Gate({"react": 0.9}), at=113, nearby=closer, health=16)["action"] == "react"  # 8 s on
    assert brain.quick == ["react"] * 4


def test_quiet_thinking_after_a_while_with_no_thought(brain, monkeypatch):
    monkeypatch.setattr(get_settings(), "mind_quiet_seconds", 25.0)
    assert brain.run(Gate({"light": 0.9}))["action"] == "light"
    assert brain.run(Gate({"none": 0.9}), at=110)["action"] == "none"  # only 10 s since that thought
    assert brain.run(Gate({"none": 0.9}), at=126)["action"] == "quiet"
    assert brain.run(Gate({"none": 0.9}), at=140)["action"] == "none"  # the quiet thought counts as a thought
    assert brain.run(Gate({"none": 0.9}), at=170, nearby=ZOMBIE)["action"] == "none"  # nothing quiet about a zombie
    monkeypatch.setattr(mind_minecraft.mind_ticks, "recent_talk", lambda db, uid, now: [{"role": "user", "content": "hey", "at": 175.0}])
    assert brain.run(Gate({"none": 0.9}), at=180)["action"] == "none"  # they just spoke: the mouth answers
    assert brain.quick == ["light", "quiet"]


def test_a_failing_gate_falls_back(brain):
    record = brain.run(Gate("error"), fallback=Gate({"light": 0.9}))
    assert record["action"] == "light"


def test_quick_thought_is_saved_said_through_the_gate_or_stale(monkeypatch):
    saved, said = [], []

    class Db:
        def __init__(self, session):
            self.session = session

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, key):
            return self.session if model is DesktopSession else SimpleNamespace(name="Vincent")

    class Thinker:
        def __init__(self, say):
            self.say = say

        def think(self, level, text, user, situation):
            assert user == "Vincent" and situation.key == "minecraft"
            return SimpleNamespace(text="zombie on my left", say=self.say, latency_ms=500, error=None,
                                   kind="next", noticed="zombie")

    session = SimpleNamespace(state="running", commentary_mode="occasional")
    monkeypatch.setattr(mind, "SessionLocal", lambda: Db(session))
    monkeypatch.setattr(mind, "save_thought", lambda db, **kw: saved.append(kw) or SimpleNamespace(id=uuid.uuid4()))
    from app.services import autonomous_speech
    monkeypatch.setattr(autonomous_speech, "submit", lambda db, **kw: said.append(kw["text"]) or "Proposed.")
    monkeypatch.setattr(mind, "_log", lambda record: None)
    sid, uid = uuid.uuid4(), uuid.uuid4()
    scope = Scope(uid, "minecraft", sid)
    monkeypatch.setattr(mind.time, "time", lambda: 100.5)
    mind._think_quick(mind_minecraft.SOURCE, scope, "react", "tick", 100.0, Thinker("zombie, left!"))
    assert said == ["zombie, left!"]
    assert saved[-1] | {} == {"user_id": uid, "session_id": sid, "level": "react", "text": "zombie on my left",
                              "status": "done", "kind": "next"}
    monkeypatch.setattr(mind.time, "time", lambda: 110.0)  # landed 10 s late: a react is stale by then
    mind._think_quick(mind_minecraft.SOURCE, scope, "react", "tick", 100.0, Thinker("zombie, left!"))
    assert saved[-1]["status"] == "stale" and saved[-1].get("said") is None and len(said) == 1
    assert mind._minds[sid].quick_busy is False


def test_a_step_that_keeps_going_is_looked_back_on_now_and_then(brain, monkeypatch):
    """Live 2026-11-06 one step ran 21 minutes and nothing looked back: a step that never ends never got the
    step-ended look back. Code picks only when (every mind_step_look_back_seconds of the step); judging is hers."""
    monkeypatch.setattr(get_settings(), "mind_step_look_back_seconds", 300.0)
    brain.session.plan = {"goal": "iron armor", "steps": [{"id": "s1", "state": "active", "do": "find iron ore"}]}
    brain.driver["step"] = {"id": "s1", "do": "find iron ore", "since": 100.0, "tries": 30, "failed": 12,
                            "failures": [{"skill": "go_to", "params": {"target": "-1 2 3"}, "status": "failed",
                                          "reason": "no path"}]}
    assert brain.run(Gate({"none": 0.9}), at=100)["hard"] is None
    assert brain.run(Gate({"none": 0.9}), at=399)["hard"] is None  # not yet: no count of tries decides it
    assert brain.run(Gate({"none": 0.9}), at=401)["hard"] == "hard: review"
    problem = brain.hard[-1]
    assert problem.trigger == "review" and '"find iron ore"' in problem.text and "going for 5 minutes" in problem.text
    assert "tried 30 times; 12 failed" in problem.text and "the approach" in problem.text
    brain.landed()
    assert brain.run(Gate({"none": 0.9}), at=650)["hard"] is None  # once per stretch of the step
    assert brain.run(Gate({"none": 0.9}), at=702)["hard"] == "hard: review"
    brain.landed()
    # A new step starts its own clock.
    brain.session.plan_revision = 2
    brain.session.plan = {"goal": "iron armor", "steps": [{"id": "s2", "state": "active", "do": "dig down"}]}
    brain.driver["step"] = {"id": "s2", "do": "dig down", "since": 710.0, "tries": 0, "failed": 0, "failures": []}
    assert brain.run(Gate({"none": 0.9}), at=730)["hard"] == "hard: review"  # the old step ended: its own look back
    assert "the plan moved on before it was done" in brain.hard[-1].text
    brain.landed()
    assert brain.run(Gate({"none": 0.9}), at=900)["hard"] is None
    assert brain.run(Gate({"none": 0.9}), at=1011)["hard"] == "hard: review"
    assert '"dig down"' in brain.hard[-1].text
    brain.landed()
    monkeypatch.setattr(get_settings(), "mind_step_look_back_seconds", 0.0)
    assert brain.run(Gate({"none": 0.9}), at=1400)["hard"] is None  # 0 turns it off
