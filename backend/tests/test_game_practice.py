"""Teaching herself a move (stage H1).

The learner loop (write, run, count, judge, retry) runs against a fake pack and
scripted models, with no database and no network. So do her hands holding while
she learns, which tools each kind of turn gets (only a turn where the user spoke
can start learning), and learning reaching her mind's tick. The record, offers,
start/stop and the worker need real PostgreSQL (``TEST_DATABASE_URL``, a
throwaway server, never the dev database).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.ai.context import GameEvent
from app.ai.desktop.driver import DriverModelError, ModelReply
from app.ai.desktop.pack_client import PackError
from app.ai.desktop.practice import (CRITIC_SCHEMA, WRITER_SCHEMA, PracticeDeps, PracticeRun, counted_facts,
                                     writer_prompt)
from app.ai.mind.tick import TickInput, news, render
from app.ai.tools.desktop import build_desktop_tools
from app.core.config import get_settings
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_skill_driver import MANIFEST, Harness, ScriptedModel, _decide


def _state(logs: int = 0, x: float = 0) -> dict:
    inventory = [{"name": "oak_log", "count": logs}] if logs else []
    return {"text": f"Columbina at ({x:g}, 64, 0). {logs} oak logs.", "position": {"x": x, "y": 64, "z": 0},
            "inventory": inventory, "health": 20, "food": 20}


class FakePack:
    """Runs each program as scripted: a list of (program result, state after) per try."""

    def __init__(self, results: list[tuple[dict, dict]], busy: list[dict] | None = None):
        self.results = list(results)
        self.busy = list(busy or [])  # what current() returns first, one per call
        self.state_now = _state()
        self.started: list[str] = []
        self.stops = 0
        self.program: dict | None = None
        self.polls = 0
        self.hold_running = 0  # polls a program stays running

        self.blocks = {"dirt": 100}

    def manifest(self):
        return MANIFEST

    def state(self):
        return self.state_now

    def block_census(self, x, y, z, radius=10):
        return {"center": {"x": x, "y": y, "z": z}, "radius": radius, "counts": dict(self.blocks), "unloaded": 0}

    def current(self):
        return self.busy.pop(0) if self.busy else None

    def stop(self):
        self.stops += 1
        if self.program and self.program["status"] == "running":
            self.program.update(status="stopped", reason="stopped by request")
        return None

    def start_program(self, code, *, seconds=None):
        self.started.append(code)
        result, after = self.results.pop(0)
        self.program = {"id": str(uuid.uuid4()), "status": "running"}
        self._final, self._after = result, after
        return dict(self.program)

    def get_program(self, program_id, *, wait=0):
        self.polls += 1
        if self.program["status"] == "running" and self.hold_running > 0:
            self.hold_running -= 1
            return dict(self.program)
        if self.program["status"] == "running":
            self.program.update(self._final)
            self.state_now = self._after
        return dict(self.program)


def _written(code="await skills.mine({ block: 'oak_log', count: 3 })", approach="mine three logs"):
    return {"approach": approach, "code": code}


def _program(status="done", reason=None, calls=None, value=None):
    return {"status": status, "reason": reason, "value": value, "log": [], "seconds": 3.1,
            "calls": calls if calls is not None else [{"skill": "mine", "params": {"block": "oak_log"},
                                                       "status": "done", "reason": None}]}


def _learner(pack, writer, critic, stop=lambda: None, max_tries=4):
    saved: list[list[dict]] = []
    deps = PracticeDeps(pack=pack, model=writer, critic=critic, should_stop=stop,
                        save=lambda tries: saved.append([dict(t) for t in tries]),
                        now=lambda: 0.0, sleep=lambda s: None)
    return PracticeRun("get 3 oak logs", deps, max_tries=max_tries), saved


class Recording(ScriptedModel):
    def __init__(self, *outputs):
        super().__init__(*outputs)
        self.schemas: list[dict] = []

    def step(self, system, text, jpeg, schema):
        self.schemas.append(schema)
        return super().step(system, text, jpeg, schema)


def test_a_miss_goes_back_to_the_writer_and_the_second_try_is_learned():
    pack = FakePack([(_program(), _state(logs=1)), (_program(), _state(logs=3))])
    writer = Recording(_written(approach="first idea"), _written(code="await skills.mine({ block: 'oak_log', count: 5 })"))
    critic = Recording({"success": False, "reason": "only 1 log gained, needed 3"},
                       {"success": True, "reason": "oak_log 0 -> 3"})
    result = _learner(pack, writer, critic)[0].run()
    assert result.status == "learned"
    assert result.code == "await skills.mine({ block: 'oak_log', count: 5 })"
    assert len(result.tries) == 2
    # The second try sees the first one's code, what the program did, the counted facts and the verdict.
    second = writer.prompts[1]
    assert "Try 2 of 4" in second and "first idea" in second and "only 1 log gained" in second
    assert "oak_log 0 -> 1 (+1)" in second
    # The critic judges from facts counted by code, not from the program's say-so.
    # Tries add up: the second try's own change, and the whole attempt's.
    assert "inventory this try: oak_log 1 -> 3 (+2)" in critic.prompts[1]
    assert "inventory since you started learning this: oak_log 0 -> 3 (+3)" in critic.prompts[1]
    assert writer.schemas[0] is WRITER_SCHEMA and critic.schemas[0] is CRITIC_SCHEMA


def test_a_program_that_breaks_is_a_failed_try_without_asking_the_critic():
    pack = FakePack([(_program("error", "ReferenceError: require is not defined (line 1)", calls=[]), _state()),
                     (_program(), _state(logs=3))])
    writer = ScriptedModel(_written(code="require('fs')"), _written())
    critic = ScriptedModel({"success": True, "reason": "3 logs"})
    result, saved = _learner(pack, writer, critic)
    result = result.run()
    assert result.status == "learned"
    assert result.tries[0]["verdict"]["reason"].startswith("the program broke: ReferenceError")
    assert len(critic.prompts) == 1  # only the second try was judged
    assert len(saved) == 2 and saved[0][0]["code"] == "require('fs')"  # every try saved as it ends


def test_four_misses_end_it_as_failed_with_the_last_reason():
    pack = FakePack([(_program(), _state())] * 4)
    writer = ScriptedModel(*[_written()] * 4)
    critic = ScriptedModel(*[{"success": False, "reason": "no logs gained"}] * 4)
    result = _learner(pack, writer, critic)[0].run()
    assert result.status == "failed"
    assert len(result.tries) == 4
    assert "after 4 tries" in result.reason and "no logs gained" in result.reason


def test_being_stopped_mid_program_stops_her_and_ends_the_attempt():
    pack = FakePack([(_program(), _state())])
    pack.hold_running = 5
    asked = iter([None, None, None, "the user asked you to stop"])
    result = _learner(pack, ScriptedModel(_written()), ScriptedModel(), stop=lambda: next(asked, "stop"))[0].run()
    assert result.status == "stopped"
    assert pack.stops == 1
    assert result.tries[0]["program"]["status"] == "stopped"


def test_getting_hurt_ends_the_attempt_without_another_try():
    pack = FakePack([(_program("hurt", "mine: health low (5/20)"), _state()), (_program(), _state())])
    result = _learner(pack, ScriptedModel(_written(), _written()), ScriptedModel())[0].run()
    assert result.status == "failed"
    assert "got hurt" in result.reason
    assert len(pack.started) == 1


def test_a_skill_of_her_hands_still_running_is_stopped_first_and_her_breathing_waited_out():
    pack = FakePack([(_program(), _state(logs=3))],
                    busy=[{"skill": "follow_player", "source": "driver"}, {"skill": "surface", "source": "reflex"}])
    result = _learner(pack, ScriptedModel(_written()), ScriptedModel({"success": True, "reason": "ok"}))[0].run()
    assert result.status == "learned"
    assert pack.stops == 1  # her hands' follow, not her breathing


def test_writer_errors_are_failed_tries_and_an_unreachable_game_fails_cleanly():
    pack = FakePack([(_program(), _state(logs=3))])
    writer = ScriptedModel(DriverModelError("timeout"), _written())
    result = _learner(pack, writer, ScriptedModel({"success": True, "reason": "ok"}))[0].run()
    assert result.status == "learned" and "couldn't write a program" in result.tries[0]["verdict"]["reason"]

    class Down(FakePack):
        def manifest(self):
            raise PackError("pack unreachable")
    assert _learner(Down([]), ScriptedModel(), ScriptedModel())[0].run().status == "failed"


def test_the_writer_failing_twice_in_a_row_ends_it():
    writer = ScriptedModel(DriverModelError("bad request"), DriverModelError("bad request"), _written())
    result = _learner(FakePack([]), writer, ScriptedModel())[0].run()
    assert result.status == "failed" and "couldn't think it through" in result.reason
    assert len(result.tries) == 2


def test_blocks_placed_around_her_are_counted_for_the_critic():
    pack = FakePack([(_program(calls=[{"skill": "place", "params": {"block": "crafting_table"}, "status": "done",
                                       "reason": None, "data": {"x": 1, "y": 64, "z": 0}}]), _state())])
    real_get = pack.get_program

    def placing(program_id, *, wait=0):
        pack.blocks = {"dirt": 100, "crafting_table": 1}
        return real_get(program_id, wait=wait)
    pack.get_program = placing
    critic = ScriptedModel({"success": True, "reason": "crafting_table +1"})
    result = _learner(pack, ScriptedModel(_written()), critic)[0].run()
    assert result.status == "learned"
    assert result.tries[0]["facts"]["blocks_around_start"] == "crafting_table 0 -> 1 (+1)"
    assert "blocks within 10 of where the try began: crafting_table 0 -> 1 (+1)" in critic.prompts[0]
    assert "[the skill reported: {'x': 1, 'y': 64, 'z': 0}]" in critic.prompts[0]


def test_counted_facts_and_the_writer_prompt():
    facts = counted_facts(_state(logs=1, x=0), _state(logs=4, x=12))
    assert facts["inventory"] == "oak_log 1 -> 4 (+3)"
    assert facts["moved_m"] == 12.0 and facts["to"] == [12, 64, 0]
    prompt = writer_prompt(MANIFEST)
    assert "skills.mine({ block: block, count?: how many" in prompt
    assert "no require" in prompt and "wait(ms)" in prompt


# --------------------------------------------------------------------------- #
# Her hands hold; only a turn where the user spoke can start learning; her mind sees it
# --------------------------------------------------------------------------- #


def test_her_hands_hold_while_she_learns():
    model = ScriptedModel(_decide("mine", {"block": "oak_log", "count": 3}))
    harness = Harness(model)
    learning = {"on": True}
    harness.run.deps.learning = lambda sid: learning["on"]
    assert harness.run.step() is True
    assert model.prompts == [] and harness.pack.runs == []  # decided nothing, started nothing
    learning["on"] = False
    harness.run.step()
    assert [r[0] for r in harness.pack.runs] == ["mine"]


def test_a_decision_made_while_learning_began_is_dropped():
    model = ScriptedModel(_decide("mine", {"block": "oak_log", "count": 3}))
    harness = Harness(model)
    learning = {"on": False}
    harness.run.deps.learning = lambda sid: learning["on"]
    model.on_call = lambda: learning.update(on=True)  # learning starts while the model thinks
    harness.run.step()
    assert harness.pack.runs == []


def test_her_own_turns_may_start_lessons_unless_she_must_ask_first(monkeypatch):
    # 2026-10-18, the user's choice "for now": no consent needed; GAME_PRACTICE_ASK_FIRST brings it back.
    event = GameEvent(("stuck: no skill for: crossing water",))
    background = {t.name for t in build_desktop_tools(uuid.uuid4(), event)}
    user_turn = {t.name for t in build_desktop_tools(uuid.uuid4())}
    assert {"learn_skill", "stop_learning"} <= background and "offer_to_learn" not in background
    assert {"learn_skill", "stop_learning"} <= user_turn and "offer_to_learn" not in user_turn
    import app.ai.tools.desktop.tools as tool_module

    asking = get_settings().model_copy(update={"game_practice_ask_first": True})
    monkeypatch.setattr(tool_module, "get_settings", lambda: asking)
    background = {t.name for t in build_desktop_tools(uuid.uuid4(), event)}
    assert "offer_to_learn" in background and "learn_skill" not in background and "stop_learning" not in background


def test_learning_is_only_for_what_no_skill_does():
    # Use the current skill inventory before learning; never recommend the retired explore skill.
    import app.ai.tools.desktop.tools as tool_module

    asking = get_settings().model_copy(update={"game_practice_ask_first": True})
    real = tool_module.get_settings
    tool_module.get_settings = lambda: asking
    try:
        offer = next(t for t in build_desktop_tools(uuid.uuid4(), GameEvent(("stuck",))) if t.name == "offer_to_learn")
    finally:
        tool_module.get_settings = real
    learn = next(t for t in build_desktop_tools(uuid.uuid4()) if t.name == "learn_skill")
    for tool in (offer, learn):
        text = " ".join(tool.description.split())
        assert "if you can name the skill that does it" in text.lower()
        assert "with its parameters" in text
        assert "explore" not in text
    learn_text = " ".join(learn.description.split())
    assert "don't just say you can't: say you haven't learned it yet and that you'll try" in learn_text


def test_each_try_is_news_in_her_mind():
    state = {"text": "Columbina at (0, 64, 0).", "health": 20}
    before = TickInput(at=0, state=state, learning='learning "cross water": try 1 of 4')
    same = TickInput(at=1, state=state, learning='learning "cross water": try 1 of 4')
    next_try = TickInput(at=2, state=state, learning='learning "cross water": try 2 of 4')
    assert news(same, before) == []
    assert news(next_try, same) == ['learning: learning "cross water": try 2 of 4']
    assert 'Teaching yourself a move: learning "cross water": try 2 of 4' in render(next_try, same)[0]


# --------------------------------------------------------------------------- #
# The record, offers, start/stop and the worker (real PostgreSQL)
# --------------------------------------------------------------------------- #

from app.db.models import DesktopEnvironment, DesktopSession, GameLearnedSkill, GameSkillAttempt, User  # noqa: E402
from app.services import desktop_sessions, game_practice, game_skills, memory_reset  # noqa: E402
from app.services.desktop_sessions import DesktopError  # noqa: E402
from tests.db_support import database  # noqa: E402,F401  (fixture)


@pytest.fixture
def db(database, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings().model_copy(update={"desktop_agent_enabled": True, "game_practice_enabled": True,
                                                 "mind_enabled": False, "redis_url": ""})
    monkeypatch.setattr(game_practice, "get_settings", lambda: settings)
    monkeypatch.setattr(game_practice, "SessionLocal", database)
    monkeypatch.setattr("app.services.game_pack.stop_driver_skill", lambda client=None: False)
    with database() as session:
        yield session


@pytest.fixture
def user(db):
    row = User(name="vincent", email=f"v-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    db.add(row)
    db.commit()
    return row


def _pack_session(db, user, state="running") -> DesktopSession:
    env = DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                             allowed_profile_ids=["minecraft_pack"], status="online")
    db.add(env)
    db.flush()
    session = DesktopSession(owner_user_id=user.id, environment_id=env.id, profile_id="minecraft_pack",
                             profile_revision=1, state=state, control_owner="gameplay" if state == "running" else "none",
                             max_duration_seconds=3600)
    db.add(session)
    db.commit()
    return session


def test_an_offer_then_a_yes_starts_one_attempt_and_her_hands_hold(db, user):
    session = _pack_session(db, user)
    assert game_practice.offer(db, user.id, task="  cross   water ") == "cross water"
    assert "You offered to learn" in game_practice.summary_lines(db, session, 0)[0]
    text = game_practice.start(db, user.id, task="cross water")
    assert text.startswith('Started learning: "cross water"')
    attempt = db.query(GameSkillAttempt).one()
    assert (attempt.status, attempt.origin) == ("queued", "offer")  # the offer is used up
    assert game_practice.pending_offer(session.id) is None
    assert game_practice.holding(db, session.id) is True
    with pytest.raises(DesktopError, match="already learning"):
        game_practice.start(db, user.id, task="make a furnace")
    assert "stopped" in game_practice.stop(db, user.id).lower()
    assert game_practice.holding(db, session.id) is False


def test_without_a_pack_session_nothing_starts(db, user):
    with pytest.raises(DesktopError, match="not playing"):
        game_practice.start(db, user.id, task="cross water")


def test_the_worker_runs_an_attempt_saves_every_try_and_tells_her(db, user, monkeypatch):
    session = _pack_session(db, user)
    game_practice.start(db, user.id, task="get 3 oak logs")
    noticed = []
    monkeypatch.setattr(desktop_sessions, "driver_noticed",
                        lambda db_, sid, *, kind, text: noticed.append((sid, kind, text)))
    pack = FakePack([(_program(), _state(logs=1)), (_program(), _state(logs=3))])
    model = ScriptedModel(_written(), {"success": False, "reason": "1 log"}, _written(code="// better"),
                          {"success": True, "reason": "3 logs"})
    assert game_practice.run_once(pack=pack, model=model) is True
    db.expire_all()
    attempt = db.query(GameSkillAttempt).one()
    assert attempt.status == "learned" and attempt.code == "// better"
    assert [t["n"] for t in attempt.tries] == [1, 2] and attempt.tries[0]["code"].startswith("await skills.mine")
    assert noticed == [(session.id, "learning", 'learned how to "get 3 oak logs" (2 tries); your hands have it '
                                                'now as the skill learned_get_3_oak_logs')]
    # H2: it's one of her moves now, for her hands and in her summary, in any session
    move = db.query(GameLearnedSkill).one()
    assert (move.name, move.code, move.enabled, move.attempt_id) == ("learned_get_3_oak_logs", "// better", True,
                                                                     attempt.id)
    assert game_skills.for_session(db, session.id)[0]["name"] == "learned_get_3_oak_logs"
    assert game_skills.summary_lines(db, user.id, "minecraft")[1] == '- learned_get_3_oak_logs: "get 3 oak logs"'
    assert game_practice.holding(db, session.id) is False
    assert 'You learned how to "get 3 oak logs"' in game_practice.summary_lines(db, session, 0)[0]
    assert game_practice.run_once(pack=pack, model=model) is False  # nothing queued


def test_a_paused_session_waits_and_an_ended_one_drops_the_attempt(db, user):
    session = _pack_session(db, user, state="paused")
    game_practice.start(db, user.id, task="cross water")
    assert game_practice.run_once(pack=FakePack([]), model=ScriptedModel()) is False  # waits for play
    session.state = "ended"
    db.commit()
    game_practice.run_once(pack=FakePack([]), model=ScriptedModel())
    db.expire_all()
    assert db.query(GameSkillAttempt).one().status == "stopped"


def test_a_running_attempt_cut_off_by_a_restart_is_closed(db, user):
    session = _pack_session(db, user)
    db.add(GameSkillAttempt(user_id=user.id, session_id=session.id, game="minecraft", task="x", origin="user",
                            status="running"))
    db.commit()
    db.execute(GameSkillAttempt.__table__.update().values(
        updated_at=game_practice._now() - timedelta(hours=1)))
    db.commit()
    game_practice.run_once(pack=FakePack([]), model=ScriptedModel())
    db.expire_all()
    assert db.query(GameSkillAttempt).one().status == "failed"


def _learned(db, user, session, task, code, status="learned") -> GameSkillAttempt:
    attempt = GameSkillAttempt(user_id=user.id, session_id=session.id, game="minecraft", task=task, origin="user",
                               status=status, code=code)
    db.add(attempt)
    db.flush()
    game_skills.save_learned(db, attempt)
    db.commit()
    return attempt


def test_learning_a_move_again_replaces_it_and_switches_it_back_on(db, user):
    session = _pack_session(db, user)
    _learned(db, user, session, "pillar up by placing blocks", "// one")
    move = db.query(GameLearnedSkill).one()
    assert game_skills.set_enabled(db, user.id, move.id, False)
    assert game_skills.for_session(db, session.id) == []  # off: her hands don't have it
    assert game_skills.summary_lines(db, user.id, "minecraft") == []
    second = _learned(db, user, session, "Pillar up, by placing blocks!", "// two")
    db.expire_all()
    move = db.query(GameLearnedSkill).one()
    assert (move.code, move.enabled, move.attempt_id) == ("// two", True, second.id)
    _learned(db, user, session, "cross water", "// failed", status="failed")  # only what worked is kept
    assert db.query(GameLearnedSkill).count() == 1


def test_a_use_is_counted_and_only_the_owner_can_change_a_move(db, user):
    session = _pack_session(db, user)
    _learned(db, user, session, "pillar up", "// p")
    game_skills.record_use(db, session.id, "learned_pillar", "done")
    game_skills.record_use(db, session.id, "learned_pillar", "failed", "no blocks")
    move = db.query(GameLearnedSkill).one()
    db.refresh(move)
    assert (move.uses, move.successes) == (2, 1) and move.last_used_at is not None
    assert "(used 2, worked 1)" in game_skills.summary_lines(db, user.id, "minecraft")[1]
    other = User(name="other", email=f"o-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    db.add(other)
    db.commit()
    assert not game_skills.set_enabled(db, other.id, move.id, False)
    assert not game_skills.delete(db, other.id, move.id)
    assert game_skills.listing(db, other.id) == []
    assert game_skills.delete(db, user.id, move.id)
    assert db.query(GameLearnedSkill).count() == 0


def test_reset_game_scope_wipes_attempts(db, user):
    session = _pack_session(db, user)
    game_practice.start(db, user.id, task="cross water")
    _learned(db, user, session, "pillar up", "// p")
    memory_reset.reset(db, user_ids=[user.id], scopes=["game"])
    assert db.query(GameSkillAttempt).count() == 0
    assert db.query(GameLearnedSkill).count() == 0
    assert db.get(DesktopSession, session.id) is not None


def test_cant_be_done_said_twice_in_a_row_ends_learning_without_the_critic():
    """Seen live: "attack a rabbit", tries 3 and 4 only said again that no skill could. Once isn't
    enough: climbing out of stone was called impossible on try 1, though digging gives the blocks."""
    impossible = {"done": False, "impossible": True, "missing": "a way to fly"}
    pack = FakePack([(_program(calls=[], value=impossible), _state()), (_program(calls=[], value=impossible), _state())])
    code = "return { done: false, impossible: true, missing: 'a way to fly' }"
    writer = Recording(_written(code=code), _written(code=code))
    critic = Recording()
    result, saved = _learner(pack, writer, critic)
    result = result.run()
    assert result.status == "failed" and "missing a way to fly" in result.reason
    assert len(result.tries) == 2 and critic.prompts == []
    assert "Be sure: think again" in writer.prompts[1]
    assert saved[-1][1]["verdict"]["reason"] == "can't be done with what I have: missing a way to fly"


def test_cant_be_done_once_then_a_try_that_works_is_learned():
    impossible = {"done": False, "impossible": True, "missing": "blocks"}
    pack = FakePack([(_program(calls=[], value=impossible), _state()), (_program(), _state(logs=3))])
    writer = Recording(_written(), _written())
    critic = Recording({"success": True, "reason": "oak_log 0 -> 3"})
    result = _learner(pack, writer, critic)[0].run()
    assert result.status == "learned" and len(result.tries) == 2


def test_single_actions_and_who_is_around_reach_the_next_try_and_the_critic():
    before = {**_state(), "nearby": [{"id": 7, "label": "rabbit", "kind": "animal"},
                                     {"id": 3, "label": "tearDrop12", "kind": "player"}]}
    after = {**_state(), "nearby": [{"id": 3, "label": "tearDrop12", "kind": "player"},
                                    {"id": 9, "label": "rabbit_hide (dropped)", "kind": "other"}]}
    actions = {"total": 4, "by": {"attack": 4}, "last": [
        {"action": "attack", "args": [7], "status": "done", "reason": None},
        {"action": "attack", "args": [7], "status": "failed", "reason": "out of reach: rabbit is 4.1 m away"}]}
    pack = FakePack([({**_program(calls=[]), "actions": actions}, after), (_program(calls=[]), after)])
    pack.state_now = before
    writer = Recording(_written(code="await act.attack(7)"), _written(code="await act.attack(7)"))
    critic = Recording({"success": False, "reason": "no"}, {"success": True, "reason": "rabbit 1 -> 0"})
    _learner(pack, writer, critic)[0].run()
    assert "Single actions: 4 (attack x4)" in critic.prompts[0]
    assert "act.attack(7) -> failed: out of reach: rabbit is 4.1 m away" in critic.prompts[0]
    assert "rabbit 1 -> 0 (-1)" in critic.prompts[0]  # dropped items aren't counted as who's around
    assert "Single actions: 4 (attack x4)" in writer.prompts[1]


def test_the_writer_is_told_about_single_actions_and_how_to_find_a_mob():
    prompt = writer_prompt(MANIFEST)
    for name in ("act.attack(id)", "act.dig(x, y, z)", "act.place(x, y, z, item?)", "act.look(x, y, z)",
                 "act.equip(item, slot?)", "act.jump()"):
        assert name in prompt
    assert '"animal"' in prompt and "find a mob by its label" in prompt
    assert "impossible: true" in prompt
