"""Post-audit 4, broader learning products (docs/learning-system/POST-AUDIT-04-implementation.md).

Scripted contract tests: the reviewer, the lesson builder and the reconsideration are scripted, so these show what
code guarantees (links that are never support, derived possibilities kept apart from what was tried, noted
occasions that count for nothing, a hypothesis carried forward by memory's own rule, premises whose change every
consumer sees at once, reconsideration on the same evidence that never adds support, and the paused-reassessment
integrity test the design requires). They are not evidence that a real model learns well: that is the behavioral
acceptance (evals/post_audit_04, scripts/broader_learning_eval.py). Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.ai.learning import assessment as A
from app.ai.learning import lessons as L
from app.ai.mind import hard as H
from app.db import models as M
from app.db.models import (
    LearningLessonJob, MemoryLesson, MemoryLessonEvidence, MemoryLessonLink, MemoryLessonRevision, SemanticMemory,
    WorkingItem,
)
from app.services import learning_assessment as LA
from app.services import learning_lessons as LL
from app.services import learning_review as LR
from app.services import memory_lessons as ML
from app.services import memory_manager
from db_support import create_conversation, create_user, db_env, database  # noqa: F401
from test_learning_assessment import Script, _observe, answer, exchange, say, soon

NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Pure: the builder's contract
# --------------------------------------------------------------------------- #


def bundle(**kw) -> L.Bundle:
    b = L.Bundle("a-1", 1, "review:x", NOW, "a review", "")
    b.lines["m1"] = L.Line("m1", "user", 'm1 [user] "phone in the kitchen helped but the silence was awful"',
                           "phone in the kitchen helped but the silence was awful", ("message:u1",))
    b.lines["m2"] = L.Line("m2", "columbina", 'm2 [columbina] "So the phone away helped."', "So the phone away helped.",
                           ("message:h1",))
    b.conclusions["a1"] = L.Conclusion("a1", "finding", "supported", "finding: phone away helped focus",
                                       ("message:u1",))
    b.lessons["L1"] = L.Shown("L1", str(uuid.uuid4()), 2, "understanding", "situation", "accepted",
                              "Sam focuses better with music on", "when Sam studies")
    b.related["M1"] = L.Related("M1", "item", str(uuid.uuid4()), 1, "open", "Sam may study better away from the phone",
                                "her hypothesis", "same_as", carryable=True)
    b.related["M2"] = L.Related("M2", "fact", str(uuid.uuid4()), 3, "active", "Sam focuses with lo-fi on",
                                "a fact memory holds about the user", "builds_on")
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def created(**kw) -> dict:
    base = {"op": "create", "lesson": "", "kind": "strategy", "scope": "situation",
            "statement": "Phone in another room with lo-fi on may let Sam focus", "applies_when": "when Sam studies",
            "exceptions": [], "user_quote": "", "conclusion_cites": ["a1"], "applicability_cites": ["m1"],
            "conflicts_with": [], "same_as_withdrawn": "", "why": "combines the two", "certainty": "established",
            "form": "", "alternatives": [], "untested": "both together", "tried": "untried", "needs": "",
            "would_support": "", "would_challenge": "", "links": [], "carries": []}
    base.update(kw)
    return base


def test_a_derived_possibility_keeps_its_basis_and_stays_a_guess_until_tried():
    b = bundle()
    checked = L.check({"changes": [created(links=[{"ref": "L1", "relation": "builds_on", "why": "music"},
                                                  {"ref": "M2", "relation": "builds_on", "why": "the fact"},
                                                  {"ref": "M2", "relation": "supports", "why": "twice"},
                                                  {"ref": "Z9", "relation": "depends_on", "why": "unknown"}])],
                       "explanation": "x"}, b)
    (c,) = checked.changes
    # Untested and not tried: established would claim it works, so it stays tentative (code, not the builder).
    assert c.certainty == "tentative" and c.details == {"untested": "both together", "tried": "untried"}
    assert [(x["target_kind"], x["relation"]) for x in c.links] == [("lesson", "builds_on"), ("fact", "builds_on")]
    reasons = " | ".join(d["why"] for d in checked.dropped)
    assert "untested part" in reasons and "same memory" in reasons and "not a lesson or memory" in reasons


def test_tried_needs_a_record_of_her_doing_it():
    b = bundle()
    no = L.check({"changes": [created(tried="tried", untested="", applicability_cites=["m1"])],
                  "explanation": ""}, b).changes[0]
    assert "tried" not in no.details
    yes = L.check({"changes": [created(tried="tried", untested="", applicability_cites=["m1", "m2"])],
                   "explanation": ""}, b).changes[0]
    assert yes.details.get("tried") == "tried" and yes.certainty == "established"


def test_a_note_counts_for_nothing_and_carrying_needs_an_open_hypothesis_of_hers():
    b = bundle()
    checked = L.check({"changes": [
        {**created(op="note", lesson="L1", certainty="established", conclusion_cites=["a1"]), "why": "a weekday"},
        created(statement="Sam may need the phone away", untested="", tried="", carries=["M1", "M2"]),
    ], "explanation": ""}, b)
    note, made = checked.changes
    assert note.op == "note" and note.certainty is None and note.carries == []
    assert made.carries == [b.related["M1"].target_id]
    assert any("open hypothesis" in d["why"] for d in checked.dropped)


def test_her_own_earlier_inference_is_never_citable_evidence():
    ev = A.Evidence("a stretch", ())
    ev.add(A.Item("m1", "user", "message:1", ("message:1",), 'm1 [user] "x"', words="x"))
    ev.add(A.Item("r1", "memory", "memory:item:h", ("item:h",), "[memory her own hypothesis]", own_inference=True,
                  memory="item:h"))
    ev.add(A.Item("r2", "memory", "memory:fact:f", ("fact:f", "message:0"), "[memory fact]", memory="fact:f"))
    raw = {"findings": [
        {"statement": "only her guess", "form": "", "status": "supported", "cites": ["r1"], "applies_when": "",
         "alternatives": [], "relates_to": [], "untested": "", "needs": "", "would_support": "", "would_challenge": ""},
        {"statement": "the same idea", "form": "", "status": "supported", "cites": ["m1", "r1"], "applies_when": "",
         "alternatives": [], "relates_to": [{"lesson": "r1", "relation": "same_as"},
                                            {"lesson": "r2", "relation": "depends_on"}],
         "untested": "", "needs": "a boat", "would_support": "", "would_challenge": ""}]}
    out = A.check(raw, ev).result["findings"]
    assert out[0]["status"] == "unresolved" and out[0]["cites"] == []
    assert out[1]["status"] == "supported" and out[1]["roots"] == ["message:1"]
    assert out[1]["relates_to"] == [{"lesson": "r1", "target": "item:h", "relation": "same_as"},
                                    {"lesson": "r2", "target": "fact:f", "relation": "depends_on"}]
    assert out[1]["details"] == {"needs": "a boat"}


def test_reconsidering_the_same_evidence_never_raises_certainty_or_widens_scope():
    s = L.Shown("L", str(uuid.uuid4()), 3, "understanding", "situation", "hypothesis", "Kim dislikes questions",
                "with Kim", support_from=("o1",))
    r = L.Reconsider(s, "critique", "she was boarding")
    r.evidence["e1"] = L.Examined("e1", "support", '[user] "stop asking so many questions"', ("message:1",))
    base = {"scope": "standing", "statement": "", "applies_when": "when Kim is rushed", "exceptions": [],
            "certainty": "established", "form": "", "alternatives": ["it was the rush"], "untested": "", "needs": "",
            "would_support": "", "would_challenge": "", "cites": ["e1"], "independent": [], "why": "one rushed moment",
            "explanation": ""}
    (c,) = L.check_reconsider({**base, "decision": "narrow"}, r).changes
    assert c.op == "narrow" and c.scope == "situation" and c.certainty is None and c.prompted_by == "critique"
    assert c.applies_when == "when Kim is rushed" and c.alternatives == ["it was the rush"]
    (k,) = L.check_reconsider({**base, "decision": "withdraw", "cites": []}, r).changes
    assert k.op == "keep"  # nothing examined is cited: kept as it is
    pref = L.Shown("L", str(uuid.uuid4()), 1, "preference", "situation", "accepted", "Listen", "venting",
                   user_words=("just listen",))
    assert L.check_reconsider({**base, "decision": "withdraw"}, L.Reconsider(pref, "critique", "x")).changes[0].op == "keep"


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    settings = LA.get_settings().model_copy(update={
        "learning_assessment_settle_seconds": 0, "learning_assessment_log_path": str(tmp_path / "assess.jsonl"),
        "learning_lessons_log_path": str(tmp_path / "lessons.jsonl"), "lessons_supplied_max": 5,
        "memory_log_path": str(tmp_path / "memory.jsonl")})
    for module in (LA, LL, ML, LR):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(ML, "_embed", lambda text: None)
    return db_env


def setup(env):
    with env.db() as db:
        LA.started_at(db)
        db.commit()
        user = create_user(db)
        conversation = create_conversation(db, user)
        db.expunge_all()
    return user, conversation


def seed(db, user, statement, *, kind="strategy", certainty="established", occasion=None, refs=None, **kw):
    """A lesson as memory holds it, resting on one occasion (a controlled record)."""
    c = L.Change("create", None, None, None, kind, kw.pop("scope", "situation"), statement,
                 kw.pop("applies_when", "in general"), [], [], refs or [f"message:{uuid.uuid4()}"], None, "seeded",
                 certainty=certainty, form=kw.pop("form", None), details=kw.pop("details", {}),
                 links=kw.pop("links", []))
    applied = ML.apply(db, user.id, [c], ML.Source(occasion or f"seed:{uuid.uuid4()}", NOW, None, None),
                       embed=lambda t: None)
    return db.get(MemoryLesson, uuid.UUID(applied.changed[0]["lesson"]))


def fact(db, user, content) -> SemanticMemory:
    row = SemanticMemory(user_id=user.id, kind="fact", normalized_key=f"fact:{uuid.uuid4()}", content=content,
                         importance=0.6, confidence=0.9)
    db.add(row)
    db.flush()
    return row


def link_to(target_kind, target_id, relation="depends_on", why="it only holds while that does"):
    return {"target_kind": target_kind, "target_id": str(target_id), "relation": relation, "why": why}


def lesson_lines(db, user, query="Fridays") -> str:
    return "\n".join(L.decision_lines(ML.for_decision(db, user.id, query, include_tentative=True).views))


class Reconsiderer:
    """Scripted reconsideration: each call takes the next answer (a dict, or a function of the text)."""

    name = f"{L.RECONSIDER_VERSION}:scripted"

    def __init__(self, *answers):
        self.answers, self.texts = list(answers), []

    def __call__(self, text):
        self.texts.append(text)
        raw = self.answers.pop(0)
        raw = raw(text) if callable(raw) else raw
        return L.Answer(raw, 1.0)


def decided(decision, **kw) -> dict:
    base = {"decision": decision, "scope": "situation", "statement": "", "applies_when": "", "exceptions": [],
            "certainty": "tentative", "form": "", "alternatives": [], "untested": "", "needs": "",
            "would_support": "", "would_challenge": "", "cites": ["P1"], "independent": [], "why": "the premise moved",
            "explanation": "read again"}
    base.update(kw)
    return base


def reconsider_jobs(env) -> list[LearningLessonJob]:
    with env.db() as db:
        return list(db.scalars(select(LearningLessonJob).where(LearningLessonJob.kind == "reconsider")
                               .order_by(LearningLessonJob.created_at)))


def run_job(env, job_id, script) -> str:
    with env.db() as db:
        job = db.get(LearningLessonJob, job_id)
        job.state, job.attempts = "running", job.attempts + 1
        attempts = job.attempts
        db.commit()
    with env.db() as db:
        return LL.process(db, job_id, attempts, reconsiderer=script)


def consumer_inputs(env, user) -> dict[str, str]:
    """What each available consumer's model would read about lessons now, rendered by its own code."""
    from app.ai.desktop import skill_driver
    from app.services import desktop_game, mind_hard

    out = {}
    with env.db() as db:
        out["conversation"] = lesson_lines(db, user)
        _, lines = mind_hard.lessons_for(db, user.id, type("P", (), {"text": "Fridays"})())
        out["mind"] = "\n".join(lines)
        out["planner"] = "\n".join(desktop_game._learned_lines(db, user.id, "minecraft"))
        out["hands"] = skill_driver.lessons_text([v.to_dict() for v in ML.for_game(db, user.id, "minecraft").views])
    # Her mind's evaluation, prepared by its own code (what a wake reads, from the database at that moment).
    from app.services import mind_turn

    ev = mind_turn.Evaluation(user_id=user.id, conversation_id=None, request_id=uuid.uuid4(), reason="user_message")
    mind_turn._lessons(ev, "Fridays")
    out["mind_evaluation"] = mind_turn.lessons_text(ev) or ""
    return out


