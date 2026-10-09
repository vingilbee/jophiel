"""Her hands picking skills in a game pack (stage G, phase 3): the decision
loop against a fake pack and a scripted model. No database, no network."""
from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.ai.desktop.driver import DriverModelError, ModelReply
from app.ai.desktop.pack_client import PackError
from app.ai.desktop.skill_driver import SkillDriverDeps, SkillDriverRun, skill_schema, system_prompt, to_action
from app.services.desktop_sessions import DriverView

MANIFEST = [
    {"name": "mine", "description": "Break blocks.", "done_when": "has count", "stops_when": "none left",
     "params": {"block": {"type": "string", "required": True, "description": "block"},
                "count": {"type": "integer", "required": False, "default": 1, "description": "how many"},
                "time_limit": {"type": "number", "required": False, "default": 120, "description": "s"}}},
    {"name": "fight", "description": "Fight.", "done_when": "none left", "stops_when": "outnumbered",
     "params": {"target": {"type": "string", "required": False, "default": "nearest_hostile", "description": "t"},
                "time_limit": {"type": "number", "required": False, "default": 90, "description": "s"}}},
    {"name": "craft", "description": "Craft.", "done_when": "crafted", "stops_when": "no recipe",
     "params": {"item": {"type": "string", "required": True, "description": "item"},
                "mode": {"type": "enum", "required": False, "values": ["a", "b"], "description": "m"}}},
]

SESSION = uuid.uuid4()


def _view(**changes) -> DriverView:
    plan = {"goal": "Get 3 logs", "steps": [
        {"id": "s1", "do": "chop 3 oak logs", "done_when": "3 oak_log in inventory", "state": "active"},
        {"id": "s2", "do": "craft planks", "done_when": "planks in inventory", "state": "pending"},
    ]}
    base = DriverView(SESSION, uuid.uuid4(), "running", "gameplay", 1, plan, 1, "minecraft_pack", "s1",
                      "blocked", 0, "stop", "occasional", None)
    return replace(base, **changes)


class FakePack:
    def __init__(self):
        self.runs: list[tuple[str, dict, str]] = []
        self.stops = 0
        self.records: dict[str, dict] = {}
        self.finish_after: dict[str, int] = {}
        self.pending_notices: list[dict] = []
        self.seq = 5
        self.refuse: str | None = None

    def manifest(self):
        return MANIFEST

    def state(self):
        return {"text": "Columbina at (0, 64, 0). Inventory: empty."}

    def notices(self, since=0, *, wait=0):
        fresh = [n for n in self.pending_notices if n["seq"] > since]
        return {"next": max([since, *[n["seq"] for n in fresh]]) if fresh else max(since, self.seq),
                "notices": fresh}

    def notice(self, kind, urgency, text):
        self.seq += 1
        self.pending_notices.append({"seq": self.seq, "kind": kind, "urgency": urgency, "text": text})

    def run(self, skill, params, *, source="driver"):
        if self.refuse:
            raise PackError(self.refuse, 400)
        run_id = str(uuid.uuid4())
        self.runs.append((skill, params, source))
        self.records[run_id] = {"id": run_id, "skill": skill, "params": params, "status": "running",
                                "reason": None, "data": {}, "source": source}
        self.finish_after[run_id] = 2
        return self.records[run_id]

    def get_run(self, run_id, *, wait=0):
        record = self.records[run_id]
        if record["status"] == "running":
            self.finish_after[run_id] -= 1
            if self.finish_after[run_id] <= 0:
                record.update(status="done", data={"collected": 3}, seconds=4.2)
        return record

    def current(self):
        return next((r for r in self.records.values() if r["status"] == "running"), None)

    def stop(self):
        self.stops += 1
        record = self.current()
        if record:
            record.update(status="stopped", reason="stopped by request")
        return record


class ScriptedModel:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.on_call = None

    def step(self, system, text, jpeg, schema):
        self.prompts.append(text)
        if self.on_call:
            self.on_call()
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ModelReply(output=output, latency_ms=900)


