"""Learning step 4, predictions and bounded game trials (docs/learning-system/04-implementation.md).

Scripted models only: the proposer, Student 2 and the assessor are replaced by scripted answers and the game pack
by a fake, so these tests show what code guarantees: what a proposal must hold before cognition sees it, that only
cognition's choice starts a trial, that preparation and the bounds are kept, that expectations are never rewritten,
that measurements and not the model's words decide what can be concluded, and what reaches each owner. Whether
Luna proposes and assesses well needs the real game (``scripts/trial_eval.py``). Pure checks run without a
database; the rest need real PostgreSQL (TEST_DATABASE_URL).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.ai.desktop.practice import PracticeDeps, PracticeRun, TrialSpec
from app.ai.learning import trials as T
from app.ai.state import student_two as S
from app.core.config import get_settings
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_skill_driver import MANIFEST, ScriptedModel

DEST = {"x": 2, "y": 66, "z": 0}
START = {"x": 0.5, "y": 64.0, "z": 0.5}


def state(position=START, *, dirt=0, health=20) -> dict:
    inventory = [{"name": "dirt", "count": dirt}] if dirt else []
    return {"text": f"Columbina at {position}. A ledge two blocks up to the east.", "position": dict(position),
            "inventory": inventory, "health": health, "food": 20}


def raw_proposal(**over) -> dict:
    base = {
        "decision": "propose", "why": "", "problem": "she can't get up the ledge",
        "hypothesis": "Jumping and placing a block under me gets me up a two-block ledge",
        "materially_different": False, "destination": dict(DEST), "radius": 1.0,
        "alternatives": [
            {"key": "a1", "action": "jump-place a pillar", "approach": "jump, place dirt under feet, twice",
             "predictions": [{"measure": "reached", "expect": "true", "value": 0, "high": 0},
                             {"measure": "blocks_placed", "expect": "at_most", "value": 4, "high": 0}],
             "expected": "up in one try with two blocks", "uncertainty": "medium", "seconds": 20, "blocks": 2,
             "risks": "falling", "prerequisites": [{"item": "dirt", "count": 4}]},
            {"key": "a2", "action": "dig steps into the ledge", "approach": "dig a step, walk up, dig again",
             "predictions": [{"measure": "reached", "expect": "true", "value": 0, "high": 0}],
             "expected": "up in two tries", "uncertainty": "high", "seconds": 40, "blocks": 0, "risks": "",
             "prerequisites": []},
        ],
        "recommendation": "a1", "recommendation_why": "cheapest and tells most", "window_seconds": 0,
        "required_evidence": ["reached"], "limits": {"attempts": 9, "seconds": 9999, "blocks": 2}, "cites": ["x1"],
    }
    base.update(over)
    return base


def bundle(position=START, **over) -> T.Bundle:
    b = T.Bundle(T.SCENARIOS["climb_obstacle"], T.Limits(3, 300, 8), 20, 24.0, "she can't get up the ledge",
                 dict(position) if position else None, "state", ["- mine: Break blocks."],
                 [T.Line("x1", "struggling: 4 failed tries", "experience:1")])
    for k, v in over.items():
        setattr(b, k, v)
    return b


# --------------------------------------------------------------------------- #
# Pure: what a proposal must hold
# --------------------------------------------------------------------------- #


def test_a_valid_proposal_is_kept_whole_and_its_limits_can_only_come_down():
    checked = T.check(raw_proposal(), bundle())
    p = checked.proposal
    assert p is not None and checked.why_none is None
    assert [a["key"] for a in p["alternatives"]] == ["a1", "a2"] and p["recommendation"] == "a1"
    assert p["limits"] == {"attempts": 3, "seconds": 300, "blocks": 2}  # asked for more attempts and time: not given
    assert p["start"] == START and p["required_evidence"] == ["reached"]
    views = T.proposal_views(p)
    assert len(views) == 2 and "Learning recommends this one" in views[0]["purpose"]
    assert "predicted:" in views[1]["purpose"] and "high uncertainty" in views[1]["purpose"]


@pytest.mark.parametrize("over, why", [
    ({"decision": "none", "why": "not a climbing problem"}, "not a climbing problem"),
    ({"cites": ["x9"]}, "rests on nothing"),
    ({"destination": {"x": 2, "y": 75, "z": 0}}, "isn't a small obstacle"),
    ({"destination": {"x": 90, "y": 66, "z": 0}}, "isn't a small obstacle"),
    ({"recommendation": "a7"}, "recommendation"),
])
def test_a_proposal_that_is_not_valid_is_not_repaired(over, why):
    checked = T.check(raw_proposal(**over), bundle())
    assert checked.proposal is None and why in checked.why_none


def test_an_alternative_without_a_prediction_of_getting_up_is_dropped_and_one_left_is_not_enough():
    raw = raw_proposal()
    raw["alternatives"][1]["predictions"] = [{"measure": "test_seconds", "expect": "at_most", "value": 30, "high": 0}]
    checked = T.check(raw, bundle())
    assert checked.proposal is None and "fewer than 2" in checked.why_none
    assert any("reached" in d["why"] for d in checked.dropped)
    raw = raw_proposal()
    raw["alternatives"][0]["action"] = "x" * 400  # nothing is cut to fit
    assert T.check(raw, bundle()).proposal is None


def test_unknown_position_means_no_proposal():
    assert T.check(raw_proposal(), bundle(position=None)).proposal is None


# --------------------------------------------------------------------------- #
# Pure: measured, never inferred from an absence
# --------------------------------------------------------------------------- #


def proposal() -> dict:
    return T.check(raw_proposal(), bundle()).proposal


def tries(*ends, placed=2) -> list[dict]:
    return [{"n": i, "program": {"status": "done", "placed": placed}, "facts": {"from": [0, 64, 0], "to": end}}
            for i, end in enumerate(ends, 1)]


def test_measurements_come_from_counted_positions_and_the_packs_own_block_count():
    m = T.measure(proposal(), start=state(), tries=tries([2.5, 66, 0.5]), end=state({"x": 2.5, "y": 66, "z": 0.5}),
                  test_seconds=12.3, started=True)
    assert m.values == {"reached": True, "climbed_m": 2.0, "blocks_placed": 2, "attempts_used": 1,
                        "test_seconds": 12.3, "health_lost": 0.0}
    assert "at the destination" in m.sources["reached"]
    never = T.measure(proposal(), start=None, tries=[], end=None, test_seconds=None, started=False)
    assert all(v is None for v in never.values.values()) and "never started" in never.notes[0]


def test_an_interruption_never_makes_a_prediction_not_met_but_a_measured_excess_does():
    p = proposal()
    preds = T.alternative(p, "a1")["predictions"]
    values = {"reached": False, "blocks_placed": 3}
    checks = T.evaluate(preds, values, complete=False, started=True)
    assert [c["status"] for c in checks] == ["unknown", "unknown"]
    assert T.allowed(checks, [], started=True) == {"unresolved"}
    checks = T.evaluate(preds, {"reached": False, "blocks_placed": 6}, complete=False, started=True)
    assert [c["status"] for c in checks] == ["unknown", "not_met"]  # already past at_most 4
    assert T.allowed(checks, [], started=True) == {"unresolved", "contradicted"}
    done = T.evaluate(preds, {"reached": True, "blocks_placed": 2}, complete=True, started=True)
    assert [c["status"] for c in done] == ["met", "met"]
    assert T.allowed(done, [], started=True) == {"unresolved", "supported"}
    assert T.allowed(done, ["reached"], started=True) == {"unresolved"}  # required evidence missing
    assert T.allowed(done, [], started=False) == {"unresolved"}


def evidence(values, *, complete=True, started=True, ending="completed") -> T.TrialEvidence:
    p = proposal()
    alt = T.alternative(p, "a1")
    checks = T.evaluate(alt["predictions"], values, complete=complete, started=started)
    measured = T.Measured(values, {}, [])
    return T.TrialEvidence(p, alt, {"reason": "try it"}, ending, None, {}, measured, checks,
                           T.missing(p, values), started)


def test_the_assessment_is_kept_only_as_far_as_the_measurements_allow():
    ev = evidence({"reached": False, "blocks_placed": 2}, complete=False, ending="interrupted")
    result, dropped = T.check_assessment({
        "hypothesis": "contradicted", "why": "she fell", "failure_of": "technique", "applicability": "ledges",
        "exceptions": [], "cites": ["c1", "zz"],
        "explanations": [{"text": "the approach can't work", "status": "supported", "cites": ["e9"]}],
        "lesson": {"propose": True, "key": "no_pillars", "situation": "a ledge", "advice": "don't", "level": "skill"}},
        ev)
    assert result["hypothesis"] == "unresolved" and result["lesson"] is None
    assert result["failure_of"] == "interrupted" and result["explanations"][0]["status"] == "plausible"
    assert {d["what"] for d in dropped} >= {"hypothesis contradicted", "lesson", "reading cite zz"}
    ok = evidence({"reached": True, "blocks_placed": 2})
    result, _ = T.check_assessment({
        "hypothesis": "supported", "why": "c1 and c2 met", "failure_of": "none", "applicability": "2-block ledges",
        "exceptions": ["not on gravel"], "cites": ["c1", "c2", "m.reached"],
        "explanations": [{"text": "the technique works", "status": "supported", "cites": ["c1"]},
                         {"text": "the ledge was low", "status": "plausible", "cites": []}],
        "lesson": {"propose": True, "key": "pillar up", "situation": "a 2-block ledge", "advice": "jump-place",
                   "level": "skill"}}, ok)
    assert result["hypothesis"] == "supported" and result["lesson"]["key"] == "pillar_up"
    assert len(result["explanations"]) == 2  # competing explanations stay
    never, _ = T.check_assessment(None, evidence({}, started=False, ending="not_started"))
    assert never["hypothesis"] == "unresolved" and never["failure_of"] == "preparation" and never["by"] == "code"


# --------------------------------------------------------------------------- #
# Her body: a trial's run (fake pack, scripted writer)
# --------------------------------------------------------------------------- #


class Pack:
    """Programs run as scripted: (result, state after) per try; the state before can be set."""

    def __init__(self, results=(), now=None):
        self.results = list(results)
        self.state_now = now or state(dirt=4)
        self.limits: list[dict | None] = []
        self.program = None
        self.stops = 0

    def manifest(self):
        return MANIFEST

    def state(self):
        return self.state_now

    def block_census(self, x, y, z, radius=10):
        return {"counts": {}}

    def current(self):
        return None

    def stop(self):
        self.stops += 1

    def start_program(self, code, *, seconds=None, limits=None):
        self.limits.append(limits)
        self._final, self._after = self.results.pop(0)
        self.program = {"id": str(uuid.uuid4()), "status": "running"}
        return dict(self.program)

    def get_program(self, program_id, *, wait=0):
        if self.program["status"] == "running":
            self.program.update(self._final)
            self.state_now = self._after
        return dict(self.program)


def written(keeps=True, other=""):
    return {"approach": "jump-place twice", "code": "await act.jump()", "keeps_approach": keeps,
            "other_approach": other}


def run(pack, writer, spec, stop=lambda: None):
    deps = PracticeDeps(pack=pack, model=writer, critic=ScriptedModel(), should_stop=stop, save=lambda t: None,
                        now=lambda: 0.0, sleep=lambda s: None)
    return PracticeRun("get up", deps, max_tries=4, trial=spec).run()


def spec(**over) -> TrialSpec:
    base = dict(approach="jump-place twice", destination=DEST, radius=1.0, attempts=3, seconds=300, blocks=8)
    base.update(over)
    return TrialSpec(**base)


def test_a_trial_try_is_judged_by_code_and_the_block_budget_goes_to_the_pack():
    at_top = state({"x": 2.5, "y": 66, "z": 0.5}, dirt=2)
    pack = Pack([({"status": "done", "placed": 1, "calls": []}, state(dirt=3)),
                 ({"status": "done", "placed": 2, "value": {"done": False}, "calls": []}, at_top)])
    result = run(pack, ScriptedModel(written(), written()), spec())
    assert (result.status, result.ending) == ("learned", "completed")
    assert pack.limits == [{"place": 8}, {"place": 7}]  # what's left of the budget, each program
    assert result.tries[1]["verdict"]["reason"].startswith("standing at the destination")
    assert result.start_state["position"] == START


def test_not_ready_right_before_the_test_means_it_never_started():
    result = run(Pack(now=state(dirt=0)), ScriptedModel(), spec(ready=lambda s: "she still needs 4 dirt"))
    assert (result.status, result.ending) == ("stopped", "not_started") and result.tries == []


def test_another_approach_runs_nothing_and_needs_its_own_prediction():
    pack = Pack()
    result = run(pack, ScriptedModel(written(keeps=False, other="walk around it")), spec())
    assert (result.ending, result.other_approach) == ("approach_changed", "walk around it")
    assert pack.limits == []  # nothing ran


def test_reaching_the_block_limit_ends_the_trial():
    pack = Pack([({"status": "limit", "reason": "placed 2 blocks: the limit", "placed": 2, "calls": []}, state())])
    result = run(pack, ScriptedModel(written()), spec(blocks=2))
    assert (result.status, result.ending) == ("failed", "limit")


# --------------------------------------------------------------------------- #
# Cognition's view: each alternative is a proposal [P*]
# --------------------------------------------------------------------------- #


def test_student_two_reads_the_alternatives_and_must_say_what_happens_to_the_goal_it_competes_with():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    views = T.proposal_views(proposal())
    live = S.Live(trigger="component_proposal", domain="minecraft", now=now, lines=(), new_message="",
                  goals=(S.Goal("g-1", "build a house", "user", "active", "the house stands", now),),
                  activity=S.Activity("s", "running", "Playing Minecraft together"),
                  proposals=tuple(S.Proposal(f"trial:t:1:{v['key']}", "curriculum", v["goal"], v["purpose"], "g-1")
                                  for v in views))
    situation = S.snapshot(live)
    text = S.render(situation, "memory-v1")
    assert "PROPOSALS (not accepted yet):" in text and "[P2] from her curriculum: try dig steps" in text
    assert situation.refs["P1"] == "trial:t:1:a1"
    pursue = dict(S.DEFAULTS) | {"work": "pursue", "target": "P2", "intention": "x"}
    assert any("G1" in p for p in S.check(pursue, situation))  # competing with the goal: say what happens to it
    assert S.check(pursue | {"other_goal": "G1", "other_goal_handling": "keep_alongside"}, situation) == []
    assert S.check(dict(S.DEFAULTS) | {"work": "defer_proposal", "target": "P1"}, situation) == []


# --------------------------------------------------------------------------- #
# The lifecycle (real PostgreSQL)
# --------------------------------------------------------------------------- #

from app.db.models import (  # noqa: E402
    AgentEventJob, DesktopEnvironment, DesktopSession, GameExperience, GameLearnedSkill, GameLesson, GameSkillAttempt,
    LearningTrial, LearningTrialAssessment, LearningTrialEvent, LearningTrialRevision, LearningTrialTrigger, User,
    WorkingItem,
)
from app.services import game_practice, learning_trials as LT, memory_manager, student_two as svc  # noqa: E402
from tests.db_support import columbina_core_memory, database  # noqa: E402,F401  (fixture)


@pytest.fixture
def db(database, monkeypatch):
    settings = get_settings()
    for key, value in {"desktop_agent_enabled": True, "learning_trials_enabled": True, "redis_url": "",
                       "lessons_supply_enabled": False}.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr("app.services.game_pack.stop_driver_skill", lambda client=None: False)
    monkeypatch.setattr("app.services.desktop_sessions.driver_noticed", lambda *a, **k: None)
    with database() as session:
        session.add(columbina_core_memory())
        session.commit()
        yield session


@pytest.fixture
def game(db):
    user = User(name="vincent", email=f"v-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    db.add(user)
    db.flush()
    env = DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                             allowed_profile_ids=["minecraft_pack"], status="online")
    db.add(env)
    db.flush()
    session = DesktopSession(owner_user_id=user.id, environment_id=env.id, profile_id="minecraft_pack",
                             profile_revision=1, state="running", control_owner="gameplay", max_duration_seconds=3600,
                             plan={"goal": "build a house", "steps": [
                                 {"id": "s1", "do": "get up the ledge", "done_when": "on top", "state": "active"}]})
    db.add(session)
    db.flush()
    goal = memory_manager.set_goal(db, user.id, "build a house", context_id=memory_manager.game_context(db, session).id)
    session.goal_item_id = goal.id
    db.commit()
    return session


def scripted_proposer(raw):
    seen = []

    def call(text):
        seen.append(text)
        return T.Answer(raw() if callable(raw) else raw, 1.0)

    call.seen = seen
    return call


def scripted_decision(**fields):
    def decide(live, capture=None):
        situation = S.snapshot(live)
        S.render(situation, "memory-v1")
        d = dict(S.DEFAULTS) | {"intention": "worth a try"} | fields
        problems = S.check(d, situation)
        return svc.Outcome("invalid" if problems else "ok", situation=situation, decision=None if problems else d,
                           problems=problems, decision_id=str(capture.attempt_id))
    return decide


PURSUE_A2 = dict(work="pursue", target="P2", other_goal="G1", other_goal_handling="keep_alongside",
                 intention="digging needs nothing I lack")


def assessor(raw):
    calls = []

    def call(text):
        calls.append(text)
        return T.Answer(raw, 1.0)

    call.calls = calls
    return call


def due(db) -> None:
    """Its next check is now (the worker would come back to it a few seconds later)."""
    db.execute(LearningTrial.__table__.update().values(next_check_at=game_practice._now() - timedelta(seconds=1)))
    db.commit()


def trial_of(db) -> LearningTrial:
    db.expire_all()
    return db.scalar(select(LearningTrial).order_by(LearningTrial.created_at.desc()).limit(1))


def propose(db, game, pack, raw=None):
    assert LT.consider(db, game, kind="struggling", text='4 failed tries at "get up the ledge"')
    db.commit()
    proposer = scripted_proposer(raw or raw_proposal())
    assert LT.investigate_once(proposer=proposer, pack=pack)
    return trial_of(db), proposer


def test_from_failure_to_assessment_only_cognitions_choice_starts_it_and_each_owner_gets_its_part(db, game):
    pack = Pack(now=state(dirt=0))
    trial, _ = propose(db, game, pack)
    # Persisted, whole, before cognition saw it; her hypothesis kept by memory.
    assert (trial.status, trial.revision) == ("proposed", 1)
    revision = db.scalar(select(LearningTrialRevision).where(LearningTrialRevision.trial_id == trial.id))
    assert revision.proposal["hypothesis"].startswith("Jumping") and len(revision.proposal["alternatives"]) == 2
    hypothesis = db.get(WorkingItem, trial.hypothesis_item_id)
    assert (hypothesis.kind, hypothesis.status, hypothesis.owner) == ("hypothesis", "open", "columbina")
    # Nothing runs without cognition: advancing a proposal does nothing.
    assert LT.advance_once(pack=pack) == 0 and db.scalar(select(GameSkillAttempt.id)) is None

    # Cognition picks the alternative learning did NOT recommend; the selection is its own.
    assert LT.present_once(decide=scripted_decision(**PURSUE_A2))
    trial = trial_of(db)
    assert trial.status == "selected" and trial.selected_alternative == "a2"
    assert trial.selection["recommended"] is False and trial.selection["reason"] == "digging needs nothing I lack"
    assert trial.limits == {"attempts": 3, "seconds": 300, "blocks": 2}
    with pytest.raises(DBAPIError):  # expectations are never rewritten once selected
        with db.begin_nested():
            db.add(LearningTrialRevision(trial_id=trial.id, user_id=trial.user_id, revision=2,
                                         proposal=revision.proposal, reason="after the fact"))
            db.flush()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE learning_trial_revisions SET reason = 'x'"))
    db.rollback()

    # a2 needs nothing and she is near: it starts at once, as a trial attempt of her body.
    pack.state_now = state(dirt=0)
    assert LT.advance_once(pack=pack) == 1
    trial = trial_of(db)
    attempt = db.get(GameSkillAttempt, trial.attempt_id)
    assert (trial.status, attempt.origin, attempt.status) == ("executing", "trial", "queued")
    assert game_practice.holding(db, game.id) is True  # her hands wait while it runs

    top = state({"x": 2.5, "y": 66, "z": 0.5})
    pack.results = [({"status": "done", "placed": 0, "calls": []}, state({"x": 1.5, "y": 65, "z": 0.5})),
                    ({"status": "done", "placed": 0, "calls": []}, top)]
    assert game_practice.run_once(pack=pack, model=ScriptedModel(written(), written()))
    trial = trial_of(db)
    assert (trial.status, trial.ending) == ("observing", "completed")
    assert trial.costs["attempts"] == 2 and db.get(GameSkillAttempt, attempt.id).status == "learned"
    assert db.scalar(select(GameLearnedSkill.id)) is None  # a trial's move waits for its assessment

    assert LT.advance_once(pack=pack) == 1 and trial_of(db).status == "assessing"
    reading = {"hypothesis": "supported", "why": "c1 met", "failure_of": "none", "applicability": "2-block ledges",
               "exceptions": [], "cites": ["c1"], "explanations": [{"text": "digging works", "status": "supported",
                                                                     "cites": ["c1"]}],
               "lesson": {"propose": True, "key": "dig_steps", "situation": "a 2-block ledge",
                          "advice": "dig a step and walk up", "level": "skill"}}
    model = assessor(reading)
    assert LT.assess_once(assessor=model)
    trial = trial_of(db)
    assert trial.status == "assessed" and trial.closed_at is not None
    current = db.scalar(select(LearningTrialAssessment).where(LearningTrialAssessment.trial_id == trial.id))
    assert current.hypothesis == "supported" and current.measurements["values"]["reached"] is True
    assert "Alternative chosen by cognition: dig steps" in model.calls[0]
    # Each owner, under its own rules.
    assert db.get(WorkingItem, trial.hypothesis_item_id).status == "supported"
    from app.db.models import LearningAssessment
    assert db.scalar(select(GameLesson)) is None
    shared = db.scalar(select(LearningAssessment).where(LearningAssessment.trigger_key == f"trial:{trial.id}"))
    assert shared is not None  # checked results now enter the shared lifecycle, never a second lesson store
    assert db.scalar(select(GameLearnedSkill)).attempt_id == attempt.id
    outcome = db.scalar(select(GameExperience).where(GameExperience.payload["skill"].astext == "trial:climb_obstacle"))
    assert outcome.payload["status"] == "supported"
    kinds = [e.kind for e in db.scalars(select(LearningTrialEvent).where(LearningTrialEvent.trial_id == trial.id)
                                        .order_by(LearningTrialEvent.id))]
    assert kinds[:4] == ["proposed", "presented", "selected", "execution_started"]
    assert kinds[-3:] == ["execution_ended", "observed", "assessed"]
    events = db.scalars(select(LearningTrialEvent).where(LearningTrialEvent.kind == "selected")).one()
    assert events.decision_attempt_id is not None  # the decision that chose it, by its capture id
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE learning_trial_events SET kind = 'x'"))
    db.rollback()

    # A later decision: the next proposal reads what this one found, and predicts afresh from where she is now.
    session = db.get(DesktopSession, game.id)
    session.plan = {"goal": "build a house", "steps": [
        {"id": "s2", "do": "get onto the roof", "done_when": "on the roof", "state": "active"}]}
    db.commit()
    elsewhere = {"x": 10.5, "y": 70.0, "z": 3.5}
    pack.state_now = state(elsewhere)
    raw = raw_proposal(destination={"x": 12, "y": 73, "z": 3})
    raw["alternatives"][0], raw["alternatives"][1] = raw["alternatives"][1], raw["alternatives"][0]
    raw["recommendation"], raw["cites"] = "a2", ["x1", "t1"]
    second, proposer = propose(db, session, pack, raw)
    assert second.id != trial.id and second.status == "proposed"
    assert "t1: tested" in proposer.seen[0] and "supported" in proposer.seen[0]
    fresh = db.scalar(select(LearningTrialRevision).where(LearningTrialRevision.trial_id == second.id))
    assert fresh.proposal["start"] == elsewhere and f"trial:{trial.id}" in fresh.evidence
    assert db.scalar(select(LearningTrialRevision).where(LearningTrialRevision.trial_id == trial.id)).proposal[
        "start"] == START  # the original trial is preserved


def test_preparation_goes_to_the_planner_and_readiness_is_checked_again(db, game, monkeypatch):
    pack = Pack(now=state(dirt=0))
    propose(db, game, pack)
    assert LT.present_once(decide=scripted_decision(**(PURSUE_A2 | {"target": "P1"})))
    assert trial_of(db).selected_alternative == "a1"
    monkeypatch.setattr("app.services.game_planning.handles", lambda session: True)
    assert LT.advance_once(pack=pack) == 0
    trial = trial_of(db)
    assert trial.status == "preparing" and "4 dirt" in trial.costs["asked_for"]
    job = db.scalar(select(AgentEventJob).where(AgentEventJob.source == "plan"))
    assert job is not None and job.reasons[0].startswith("trial: she still needs 4 dirt")
    assert "Her cognition chose to run an experiment: jump-place a pillar" in "\n".join(
        LT.planning_lines(db, db.get(DesktopSession, game.id)))
    pack.state_now = state(dirt=4)
    due(db)
    assert LT.advance_once(pack=pack) == 1 and trial_of(db).status == "executing"
    # Right before the test, it's checked again: the dirt is gone, so the test never starts and preparation goes on.
    pack.state_now = state(dirt=1)
    assert game_practice.run_once(pack=pack, model=ScriptedModel())
    trial = trial_of(db)
    assert trial.status == "preparing" and trial.attempt_id is None and "not ready" in trial.costs["asked_for"]


def test_an_interrupted_trial_is_recovered_never_repeated_and_concludes_nothing(db, game):
    pack = Pack(now=state())
    propose(db, game, pack)
    LT.present_once(decide=scripted_decision(**PURSUE_A2))
    LT.advance_once(pack=pack)
    trial = trial_of(db)
    attempt = db.get(GameSkillAttempt, trial.attempt_id)
    # A restart cut it off mid-try: one try saved, not at the top.
    attempt.status, attempt.result, attempt.ended_at = "failed", "cut off (the worker restarted)", game_practice._now()
    attempt.started_at = game_practice._now() - timedelta(seconds=30)
    attempt.tries = [{"n": 1, "approach": "dig", "program": {"status": "done", "placed": 0},
                      "facts": {"from": [0, 64, 0], "to": [1.5, 65, 0.5]}, "verdict": {"reason": "not yet"}}]
    db.commit()
    due(db)
    assert LT.advance_once(pack=pack) >= 1  # collected from what it saved, then (no window) to its assessment
    trial = trial_of(db)
    assert (trial.status, trial.ending) == ("assessing", "interrupted")
    ended = db.scalar(select(LearningTrialEvent).where(LearningTrialEvent.kind == "execution_ended"))
    assert ended.payload["recovered"] is True and len(ended.payload["tries"]) == 1
    assert db.scalar(select(GameSkillAttempt.id).where(GameSkillAttempt.status == "queued")) is None  # not repeated
    LT.advance_once(pack=pack)
    model = assessor({"hypothesis": "contradicted", "why": "she never got up", "failure_of": "technique",
                      "applicability": "", "exceptions": [], "cites": ["c1"], "explanations": [],
                      "lesson": {"propose": True, "key": "no_digging", "situation": "a ledge", "advice": "don't dig",
                                 "level": "skill"}})
    LT.assess_once(assessor=model)
    trial = trial_of(db)
    reading = db.scalar(select(LearningTrialAssessment).where(LearningTrialAssessment.trial_id == trial.id))
    assert reading.hypothesis == "unresolved" and reading.result["lesson"] is None
    assert reading.prediction_checks[0]["status"] == "unknown"
    assert {d["what"] for d in reading.dropped} >= {"hypothesis contradicted", "lesson"}
    assert db.get(WorkingItem, trial.hypothesis_item_id).status == "open"  # nothing settled
    assert db.scalar(select(GameLesson.id)) is None


def test_deferral_keeps_a_reminder_new_evidence_revises_and_the_reasons_are_kept(db, game):
    pack = Pack(now=state())
    trial, _ = propose(db, game, pack)
    LT.present_once(decide=scripted_decision(work="defer_proposal", target="P1", intention="after the house"))
    trial = trial_of(db)
    assert trial.status == "deferred" and trial.deferral["reason"] == "after the house"
    assert trial.deferral["condition"]["by"] == "learning"  # cognition named no condition: learning's default
    reminder = db.get(WorkingItem, trial.reminder_item_id)
    assert (reminder.kind, reminder.status) == ("intention", "active") and "Reconsider the experiment" in reminder.text
    # Not presented again on its own: only when its condition comes true.
    assert LT.present_once(decide=scripted_decision(**PURSUE_A2)) is False
    trial.next_check_at = game_practice._now() - timedelta(seconds=1)
    db.commit()
    assert LT.reconsider_once(pack=pack) == 0 and trial_of(db).status == "deferred"

    # New evidence about the same problem: the same proposal, revised; the deferral reason is kept.
    raw = raw_proposal(hypothesis="Digging steps gets me up faster than pillaring")
    propose(db, game, pack, raw)
    trial = trial_of(db)
    assert (trial.status, trial.revision) == ("proposed", 2) and len(trial.deferrals) == 1
    assert db.scalar(select(LearningTrial.id).where(LearningTrial.id != trial.id)) is None  # no duplicate
    assert db.get(WorkingItem, reminder.id).status == "dropped"
    revisions = list(db.scalars(select(LearningTrialRevision.revision).where(LearningTrialRevision.trial_id == trial.id)))
    assert revisions == [1, 2]
    # Cognition decides about the revision it was shown: deferred again, then the goal it competed with ends.
    LT.present_once(decide=scripted_decision(work="defer_proposal", target="P2", intention="still busy"))
    trial = trial_of(db)
    assert len(trial.deferrals) == 2 and "Put off before: after the house" in "".join(
        v["purpose"] for v in T.proposal_views(LT.current_revision(db, trial).proposal,
                                               deferred_because=[d["reason"] for d in trial.deferrals]))
    memory_manager.resolve(db, trial.user_id, game.goal_item_id, status="abandoned", reason="changed plans")
    db.commit()
    trial.next_check_at = game_practice._now() - timedelta(seconds=1)
    db.commit()
    assert LT.reconsider_once(pack=pack) == 1
    trial = trial_of(db)
    assert trial.status == "proposed" and trial.presentations == 2
    LT.present_once(decide=scripted_decision(work="decline_proposal", target="P1", intention="not interested"))
    assert (trial_of(db).status, trial_of(db).stop_reason) == ("rejected", "not interested")


def test_running_out_of_time_while_getting_ready_is_assessed_as_never_started_without_a_model(db, game):
    pack = Pack(now=state(dirt=0))
    propose(db, game, pack)
    LT.present_once(decide=scripted_decision(**(PURSUE_A2 | {"target": "P1"})))
    trial = trial_of(db)
    trial.active_since = game_practice._now() - timedelta(seconds=400)
    db.commit()
    LT.advance_once(pack=pack)  # ended before the test, and (no window to watch) on to its assessment
    assert (trial_of(db).status, trial_of(db).ending) == ("assessing", "limit")

    def never(text):
        raise AssertionError("no model call for a test that never started")

    LT.assess_once(assessor=never)
    trial = trial_of(db)
    reading = db.scalar(select(LearningTrialAssessment).where(LearningTrialAssessment.trial_id == trial.id))
    assert trial.status == "assessed" and reading.test_started is False
    assert (reading.hypothesis, reading.result["failure_of"]) == ("unresolved", "preparation")
    assert trial.costs["prep_seconds"] >= 399


def test_cognition_can_stop_a_running_trial_and_what_ran_is_kept(db, game):
    pack = Pack(now=state())
    propose(db, game, pack)
    LT.present_once(decide=scripted_decision(**PURSUE_A2))
    LT.advance_once(pack=pack)
    trial = trial_of(db)
    assert LT.on_work(db, trial.user_id, f"trial:{trial.id}", "cancel", reason="the user needs me") == \
        "experiment stopping"
    attempt = db.get(GameSkillAttempt, trial.attempt_id)
    assert attempt.stop_requested is True
    # Shown to Student 2 on a user message as work it can act on.
    work = svc._work(db, trial.user_id, None, db.get(DesktopSession, game.id))
    assert any(w.key == f"trial:{trial.id}" and w.state == "running" for w in work)


def test_only_one_pending_investigation_per_problem_and_a_hypothesis_is_investigated_once(db, game):
    for _ in range(3):
        LT.consider(db, game, kind="struggling", text="again")
    db.commit()
    trigger = db.scalars(select(LearningTrialTrigger)).one()
    assert len(trigger.payload["evidence"]) == 3
    item = memory_manager.add_item(db, game.owner_user_id, kind="hypothesis", text_="sand falls when dug under",
                                   owner="columbina", epistemic="inferred",
                                   context_id=memory_manager.game_context(db, game).id)
    db.commit()
    assert LT.sweep(db) == 1 and LT.sweep(db) == 0
    assert db.scalar(select(LearningTrialTrigger).where(
        LearningTrialTrigger.problem_key == f"hypothesis:{item.id}")) is not None


def test_pausing_stops_the_clock_and_resuming_checks_readiness_again_with_what_is_left(db, game):
    pack = Pack(now=state())
    propose(db, game, pack)
    LT.present_once(decide=scripted_decision(**PURSUE_A2))
    LT.advance_once(pack=pack)
    trial = trial_of(db)
    attempt_id = trial.attempt_id
    assert LT.on_work(db, trial.user_id, f"trial:{trial.id}", "pause", reason="a creeper") == "experiment paused"
    trial = trial_of(db)
    assert (trial.status, trial.attempt_id) == ("paused", None)  # its attempt hadn't begun: nothing to collect
    assert db.get(GameSkillAttempt, attempt_id).status == "stopped"
    due(db)
    assert LT.advance_once(pack=pack) == 0  # paused: nothing moves on its own
    assert LT.on_work(db, trial.user_id, f"trial:{trial.id}", "resume", reason="safe now") == "experiment resumed"
    assert trial_of(db).status == "selected"
    assert LT.advance_once(pack=pack) == 1 and trial_of(db).status == "executing"
    assert trial_of(db).attempt_id != attempt_id
