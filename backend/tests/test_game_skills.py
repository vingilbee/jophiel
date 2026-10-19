"""Moves she taught herself, kept as skills (stage H2): naming, the program that
runs a move N times, outcomes, and her hands picking one. No database."""
from __future__ import annotations

from app.ai.desktop.skill_driver import SkillDriverRun, skill_schema, system_prompt, to_action
from app.services import game_skills
from tests.test_skill_driver import MANIFEST, SESSION, Harness, ScriptedModel, _decide

PILLAR = {"name": "learned_pillar_upward_jumping_placing", "task": "pillar upward by jumping and placing a block",
          "code": "await act.jump(); return { done: true }", "uses": 2, "successes": 1}


def test_names_are_the_tasks_main_words():
    assert game_skills.name_for("climb by jumping and placing a block beneath yourself") == \
        "learned_climb_jumping_placing_block"
    assert game_skills.name_for("fight and kill a rabbit") == "learned_fight_kill_rabbit"
    assert game_skills.name_for("Fight and KILL a rabbit!") == "learned_fight_kill_rabbit"  # same move
    assert game_skills.name_for("the a an") == "learned_move"
    assert game_skills.is_learned("learned_fight_kill_rabbit") and not game_skills.is_learned("mine")


def test_the_program_runs_the_move_times_in_a_row_and_stops_at_a_miss():
    code = game_skills.program("return { done: true }", 3)
    assert code.startswith("const __move = async () => {\nreturn { done: true }\n};")
    assert "const __times = 3;" in code
    assert "__last.done === false" in code
    assert "const __times = 10;" in game_skills.program("x", 99)  # capped
    assert "const __times = 1;" in game_skills.program("x", 0)


def test_a_finished_program_becomes_a_skill_outcome():
    params = {"times": 3}
    done = game_skills.outcome({"status": "done", "value": {"done": True, "times_done": 3}, "seconds": 4.1},
                               "learned_x", params)
    assert done == {"skill": "learned_x", "params": params, "status": "done", "reason": None,
                    "data": {"times_done": 3}, "seconds": 4.1}
    missed = game_skills.outcome({"status": "done", "value": {"done": False, "times_done": 1, "reason": "no blocks"}},
                                 "learned_x", params)
    assert missed["status"] == "failed" and missed["reason"] == "time 2 didn't work: no blocks"
    broke = game_skills.outcome({"status": "error", "reason": "x is not defined"}, "learned_x", params)
    assert broke["status"] == "failed" and "x is not defined" in broke["reason"]
    hurt = game_skills.outcome({"status": "stopped", "reason": "hurt"}, "learned_x", params)
    assert hurt["status"] == "stopped"


def test_a_learned_move_is_one_more_skill_in_the_hands_schema_and_prompt():
    manifest = MANIFEST + [game_skills.manifest_entry(PILLAR)]
    schema = skill_schema(manifest)
    names = [v["properties"]["skill"]["enum"][0] for v in schema["properties"]["action"]["anyOf"][:-1]]
    assert PILLAR["name"] in names
    prompt = system_prompt(manifest)
    assert f'- {PILLAR["name"]}(times?: how many times in a row' in prompt
    assert 'A move you taught yourself: "pillar upward by jumping and placing a block". Used 2 times, worked 1.' \
        in prompt
    assert "Skills named learned_... are moves Columbina taught herself" in prompt
    # times out of bounds is clamped like any skill's numbers
    assert to_action(_decide(PILLAR["name"], {"times": 40, "time_limit": None}), manifest) == \
        (PILLAR["name"], {"times": game_skills.MAX_TIMES})


class ProgramPack:
    """FakePack plus programs: a program finishes on the second look."""

    def __init__(self, pack):
        self.pack = pack
        self.programs: dict[str, dict] = {}
        self.started: list[tuple[str, float | None]] = []
        self.value = {"done": True, "times_done": 3}

    def __getattr__(self, name):
        return getattr(self.pack, name)

    def start_program(self, code, *, seconds=None):
        program_id = f"p{len(self.programs) + 1}"
        self.started.append((code, seconds))
        self.programs[program_id] = {"id": program_id, "status": "running", "looks": 0}
        return {"id": program_id, "status": "running"}

    def get_program(self, program_id, *, wait=0):
        record = self.programs[program_id]
        record["looks"] += 1
        if record["status"] == "running" and record["looks"] >= 2:
            record.update(status="done", value=self.value, seconds=3.3)
        return record

    def stop(self):
        for record in self.programs.values():
            if record["status"] == "running":
                record.update(status="stopped", reason="stopped by request")
        return self.pack.stop()


def _harness(model, learned=(PILLAR,)):
    h = Harness(model)
    h.pack = ProgramPack(h.pack)
    h.used: list[tuple[str, bool]] = []
    deps = h.run.deps
    deps.pack = h.pack
    deps.learned = lambda sid: list(learned)
    deps.learned_used = lambda sid, name, worked: h.used.append((name, worked))
    h.run = SkillDriverRun(SESSION, deps)
    return h


def test_her_hands_run_a_learned_move_as_her_program_and_count_the_use():
    model = ScriptedModel(_decide(PILLAR["name"], {"times": 3, "time_limit": None}),
                          _decide(status="step_done", note="up"))
    h = _harness(model)
    h.steps(1)
    (code, seconds), = h.pack.started
    assert PILLAR["code"] in code and "const __times = 3;" in code
    assert seconds == game_skills.DEFAULT_SECONDS
    assert h.pack.runs == []  # not a pack skill
    h.steps(2)  # it runs, then finishes
    assert h.used == [(PILLAR["name"], True)]
    h.steps(1)  # the next decision sees the outcome
    assert f"{PILLAR['name']}(times=3) -> done" in model.prompts[1]


def test_a_move_that_misses_is_a_failed_outcome():
    model = ScriptedModel(_decide(PILLAR["name"], {"times": 2}), _decide(status="stuck", note="no"))
    h = _harness(model)
    h.pack.value = {"done": False, "times_done": 0, "reason": "nothing to stand on"}
    h.steps(4)
    assert h.used == [(PILLAR["name"], False)]
    assert "-> failed: time 1 didn't work: nothing to stand on" in model.prompts[1]


def test_stopping_a_running_move_stops_her_program():
    model = ScriptedModel(_decide(PILLAR["name"], {"times": 5}))
    h = _harness(model)
    h.steps(1)
    h.run._stop_running("the plan changed")
    assert h.pack.programs["p1"]["status"] == "stopped"
    assert h.run.outcomes[-1]["status"] == "stopped" and h.run.outcomes[-1]["skill"] == PILLAR["name"]


def test_a_move_learned_during_play_is_picked_up_after_the_lesson():
    learned: list[dict] = []
    model = ScriptedModel(_decide("mine", {"block": "dirt"}), *[_decide(status="working")] * 6)
    h = _harness(model, learned=learned)
    h.steps(1)
    assert PILLAR["name"] not in {s["name"] for s in h.run.manifest}
    holding = [True]
    h.run.deps.learning = lambda sid: holding[0]
    h.steps(1)  # her body is the learner's for a while
    learned.append(PILLAR)
    holding[0] = False
    h.steps(2)
    assert PILLAR["name"] in {s["name"] for s in h.run.manifest}


def test_columbinas_tools_say_learned_moves_are_skills():
    from app.ai.tools.desktop import tools

    text = " ".join(open(tools.__file__).read().split())
    assert "Moves you taught yourself (learned_..., in your game summary) are skills too" in text
    assert "learning one again (only when the user asks) replaces it" in text
