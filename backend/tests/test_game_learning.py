"""Game learning: experience -> reflection -> lessons, proposals and an episode.

The driver half (recording, lessons in the prompt) and the timeline are pure
and always run. The rest needs real PostgreSQL (``TEST_DATABASE_URL``, a
throwaway server, never the dev database) and uses a scripted reflector, so
no model is ever called.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import (
    ChatMessage, Conversation, DesktopEnvironment, DesktopSession, DesktopSessionEvent, EpisodicMemory,
    GameExperience, GameLesson, GameProposal, GameReflectionJob, SemanticMemory, User,
)
from app.schemas.game_learning import ReflectedLesson, ReflectedProposal, Reflection
from app.services import desktop_game, desktop_sessions, game_learning, long_term_memory, memory_reset
from app.services.game_learning import LearningError, build_timeline, lesson_status, resolve_refs
from tests.db_support import database  # noqa: F401  (fixture)
from tests.desktop_support import isolated_redis  # noqa: F401  (autouse fixture)
from tests.test_skill_driver import Harness, ScriptedModel, _decide

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Her hands record experience and read lessons (no database)
# --------------------------------------------------------------------------- #


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
    assert ("notice", {"kind": "village", "urgency": "heads_up", "text": "a village, 80 m north", "data": {}}) \
        in h.experiences


def test_lessons_go_into_the_system_prompt_trial_ones_marked_tentative():
    lessons = [{"situation": "crafting tools", "advice": "place a crafting table first", "status": "confirmed",
                "level": "skill"},
               {"situation": "mining stone", "advice": "carry a pickaxe", "status": "trial", "level": "skill"},
               {"situation": "going underground", "advice": "bring torches", "status": "confirmed", "level": "plan"}]
    h = _recording_harness(ScriptedModel(_decide("mine", {"block": "oak_log"})), lessons)
    h.steps(1)
    skill, plan = h.run._system.split("Planning lessons Jophiel learned")
    assert "What you learned in earlier sessions" in skill
    assert "- When crafting tools: place a crafting table first\n" in skill
    assert "- When mining stone: carry a pickaxe (tentative: seen once)" in skill
    assert "- When going underground: bring torches" in plan and "torches" not in skill


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


# --------------------------------------------------------------------------- #
# Timeline and trust rules (no database)
# --------------------------------------------------------------------------- #


def _exp(i, kind, payload, at):
    return GameExperience(id=i, kind=kind, payload=payload, occurred_at=at)


def _session_row(**extra):
    return DesktopSession(id=uuid.uuid4(), started_at=T0, ended_at=T0 + timedelta(minutes=10),
                          reason_code="user_end", **extra)


def test_timeline_numbers_entries_collapses_repeats_and_merges_session_events():
    fail = {"skill": "craft", "params": {"item": "wooden_pickaxe"}, "status": "failed",
            "reason": "no crafting table in reach", "seconds": 0.1}
    experiences = [
        _exp(101, "lessons_applied", {"keys": []}, T0),
        _exp(102, "skill_outcome", {"skill": "mine", "params": {"block": "oak_log"}, "status": "done",
                                    "data": {"collected": 3}, "seconds": 12}, T0 + timedelta(seconds=20)),
        _exp(103, "skill_outcome", fail, T0 + timedelta(seconds=30)),
        _exp(104, "skill_outcome", fail, T0 + timedelta(seconds=31)),
        _exp(105, "skill_outcome", fail, T0 + timedelta(seconds=32)),
        _exp(106, "notice", {"kind": "died", "urgency": "urgent", "text": "you died (fell)"},
             T0 + timedelta(seconds=70)),
    ]
    events = [DesktopSessionEvent(id=1, type="desktop.plan_set", payload={"goal": "make a pickaxe",
                                                                          "source": "user_tool"},
                                  occurred_at=T0 + timedelta(seconds=1)),
              DesktopSessionEvent(id=2, type="desktop.stuck", payload={"step_id": "s2", "note": "no table"},
                                  occurred_at=T0 + timedelta(seconds=40))]
    timeline = build_timeline(_session_row(), experiences, events)
    lines = timeline.text.splitlines()
    assert lines[0] == "[00:01] new goal: 'make a pickaxe' (set by user_tool)"
    assert lines[1] == "[00:20] e1 mine(block=oak_log) -> done (12 s) collected=3"
    assert lines[2] == ("[00:30] e2-e4 craft(item=wooden_pickaxe) -> failed: no crafting table in reach (0 s) (x3)")
    assert lines[3] == '[00:40] stuck on step s2: "no table"'
    assert lines[4] == "[01:10] e5 noticed [urgent] you died (fell)"
    assert timeline.outcomes == 4
    assert resolve_refs(["e2-e4", "e1", "E5", "e9", "nonsense", "e3"], timeline) == [103, 104, 105, 102, 106]


@pytest.mark.parametrize("support,contradict,status", [
    (1, 0, "trial"), (2, 0, "confirmed"), (3, 1, "confirmed"), (2, 1, "trial"),
    (1, 1, "trial"), (1, 2, "retired"), (2, 2, "retired"), (5, 2, "confirmed"),
])
def test_trust_comes_from_evidence_counts(support, contradict, status):
    assert lesson_status(support, contradict) == status


# --------------------------------------------------------------------------- #
# Database: scheduling, reflection, lessons, proposals, episodes
# --------------------------------------------------------------------------- #


@pytest.fixture
def db(database, monkeypatch):
    monkeypatch.setattr(long_term_memory, "_embedding", lambda text: None)
    monkeypatch.setattr(long_term_memory, "SessionLocal", database)
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
                             profile_revision=1, state=state, control_owner="gameplay",
                             max_duration_seconds=3600)
    db.add(session)
    db.commit()
    return session


def _play(db, session, outcomes: list[tuple[str, str, str | None]]) -> None:
    for skill, status, reason in outcomes:
        game_learning.record_experience(db, session.id, "skill_outcome",
                                        {"skill": skill, "params": {}, "status": status, "reason": reason})


def _end(db, user, session) -> GameReflectionJob:
    desktop_sessions.end_session(db, user.id, session.id)
    return db.scalar(select(GameReflectionJob).where(GameReflectionJob.session_id == session.id))


CRAFT_FAILS = [("mine", "done", None), ("craft", "failed", "no crafting table in reach"),
               ("craft", "failed", "no crafting table in reach"), ("craft", "done", None), ("mine", "done", None)]


def _reflector(*reflections):
    calls = []
    queue = list(reflections)

    def reflect(system, text):
        calls.append((system, text))
        return queue.pop(0)

    reflect.calls = calls
    return reflect


def _lesson(key="crafting:place_table_first", verdict="new", evidence=("e2-e3",), level="skill"):
    return ReflectedLesson(key=key, verdict=verdict, level=level, situation="crafting tools",
                           advice="place a crafting table first", evidence=list(evidence))


def test_ending_a_pack_session_queues_one_reflection_for_the_stretch(db, user):
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    job = _end(db, user, session)
    assert job is not None and job.status == "pending" and job.game == "minecraft"
    assert job.from_experience_id == 0 and job.up_to_experience_id > 0
    desktop_sessions.end_session(db, user.id, session.id)  # idempotent
    assert db.scalar(select(func.count()).select_from(GameReflectionJob)) == 1


def test_a_stretch_too_short_to_learn_from_queues_nothing(db, user):
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS[:2])
    assert _end(db, user, session) is None


def _jobs(db, session):
    return list(db.scalars(select(GameReflectionJob).where(GameReflectionJob.session_id == session.id)
                           .order_by(GameReflectionJob.created_at)))


def test_offline_and_long_pauses_reflect_on_each_stretch_short_pauses_wait(db, user):
    """Seen live: two play sessions were one desktop session that went offline
    twice and never ended, so nothing was ever reflected on."""
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    desktop_sessions.controller_pause(db, session.id, reason="user_pause")  # 5 outcomes: a pause waits for 15
    assert _jobs(db, session) == []
    db.refresh(session)
    desktop_sessions.resume_session(db, user.id, session.id, expected_state_revision=session.state_revision)
    _play(db, session, CRAFT_FAILS)
    env = db.get(DesktopEnvironment, session.environment_id)
    desktop_sessions.disconnect_sessions_locked(db, env.id, reason="pack_offline")  # offline: 5 is enough
    db.commit()
    [first] = _jobs(db, session)
    assert first.from_experience_id == 0
    db.refresh(session)
    desktop_sessions.resume_session(db, user.id, session.id, expected_state_revision=session.state_revision)
    _play(db, session, CRAFT_FAILS * 3)  # 15 more: a long stretch, so a pause reflects on it
    desktop_sessions.controller_pause(db, session.id, reason="user_pause")
    first, second = _jobs(db, session)
    assert second.from_experience_id == first.up_to_experience_id

    reflect = _reflector(Reflection(episode="", episode_importance=0, lessons=[], proposals=[]))
    assert game_learning.process_job(db, second.id, reflect) is True
    text = reflect.calls[0][1]
    assert "e15" in text and "e16" not in text  # only this stretch's 15 outcomes


def test_what_the_user_said_is_in_the_timeline_and_can_back_a_lesson(db, user):
    session = _pack_session(db, user)
    conversation = Conversation(user_id=user.id, title="game")
    db.add(conversation)
    db.commit()
    _play(db, session, CRAFT_FAILS[:2])
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content="You can break blocks."))
    db.commit()
    _play(db, session, CRAFT_FAILS[2:])
    job = _end(db, user, session)
    reflect = _reflector(Reflection(episode="", episode_importance=0, proposals=[], lessons=[
        _lesson(key="movement:may_break_blocks", evidence=["u1"]).model_copy(
            update={"situation": "something is in the way", "advice": "break it: the user allows it"})]))
    game_learning.process_job(db, job.id, reflect)
    assert 'u1 vincent said: "You can break blocks."' in reflect.calls[0][1]
    lesson = db.scalar(select(GameLesson))
    assert lesson.normalized_key == "movement:may_break_blocks" and len(lesson.evidence[0]["message_ids"]) == 1


def test_reflection_writes_lessons_proposals_and_an_episode_only_with_evidence(db, user):
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    job = _end(db, user, session)
    reflect = _reflector(Reflection(
        episode="I made a wooden pickaxe after forgetting the crafting table twice.", episode_importance=0.4,
        lessons=[_lesson(), _lesson(key="vague:be_careful", evidence=["e99"])],
        proposals=[ReflectedProposal(key="skill:craft_places_table", title="craft should place a table itself",
                                     area="craft", problem="craft failed twice for want of a table",
                                     suggestion="place one from the inventory when needed", evidence=["e2", "e3"])],
    ))
    assert game_learning.process_job(db, job.id, reflect) is True
    system, text = reflect.calls[0]
    assert "e2-e3 craft() -> failed: no crafting table in reach (x2)" in text and "Lessons you already have" in text

    lesson = db.scalar(select(GameLesson))
    assert (lesson.normalized_key, lesson.status, lesson.support_count) == ("crafting:place_table_first", "trial", 1)
    assert len(lesson.evidence[0]["experience_ids"]) == 2
    proposal = db.scalar(select(GameProposal))
    assert proposal.status == "open" and proposal.evidence[0]["examples"] == [
        "craft() -> failed: no crafting table in reach"] * 2
    episode = db.scalar(select(EpisodicMemory))
    assert episode.context["source"] == "game" and "wooden pickaxe" in episode.summary
    db.refresh(job)
    assert job.status == "done" and job.result["lessons"]["new"] == ["crafting:place_table_first"]
    assert job.result["dropped"] == [{"lesson": "vague:be_careful", "why": "no key, advice or valid evidence"}]


def test_one_evening_is_one_memory_named_for_the_user_and_dated_when_it_happened(db, user):
    """Seen live: three recaps for one evening (one per pause), dated when written, about "tearDrop12"."""
    from datetime import timedelta

    from app.db.models import GamePlayerLink

    db.add(GamePlayerLink(user_id=user.id, game="minecraft", player_name="tearDrop12"))
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    env = db.get(DesktopEnvironment, session.environment_id)
    desktop_sessions.disconnect_sessions_locked(db, env.id, reason="pack_offline")
    db.commit()
    [first] = _jobs(db, session)
    reflect = _reflector(
        Reflection(episode="Vincent and I made a pickaxe.", episode_importance=0.4, lessons=[], proposals=[]),
        Reflection(episode="Vincent and I made a pickaxe, then he gave me bread.", episode_importance=0.6,
                   lessons=[], proposals=[]))
    game_learning.process_job(db, first.id, reflect)
    system = reflect.calls[0][0]
    assert "together with vincent (tearDrop12 in the game)" in system and '"we"' in system
    earlier = db.scalar(select(EpisodicMemory))
    last = db.scalar(select(GameExperience).order_by(GameExperience.id.desc()).limit(1))
    assert earlier.event_at == last.occurred_at  # when it was played, not when it was written

    db.refresh(session)
    desktop_sessions.resume_session(db, user.id, session.id, expected_state_revision=session.state_revision)
    _play(db, session, CRAFT_FAILS)
    desktop_sessions.disconnect_sessions_locked(db, env.id, reason="pack_offline")
    db.commit()
    second = _jobs(db, session)[1]
    game_learning.process_job(db, second.id, reflect)
    assert "Your recap of earlier this evening: Vincent and I made a pickaxe.\nYour episode replaces it" \
        in reflect.calls[1][1]
    episodes = list(db.scalars(select(EpisodicMemory).order_by(EpisodicMemory.created_at)))
    assert [e.status for e in episodes] == ["superseded", "active"]
    assert episodes[1].summary.endswith("he gave me bread.") and episodes[1].importance == 0.6

    # A stretch the next day is its own memory.
    for episode in episodes:
        episode.event_at -= timedelta(days=1)
    db.commit()
    db.refresh(session)
    desktop_sessions.resume_session(db, user.id, session.id, expected_state_revision=session.state_revision)
    _play(db, session, CRAFT_FAILS)
    desktop_sessions.disconnect_sessions_locked(db, env.id, reason="pack_offline")
    db.commit()
    third = _jobs(db, session)[2]
    game_learning.process_job(db, third.id, _reflector(
        Reflection(episode="We played again.", episode_importance=0.3, lessons=[], proposals=[])))
    assert db.scalar(select(func.count()).select_from(EpisodicMemory).where(EpisodicMemory.status == "active")) == 2


def test_a_long_break_is_marked_in_the_timeline():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    start = datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc)
    rows = [SimpleNamespace(id=i, kind="skill_outcome", occurred_at=at,
                            payload={"skill": "mine", "params": {}, "status": "done"})
            for i, at in enumerate([start, start + timedelta(minutes=1), start + timedelta(hours=5, minutes=40)], 1)]
    timeline = game_learning.build_timeline(SimpleNamespace(started_at=start), rows, [], speaker="Vincent")
    assert "--- 6 h later ---" in timeline.text.splitlines()[1]


def test_lessons_are_confirmed_by_later_sessions_and_retired_by_contradiction(db, user):
    def session_with(reflection):
        session = _pack_session(db, user)
        _play(db, session, CRAFT_FAILS)
        job = _end(db, user, session)
        game_learning.process_job(db, job.id, _reflector(reflection))
        db.expire_all()
        return db.scalar(select(GameLesson))

    def only(*lessons):
        return Reflection(episode="", episode_importance=0, lessons=list(lessons), proposals=[])

    assert session_with(only(_lesson())).status == "trial"
    assert session_with(only(_lesson(verdict="supports"))).status == "confirmed"
    assert session_with(only(_lesson(verdict="contradicts"))).status == "trial"  # 2 vs 1
    lesson = session_with(only(_lesson(verdict="contradicts")))
    assert (lesson.support_count, lesson.contradict_count, lesson.status) == (2, 2, "retired")
    # Retired lessons are never revived by a later "new".
    assert session_with(only(_lesson(verdict="new"))).status == "retired"
    assert db.scalar(select(EpisodicMemory)) is None  # empty episodes aren't stored


def test_lessons_for_run_respects_the_ab_switch_and_records_what_it_gave(db, user, monkeypatch):
    db.add(GameLesson(user_id=user.id, game="minecraft", normalized_key="a:b", situation="s", advice="a",
                      status="confirmed", support_count=2))
    db.add(GameLesson(user_id=user.id, game="minecraft", normalized_key="c:d", situation="s", advice="r",
                      status="retired"))
    db.commit()
    session = _pack_session(db, user)
    lesson_id = db.scalar(select(GameLesson.id).where(GameLesson.normalized_key == "a:b"))
    # id and revision: which lesson versions her hands saw, for the learning capture.
    assert game_learning.lessons_for_run(db, session.id) == [
        {"situation": "s", "advice": "a", "status": "confirmed", "level": "skill", "id": str(lesson_id),
         "revision": 1}]
    monkeypatch.setattr(game_learning, "get_settings",
                        lambda: get_settings().model_copy(update={"game_learning_apply_lessons": False}))
    assert game_learning.lessons_for_run(db, session.id) == []
    applied = [e.payload for e in db.scalars(select(GameExperience).where(GameExperience.kind == "lessons_applied")
                                             .order_by(GameExperience.id))]
    assert [(p["enabled"], p["keys"]) for p in applied] == [(True, ["a:b"]), (False, [])]


def test_an_implemented_proposal_that_recurs_is_reopened(db, user):
    proposal = ReflectedProposal(key="skill:x", title="t", area="craft", problem="p", suggestion="s", evidence=["e2"])
    first = _pack_session(db, user)
    _play(db, first, CRAFT_FAILS)
    game_learning.process_job(db, _end(db, user, first).id, _reflector(
        Reflection(episode="", episode_importance=0, lessons=[], proposals=[proposal])))
    row = db.scalar(select(GameProposal))
    game_learning.update_proposal(db, user.id, row.id, status="implemented", user_note="fixed in abc123")
    second = _pack_session(db, user)
    _play(db, second, CRAFT_FAILS)
    game_learning.process_job(db, _end(db, user, second).id, _reflector(
        Reflection(episode="", episode_importance=0, lessons=[], proposals=[proposal])))
    db.refresh(row)
    assert (row.status, row.occurrences, row.evidence[-1]["after_implemented"]) == ("open", 2, True)


def test_a_failed_reflection_is_retried_then_marked_failed(db, user):
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    job = _end(db, user, session)

    def broken(system, text):
        raise RuntimeError("model down")

    for _ in range(game_learning.MAX_ATTEMPTS):
        assert game_learning.process_job(db, job.id, broken) is False
    db.refresh(job)
    assert job.status == "failed" and "model down" in job.last_error


def test_users_manage_only_their_own_lessons(db, user):
    other = User(name="o", email=f"o-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    db.add(other)
    lesson = GameLesson(user_id=user.id, game="minecraft", normalized_key="a:b", situation="s", advice="a")
    db.add(lesson)
    db.commit()
    with pytest.raises(LearningError):
        game_learning.set_lesson_status(db, other.id, lesson.id, "retired")
    assert game_learning.set_lesson_status(db, user.id, lesson.id, "retired").status == "retired"
    assert lesson.evidence[-1]["verdict"] == "user_retired"


def test_game_episodes_are_recalled_in_conversation(db, user):
    db.add(SemanticMemory(user_id=user.id, normalized_key="preference:x", kind="preference",
                          content="Prefers short replies", importance=0.5, confidence=1.0))
    db.add(EpisodicMemory(user_id=user.id, summary="I fell into a ravine while mining iron in Minecraft.",
                          importance=0.6, context={"source": "game", "game": "minecraft"},
                          event_at=T0))
    db.commit()
    block = long_term_memory.retrieve_for_prompt(user.id, "remember the ravine in minecraft?")
    assert "- [episode, 2026-10-10, game] I fell into a ravine while mining iron in Minecraft." in block


def test_recall_skips_small_talk_prefers_recent_recaps_and_leaves_room_for_facts(db, user):
    """Seen live: every message pulled in 6 game recaps (any shared "you" matched), the oldest often
    first, plus a stale "the bed is in your inventory" fact on importance alone."""
    now = datetime.now(timezone.utc)
    for days in range(5):
        db.add(EpisodicMemory(user_id=user.id, summary=f"Trip {days}: we explored a cave in Minecraft together.",
                              importance=0.7, context={"source": "game"}, event_at=now - timedelta(days=days)))
    db.add(SemanticMemory(user_id=user.id, normalized_key="item:bed", kind="fact", importance=1.0, confidence=0.9,
                          content="The bed is in the assistant's inventory."))
    db.add(SemanticMemory(user_id=user.id, normalized_key="preference:game_discoveries", kind="preference",
                          importance=0.9, confidence=1.0,
                          content="While playing together, tell the user when you find something."))
    db.commit()
    assert long_term_memory.retrieve_for_prompt(user.id, "how was your day?").splitlines() == [
        "- [preference] While playing together, tell the user when you find something."]
    lines = long_term_memory.retrieve_for_prompt(user.id, "remember that cave we explored?").splitlines()
    episodes = [line for line in lines if "episode" in line]
    assert [line.split("] ")[1][:6] for line in episodes] == ["Trip 0", "Trip 1", "Trip 2"]  # 3 at most, newest first
    assert not any("bed" in line for line in lines)


def test_the_fact_saver_sees_earlier_messages_and_reuses_known_keys(db, user, monkeypatch):
    from app.db.models import MemoryJob
    from app.schemas.long_term_memory import MemoryCandidate, MemoryExtraction

    conversation = Conversation(user_id=user.id, title="game")
    db.add(conversation)
    db.commit()
    exchange_turn = None
    for role, text in [("user", "if you find anything, just tell me"), ("assistant", "will do!"),
                       ("user", "there was iron and you didn't say anything"), ("assistant", "sorry, I missed it")]:
        if role == "user":
            exchange_turn = uuid.uuid4()
        db.add(ChatMessage(conversation_id=conversation.id, role=role, content=text, turn_id=exchange_turn))
        db.commit()
    reply = db.scalar(select(ChatMessage).where(ChatMessage.role == "assistant").order_by(ChatMessage.created_at.desc()))
    db.add(SemanticMemory(user_id=user.id, normalized_key="preference:game_discoveries", kind="preference",
                          importance=0.7, confidence=1.0, content="Tell the user when you find something."))
    job = MemoryJob(user_id=user.id, conversation_id=conversation.id, assistant_message_id=reply.id, status="pending",
                    attempts=0)
    db.add(job)
    db.commit()
    seen = {}

    def extract(user_text, assistant_text, earlier=(), known=(), **_context):
        seen.update(user=user_text, earlier=list(earlier), known=list(known))
        return MemoryExtraction(memories=[MemoryCandidate(
            kind="preference", normalized_key="preference:game_discoveries", importance=0.7, confidence=1.0,
            content="Tell the user when you find something, like iron.")])
    monkeypatch.setattr(long_term_memory, "_extract", extract)
    assert long_term_memory.process_job(db, job.id)
    assert seen["user"] == "there was iron and you didn't say anything"
    assert seen["earlier"] == ["USER: if you find anything, just tell me", "ASSISTANT: will do!"]
    assert seen["known"] == ["preference:game_discoveries: Tell the user when you find something."]
    [memory] = db.scalars(select(SemanticMemory)).all()
    assert memory.evidence_count == 2 and memory.content.endswith("like iron.")


def test_reset_game_scope_removes_all_game_learning(db, user):
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    _end(db, user, session)
    db.add(GameLesson(user_id=user.id, game="minecraft", normalized_key="a:b", situation="s", advice="a"))
    db.commit()
    counts = memory_reset.reset(db, user_ids=[user.id], scopes=["game"])
    assert counts["game_experiences"] == 5 and counts["game_lessons"] == 1 and counts["game_reflection_jobs"] == 1
    assert db.scalar(select(GameExperience.id)) is None


def test_plan_lessons_and_the_last_recap_reach_columbina_where_she_plans(db, user, monkeypatch):
    monkeypatch.setattr(desktop_game, "get_settings",
                        lambda: get_settings().model_copy(update={"desktop_agent_enabled": True}))
    session = _pack_session(db, user)
    _play(db, session, CRAFT_FAILS)
    job = _end(db, user, session)
    game_learning.process_job(db, job.id, _reflector(Reflection(
        episode="I made a pickaxe and found a village.", episode_importance=0.5, proposals=[],
        lessons=[_lesson(),
                 _lesson(key="caving:bring_torches", level="plan", evidence=["e1"]).model_copy(
                     update={"situation": "Going underground", "advice": "bring food and torches"})])))
    summary = desktop_game.describe(db, user.id).summary
    assert "What you learned playing Minecraft" in summary
    assert "- When going underground: bring food and torches (tentative: seen once)" in summary
    # Skill lessons reach her too, apart: she plans with how her moves behave.
    assert summary.index("What you learned playing Minecraft") < summary.index("How your moves behave")
    assert "place a crafting table" in summary.split("How your moves behave")[1]
    assert "Last session (your recap, " in summary and "found a village" in summary
    # Her hands get both levels, plan lessons as background.
    levels = {lesson["level"] for lesson in game_learning.lessons_for_run(db, session.id)}
    assert levels == {"skill", "plan"}


def test_the_ab_switch_also_hides_plan_lessons(db, user, monkeypatch):
    db.add(GameLesson(user_id=user.id, game="minecraft", normalized_key="a:b", situation="s", advice="a",
                      level="plan"))
    db.commit()
    assert len(game_learning.plan_lessons(db, user.id, "minecraft")) == 1
    monkeypatch.setattr(game_learning, "get_settings",
                        lambda: get_settings().model_copy(update={"game_learning_apply_lessons": False}))
    assert game_learning.plan_lessons(db, user.id, "minecraft") == []


def test_rules_from_her_plan_tools_are_trimmed_into_the_plan():
    plan = desktop_game._plan("explore the cave", None, ["  keep 5 torches ", "", "x" * 300, *"abcdef"])
    assert plan.constraints[0] == "keep 5 torches" and len(plan.constraints[1]) == 120
    assert len(plan.constraints) == 5


@pytest.mark.parametrize("raw,clause", [
    ("Crafting tools", "crafting tools"), ("When the user says so.", "the user says so"),
    ("if night falls", "night falls"), ("TNT is near", "TNT is near"),
])
def test_situations_read_after_when(raw, clause):
    assert game_learning._clause(raw) == clause