# --------------------------------------------------------------------------- #
# The paused-reassessment integrity test (POST-AUDIT-04, "Required paused-reassessment integrity test")
# --------------------------------------------------------------------------- #


def test_a_changed_premise_is_visible_to_every_consumer_while_reassessment_is_paused(env, monkeypatch):
    user, _ = setup(env)
    monkeypatch.setattr(LA.get_settings(), "lessons_supply_enabled", True, raising=False)
    with env.db() as db:
        wfh = fact(db, user, "Lena works from home on Fridays")
        midday = seed(db, user, "A short midday check-in on Fridays is welcome", applies_when="Fridays at lunch",
                      links=[link_to("fact", wfh.id)])
        other = seed(db, user, "Lena values it when her family's events are remembered", kind="understanding",
                     certainty="tentative")
        db.commit()
        midday_id, other_id, other_rev, fact_id = midday.id, other.id, other.revision, wfh.id
        before = consumer_inputs(env, user)
    for name, text in before.items():
        line = next((x for x in text.splitlines() if "midday check-in" in x), "")
        assert line and "UNSETTLED" not in line, (name, text)
    assert "Rests on a fact memory holds about the user" in before["conversation"]

    # The premise changes through memory's own path and commits; the reassessment worker is held (never run).
    with env.db() as db:
        row = db.get(SemanticMemory, fact_id)
        row.content, row.revision = "Lena works in the office on Fridays now", row.revision + 1
        db.commit()

    def visible(seen: dict[str, str]):
        for name, text in seen.items():
            line = next((x for x in text.splitlines() if "midday check-in" in x), "")
            assert "UNSETTLED" in line and "Lena works from home on Fridays" in line, (name, text)
            assert "works in the office on Fridays now" in line and "being reassessed" in line, (name, line)
        assert "Status: accepted, UNSETTLED" in seen["conversation"]

    visible(consumer_inputs(env, user))
    # A restart: nothing held in memory between sessions; the same state is visible from the database alone.
    from app.services import memory_lessons as fresh
    visible(consumer_inputs(env, user))
    with env.db() as db:
        assert fresh.changed_lessons(db)[0][0].id == midday_id

    # Released: the sweep queues one reassessment (once per premise state), the LLM's decision is applied.
    with env.db() as db:
        assert LL.sweep_premises(db) == 1 and LL.sweep_premises(db) == 0
        db.commit()
    (job,) = reconsider_jobs(env)
    assert job.input["prompted_by"] == "premise" and job.lesson_id == midday_id

    def read_again(text):
        assert "it depends on a fact memory holds about the user" in text and "CHANGED" in text
        assert "works in the office on Fridays now" in text
        return decided("narrow", applies_when="Fridays at lunch, only if she is working from home that day",
                       why="she is in the office on Fridays now, so midday may not suit", cites=["P1"])

    assert run_job(env, job.id, Reconsiderer(read_again)) == "narrow"
    with env.db() as db:
        row = db.get(MemoryLesson, midday_id)
        rev = db.scalar(select(MemoryLessonRevision).where(MemoryLessonRevision.lesson_id == midday_id,
                                                           MemoryLessonRevision.revision == row.revision))
        assert rev.prompted_by == "premise" and "office" in rev.reason and row.status == "hypothesis"
        # No evidence was added and nothing counted: a reassessment is not an occasion.
        assert db.scalar(select(func.count()).select_from(MemoryLessonEvidence)
                         .where(MemoryLessonEvidence.lesson_id == midday_id)) == 1
        untouched = db.get(MemoryLesson, other_id)
        assert (untouched.revision, untouched.status) == (other_rev, "hypothesis")
        assert ML.changed_lessons(db) == [] and LL.sweep_premises(db) == 0
    after = consumer_inputs(env, user)
    line = next(x for x in after["conversation"].splitlines() if "midday check-in" in x)
    assert "UNSETTLED" not in line and "only if she is working from home" in line
    assert "reassessed: she is in the office on Fridays now" in line