def _decide(skill=None, params=None, status="working", note="on it"):
    action = {"skill": skill, "params": params or {}} if skill else None
    return {"note": note, "status": status, "step_id": "s1", "action": action}


class Harness:
    def __init__(self, model, view=None):
        self.pack = FakePack()
        self.view = view or _view()
        self.progress_calls: list[dict] = []
        self.pauses: list[str] = []
        self.noticed: list[tuple[str, str]] = []
        self.messages: list[dict] = []
        self.clock = 1000.0
        self.model = model
        deps = SkillDriverDeps(
            view=lambda sid: self.view,
            progress=lambda sid, **kw: self.progress_calls.append(kw) or True,
            pause=lambda sid, reason: self.pauses.append(reason),
            pack=self.pack, model=model, metric=lambda record: None, save_state=lambda sid, state: None,
            notice=lambda sid, kind, text: self.noticed.append((kind, text)),
            auto_goals=lambda sid: 0, messages=lambda sid: list(self.messages),
            now=lambda: self.clock, sleep=lambda s: None,
        )
        self.run = SkillDriverRun(SESSION, deps)

    def steps(self, n):
        results = []
        for _ in range(n):
            self.clock += 1
            results.append(self.run.step())
        return results


# -- schema and parsing -------------------------------------------------------------


def test_schema_has_one_strict_variant_per_skill_plus_wait_and_none():
    schema = skill_schema(MANIFEST)
    variants = schema["properties"]["action"]["anyOf"]
    names = [v["properties"]["skill"]["enum"][0] for v in variants if v.get("type") == "object"]
    assert names == ["mine", "fight", "craft", "wait"]
    assert {"type": "null"} in variants
    mine = variants[0]["properties"]["params"]
    assert mine["required"] == ["block", "count", "time_limit"] and mine["additionalProperties"] is False
    assert mine["properties"]["block"]["type"] == "string"
    assert mine["properties"]["count"]["type"] == ["integer", "null"]  # optional -> nullable
    assert variants[2]["properties"]["params"]["properties"]["mode"]["enum"] == ["a", "b", None]


def test_prompt_lists_every_skill_with_its_conditions():
    prompt = system_prompt(MANIFEST)
    assert "- mine(block: block, count?: how many): Break blocks. Done when has count. Stops when none left." in prompt
    assert "wait(seconds)" in prompt and "Never attack players" in prompt


def test_actions_drop_nulls_and_reject_unknown_skills():
    assert to_action({"action": {"skill": "mine", "params": {"block": "oak_log", "count": None}}}, MANIFEST) == (
        "mine", {"block": "oak_log"})
    assert to_action({"action": None}, MANIFEST) is None
    with pytest.raises(ValueError):
        to_action({"action": {"skill": "fly", "params": {}}}, MANIFEST)


# -- the loop -----------------------------------------------------------------------


def test_picks_a_skill_waits_for_it_then_reports_the_step_done():
    model = ScriptedModel(_decide("mine", {"block": "oak_log", "count": 3, "time_limit": None}),
                          _decide(status="step_done", note="have 3 logs"))
    h = Harness(model)
    h.steps(1)
    assert h.pack.runs == [("mine", {"block": "oak_log", "count": 3}, "driver")]
    h.steps(2)  # the skill runs: no new model call meanwhile
    assert len(model.prompts) == 1
    h.steps(1)  # it finished: the next decision sees the outcome
    assert "mine(block=oak_log, count=3) -> done" in model.prompts[1]
    assert '"collected":3' in model.prompts[1]
    assert {"status": "step_done", "step_id": "s1"}.items() <= h.progress_calls[-1].items()


