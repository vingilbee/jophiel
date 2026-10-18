"""Explore and learn (stage H3): her next challenge is picked from her game and her
record, the plan she makes of it is the challenge, and how it ends is counted on plan
changes. A goal from the user replaces it and stops a lesson she started herself."""
from __future__ import annotations

import pytest

from app.ai.desktop import curriculum
from app.ai.desktop.driver import ModelReply
from app.ai.mind.tick import TickInput, news
from app.db.models import DesktopSession, GameChallenge, GameSkillAttempt
from app.services import desktop_game, game_challenges, game_practice
from app.services import desktop_sessions as svc
from app.services.desktop_sessions import DesktopError
from tests.cognitive_shadow_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_game_practice import _pack_session, db, user  # noqa: F401  (fixtures)

PICK = {"challenge": "make a stone pickaxe", "why": "I've only got a wooden one, and there's stone right here.",
        "aim": "discover", "check_kind": "has", "check_what": "stone_pickaxe", "check_count": 1}


class Model:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls: list[tuple[str, str]] = []

    def step(self, system, text, jpeg, schema):
        self.calls.append((system, text))
        return ModelReply(self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0], 10)


# --------------------------------------------------------------------------- #
# The pick (pure)
# --------------------------------------------------------------------------- #


def test_the_pick_reads_her_game_and_her_record_and_gives_a_counted_end():
    model = Model(PICK)
    picked = curriculum.pick(model, "Inventory: wooden_pickaxe, 3 cobblestone", done=["make a crafting table"],
                             failed=["cross the river (you set another goal: follow the user)"],
                             offered=["find a village"])
    assert picked == curriculum.Challenge("make a stone pickaxe", PICK["why"], "discover",
                                          {"kind": "has", "what": "stone_pickaxe", "count": 1})
    system, text = model.calls[0]
    assert "Just beyond what you can do now" in system and "something for the two of you" in system
    for part in ("Inventory: wooden_pickaxe", "- make a crafting table", "- cross the river (you set another goal",
                 "- find a village"):
        assert part in text


def test_a_check_is_kept_only_when_code_can_count_it():
    assert curriculum.check_from({"check_kind": "none"}) is None
    assert curriculum.check_from({"check_kind": "gained", "check_what": "", "check_count": 3}) is None
    assert curriculum.check_from({"check_kind": "killed", "check_what": "zombie", "check_count": 0}) is None
    assert curriculum.check_from({"check_kind": "peak", "check_what": "x", "check_count": 9}) == {"kind": "peak"}
    assert curriculum.pick(Model({**PICK, "challenge": "  "}), "", done=[], failed=[], offered=[]) is None


def test_a_new_challenge_or_its_end_is_news_for_her_mind():
    state = {"text": "Columbina at (0, 64, 0).", "health": 20}
    before = TickInput(at=0, state=state)
    on = TickInput(at=1, state=state, challenge='your challenge: "make a stone pickaxe"')
    done = TickInput(at=2, state=state, challenge='your challenge "make a stone pickaxe": done!')
    assert news(on, before) == ['your challenge: "make a stone pickaxe"']
    assert news(on, on) == []
    assert news(done, on) == ['your challenge "make a stone pickaxe": done!']


# --------------------------------------------------------------------------- #
# The record (real database)
# --------------------------------------------------------------------------- #


def _replan(db, session, goal, *, source="columbina_planning_turn", challenge=False):
    from app.schemas.desktop import PlanReplace

    svc.replace_plan(db, session.owner_user_id, session.id, PlanReplace(
        expected_plan_revision=session.plan_revision, source=source, goal=goal,
        steps=[{"do": goal, "done_when": "done"}]))
    game_challenges.on_plan_set(db, session, source=source, goal=goal, challenge=challenge)
    db.commit()