def test_a_withdrawn_premise_lesson_unsettles_its_dependents_down_a_chain_and_keep_settles_them(env):
    user, _ = setup(env)
    with env.db() as db:
        village = seed(db, user, "The east village blacksmith has iron", kind="understanding", certainty="tentative")
        route = seed(db, user, "Get iron from the east village", links=[link_to("lesson", village.id)])
        trip = seed(db, user, "Plan the iron trip for the morning", links=[link_to("lesson", route.id)])
        # A link that would make a lesson rest on itself is refused.
        c = L.Change("support", "L", str(village.id), village.revision, "understanding", "situation",
                     village.statement, village.applies_when, [], [], [f"message:{uuid.uuid4()}"], None, "x",
                     links=[link_to("lesson", trip.id)])
        applied = ML.apply(db, user.id, [c], ML.Source(f"seed:{uuid.uuid4()}", NOW, None, None))
        assert any("rest on itself" in s["why"] for s in applied.skipped)
        db.commit()
        ids = village.id, route.id, trip.id
        withdraw = L.Change("withdraw", "L", str(village.id), village.revision, "understanding", "situation",
                            village.statement, village.applies_when, [], [], [f"message:{uuid.uuid4()}"], None,
                            "the chest is empty")
        ML.apply(db, user.id, [withdraw], ML.Source(f"seen:{uuid.uuid4()}", NOW, None, None))
        db.commit()
        text = "\n".join(L.decision_lines(ML.for_game(db, user.id, "minecraft").views))
    route_line = next(x for x in text.splitlines() if "Get iron" in x)
    trip_line = next(x for x in text.splitlines() if "iron trip" in x)
    assert "is now withdrawn" in route_line and "UNSETTLED" in route_line
    assert "itself unsettled" in trip_line and "UNSETTLED" in trip_line
    with env.db() as db:
        LL.sweep_premises(db)
        db.commit()
    (job,) = reconsider_jobs(env)  # only the direct dependent: the chain settles as each is reassessed
    assert job.lesson_id == ids[1]
    assert run_job(env, job.id, Reconsiderer(decided("keep", why="the cave near the base also has iron",
                                                     cites=["P1"]))) == "keep"
    with env.db() as db:
        row = db.get(MemoryLesson, ids[1])
        assert row.revision == 1 and row.status == "accepted"  # keep changes nothing it says
        text = "\n".join(L.decision_lines(ML.for_game(db, user.id, "minecraft").views))
    route_line = next(x for x in text.splitlines() if "Get iron" in x)
    assert "UNSETTLED" not in route_line and "Rested on another lesson of hers" in route_line
    assert "since withdrawn (reassessed: the cave near the base also has iron)" in route_line
    assert "UNSETTLED" not in next(x for x in text.splitlines() if "iron trip" in x)


