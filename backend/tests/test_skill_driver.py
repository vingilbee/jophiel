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
        self.finish_seconds = 4.2
        self.fail_reasons: list[str] = []  # each run that finishes takes the next one and fails

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
            if self.finish_after[run_id] <= 0 and self.fail_reasons:
                record.update(status="failed", reason=self.fail_reasons.pop(0), data={}, seconds=0.1)
            elif self.finish_after[run_id] <= 0:
                record.update(status="done", data={"collected": 3}, seconds=self.finish_seconds)
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
        self.saved: dict = {}
        deps = SkillDriverDeps(
            view=lambda sid: self.view,
            progress=lambda sid, **kw: self.progress_calls.append(kw) or True,
            pause=lambda sid, reason: self.pauses.append(reason),
            pack=self.pack, model=model, metric=lambda record: None,
            save_state=lambda sid, state: setattr(self, "saved", state),
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


def test_limits_are_shown_and_near_misses_clamped():
    """Seen live: "seconds must be at most 600" and "radius must be at least 16"
    refusals, because the bounds never reached the hands."""
    manifest = [{"name": "follow_player", "description": "Follow.", "done_when": "time up", "stops_when": "lost",
                 "params": {"seconds": {"type": "number", "required": False, "min": 1, "max": 600,
                                        "description": "how long"},
                            "player": {"type": "string", "required": True, "description": "name"}}}]
    assert "seconds?: how long (1 to 600)" in system_prompt(manifest)
    variant = skill_schema(manifest)["properties"]["action"]["anyOf"][0]
    assert variant["properties"]["params"]["properties"]["seconds"]["description"] == "how long (1 to 600)"
    assert to_action({"action": {"skill": "follow_player", "params": {"seconds": 900, "player": "Vince"}}},
                     manifest) == ("follow_player", {"seconds": 600, "player": "Vince"})


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


def test_amounts_count_from_the_steps_start_not_the_total_held():
    """Seen live: "chop oak, done when 10 oak logs are collected" was marked done
    at once with 60 already held, and she stood still."""
    model = ScriptedModel(_decide("mine", {"block": "oak_log", "count": 3, "time_limit": None}),
                          _decide("mine", {"block": "oak_log", "count": 3, "time_limit": None}))
    h = Harness(model)
    held = {"oak_log": 60, "apple": 1}
    h.pack.state = lambda: {"text": "Columbina at (0, 64, 0).",
                            "inventory": [{"name": k, "count": v} for k, v in held.items()]}
    h.steps(1)
    assert "Inventory change since this step began (counted): none yet." in model.prompts[0]
    held.update(oak_log=63, apple=0)
    h.steps(3)
    assert "apple 1 -> 0 (-1); oak_log 60 -> 63 (+3)" in model.prompts[1]
    assert 'counts only what you gained' in system_prompt(MANIFEST)


def test_the_inventory_snapshot_restarts_with_each_step():
    model = ScriptedModel(_decide(status="step_done"), _decide())
    h = Harness(model)
    held = {"oak_log": 5}
    h.pack.state = lambda: {"text": "", "inventory": [{"name": "oak_log", "count": held["oak_log"]}]}
    h.steps(1)
    held["oak_log"] = 9
    steps = h.view.plan["steps"]
    h.view = _view(plan={**h.view.plan, "steps": [{**steps[0], "state": "done"}, {**steps[1], "state": "active"}]},
                   current_step_id="s2")
    h.steps(1)
    assert "Inventory change since this step began (counted): none yet." in model.prompts[1]


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


def test_plan_change_keeps_a_skill_that_still_fits():
    # Seen live: every new plan ("follow them to the cave", "...without sleeping") cut her follow off.
    h = Harness(ScriptedModel(_decide("mine", {"block": "stone", "time_limit": 60}),
                              _decide("mine", {"block": "stone", "time_limit": 120})))
    h.steps(1)
    h.view = _view(plan_revision=2, execution_generation=2)
    h.steps(1)
    assert h.pack.stops == 0
    assert len(h.pack.runs) == 1
    assert "Still running from before the plan changed: mine(block=stone)" in h.model.prompts[-1]
    assert h.run.running["plan_revision"] == 2 and "carried" not in h.run.running
    h.steps(2)  # it finishes as usual under the new plan
    assert h.run.running is None and h.run.outcomes[-1]["status"] == "done"


def test_plan_change_to_another_target_stops_the_skill():
    h = Harness(ScriptedModel(_decide("mine", {"block": "stone"}), _decide("mine", {"block": "coal_ore"})))
    h.steps(1)
    h.view = _view(plan_revision=2)
    h.steps(1)
    assert h.pack.stops == 1
    assert [run[1]["block"] for run in h.pack.runs] == ["stone", "coal_ore"]


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


def test_needs_human_right_after_dying_is_overruled():
    model = ScriptedModel(_decide("mine", {"block": "stone"}),
                          _decide(status="needs_human", note="I died; need to respawn"),
                          _decide("mine", {"block": "stone"}))
    h = Harness(model)
    h.steps(1)
    h.pack.notice("died", "urgent", "died")
    assert h.steps(2) == [True, True]  # cut short by the death, then asked again
    assert h.pauses == [] and all(c["status"] != "needs_human" for c in h.progress_calls)
    assert "You died" in model.prompts[1]
    h.steps(1)
    assert h.pack.runs[-1][0] == "mine"


def test_needs_human_long_after_a_death_is_respected():
    model = ScriptedModel(_decide(status="needs_human", note="give me a pickaxe"))
    h = Harness(model)
    h.run.died_at = h.clock - 300
    assert h.steps(1) == [False]
    assert h.progress_calls[-1]["status"] == "needs_human"


def test_an_action_that_changes_nothing_is_flagged_then_stuck():
    go = _decide("craft", {"item": "planks"})
    model = ScriptedModel(*[go] * 7)
    h = Harness(model)
    h.pack.finish_seconds = 0  # "done" instantly: she was already there
    h.steps(3 * 3 + 1)  # each run: decide, poll, finish; then the 4th decision
    assert "the same way 3 times in a row" not in model.prompts[2]
    assert "the same way 3 times in a row" in model.prompts[3]
    h.steps(3 * 3)  # three more runs, then the 7th decision is overruled to stuck
    assert h.progress_calls[-1]["status"] == "stuck"
    assert "same result" in h.progress_calls[-1]["note"]


def test_a_skill_that_ran_its_course_is_not_a_repeat():
    model = ScriptedModel(*[_decide("craft", {"item": "planks"})] * 8)
    h = Harness(model)  # each run lasts 4.2 s, like a follow that finished
    h.steps(3 * 7)
    assert all(c["status"] != "stuck" for c in h.progress_calls)
    assert not any("times in a row" in p for p in model.prompts)


def test_pack_down_pauses_after_a_while():
    h = Harness(ScriptedModel())
    h.pack.manifest = lambda: (_ for _ in ()).throw(PackError("pack unreachable"))
    results = [h.run.step() for _ in range(3)]
    assert results == [True, True, True]
    h.clock += 20
    assert h.run.step() is False
    assert h.pauses == ["pack_offline"]


def test_failing_in_different_ways_tells_columbina_with_the_counted_tries():
    """Seen live: a bed craft failed 4 times and explore 3 times, each a little
    differently, so nothing ever reached "stuck" and Columbina never knew."""
    model = ScriptedModel(_decide("craft", {"item": "white_bed"}), _decide("craft", {"item": "bed"}),
                          _decide("craft", {"item": "white_bed"}), _decide("mine", {"block": "oak_log"}),
                          _decide("craft", {"item": "white_bed"}))
    h = Harness(model)
    h.pack.fail_reasons = ["missing ingredients", "no recipe", "missing ingredients", "no tree nearby"]
    h.steps(3 * 3)
    assert not [n for n in h.noticed if n[0] == "struggling"]  # 3 failed tries: not yet
    assert h.saved["step"]["tries"] == 3 and h.saved["step"]["failed"] == 3
    h.steps(3)
    struggling = [text for kind, text in h.noticed if kind == "struggling"]
    assert len(struggling) == 1
    assert struggling[0].startswith('4 failed tries at "chop 3 oak logs"')
    assert "craft(item=white_bed) -> missing ingredients x2" in struggling[0]
    assert "mine(block=oak_log) -> no tree nearby" in struggling[0]
    assert all(c["status"] != "stuck" for c in h.progress_calls)  # she keeps playing


def test_a_new_step_starts_a_fresh_count():
    model = ScriptedModel(_decide("craft", {"item": "white_bed"}), _decide(status="step_done"),
                          _decide("mine", {"block": "stone"}))
    h = Harness(model)
    h.pack.fail_reasons = ["missing ingredients"]
    h.steps(3)
    assert h.saved["step"]["failed"] == 1
    h.steps(1)
    h.view = _view(current_step_id="s2", plan={"goal": "g", "steps": [
        {"id": "s1", "do": "chop", "done_when": "x", "state": "done"},
        {"id": "s2", "do": "craft planks", "done_when": "y", "state": "active"}]})
    h.steps(1)
    assert h.saved["step"]["id"] == "s2" and h.saved["step"]["failed"] == 0


def test_hands_are_told_to_say_no_skill_at_once():
    assert '"no skill for:"' in system_prompt(MANIFEST)