def test_a_proposal_becomes_her_challenge_and_is_done_when_every_step_is(db, user):
    session = _pack_session(db, user)
    row = game_challenges.propose(db, session, "her game", model=Model(PICK))
    assert (row.status, row.challenge, row.check) == ("proposed", "make a stone pickaxe",
                                                      {"kind": "has", "what": "stone_pickaxe", "count": 1})
    lines = game_challenges.proposal_lines(row)
    assert 'Your next challenge (you picked it just now, exploring): "make a stone pickaxe"' in lines[0]
    assert "It counts as done when: has 1 stone_pickaxe." in lines[0]
    assert "set_game_plan(challenge=true)" in lines[1]
    _replan(db, session, "make a stone pickaxe", challenge=True)
    db.refresh(row)
    assert (row.status, row.plan_revision) == ("active", session.plan_revision)
    assert game_challenges.propose(db, session, "x", model=Model(PICK)) is None  # she's on one
    assert game_challenges.tick_line(db, session.id) == 'your challenge: "make a stone pickaxe"'
    assert 'Your challenge now (you picked it): "make a stone pickaxe"' in " ".join(
        game_challenges.summary_lines(db, session))
    game_challenges.on_learned(db, session.id, "learned_place_block_under")
    game_challenges.on_goal_done(db, session, session.plan_revision - 1)  # another plan: not this one
    db.commit()
    db.refresh(row)
    assert row.status == "active"
    game_challenges.on_goal_done(db, session, session.plan_revision)
    db.commit()
    db.refresh(row)
    assert (row.status, row.result) == ("done", "every step done; learned on the way: learned_place_block_under")
    assert game_challenges.tick_line(db, session.id) == 'your challenge "make a stone pickaxe": done!'
    assert "Challenges you've done: make a stone pickaxe." in game_challenges.summary_lines(db, session)


def test_the_users_goal_replaces_it_and_her_own_other_goal_gives_it_up(db, user):
    session = _pack_session(db, user)
    _replan(db, session, "get iron", challenge=True)  # her own idea, no proposal: still a challenge
    _replan(db, session, "follow me", source="columbina_user_turn")
    _replan(db, session, "make a furnace", challenge=True)
    _replan(db, session, "sleep", source="columbina_planning_turn")
    rows = {r.challenge: r for r in db.query(GameChallenge)}
    assert (rows["get iron"].status, rows["get iron"].result) == ("replaced", "the user wanted something else: follow me")
    assert (rows["make a furnace"].status, rows["make a furnace"].result) == ("given_up", "you set another goal: sleep")
    done, failed, _ = game_challenges.record(db, session)
    assert done == [] and failed == ["make a furnace (you set another goal: sleep)"]
    assert [c["status"] for c in game_challenges.listing(db, user.id)] == ["given_up", "replaced"]


def test_proposals_not_taken_are_kept_aside_and_old_sessions_expire(db, user):
    first = _pack_session(db, user)
    _replan(db, first, "get iron", challenge=True)
    first.state = "ended"
    db.commit()
    session = _pack_session(db, user)
    model = Model({**PICK, "challenge": "find a village"}, PICK)
    game_challenges.propose(db, session, "game", model=model)
    game_challenges.propose(db, session, "game", model=model)  # the first one wasn't taken
    rows = {r.challenge: r for r in db.query(GameChallenge)}
    assert (rows["get iron"].status, rows["get iron"].result) == ("expired", "the session ended")
    assert (rows["find a village"].status, rows["find a village"].result) == ("expired", "not taken")
    assert rows["make a stone pickaxe"].status == "proposed"
    assert "Just offered (not taken):\n- find a village" in model.calls[1][1]
    assert [c["challenge"] for c in game_challenges.listing(db, user.id)] == ["get iron"]  # proposals not listed