def test_a_closed_hypothesis_premise_and_a_forgotten_fact_are_both_visible_changes(env):
    user, conversation = setup(env)
    with env.db() as db:
        away = memory_manager.add_item(db, user.id, kind="hypothesis", text_="Jamie is away until Sunday",
                                       owner="columbina", epistemic="inferred", reason="Chris said so")
        gone = fact(db, user, "Chris likes long evening chats")
        evening = seed(db, user, "Chris may welcome longer evening chats this week", kind="understanding",
                       certainty="tentative", links=[link_to("item", away.id), link_to("fact", gone.id, "builds_on")])
        db.commit()
        memory_manager.resolve(db, user.id, away.id, status="disproved", reason="Jamie came back early",
                               source=memory_manager.Source("message", say(db, conversation, "user", "Jamie's back").id))
        db.delete(db.get(SemanticMemory, gone.id))
        db.commit()
        line = lesson_lines(db, user, "evening")
    assert "her hypothesis that said \"Jamie is away until Sunday\", which is now closed as disproved" in line
    assert "builds on a fact memory holds about the user that said \"Chris likes long evening chats\", which is gone" in line
    assert evening.id


# --------------------------------------------------------------------------- #
# Reconsideration on the same evidence (a critique)
# --------------------------------------------------------------------------- #