def test_urgent_notice_cuts_a_skill_short_and_wakes_columbina():
    model = ScriptedModel(_decide("mine", {"block": "stone"}), _decide("fight", {"target": None}))
    h = Harness(model)
    h.steps(1)
    h.pack.notice("hostile", "urgent", "creeper 4 m south (behind)")
    h.steps(1)
    assert h.pack.stops == 1
    assert ("danger", "creeper 4 m south (behind)") in h.noticed
    h.steps(1)
    assert "[urgent] creeper 4 m south (behind)" in model.prompts[1]
    assert "mine(block=stone) -> stopped: something urgent was noticed" in model.prompts[1]
    assert h.pack.runs[-1][0] == "fight"


def test_fight_is_not_cut_short_by_danger():
    h = Harness(ScriptedModel(_decide("fight", {"target": None})))
    h.steps(1)
    h.pack.notice("hostile", "urgent", "zombie 3 m ahead")
    h.steps(1)
    assert h.pack.stops == 0


def test_discoveries_reach_columbina_and_quiet_notices_do_not():
    h = Harness(ScriptedModel(_decide("mine", {"block": "stone"})))
    h.steps(1)
    h.pack.notice("village", "heads_up", "a village (saw a bell), 70 m north")
    h.pack.notice("morning", "info", "morning")
    h.steps(1)
    assert h.noticed == [("discovery", "a village (saw a bell), 70 m north")]


def test_session_pause_stops_her_skill_and_ends_the_run():
    h = Harness(ScriptedModel(_decide("mine", {"block": "stone"})))
    h.steps(1)
    h.view = _view(state="paused", control_owner="none")
    assert h.steps(1) == [False]
    assert h.pack.stops == 1


def test_plan_change_stops_the_running_skill():
    h = Harness(ScriptedModel(_decide("mine", {"block": "stone"}), _decide("craft", {"item": "stick"})))
    h.steps(1)
    h.view = _view(plan_revision=2)
    h.steps(1)
    assert h.pack.stops == 1


def test_answer_dropped_when_the_plan_changes_while_thinking():
    model = ScriptedModel(_decide("mine", {"block": "stone"}))
    h = Harness(model)
    model.on_call = lambda: setattr(h, "view", _view(plan_revision=2))
    h.steps(1)
    assert h.pack.runs == []


def test_answer_dropped_when_columbina_messages_while_thinking():
    model = ScriptedModel(_decide("mine", {"block": "stone"}))
    h = Harness(model)
    model.on_call = lambda: h.messages.append({"id": "m1", "text": "mine the coal instead"})
    h.steps(1)
    assert h.pack.runs == []


def test_refused_decisions_become_outcomes_then_stuck():
    model = ScriptedModel(*[_decide("mine", {"block": "air_please"})] * 3)
    h = Harness(model)
    h.pack.refuse = "no block is called air_please"
    h.steps(3)
    assert "-> refused: no block is called air_please" in model.prompts[1]
    assert h.progress_calls[-1]["status"] == "stuck"


def test_model_errors_pause_after_three():
    h = Harness(ScriptedModel(*[DriverModelError("timeout")] * 3))
    assert h.steps(3) == [True, True, False]
    assert h.pauses == ["driver_error"]


def test_stuck_status_ends_the_run():
    h = Harness(ScriptedModel(_decide(status="stuck", note="no trees anywhere")))
    assert h.steps(1) == [False]
    assert h.progress_calls[-1]["status"] == "stuck"


def test_wait_is_local_and_ends_early_on_danger():
    model = ScriptedModel(_decide("wait", {"seconds": 20}), _decide("fight", {"target": None}))
    h = Harness(model)
    h.steps(3)
    assert len(model.prompts) == 1 and h.pack.runs == []
    h.pack.notice("hostile", "urgent", "zombie 2 m ahead")
    h.steps(2)
    assert h.pack.runs[-1][0] == "fight"


def test_pack_down_pauses_after_a_while():
    h = Harness(ScriptedModel())
    h.pack.manifest = lambda: (_ for _ in ()).throw(PackError("pack unreachable"))
    results = [h.run.step() for _ in range(3)]
    assert results == [True, True, True]
    h.clock += 20
    assert h.run.step() is False
    assert h.pauses == ["pack_offline"]