def test_nothing_is_picked_unless_she_is_exploring(db, user):
    session = _pack_session(db, user)
    model = Model(PICK)
    session.explore = False
    db.commit()
    assert game_challenges.propose(db, session, "game", model=model) is None
    session.explore, session.autonomy = True, "stop"  # keep playing off: she stops after a goal anyway
    db.commit()
    assert game_challenges.propose(db, session, "game", model=model) is None
    assert model.calls == [] and game_challenges.summary_lines(db, session) == []
    assert "Exploring is on" in game_challenges.set_explore(db, user.id, enabled=True)
    db.refresh(session)
    assert (session.explore, session.autonomy) == (True, "continue")
    assert game_challenges.exploring(session)
    assert svc.driver_view(db, session.id).exploring


def test_a_goal_from_the_user_stops_a_lesson_she_started_herself_only(db, user):
    session = _pack_session(db, user)
    own = GameSkillAttempt(user_id=user.id, session_id=session.id, game="minecraft", task="pillar up",
                           origin="self", status="running")
    db.add(own)
    db.commit()
    assert game_practice.stop_own(db, user.id) == ' (Stopped teaching yourself "pillar up": the user\'s goal comes first.)'
    db.refresh(own)
    assert own.stop_requested
    own.status = "stopped"
    asked = GameSkillAttempt(user_id=user.id, session_id=session.id, game="minecraft", task="cross water",
                             origin="user", status="running")
    db.add(asked)
    db.commit()
    assert game_practice.stop_own(db, user.id) == ""  # they asked for this one
    db.refresh(asked)
    assert not asked.stop_requested


def test_her_own_lesson_lifts_only_her_own_pause(db, user, monkeypatch):
    lifted = []
    monkeypatch.setattr("app.services.desktop_game._unpause", lambda db, uid, source: lifted.append(source) or "")
    _pack_session(db, user, state="paused")
    game_practice.start(db, user.id, task="pillar up", origin="self")
    assert lifted == ["columbina_planning_turn"]
    attempt = db.query(GameSkillAttempt).one()
    assert attempt.origin == "self"


def test_exploring_lifts_the_cap_on_goals_she_picks(db, user, monkeypatch):
    from app.core.config import get_settings

    capped = get_settings().model_copy(update={"desktop_max_auto_goals": 0, "desktop_agent_enabled": True})
    monkeypatch.setattr(desktop_game, "get_settings", lambda: capped)
    session = _pack_session(db, user)
    session.plan = {"goal": "start", "steps": [{"id": "s1", "do": "start", "done_when": "x", "state": "active"}]}
    session.plan_revision = 1
    db.commit()
    reply = desktop_game.set_plan(db, user.id, goal="make a stone pickaxe", source="columbina_planning_turn",
                                  challenge=True)
    assert reply.startswith("Done; you're now on: make a stone pickaxe.")
    assert 'This is your challenge now: "make a stone pickaxe".' in reply
    session = db.get(DesktopSession, session.id)
    session.explore = False
    db.commit()
    with pytest.raises(DesktopError) as excinfo:  # not exploring: the cap is back
        desktop_game.set_plan(db, user.id, goal="something else", source="columbina_planning_turn")
    assert excinfo.value.code == "limit_reached"


def test_the_turn_after_a_goal_gets_her_next_challenge(database, db, user, monkeypatch):
    from app.services import game_events

    session = _pack_session(db, user)
    monkeypatch.setattr(game_events, "SessionLocal", database)
    monkeypatch.setattr(desktop_game, "describe", lambda db, uid: desktop_game.GameContext("her game", tools=True))
    picks = []

    def propose(db, s, summary):
        picks.append(summary)
        return GameChallenge(challenge="make a furnace", why="I have 8 cobblestone.", aim="discover")
    monkeypatch.setattr(game_challenges, "propose", propose)
    *_, game, _ = game_events._prepare(user.id, session.id, ["goal_done"], None)
    assert picks == ["her game"]
    assert game.summary.startswith("her game\nYour next challenge (you picked it just now, exploring): \"make a furnace\"")
    *_, game, _ = game_events._prepare(user.id, session.id, ["stuck: no skill for: x"], None)
    assert picks == ["her game"] and game.summary == "her game"  # only after a goal