def test_a_critique_revises_on_the_same_evidence_without_support_and_unchanged_wakes_call_no_model(env):
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "at the gate, boarding in 5", "Where to? Window seat? Work trip?")
        msg = say(db, conversation, "user", "omg stop asking so many questions, they're calling my row")
        lesson = seed(db, user, "Kim dislikes being asked follow-up questions", kind="understanding",
                      scope="standing", applies_when="in conversation with Kim", refs=[f"message:{msg.id}"])
        db.commit()
        lesson_id, before = lesson.id, ML._counts(db, [lesson.id])[lesson.id]
        assert LL.critique(db, user.id, lesson_id, "Kim was boarding; that was about the rush", source="test")
        assert not LL.critique(db, user.id, lesson_id, "Kim was  boarding; that was about the rush", source="test")
        db.commit()
    (job,) = reconsider_jobs(env)

    def read_again(text):
        assert "Her mind questioned its reasoning" in text and "stop asking so many questions" in text
        ref = next(x.split()[0] for x in text.splitlines() if "stop asking" in x and x.startswith("e"))
        return decided("narrow", scope="standing", applies_when="when Kim is rushed (boarding, busy)",
                       certainty="established", alternatives=["it was only the boarding moment"], cites=[ref],
                       why="one rushed message was read as a general dislike")

    assert run_job(env, job.id, Reconsiderer(read_again)) == "narrow"
    with env.db() as db:
        row = db.get(MemoryLesson, lesson_id)
        rev = db.scalar(select(MemoryLessonRevision).where(MemoryLessonRevision.lesson_id == lesson_id,
                                                           MemoryLessonRevision.revision == row.revision))
        after = ML._counts(db, [lesson_id])[lesson_id]
        assert rev.prompted_by == "critique" and "one rushed message" in rev.reason
        assert row.applies_when.startswith("when Kim is rushed") and row.alternatives == ["it was only the boarding moment"]
        # Certainty didn't rise (it stays what it was: the builder's "established" here is dropped as not new).
        assert row.status == "accepted" and after["support"] == before["support"]
        assert db.scalar(select(func.count()).select_from(MemoryLessonEvidence)
                         .where(MemoryLessonEvidence.lesson_id == lesson_id)) == 1
        # The same critique again: nothing queued. The worker woken with nothing new: no model call.
        assert not LL.critique(db, user.id, lesson_id, "Kim was boarding; that was about the rush", source="test")
        assert LL.sweep_premises(db) == 0
        db.commit()
        assert LL.claim(db, soon(hours=1)) is None


