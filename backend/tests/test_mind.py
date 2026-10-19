"""Her mind's tick rules (services/mind.py), with a fake gate, thinker and pack: no network, no database."""
import uuid
from types import SimpleNamespace

import pytest

from app.ai.mind import gates as g
from app.core.config import get_settings
from app.services import mind


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
    for name, value in (("mind_enabled", True), ("desktop_agent_enabled", True), ("mind_think_threshold", 0.7),
                        ("mind_quick_gap_seconds", 4.0), ("mind_hard_gap_seconds", 20.0), ("mind_quiet_seconds", 0),
                        ("mind_tick_recording_enabled", False), ("mind_log_path", str(tmp_path / "mind.jsonl"))):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(mind, "recent_thoughts", lambda db, sid, now, limit=5: [])
    monkeypatch.setattr(mind.mind_ticks, "recent_talk", lambda db, uid, now: [])
    monkeypatch.setattr(mind.mind_ticks.desktop_commands, "load_driver_state", lambda sid: {})
    monkeypatch.setattr(mind.desktop_preview, "redis_client", lambda: None)
    hard, quick = [], []
    monkeypatch.setattr(mind, "_think_hard", lambda db, session, topic, at: (
        hard.append(topic), mind._set_thinking(session.id, topic)))
    monkeypatch.setattr(mind._pool, "submit", lambda fn, sid, uid, level, text, at, thinker: (
        quick.append(level), setattr(mind._minds[sid], "quick_busy", False)))
    mind._minds.clear()
    mind._notice_cursors.clear()
    session = SimpleNamespace(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), state="running", plan=None,
                              plan_revision=1, commentary_mode="occasional")

    picked = iter(range(1, 1000))

    def run(gate, fallback=None, at=100.0, fresh=True, **over):
        """One tick. ``fresh``: she picks up a dirt block, so the tick has news."""
        monkeypatch.setattr(mind, "_gates", lambda: (gate, fallback or gate, None))
        if fresh:
            over.setdefault("inventory", [{"name": "dirt", "count": next(picked)}])
        return mind.tick(None, Pack(), session, state(**over), at)

    return SimpleNamespace(run=run, hard=hard, quick=quick, session=session)


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


def test_urgent_pressure_postpones_hard_thinking_until_it_is_off(brain):
    record = brain.run(Gate({"hard": 0.9}), nearby=ZOMBIE)
    assert (record["pressure"], record["level"], record["action"]) == ("urgent", "react", "react")
    assert record["note"] == "hard postponed until the pressure is off" and brain.hard == []
    record = brain.run(Gate({"none": 0.95}), at=101)  # the zombie is gone
    assert (record["level"], record["note"]) == ("hard", "the pressure is off: thinking it through now")
    assert brain.hard == ["zombie 3 m from you"]  # what she postponed: the fight


def test_one_hard_thought_at_a_time(brain):
    assert brain.run(Gate({"hard": 0.9}))["action"] == "hard"
    record = brain.run(Gate({"hard": 0.9}), at=105)
    assert (record["level"], record["note"]) == ("light", "already thinking hard")
    mind._set_thinking(brain.session.id, None)  # the thought landed
    assert brain.run(Gate({"hard": 0.9}), at=110)["note"] == "hard thought too soon"  # under the 20 s gap
    assert brain.run(Gate({"hard": 0.9}), at=125)["action"] == "hard"
    assert len(brain.hard) == 2


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
    monkeypatch.setattr(mind.mind_ticks, "recent_talk", lambda db, uid, now: [{"role": "user", "content": "hey", "at": 175.0}])
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
            return self.session if model is mind.DesktopSession else SimpleNamespace(name="Vincent")

    class Thinker:
        def __init__(self, say):
            self.say = say

        def think(self, level, text, user, situation):
            assert user == "Vincent" and situation.key == "minecraft"
            return SimpleNamespace(text="zombie on my left", say=self.say, latency_ms=500, error=None,
                                   kind="next", noticed="zombie")

    session = SimpleNamespace(state="running", commentary_mode="occasional")
    monkeypatch.setattr(mind, "SessionLocal", lambda: Db(session))
    monkeypatch.setattr(mind, "save_thought", lambda db, **kw: saved.append(kw))
    monkeypatch.setattr(mind.agent_say, "deliver", lambda db, **kw: said.append(kw["text"]) or "Said it.")
    monkeypatch.setattr(mind, "_log", lambda record: None)
    sid, uid = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(mind.time, "time", lambda: 100.5)
    mind._think_quick(sid, uid, "react", "tick", 100.0, Thinker("zombie, left!"))
    assert said == ["zombie, left!"]
    assert saved[-1] | {} == {"user_id": uid, "session_id": sid, "level": "react", "text": "zombie on my left",
                              "said": "zombie, left!", "status": "done", "kind": "next"}
    monkeypatch.setattr(mind.time, "time", lambda: 110.0)  # landed 10 s late: a react is stale by then
    mind._think_quick(sid, uid, "react", "tick", 100.0, Thinker("zombie, left!"))
    assert saved[-1]["status"] == "stale" and saved[-1]["said"] is None and len(said) == 1
    assert mind._minds[sid].quick_busy is False
