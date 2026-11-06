"""Consolidation contracts: retained play interpretation, cautious use and one lifecycle."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest
from sqlalchemy import select, func

from app.ai.learning import assessment as A, lessons as L
from app.db import models as M
from app.services import game_assessment, game_experience, learning_assessment as LA, learning_review
from app.services import learning_lessons as LL, memory_lessons as ML, memory_reset, long_term_memory
from db_support import database, create_user  # noqa: F401


@pytest.fixture
def env(database, monkeypatch, tmp_path):
    settings = LA.get_settings().model_copy(update={
        "learning_assessment_settle_seconds": 0, "lessons_supply_enabled": True,
        "learning_assessment_log_path": str(tmp_path / "assessment.jsonl"),
        "learning_lessons_log_path": str(tmp_path / "lessons.jsonl"),
        "lessons_log_path": str(tmp_path / "memory-lessons.jsonl")})
    for mod in (LA, LL, ML, learning_review):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    monkeypatch.setattr(long_term_memory, "_embedding", lambda _: None)
    monkeypatch.setattr(ML, "_embed", lambda _: None)
    with database() as db:
        user = create_user(db)
        environment = M.DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                                            allowed_profile_ids=["minecraft_pack"], status="online")
        db.add(environment)
        db.flush()
        session = M.DesktopSession(owner_user_id=user.id, environment_id=environment.id,
                                  profile_id="minecraft_pack", profile_revision=1, state="running",
                                  control_owner="gameplay", max_duration_seconds=3600)
        db.add(session)
        db.commit()
    return database, user, session


def experience(db, session, *, status="done", reason="crafted a table"):
    row = M.GameExperience(user_id=session.owner_user_id, session_id=session.id, game="minecraft",
                          kind="skill_outcome", payload={"skill": "craft", "status": status, "reason": reason})
    db.add(row)
    db.commit()
    return row


def assess(factory, script):
    with factory() as db:
        claimed = LA.claim(db, datetime.now(timezone.utc) + timedelta(hours=1))
    assert claimed
    with factory() as db:
        assert LA.assess(db, claimed, evaluator=script) == "revised"


def test_successful_and_ordinary_stretches_reach_shared_assessment_with_retry_safe_boundaries(env):
    factory, user, session = env
    with factory() as db:
        e = experience(db, session)
        db.add(M.GameExperience(user_id=user.id, session_id=session.id, game="minecraft", kind="notice",
                               payload={"text": "ordinary movement", "kind": "mode"}))
        db.commit()
        game_assessment.schedule(db, session, "ended")
        game_assessment.schedule(db, session, "ended")
        db.commit()
        rows = list(db.scalars(select(M.LearningAssessment)))
        assert len(rows) == 1
        gathered = LA.gather(db, rows[0], LA.Plan())
        text = A.render(gathered.ev)
        assert "crafted a table" in text and "ordinary movement" in text
        assert "trajectory missing or expired" in str(gathered.awaiting)
        assert db.scalar(select(func.count()).select_from(M.GameReflectionJob)) == 0
        assert db.scalar(select(func.count()).select_from(M.GameLesson)) == 0
        assert gathered.ev.targets["s1"].kind == "stretch"
        # More play before the stretch is read joins it (one opportunity, nothing lost)...
        e2 = experience(db, session, reason="crafted planks")
        game_assessment.schedule(db, session, "ended")
        db.commit()
        assert db.scalar(select(func.count()).select_from(M.LearningAssessment)) == 1
        refs = set(db.scalars(select(M.LearningAssessmentEvidence.ref).where(
            M.LearningAssessmentEvidence.assessment_id == rows[0].id)))
        assert {f"experience:{e.id}", f"experience:{e2.id}"} <= refs
        # ...and once a stretch is concluded, later play is a distinct stretch, retained.
        row = db.get(M.LearningAssessment, rows[0].id)
        row.state, row.outcome = "done", "concluded"
        db.commit()
        experience(db, session, reason="crafted sticks")
        game_assessment.schedule(db, session, "ended")
        db.commit()
        assert db.scalar(select(func.count()).select_from(M.LearningAssessment)) == 2


def test_one_cost_with_unknown_cause_becomes_a_scoped_hypothesis_and_changes_later_hands_action(env):
    factory, user, session = env
    with factory() as db:
        e = experience(db, session, status="failed", reason="died while descending; damage cause unknown")
        game_assessment.schedule(db, session, "ended")
        db.commit()

    def evaluator(text):
        assert "damage cause unknown" in text
        return A.Answer({"dimensions": [{"target": "s1", "dimension": "cost", "value": "poor",
                                         "status": "supported", "cites": [f"g{e.id}"], "note": "descent ended in death"}],
                         "causes": [{"target": "s1", "component": "unclear", "status": "supported",
                                     "cites": [f"g{e.id}"], "why": "cause unknown"}],
                         "episode": {"summary": "I died while descending; I do not know why.", "cites": [f"g{e.id}"]}}, 0)
    assess(factory, evaluator)
    with factory() as db:
        row = db.scalar(select(M.LearningAssessment))
        assert db.scalar(select(func.count()).select_from(M.LearningLessonJob)) == 1
        claimed = LL.claim(db, datetime.now(timezone.utc) + timedelta(hours=1))

    def builder(text):
        assert "cause unknown" in text
        return L.Answer({"changes": [{"op": "create", "kind": "strategy", "scope": "situation",
            "statement": "Inspect the descent before committing to it; the earlier damage cause remains unknown",
            "applies_when": "minecraft descending into an uninspected shaft", "exceptions": [],
            "conclusion_cites": ["a2"], "applicability_cites": ["a2"], "why": "one costly experience"}]}, 0)
    with factory() as db:
        assert LL.process(db, *claimed, builder=builder, embed=lambda _: None) == "changed"
        lesson = db.scalar(select(M.MemoryLesson))
        assert lesson.status == "hypothesis" and lesson.scope == "situation"
        supply = ML.for_game(db, user.id, "minecraft", query="descending shaft")
        assert supply.views[0].status == "hypothesis" and supply.views[0].source_refs == (f"experience:{e.id}",)
        lessons = game_experience.lessons_for_run(db, session.id)

    # A fresh driver has no original outcome context. Its actual chosen action depends on the supplied lesson.
    from test_skill_driver import Harness, ScriptedModel, _decide
    class CautiousModel(ScriptedModel):
        def step(self, system, text, images, schema):
            from app.ai.desktop.driver import ModelReply
            cautious = "damage cause remains unknown" in system and "Status: hypothesis" in system
            return ModelReply(output=_decide("wait", {"seconds": 2}, note="inspect before descending") if cautious
                              else _decide("mine", {"block": "stone"}), latency_ms=0)
    baseline = Harness(CautiousModel())
    baseline.steps(1)
    assert baseline.run.running["skill"] == "mine"
    harness = Harness(CautiousModel())
    harness.run.deps = replace(harness.run.deps, lessons=lambda _: lessons)
    assert harness.steps(1) == [True]
    assert harness.run.waiting_until is not None
    assert "inspect before descending" in harness.run.note
    with factory() as db:
        game_experience.record_lesson_use(db, session.id, uuid.uuid4(), lessons,
                                         {"lessons_weighed": [{"lesson": "L1", "use": "applied",
                                                                "why": "inspect before descending"}]}, "ok")
        use = db.scalar(select(M.MemoryLessonUse))
        assert use.received and use.revision == 1 and use.reported == "applied"
        assert db.scalar(select(M.EpisodicMemory)).context["assessment_revision"] == 1
        LA.add_note(db, user.id, row.id, "The server log says this was a reset, not a dangerous descent.")
        db.commit()
    # New conflicting evidence revises the assessment and invalidates what memory took from it.
    assess(factory, lambda _: A.Answer({"missing": [{"what": "whether descent was costly",
                                                    "needed_for": "the tentative strategy"}]}, 0))
    with factory() as db:
        assert not ML.for_game(db, user.id, "minecraft").views
        assert db.scalar(select(M.EpisodicMemory)).status == "superseded"
        assert db.scalar(select(M.MemoryLessonEvidence)).current is False
        assert db.scalar(select(func.count()).select_from(M.MemoryLessonEvidence)) == 1
        assert db.get(M.LearningAssessment, row.id).revision == 2


def test_overlapping_game_sources_do_not_manufacture_independent_support(env):
    factory, user, session = env
    with factory() as db:
        c = L.Change("create", None, None, None, "strategy", "situation", "Check the shaft", "minecraft shafts",
                     [], [], ["experience:17"], None, "first", certainty="tentative")
        source = ML.Source("skill:one", datetime.now(timezone.utc), None, None)
        ML.apply(db, user.id, [c], source, embed=lambda _: None)
        lesson = db.scalar(select(M.MemoryLesson))
        # The same run read under another assessment, judged established: not independent, so it stays a guess.
        again = replace(c, op="support", lesson_id=str(lesson.id), expected_revision=1, certainty="established")
        ML.apply(db, user.id, [again], replace(source, occasion="stretch:one"), embed=lambda _: None)
        assert lesson.status == "hypothesis"
        # Distinct evidence is a second occasion, but no count promotes it: the builder's judgment does.
        other = replace(again, roots=["experience:18"], certainty=None)
        ML.apply(db, user.id, [other], replace(source, occasion="stretch:two"), embed=lambda _: None)
        assert lesson.status == "hypothesis"
        third = replace(again, roots=["experience:19"], expected_revision=lesson.revision)
        ML.apply(db, user.id, [third], replace(source, occasion="stretch:three"), embed=lambda _: None)
        assert lesson.status == "accepted"  # same session is allowed, distinct evidence required
        db.commit()
        assert ML.for_game(db, user.id, "minecraft").views[0].occasions == 3


def test_consolidation_reset_preserves_evidence_and_programs_and_fences_old_intake(env):
    factory, user, session = env
    with factory() as db:
        old = experience(db, session)
        game_assessment.schedule(db, session, "ended")
        db.commit()
        db.add(M.GameLesson(user_id=user.id, game="minecraft", normalized_key="old", situation="old", advice="old"))
        attempt = M.GameSkillAttempt(user_id=user.id, session_id=session.id, game="minecraft", task="climb",
                                    origin="user", status="learned", code="await api.jump()")
        db.add(attempt)
        db.flush()
        move = M.GameLearnedSkill(user_id=user.id, game="minecraft", name="learned_climb", task="climb",
                                 code=attempt.code, attempt_id=attempt.id)
        db.add(move)
        db.commit()
        memory_reset.reset(db, user_ids=[user.id], scopes=["consolidation"])
        assert db.get(M.GameExperience, old.id) is not None
        assert db.get(M.GameSkillAttempt, attempt.id).code == "await api.jump()"
        assert db.get(M.GameLearnedSkill, move.id).code == "await api.jump()"
        assert db.scalar(select(func.count()).select_from(M.LearningAssessment)) == 0
        game_assessment.schedule(db, session, "ended")
        db.commit()
        assert db.scalar(select(func.count()).select_from(M.LearningAssessment)) == 0
        assert not ML.for_game(db, user.id, "minecraft").views
        new = experience(db, session, reason="a fresh attempt")
        game_assessment.schedule(db, session, "ended")
        db.commit()
        row = db.scalar(select(M.LearningAssessment))
        refs = list(db.scalars(select(M.LearningAssessmentEvidence.ref)))
        assert refs == [f"experience:{new.id}"]
        text = A.render(LA.gather(db, row, LA.Plan()).ev)
        assert "crafted a table" not in text and "a fresh attempt" in text


def test_critical_notice_intake_does_not_need_a_failed_skill_or_a_full_stretch(env):
    factory, user, session = env
    from app.services import observation, observation_adapters
    with factory() as db:
        e = M.GameExperience(user_id=user.id, session_id=session.id, game="minecraft", kind="notice",
                             payload={"kind": "died", "text": "died; cause unknown", "urgency": "urgent"})
        db.add(e)
        db.flush()
        observation.record(db, observation_adapters.experience_report(session, e, None))
        db.commit()
        LA.triage(db, datetime.now(timezone.utc) + timedelta(seconds=1))
        row = db.scalar(select(M.LearningAssessment))
        assert row.trigger_key == f"notice:{e.id}"
        assert "assuming a cause" in row.trigger["why"]
        assert row.revision == 0


def test_mind_reads_the_same_shared_hypothesis_and_captures_its_revision(env, monkeypatch):
    factory, user, session = env
    from app.ai.mind.hard import HardInput, HardThought, Problem, render
    from app.ai.mind.contracts import Scope
    from app.services import mind_minecraft, mind_hard, game_moment, effort, game_skills
    from app.ai.desktop.pack_client import PackClient
    problem = Problem("failing", "descending shaft", "shaft", "shaft", {})
    scope = Scope(user.id, "minecraft", session.id)
    monkeypatch.setattr(PackClient, "from_settings", classmethod(lambda cls: type("Pack", (), {"state": lambda _: {}})()))
    monkeypatch.setattr(effort, "lines_for_session", lambda *a, **k: ())
    monkeypatch.setattr(game_moment, "capture", lambda *a: None)
    with factory() as db:
        c = L.Change("create", None, None, None, "strategy", "situation", "Inspect before descending",
                     "minecraft shafts", [], [], ["experience:44"], None, "one costly outcome")
        ML.apply(db, user.id, [c], ML.Source("notice:44", datetime.now(timezone.utc), None, None), embed=lambda _: None)
        db.commit()
        inp, _ = mind_minecraft.SOURCE.gather(db, scope, problem, HardInput(problem))
        assert "Inspect before descending" in render(inp) and "Status: hypothesis" in render(inp)
        aid = uuid.uuid4()
        mind_hard._capture_attempt(aid, mind_minecraft.SOURCE, scope, problem, inp,
                                  HardThought("decision", "Inspect the shaft first"),
                                  started_at=datetime.now(timezone.utc))
        use = db.scalar(select(M.MemoryLessonUse).where(M.MemoryLessonUse.attempt_id == aid))
        assert use.received and use.consumer == "mind" and use.revision == 1


def test_callback_retries_keep_one_event_and_one_observation(env):
    factory, user, session = env
    with factory() as db:
        payload = {"attempt_id": str(uuid.uuid4()), "skill": "craft", "status": "done", "reason": "made a table"}
        first = game_experience.record_experience(db, session.id, "skill_outcome", payload)
        again = game_experience.record_experience(db, session.id, "skill_outcome", payload)
        assert first == again
        assert db.scalar(select(func.count()).select_from(M.GameExperience)) == 1
        assert db.scalar(select(func.count()).select_from(M.Observation)) == 1
        # A trial assessment retry has the same identity; a later revision is a distinct report.
        for n in (1, 1, 2):
            game_experience.record_experience(db, session.id, "skill_outcome", {
                "event_key": f"trial:one:assessment:{n}", "skill": "trial:climb_obstacle", "status": "supported"})
        assert db.scalar(select(func.count()).select_from(M.GameExperience)) == 3


def test_game_assessment_can_read_claim_execution_observed_state_and_available_actions_separately(env):
    factory, user, session = env
    now = datetime.now(timezone.utc)
    aid = uuid.uuid4()
    with factory() as db:
        db.add(M.DecisionAttempt(id=aid, user_id=user.id, learner="hands", trigger="hands_step",
                               game_session_id=session.id, status="ok", started_at=now,
                               expires_at=now + timedelta(days=1),
                               versions={"manifest": ["craft", "inspect"]},
                               input={"text": "make boots", "plan": {"goal": "armor"}, "schema": {"choices": ["craft", "inspect"]}},
                               output={"action": ["craft", {"item": "boots"}], "claim": "finished all armor"}))
        db.add(M.DecisionExecution(id=uuid.uuid4(), user_id=user.id, attempt_id=aid, kind="skill_run", executor="pack",
                                  game_session_id=session.id, result="succeeded", ended_at=now, expires_at=now + timedelta(days=1),
                                  intended={"claim": "finished all armor"},
                                  executed={"report": {"status": "done"}, "observed": {"after": {"inventory": {"boots": 1}}}}))
        db.flush()
        e = experience(db, session)
        e.payload = {**e.payload, "attempt_id": str(aid)}
        db.commit()
        game_assessment.schedule(db, session, "ended")
        db.commit()
        row = db.scalar(select(M.LearningAssessment))
        text = A.render(LA.gather(db, row, LA.Plan(inputs=True)).ev)
        assert "finished all armor" in text and '"boots": 1' in text
        assert "available actions" in text and "inspect" in text and "observed" in text
        assert "A recorded report, not proof of the cause" in text


def test_old_routes_and_workers_are_absent():
    from app.api.routes.learning import router
    paths = {r.path for r in router.routes}
    assert not paths & {"/learning/lessons", "/learning/proposals", "/learning/reflections"}
    assert "/learning/adaptation/lessons" in paths and "/learning/skills" in paths


def test_game_decision_capture_keeps_lesson_sources_for_existing_forget_ownership(env, monkeypatch):
    factory, user, session = env
    from app.services import learning_capture, game_planning, mind_hard, mind_minecraft
    from app.workers.desktop import _hands_capture
    from app.ai.mind.plan import PlanDecision
    from app.ai.mind.hard import HardInput, HardThought, Problem
    from app.ai.mind.contracts import Scope
    lesson_id = str(uuid.uuid4())
    view = L.LessonView("L1", lesson_id, 1, "strategy", "situation", "Inspect first", "minecraft shafts",
                        status="hypothesis")
    calls = []
    monkeypatch.setattr(learning_capture, "attempt", lambda ctx, **kw: calls.append(ctx))
    monkeypatch.setattr(ML, "record_uses", lambda *a, **k: 0)
    _hands_capture(session.id)("attempt", attempt_id=uuid.uuid4(), input={}, status="ok", asked_at=0,
                               input_refs=[{"ref": f"lesson:{lesson_id}", "revision": 1}])
    game_planning._capture_attempt(uuid.uuid4(), user_id=user.id, session_id=session.id, job_id=None,
                                  try_number=None, text="inspect", reasons=[], goal=None,
                                  read={"lessons": [{"ref": f"lesson:{lesson_id}", "revision": 1}]},
                                  read_revision=1, plan_before={}, decision=PlanDecision("keep"),
                                  started_at=datetime.now(timezone.utc))
    problem = Problem("failing", "shaft", "shaft", "shaft", {})
    mind_hard._capture_attempt(uuid.uuid4(), mind_minecraft.SOURCE, Scope(user.id, "minecraft", session.id),
                              problem, HardInput(problem, lesson_supply=ML.Supply(views=(view,))),
                              HardThought("decision", "inspect"), started_at=datetime.now(timezone.utc))
    assert len(calls) == 3
    assert all(f"lesson:{lesson_id}" in ctx.sources for ctx in calls)