# --------------------------------------------------------------------------- #
# Cross-owner reconciliation, notes, carrying forward
# --------------------------------------------------------------------------- #


def _assessed_review(env, user, conversation, script):
    with env.db() as db:
        exchange(db, user, conversation, "mm", "Morning! Everything okay?")
        msg = say(db, conversation, "user", "sorry, just off a 12 hour shift, brain is soup")
        _observe(db, user, msg)
        LA.triage(db, soon(seconds=1))
        db.commit()
        aid = LR.latest_conversation_review(db, user.id, conversation.id).id
        claimed = LA.claim(db, soon(hours=1))
    with env.db() as db:
        LA.assess(db, claimed, evaluator=script)
    return aid, msg


def test_a_finding_relates_to_memory_and_the_builder_links_and_carries_without_support(env):
    user, conversation = setup(env)
    with env.db() as db:
        nights = fact(db, user, "Dana works night shifts at the hospital")
        tired = memory_manager.add_item(db, user.id, kind="hypothesis", owner="columbina", epistemic="inferred",
                                        text_="Dana's curt mornings might be tiredness", reason="short replies")
        brief = seed(db, user, "Dana's early-morning messages are very short; not upset with Jophiel",
                     kind="understanding", certainty="tentative", applies_when="Dana writing early")
        db.commit()
        ids = nights.id, tired.id, brief.id

    def first(text):
        assert "# Her existing lessons, the most relevant" in text  # shown without being asked
        return answer(requests=[{"source": "memory", "query": "Dana night shift tired mornings"}],
                      next={"step": "look_further", "look": [], "why": "memory may hold it"})

    def second(text):
        refs = {}
        for line in text.splitlines():
            if "[memory" in line and "night shifts" in line:
                refs["fact"] = line.split()[0]
            if "[memory her own hypothesis" in line:
                refs["item"] = line.split()[0]
                assert "Her own inference: never evidence" in line
        m = next(x.split()[0] for x in text.splitlines() if "12 hour shift" in x and x.startswith("m"))
        lesson = next(x.split()[0] for x in text.splitlines() if x.startswith("L") and "early-morning" in x)
        return answer(findings=[{"statement": "Dana's curt morning messages come right after night shifts",
                                 "form": "an explanation", "status": "supported", "cites": [m, refs["item"]],
                                 "applies_when": "Dana's mornings", "alternatives": [],
                                 "relates_to": [{"lesson": refs["fact"], "relation": "depends_on"},
                                                {"lesson": refs["item"], "relation": "same_as"},
                                                {"lesson": lesson, "relation": "supports"}],
                                 "untested": "", "needs": "", "would_support": "", "would_challenge": ""}])

    aid, msg = _assessed_review(env, user, conversation, Script(first, second))
    with env.db() as db:
        row = db.get(M.LearningAssessment, aid)
        rev = db.scalar(select(M.LearningAssessmentRevision).where(M.LearningAssessmentRevision.assessment_id == aid))
        f = rev.result["findings"][0]
        assert f["roots"] == [f"message:{msg.id}"]  # her hypothesis was dropped from what it rests on
        assert {r.get("target") for r in f["relates_to"]} >= {f"fact:{ids[0]}", f"item:{ids[1]}"}
        job = db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == aid))
        job.state, job.attempts = "running", 1
        db.commit()
        bundle_text = L.render(LL.bundle_for(db, row, rev))
    assert "# Other memories the reviewer related" in bundle_text and "an open hypothesis of hers" in bundle_text

    def build(text, n=0):
        a = next(x.split()[0] for x in text.splitlines() if x.startswith("a") and "night shifts" in x)
        lesson = next(x.split()[0] for x in text.splitlines() if x.startswith("L") and "early-morning" in x)
        fact_ref = next(x.split()[0] for x in text.splitlines() if x.startswith("M") and "night shifts" in x)
        item_ref = next(x.split()[0] for x in text.splitlines() if x.startswith("M") and "tiredness" in x)
        return {"changes": [{**created(op="support", lesson=lesson, certainty="tentative", untested="", tried="",
                                       conclusion_cites=[a], applicability_cites=[a]),
                             "links": [{"ref": fact_ref, "relation": "depends_on", "why": "night shifts"}],
                             "carries": [item_ref]}], "explanation": "linked"}

    from test_learning_lessons import Builder
    with env.db() as db:
        LL.process(db, job.id, 1, builder=Builder(build))
    with env.db() as db:
        links = {(l.target_kind, l.relation) for l in db.scalars(select(MemoryLessonLink)
                                                                .where(MemoryLessonLink.lesson_id == ids[2]))}
        assert links == {("fact", "depends_on"), ("item", "same_as")}
        item = db.get(WorkingItem, ids[1])
        assert item.status == "unresolved" and str(ids[2]) in item.status_reason
        assert db.get(SemanticMemory, ids[0]).content == "Dana works night shifts at the hospital"
        evidence = list(db.scalars(select(MemoryLessonEvidence).where(MemoryLessonEvidence.lesson_id == ids[2])))
        assert all(not any(r.startswith(("item:", "fact:")) for r in e.refs) for e in evidence)
        assert len(ML._counts(db, [ids[2]])[ids[2]]["support"]) == 2  # the seed and this stretch, nothing more
        # Closing the hypothesis it carries is not a changed premise of its own.
        assert ML.changed_lessons(db) == []


