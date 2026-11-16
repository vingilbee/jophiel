"""Post-audit 3, broaden experience understanding (docs/learning-system/POST-AUDIT-03-implementation.md).

Scripted contract tests: the reviewer and the lesson builder are scripted (her mind's evaluation is prepared and
recorded by its own code, without its model call), so these show what code
guarantees (review opportunities, iterative requests answered by their owners, parked and resumed inquiries,
broader products, tentative delivery to every conversational consumer, revision, follow-up authority, integrity).
They are not evidence that a real model learns well: that is the behavioral acceptance in
docs/learning-system/POST-AUDIT-03-behavioral-acceptance.md, run with real models. Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.learning import assessment as A
from app.ai.learning import lessons as L
from app.ai.mind import hard as H
from app.db import models as M
from app.db.models import (
    ChatMessage, LearningAssessment, LearningAssessmentEvidence, LearningAssessmentRevision, MemoryLesson,
    MemoryLessonUse, WorkingItem,
)
from app.services import learning_assessment as LA
from app.services import learning_capture
from app.services import learning_lessons as LL
from app.services import learning_review as LR
from app.services import memory_lessons as ML
from db_support import create_conversation, create_user, db_env, database  # noqa: F401
from test_learning_assessment import Script, _observe, answer, exchange, say, soon
from test_learning_lessons import Builder, change, evaluate  # noqa: F401

NOW = datetime.now(timezone.utc)


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    settings = LA.get_settings().model_copy(update={
        "learning_assessment_settle_seconds": 0, "learning_assessment_log_path": str(tmp_path / "assess.jsonl"),
        "learning_lessons_log_path": str(tmp_path / "lessons.jsonl"), "lessons_supplied_max": 5,
        "learning_review_pause_seconds": 60, "learning_review_game_seconds": 60})
    holder = {"s": settings}
    for module in (LA, LL, ML, LR):
        monkeypatch.setattr(module, "get_settings", lambda: holder["s"])

    def configure(**updates):
        holder["s"] = holder["s"].model_copy(update=updates)

    db_env.configure_learning = configure
    monkeypatch.setattr(ML, "_embed", lambda text: None)
    return db_env


def setup(env):
    with env.db() as db:
        LA.started_at(db)
        db.commit()
        user = create_user(db)
        conversation = create_conversation(db, user)
    return user, conversation


def opened(env, user, conversation, question="Nora called me today", reply="Oh nice, how is she?",
           follow="she sounded off") -> tuple[uuid.UUID, ChatMessage]:
    """An exchange and the user's next message, triaged: the conversation's review stretch."""
    with env.db() as db:
        exchange(db, user, conversation, question, reply)
        msg = say(db, conversation, "user", follow)
        _observe(db, user, msg)
        LA.triage(db, soon(seconds=1))
        row = LR.latest_conversation_review(db, user.id, conversation.id)
        return row.id, msg


def read(env, aid, script):
    """Claim and assess this assessment (others stay pending)."""
    with env.db() as db:
        others = list(db.scalars(select(LearningAssessment).where(LearningAssessment.id != aid,
                                                                  LearningAssessment.state == "pending")))
        for o in others:
            o.next_attempt_at = soon(days=1)
        db.commit()
        claimed = LA.claim(db, soon(hours=1))
        assert claimed is not None and claimed.id == aid
        for o in others:
            db.get(LearningAssessment, o.id).next_attempt_at = soon(seconds=-1)
        db.commit()
    with env.db() as db:
        return LA.assess(db, claimed, evaluator=script)


def current(env, aid):
    with env.db() as db:
        row = db.get(LearningAssessment, aid)
        rev = db.scalar(select(LearningAssessmentRevision).where(
            LearningAssessmentRevision.assessment_id == aid, LearningAssessmentRevision.revision == row.revision))
        return row, rev


def finding(statement, cites, status="supported", **kw):
    base = {"statement": statement, "form": kw.pop("form", "an expectation"), "status": status, "cites": list(cites),
            "applies_when": kw.pop("applies_when", "in conversation"), "alternatives": kw.pop("alternatives", []),
            "relates_to": kw.pop("relates_to", [])}
    return base


# --------------------------------------------------------------------------- #
# Iterative evidence access (B4)
# --------------------------------------------------------------------------- #


def test_the_reviewer_asks_another_owner_inspects_the_answer_and_cites_it(env):
    user, conversation = setup(env)
    with env.db() as db:
        earlier = create_conversation(db, db.get(M.User, user.id))
        exchange(db, user, earlier, "my sister Nora just moved to Lisbon for a hard new job", "That's a big move.")
        source = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == earlier.id,
                                                     ChatMessage.role == "user"))
    aid, msg = opened(env, user, conversation)

    def second(text):
        assert "# What you asked for" in text and 'q1 conversations "Nora Lisbon": matched' in text
        line = next(x for x in text.splitlines() if "moved to Lisbon" in x)
        ref = line.split()[0]
        assert ref.startswith("r") and "another conversation" in line
        return answer(findings=[finding("Nora's mood may be about the new job in Lisbon", [ref, f"m{msg.conversation_sequence}"],
                                        status="plausible", alternatives=["something else entirely"])],
                      inquiry={"question": "is Nora struggling with the move?", "provisional": "maybe",
                               "alternatives": ["unrelated"], "unknown": "what she said", "resume_when": "they say more"},
                      next={"step": "wait", "look": [], "why": "more may come"})

    script = Script(answer(requests=[{"source": "conversations", "query": "Nora Lisbon"}],
                           next={"step": "look_further", "look": [], "why": "she mentioned Nora before"}), second)
    assert read(env, aid, script) == "revised"
    assert len(script.texts) == 2 and "# What you asked for" not in script.texts[0]
    row, rev = current(env, aid)
    f = rev.result["findings"][0]
    assert f"message:{source.id}" in f["roots"] and f["independent"] == 2
    assert rev.rounds[-1]["plan"]["requests"] == [["conversations", "Nora Lisbon"]]
    # Parked with its state; nothing about it was decided by code.
    assert row.stop_reason == "waiting" and row.inquiry["question"] == "is Nora struggling with the move?"
    assert row.inquiry["requests"] == [["conversations", "Nora Lisbon"]]


def test_a_request_that_finds_nothing_is_a_gap_and_asking_again_for_nothing_parks_it(env):
    user, conversation = setup(env)
    aid, _ = opened(env, user, conversation, question="what's a quokka", reply="A small marsupial.",
                    follow="like the one at the zoo")
    first = answer(requests=[{"source": "conversations", "query": "zoo visit"}],
                   next={"step": "look_further", "look": [], "why": "maybe they went before"})

    def again(text):
        assert 'q1 conversations "zoo visit": no_match' in text  # said, never read as "it didn't happen"
        return answer(requests=[{"source": "memory", "query": "zoo"}],
                      inquiry={"question": "which zoo?", "provisional": "", "alternatives": [], "unknown": "the zoo",
                               "resume_when": "they name it"},
                      next={"step": "look_further", "look": [], "why": "maybe memory"})

    script = Script(first, again, RuntimeError("must not be called: nothing new to read"))
    assert read(env, aid, script) == "revised"
    assert len(script.texts) == 2
    row, rev = current(env, aid)
    assert row.stop_reason == "stuck" and "no new evidence" in rev.result["explanation"] + row.inquiry["stop_why"]
    assert row.inquiry["question"] == "which zoo?" and row.outcome == "unresolved"


def test_every_owner_answers_a_request_with_its_status_and_never_fails_silently(env):
    user, conversation = setup(env)
    aid, _ = opened(env, user, conversation)
    with env.db() as db:
        row = db.get(LearningAssessment, aid)
        for source in ("memory", "conversations", "experiences", "lessons"):
            status, coverage, found = LA._ask(db, row, source, "zebra crossing", set())
            assert status in ("matched", "no_match", "partial") and coverage, (source, status)
        assert LA._ask(db, row, "experiences", "!!", set())[0] == "no_match"


def test_existing_lessons_are_shown_to_compare_with_never_as_evidence(env):
    user, conversation = setup(env)
    with env.db() as db:
        c = L.Change("create", None, None, None, "understanding", "situation", "They joke when stressed",
                     "when deadlines come up", [], [], [f"message:{uuid.uuid4()}"], None, "first",
                     certainty="tentative", form="how they mean things")
        ML.apply(db, user.id, [c], ML.Source("occasion:old", NOW, None, None), embed=lambda t: None)
        db.commit()
        lesson = db.scalar(select(MemoryLesson))
    aid, msg = opened(env, user, conversation, question="deadline tomorrow lol", reply="Ha, you okay?",
                      follow="seriously, I'm not ok")
    m = f"m{msg.conversation_sequence}"

    def compare(text):
        assert "# Her existing lessons, the most relevant and any you asked for" in text and "L1 (revision 1)" in text
        return answer(findings=[finding("a serious remark right after a joke", [m], relates_to=[
                          {"lesson": "L1", "relation": "narrows"}]),
                                finding("cites only the lesson", ["L1"], relates_to=[{"lesson": "L1", "relation": "supports"}])])

    script = Script(answer(requests=[{"source": "lessons", "query": "joke stressed deadline"}],
                           next={"step": "look_further", "look": [], "why": "compare"}), compare)
    read(env, aid, script)
    _, rev = current(env, aid)
    kept = rev.result["findings"]
    assert kept[0]["relates_to"] == [{"lesson": "L1", "id": str(lesson.id), "revision": 1, "relation": "narrows"}]
    # A lesson is never evidence for itself: citing it is dropped, and so is the relation it would carry.
    assert kept[1]["cites"] == [] and kept[1]["relates_to"] == [] and kept[1]["status"] == "unresolved"
    assert any("L1" in d["what"] for d in rev.dropped)


# --------------------------------------------------------------------------- #
# Parking and resuming (B9, B12)
# --------------------------------------------------------------------------- #


def test_a_parked_inquiry_resumes_with_its_state_and_unchanged_or_unrelated_wakes_call_no_model(env):
    user, conversation = setup(env)
    aid, _ = opened(env, user, conversation, question="my boss said 'great job' again", reply="Nice!",
                    follow="yeah... great")
    waiting = answer(review={"decision": "wait", "why": "could be sarcasm, too early"},
                     inquiry={"question": "is 'great' sarcastic here?", "provisional": "probably",
                              "alternatives": ["sincere"], "unknown": "their tone", "resume_when": "more of the talk"})
    assert read(env, aid, Script(waiting)) == "revised"
    row, _ = current(env, aid)
    assert (row.state, row.stop_reason, row.outcome) == ("done", "waiting", "unresolved")
    # A wake with nothing new (a worker restart, a reprocessed signal): no model call, no revision.
    with env.db() as db:
        LA._reopen(db.get(LearningAssessment, aid), NOW, "woken")
        db.commit()
    never = Script(RuntimeError("no call on an unchanged wake"))
    assert read(env, aid, never) == "unchanged" and never.texts == []
    # Unrelated activity elsewhere opens its own opportunity and leaves this one parked.
    with env.db() as db:
        other = create_conversation(db, db.get(M.User, user.id))
    opened(env, user, other, question="weather?", reply="Sunny.", follow="cool")
    row, _ = current(env, aid)
    assert row.state == "done" and row.revision == 1
    # Relevant evidence (more of this conversation) resumes it with its state.
    with env.db() as db:
        msg = say(db, conversation, "user", "honestly I'm sick of him")
        _observe(db, user, msg)
        LA.triage(db, soon(seconds=2))
    resumed = Script(lambda text: answer(findings=[finding("the earlier 'great' was frustrated, not pleased",
                                                           [f"m{msg.conversation_sequence}"], form="an explanation")]))
    assert read(env, aid, resumed) == "revised"
    assert "Open inquiry then: is 'great' sarcastic here?" in resumed.texts[0]
    row, rev = current(env, aid)
    assert row.revision == 2 and row.outcome == "concluded" and row.inquiry == {}


def test_evidence_in_another_conversation_resumes_a_parked_inquiry_without_resetting_it(env):
    user, first = setup(env)
    parked, _ = opened(env, user, first, question="I might quit piano", reply="Oh? Why?", follow="dunno")
    read(env, parked, Script(answer(review={"decision": "wait", "why": "unclear"},
                                    inquiry={"question": "why might they quit piano?", "provisional": "",
                                             "alternatives": ["time", "boredom"], "unknown": "the reason",
                                             "resume_when": "they say why"})))
    with env.db() as db:
        second = create_conversation(db, db.get(M.User, user.id))
    later, msg = opened(env, user, second, question="practice again tonight", reply="How long?",
                        follow="an hour, my teacher keeps yelling at me")
    m = f"m{msg.conversation_sequence}"

    def elsewhere(text):
        line = next(x for x in text.splitlines() if "why might they quit piano" in x)
        assert "(elsewhere)" in line
        return answer(findings=[finding("their teacher yells at them", [m], form="a relationship")],
                      reopens=[{"ref": line.split()[0], "cites": [m], "why": "a reason they might quit"}])

    read(env, later, Script(elsewhere))
    row, _ = current(env, parked)
    assert row.state == "pending" and row.inquiry["resumed_by"] == str(later)
    resumed = Script(lambda text: answer(findings=[finding("they may quit because of the teacher", ["e1"],
                                                           status="plausible", alternatives=["time"])]))
    assert read(env, parked, resumed) == "revised"
    text = resumed.texts[0]
    assert "# Found elsewhere and attached to this" in text and "teacher keeps yelling" in text
    assert "Open inquiry then: why might they quit piano?" in text
    row, rev = current(env, parked)
    assert row.id == parked and row.revision == 2 and rev.result["findings"][0]["roots"] == [f"message:{msg.id}"]


def test_ongoing_play_gets_a_review_before_the_session_ends_and_more_play_joins_it(env):
    user, _ = setup(env)
    with env.db() as db:
        environment = M.DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                                           allowed_profile_ids=["minecraft_pack"], status="online")
        db.add(environment)
        db.flush()
        session = M.DesktopSession(owner_user_id=user.id, environment_id=environment.id, profile_id="minecraft_pack",
                                   profile_revision=1, state="running", control_owner="gameplay",
                                   max_duration_seconds=3600)
        db.add(session)
        db.flush()
        db.add(M.GameExperience(user_id=user.id, session_id=session.id, game="minecraft", kind="skill_outcome",
                                payload={"skill": "walk", "status": "done", "reason": "took the bridge"},
                                occurred_at=NOW - timedelta(minutes=5)))
        db.commit()
        assert LA.sweep(db, soon(seconds=1))["games"] == 1
        db.add(M.GameExperience(user_id=user.id, session_id=session.id, game="minecraft", kind="skill_outcome",
                                payload={"skill": "walk", "status": "done", "reason": "bridge again, faster"},
                                occurred_at=NOW - timedelta(minutes=4)))
        db.commit()
        assert LA.sweep(db, soon(seconds=2))["games"] == 1
        rows = list(db.scalars(select(LearningAssessment)))
    assert len(rows) == 1 and rows[0].trigger_kind == "review" and rows[0].trigger["count"] == 2
    assert "ongoing play" in rows[0].trigger["why"]


# --------------------------------------------------------------------------- #
# Broader products, tentative use by every conversational consumer (B6, B8)
# --------------------------------------------------------------------------- #


def _understanding(env, user, conversation):
    aid, msg = opened(env, user, conversation, question="I said I'd come, but maybe not", reply="Okay!",
                      follow="I mean I want to, I just can't")
    m = f"m{msg.conversation_sequence}"
    read(env, aid, Script(answer(findings=[finding(
        "they said yes and then hedged about coming", [m, f"m{msg.conversation_sequence - 2}"],
        form="an apparent inconsistency", alternatives=["they can't, not won't", "they don't want to"])])))

    def build(text, n):
        ref = next(x.split()[0] for x in text.splitlines() if x.startswith("a") and "finding (" in x)
        return {"changes": [change(kind="understanding", statement="When they hedge after agreeing, it may be about "
                                   "ability rather than willingness", applies_when="when they hedge on plans",
                                   exceptions=[], conclusion_cites=[ref], applicability_cites=[ref],
                                   certainty="tentative", form="how they mean things",
                                   alternatives=["they don't want to and say they can't"])],
                "explanation": "a guess"}

    with env.db() as db:
        claimed = LL.claim(db, soon(hours=1))
    with env.db() as db:
        assert LL.process(db, *claimed, builder=Builder(build), embed=lambda t: None) == "changed"
        return db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))


def test_a_finding_becomes_a_tentative_understanding_that_conversational_consumers_get_as_a_guess(env):
    from app.ai.mind.hard import Problem
    from app.services import mind_hard

    user, conversation = setup(env)
    lesson = _understanding(env, user, conversation)
    assert (lesson.kind, lesson.origin, lesson.status) == ("understanding", "inferred", "hypothesis")
    assert lesson.form == "how they mean things" and lesson.alternatives == ["they don't want to and say they can't"]
    with env.db() as db:
        fresh = create_conversation(db, db.get(M.User, user.id))
        supply, lines = mind_hard.lessons_for(db, user.id, Problem("handoff", "they hedged", "hedge", "hedge"))
    # Her mind's evaluation in a fresh conversation, and her hard thoughts: both get it, labelled as a guess.
    ev, prompt = evaluate(env, user, fresh, "I'd love to come Saturday but hmm")
    assert [v.id for v in ev.lessons.views] == [str(lesson.id)] and ev.lessons.views[0].status == "hypothesis"
    assert "she has come to understand (how they mean things)" in prompt and "a guess" in prompt
    assert "Other explanations: they don't want to and say they can't" in prompt
    assert [v.id for v in supply.views] == [str(lesson.id)] and any("a guess" in x for x in lines)
    with env.db() as db:
        uses = {u.consumer: u for u in db.scalars(select(MemoryLessonUse).where(MemoryLessonUse.user_id == user.id))}
    assert uses["mind"].received and uses["mind"].attempt_id == ev.capture.attempt_id
    # Nothing else stands between her and it: no interpretation stage that couldn't receive it.
    assert set(uses) == {"mind"}


def test_contrary_evidence_withdraws_an_understanding_at_once_and_consumers_stop_receiving_it(env):
    user, conversation = setup(env)
    with env.db() as db:
        c = L.Change("create", None, None, None, "understanding", "situation", "They prefer mornings for calls",
                     "when scheduling calls", [], [], [f"message:{uuid.uuid4()}"], None, "first",
                     certainty="established", form="an expectation")
        ML.apply(db, user.id, [c], ML.Source("occasion:a", NOW, None, None), embed=lambda t: None)
        db.commit()
        lesson = db.scalar(select(MemoryLesson))
        assert lesson.status == "accepted"
        assert [v.id for v in ML.for_decision(db, user.id, "call", include_tentative=True).views] == [str(lesson.id)]
        shown = L.Shown("L1", str(lesson.id), 1, "understanding", "situation", "accepted", lesson.statement,
                        lesson.applies_when, support_from=("occasion:a",), occasions=("occasion:a",))
        b = L.Bundle("a-2", 1, "occasion:b", NOW, "a review", "they said mornings never work")
        b.conclusions["a1"] = L.Conclusion("a1", "finding", "supported", "finding: mornings never work for them",
                                           (f"message:{uuid.uuid4()}",))
        b.lessons["L1"] = shown
        checked = L.check({"changes": [change(op="withdraw", lesson="L1", conclusion_cites=["a1"])]}, b)
        assert [ch.op for ch in checked.changes] == ["withdraw"]  # one strong contrary finding is enough
        ML.apply(db, user.id, checked.changes, ML.Source("occasion:b", NOW + timedelta(seconds=1), None, None),
                 embed=lambda t: None)
        db.commit()
        lesson = db.get(MemoryLesson, lesson.id)
        assert lesson.status == "withdrawn" and "builder judged against it" in lesson.status_reason
        assert ML.for_decision(db, user.id, "call", include_tentative=True).views == ()


# --------------------------------------------------------------------------- #
# Authority, nominations, integrity (B10, B11, reliability)
# --------------------------------------------------------------------------- #


def test_a_follow_up_is_a_proposal_kept_by_memory_never_an_act_of_learning(env):
    user, conversation = setup(env)
    aid, _ = opened(env, user, conversation)
    with env.db() as db:
        before = db.scalar(select(M.func.count()).select_from(ChatMessage))
    read(env, aid, Script(answer(
        findings=[finding("Nora may be unwell", [], status="unresolved")],
        follow_up={"propose": True, "kind": "observation", "text": "notice whether Nora comes up again and how",
                   "resolves": "whether something is wrong with Nora"},
        inquiry={"question": "is Nora okay?", "provisional": "", "alternatives": ["just tired"], "unknown": "",
                 "resume_when": "Nora comes up"}, next={"step": "wait", "look": [], "why": "wait"})))
    with env.db() as db:
        items = list(db.scalars(select(WorkingItem).where(WorkingItem.user_id == user.id)))
        after = db.scalar(select(M.func.count()).select_from(ChatMessage))
        row = db.get(LearningAssessment, aid)
        again = LR.propose(db, row, 1, {"propose": True, "kind": "observation", "text": "again", "resolves": ""}, {})
    assert after == before  # learning said nothing to anyone
    assert [(i.kind, i.owner, i.epistemic, i.closed_at) for i in items] == [("hypothesis", "columbina", "inferred", None)]
    assert "whether and when is her choice" in items[0].reason and "just tired" in items[0].reason
    assert again == f"item:{items[0].id}"  # one proposal per open assessment
    from app.services import memory_reset
    with env.db() as db:
        counts = memory_reset.reset(db, user_ids=[user.id], scopes=["consolidation"])
        assert counts["learning_follow_ups"] == 1
        assert db.get(WorkingItem, items[0].id).closed_at is not None


def test_her_mind_points_learning_at_an_activity_and_the_pointer_is_only_a_signal(env):
    thought = H.parse({"kind": "", "conclusion": "", "learn": "the bridge route got us there faster"})
    assert thought.kind is None and thought.learn == "the bridge route got us there faster"
    assert "learn" in H.SCHEMA["required"]
    user, conversation = setup(env)
    aid, _ = opened(env, user, conversation)
    read(env, aid, Script(answer(review={"decision": "leave", "why": "nothing"})))
    with env.db() as db:
        assert LR.nominate(db, user.id, source="conversation", activity=conversation.id, thought_id=uuid.uuid4(),
                           text="they always bring up Nora when tired")
        db.commit()
        row = db.get(LearningAssessment, aid)
        assert row.state == "pending"  # a pointer resumes the stretch it points at
    script = Script(answer(review={"decision": "leave", "why": "the records don't bear it out"}))
    read(env, aid, script)
    assert 'her mind pointed at this: "they always bring up Nora when tired"' in script.texts[0]
    assert "not evidence" in script.texts[0]
    _, rev = current(env, aid)
    assert all(not r.startswith("nomination:") for f in rev.result["findings"] for r in f["roots"])


def test_a_review_that_repeats_the_same_evidence_adds_no_support(env):
    user, conversation = setup(env)
    lesson = _understanding(env, user, conversation)
    # The same exchange reviewed again under a new stretch (a later message re-cites the earlier lines).
    with env.db() as db:
        row = LR.latest_conversation_review(db, user.id, conversation.id)
        row.outcome = "concluded"
        db.commit()
        msg = say(db, conversation, "user", "anyway")
        _observe(db, user, msg)
        LA.triage(db, soon(seconds=3))
        second = LR.latest_conversation_review(db, user.id, conversation.id)
        old = db.scalar(select(ChatMessage).where(ChatMessage.content == "I mean I want to, I just can't"))
    assert second.id != row.id
    read(env, second.id, Script(lambda text: answer(findings=[finding(
        "they hedged after agreeing", [next(x.split()[0] for x in text.splitlines() if "I just can't" in x)],
        form="an apparent inconsistency")])))

    def support(text, n):
        assert "this experience is already counted for it" in text
        ref = next(x.split()[0] for x in text.splitlines() if x.startswith("a") and "finding (" in x)
        return {"changes": [change(op="support", lesson="L1", conclusion_cites=[ref], certainty="established")],
                "explanation": "same again"}

    with env.db() as db:
        claimed = LL.claim(db, soon(hours=1))
    with env.db() as db:
        LL.process(db, *claimed, builder=Builder(support), embed=lambda t: None)
        lesson = db.get(MemoryLesson, lesson.id)
        assert lesson.status == "hypothesis"
        assert ML.for_decision(db, user.id, "plans", include_tentative=True).views[0].occasions == 1
    assert old is not None


def test_forgetting_a_message_removes_a_stretch_that_only_showed_it(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, her, _, _ = exchange(db, user, conversation, "first", "my reply in the middle")
        msg = say(db, conversation, "user", "ok")
        _observe(db, user, msg)
        LA.triage(db, soon(seconds=1))
        row = LR.latest_conversation_review(db, user.id, conversation.id)
        refs = set(db.scalars(select(LearningAssessmentEvidence.ref).where(
            LearningAssessmentEvidence.assessment_id == row.id)))
        assert f"message:{her.id}" not in refs  # shown in its window, never attached
        assert LA.drop_for(db, user.id, [f"message:{her.id}"]) == 1
        db.commit()
        assert db.get(LearningAssessment, row.id) is None


def test_model_constants_match_the_pure_contracts():
    assert set(M.ASSESSMENT_OUTCOMES) >= {"concluded", "unresolved", "no_feedback", "left"}
    assert set(M.ASSESSMENT_STOPS) >= {"waiting", "left", "stuck", "missing_evidence", "concluded"}
    assert "review" in M.ASSESSMENT_TRIGGERS and set(L.KINDS) == set(M.LESSON_KINDS)
    assert set(A.SCHEMA["required"]) >= {"review", "findings", "requests", "inquiry", "follow_up"}
    assert "clarification" not in A.SCHEMA["properties"]