def test_a_noted_occasion_is_coverage_counted_for_nothing(env):
    user, _ = setup(env)
    with env.db() as db:
        lesson = seed(db, user, "Raj replies fast on weekends", kind="understanding", certainty="tentative",
                      details={"would_support": "quick weekend replies", "would_challenge": "slow weekend replies"})
        note = L.Change("note", "L1", str(lesson.id), lesson.revision, "understanding", "situation",
                        lesson.statement, lesson.applies_when, [], [], [f"message:{uuid.uuid4()}"], None,
                        "a weekend without signal: its conditions didn't hold")
        out = ML.apply(db, user.id, [note], ML.Source("review:hike", NOW, None, None))
        db.commit()
        row = db.get(MemoryLesson, lesson.id)
        counts = ML._counts(db, [lesson.id])[lesson.id]
        assert out.changed[0]["note"] == "noted" and row.revision == 1
        assert (len(counts["support"]), len(counts["counter"]), len(counts["noted"])) == (1, 0, 1)
        learned = ML.for_learning(db, user.id, "weekend replies")[0].text
    assert "noted without either on 1" in learned and "Would be supported by: quick weekend replies" in learned


def test_a_hard_thought_can_point_a_critique_at_a_lesson_it_read():
    out = H.parse({"kind": "guess", "conclusion": "maybe it was the rush", "thinking": "",
                   "reconsider": {"lesson": "L2", "why": "one rushed moment"}})
    assert out.reconsider == {"lesson": "L2", "why": "one rushed moment"}
    assert H.parse({"kind": "", "conclusion": "", "reconsider": {"lesson": "", "why": "x"}}).reconsider is None
    assert "reconsider" in H.SCHEMA["required"]


def test_model_constants_match_the_pure_contract():
    assert set(L.LINKS) | {"related"} == set(M.LESSON_LINK_RELATIONS)
    assert set(L.BASIS) == set(M.LESSON_BASIS_RELATIONS)
    assert set(L.OPS) <= set(M.LESSON_OPS) and "keep" in M.LESSON_OPS and "noted" in M.LESSON_EVIDENCE_ROLES


def test_a_lesson_built_on_one_the_same_reading_revised_is_not_unsettled(env):
    user, _ = setup(env)
    with env.db() as db:
        base = seed(db, user, "Sam focuses with music on", kind="understanding", certainty="tentative")
        db.commit()
        revise = L.Change("revise", "L1", str(base.id), base.revision, "understanding", "situation",
                          "Sam focuses with lo-fi on, not with silence", base.applies_when, [], [],
                          [f"message:{uuid.uuid4()}"], None, "better reading")
        made = L.Change("create", None, None, None, "strategy", "situation", "Phone away plus lo-fi may help Sam",
                        "when Sam studies", [], [], [f"message:{uuid.uuid4()}"], None, "combined",
                        certainty="tentative", details={"untested": "together", "tried": "untried"},
                        links=[link_to("lesson", base.id, "builds_on", "music")])
        ML.apply(db, user.id, [revise, made], ML.Source("review:study", NOW, None, None))
        db.commit()
        line = next(x for x in lesson_lines(db, user, "Sam").splitlines() if "Phone away" in x)
    assert "UNSETTLED" not in line and 'Builds on another lesson of hers: "Sam focuses with lo-fi on' in line
    assert "Not tried yet" in line and "Untested: together" in line


def test_memory_relevant_to_a_stretch_is_shown_unasked_and_her_hypotheses_stay_uncitable(env, monkeypatch):
    from app.ai.recall.contracts import MemoryRef
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "mm", "Morning! Everything okay?")
        msg = say(db, conversation, "user", "sorry, just off a 12 hour shift")
        tired = memory_manager.add_item(db, user.id, kind="hypothesis", owner="columbina", epistemic="inferred",
                                        text_="Dana's curt mornings might be tiredness", reason="short replies")
        own = fact(db, user, "Dana said she just got off a 12 hour shift")
        own.source_message_id = msg.id
        db.commit()
        row = LR.conversation(db, user.id, msg, why="a completed exchange")
        db.commit()

        def found(db_, row_, source, query, shown):
            return "matched", "x", [
                A.Item("", "memory", f"memory:item:{tired.id}", (f"item:{tired.id}",), "[memory her own hypothesis] tired",
                       own_inference=True, memory=f"item:{tired.id}"),
                A.Item("", "memory", f"memory:fact:{own.id}", (f"fact:{own.id}", f"message:{msg.id}"), "[memory fact]",
                       memory=f"fact:{own.id}")]

        monkeypatch.setattr(LA, "_ask", found)
        ev = LA.gather(db, db.get(M.LearningAssessment, row.id), LA.Plan()).ev
    text = A.render(ev)
    assert "# What memory holds that may bear on this" in text and "her own hypothesis" in text
    related = [i for i in ev.items.values() if i.group == "related"]
    assert [i.memory for i in related] == [f"item:{tired.id}"]  # memory's record of this very message is left out
    assert related[0].own_inference and '"user" below is Test user' in text


def test_a_reconsideration_never_hides_a_withdrawal_as_one_past_episode():
    s = L.Shown("L", str(uuid.uuid4()), 2, "strategy", "situation", "hypothesis", "Take the bridge to the mine",
                "going to the mine", support_from=("o1",))
    r = L.Reconsider(s, "premise", None)
    r.premises["P1"] = L.Premise("P1", "depends_on", "another lesson of hers", "the bridge is intact", "hypothesis",
                                 "the bridge is intact", "withdrawn", True)
    raw = {"decision": "narrow", "scope": "episode", "statement": "", "applies_when": "that one trip",
           "exceptions": [], "certainty": "tentative", "form": "", "alternatives": [], "untested": "", "needs": "",
           "would_support": "", "would_challenge": "", "cites": ["P1"], "independent": [], "why": "one trip only",
           "explanation": ""}
    checked = L.check_reconsider(raw, r)
    (c,) = checked.changes
    assert c.scope == "situation" and any("past episode" in d["why"] for d in checked.dropped)
    withdrawn = L.check_reconsider({**raw, "decision": "withdraw"}, r).changes[0]
    assert withdrawn.op == "withdraw"


def test_a_lesson_never_carries_a_hypothesis_it_challenges_and_a_fact_is_never_narrowed_by_learning():
    b = bundle()
    out = L.check({"changes": [created(statement="Pia avoided Lea because of the boyfriend, not the cat",
                                       untested="", tried="", carries=["M1"],
                                       links=[{"ref": "M1", "relation": "challenges", "why": "not the cat"}])],
                   "explanation": ""}, b)
    assert out.changes[0].carries == [] and any("can't carry" in d["why"] for d in out.dropped)
    ev = A.Evidence("a stretch", ())
    ev.add(A.Item("m1", "user", "message:1", ("message:1",), 'm1 [user] "x"', words="x"))
    ev.add(A.Item("r1", "memory", "memory:fact:f", ("fact:f",), "[memory fact]", memory="fact:f"))
    raw = {"findings": [{"statement": "he moved to Bilbao", "form": "", "status": "supported", "cites": ["m1"],
                         "applies_when": "", "alternatives": [], "relates_to": [{"lesson": "r1", "relation": "narrows"}],
                         "untested": "", "needs": "", "would_support": "", "would_challenge": ""}]}
    assert A.check(raw, ev).result["findings"][0]["relates_to"] == [
        {"lesson": "r1", "target": "fact:f", "relation": "challenges"}]
