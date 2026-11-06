"""Learning step 3, immediate adaptation (docs/learning-system/03-implementation.md, "Completion evidence").

Scripted models only: the lesson builder and Student 2 (the live teacher path, Luna) are replaced by scripted
answers, so these tests show what code guarantees: what a lesson may rest on, its lifecycle and fences, what a
decision is given and records, and what inspection can tell apart. Whether Luna weighs lessons well is measured
by ``scripts/lesson_adaptation_eval.py`` (real calls). Pure checks run without a database; the rest need real
PostgreSQL (TEST_DATABASE_URL).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.learning import assessment as A
from app.ai.learning import lessons as L
from app.ai.state import student_two as S
from app.db import models as M
from app.db.models import (
    ChatMessage, Conversation, LearningAssessment, LearningAssessmentUse, LearningLessonJob, MemoryLesson,
    MemoryLessonEvidence, MemoryLessonRevision, MemoryLessonUse,
)
from app.services import chat as chat_service
from app.services import learning_assessment as LA
from app.services import learning_capture
from app.services import learning_lessons as LL
from app.services import memory_lessons as ML
from app.services import student_two as svc
from db_support import create_conversation, create_user, db_env, database  # noqa: F401

NOW = datetime.now(timezone.utc)


def soon(**delta) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


# --------------------------------------------------------------------------- #
# Pure: what a lesson may rest on
# --------------------------------------------------------------------------- #


def bundle(*, lessons=(), withdrawn=(), occasion="message:new", at=None) -> L.Bundle:
    b = L.Bundle("a-1", 1, occasion, at or NOW, "a correction", "they wanted listening")
    b.lines["m1"] = L.Line("m1", "user", 'm1 [user] "my boss yelled at me again"', "my boss yelled at me again",
                           ("message:u1",))
    b.lines["m2"] = L.Line("m2", "columbina", 'm2 [columbina] "Here are five fixes."', "Here are five fixes.",
                           ("message:h1",))
    b.lines["m3"] = L.Line("m3", "user", 'm3 [user] "from now on, when I vent, please just listen"',
                           "from now on, when I vent, please just listen", ("message:u2",))
    b.lines["m4"] = L.Line("m4", "user", 'm4 [user] "yesterday I only wanted you to listen"',
                           "yesterday I only wanted you to listen", ("message:u3",))
    b.conclusions["a1"] = L.Conclusion("a1", "stated", "supported", 'the user stated (preference): "please just listen"',
                                       ("message:u2",))
    b.conclusions["a2"] = L.Conclusion("a2", "cause", "supported", "cause: Student 2's choice", ("attempt:s2",))
    b.conclusions["a3"] = L.Conclusion("a3", "cause", "plausible", "cause: the mouth's wording", ("message:h1",))
    for s in lessons:
        b.lessons[s.ref] = s
    for s in withdrawn:
        b.withdrawn[s.ref] = s
    return b


def change(**kw) -> dict:
    base = {"op": "create", "lesson": "", "kind": "preference", "scope": "situation",
            "statement": "Listen rather than offer fixes", "applies_when": "when they are venting",
            "exceptions": ["when they ask for advice"], "user_quote": "", "conclusion_cites": [],
            "applicability_cites": [], "conflicts_with": [], "same_as_withdrawn": "", "why": "because"}
    base.update(kw)
    return base


def shown(ref="L1", **kw) -> L.Shown:
    base = dict(ref=ref, id=str(uuid.uuid4()), revision=1, kind="preference", scope="situation", status="accepted",
                statement="Listen rather than offer fixes", applies_when="when they are venting",
                exceptions=("when they ask for advice",), user_words=("please just listen",),
                support_from=("message:old",), counter_from=(), occasions=("message:old",))
    base.update(kw)
    return L.Shown(**base)


def test_a_preference_rests_on_the_users_exact_words_and_an_inference_on_supported_evidence():
    b = bundle()
    checked = L.check({"changes": [
        change(user_quote="when I vent, please just listen", conclusion_cites=["m3", "a1"]),   # kept
        change(user_quote="never give me advice", conclusion_cites=["m3"]),                   # invented: dropped
        change(user_quote="Here are five fixes", conclusion_cites=["m2"]),                    # her words: dropped
        change(kind="strategy", conclusion_cites=["a3"], applicability_cites=["m1"]),         # plausible only
        change(kind="strategy", conclusion_cites=["a2"], applicability_cites=[]),             # no applicability
        change(kind="strategy", conclusion_cites=["a2", "zz9"], applicability_cites=["m1"]),  # kept, zz9 dropped
    ], "explanation": "x"}, b)
    kinds = [(c.op, c.kind) for c in checked.changes]
    assert kinds == [("create", "preference"), ("create", "strategy")]
    pref, strat = checked.changes
    assert pref.user_words == ["when I vent, please just listen"] and pref.quote and "message:u2" in pref.roots
    assert strat.user_words == [] and "attempt:s2" in strat.roots
    reasons = " | ".join(d["why"] for d in checked.dropped)
    assert "exact words" in reasons and "supported conclusion" in reasons and "where it applies" in reasons
    assert any("zz9" in d["what"] for d in checked.dropped)
    # The stated preference is accepted; an inference is tentative unless the builder judged it established
    # (post-audit 3: its judgment, never a count of occasions).
    assert strat.certainty == "tentative" and pref.certainty is None
    assert L.status_for("preference", "situation", "tentative") == "accepted"
    assert L.status_for("strategy", "situation", "tentative") == "hypothesis"
    assert L.status_for("understanding", "situation", "established") == "accepted"


def test_a_cited_stated_conclusion_vouches_only_for_the_users_own_line_it_rests_on():
    # The real-model builder often cites a1 ("the user stated: ...") without the message line itself.
    b = bundle()
    b.conclusions["a4"] = L.Conclusion("a4", "stated", "plausible", 'the user stated (preference): "listen"',
                                       ("message:u3",))
    checked = L.check({"changes": [
        change(user_quote="when I vent, please just listen", conclusion_cites=["a1"]),        # kept, via m3
        change(user_quote="yesterday I only wanted you to listen", conclusion_cites=["a1"]),  # m4: not what a1 rests on
        change(user_quote="yesterday I only wanted you to listen", conclusion_cites=["a4"]),  # a4 isn't supported
        change(user_quote="please just listen to me always", conclusion_cites=["a1"]),        # not their words
    ], "explanation": "x"}, b)
    assert [c.user_words for c in checked.changes] == [["when I vent, please just listen"]]
    assert "message:u2" in checked.changes[0].roots
    assert len(checked.dropped) == 3 and all("exact words" in d["why"] for d in checked.dropped)


def test_an_episode_correction_does_not_become_a_standing_preference():
    b = bundle()
    checked = L.check({"changes": [change(scope="episode", user_quote="yesterday I only wanted you to listen",
                                          conclusion_cites=["m4"])]}, b)
    assert checked.changes[0].scope == "episode"
    assert L.status_for("preference", "episode", "established") == "episode_only"
    # Widening it needs their explicit words about the future or further occasions.
    episode = shown(scope="episode", status="episode_only", user_words=("yesterday I only wanted you to listen",))
    b = bundle(lessons=[episode])
    widen = change(op="revise", lesson="L1", scope="standing", conclusion_cites=["a1"])
    assert L.check({"changes": [widen]}, b).changes == []
    widen["user_quote"] = "from now on, when I vent, please just listen"
    widen["conclusion_cites"] = ["m3"]
    kept = L.check({"changes": [widen]}, b).changes
    assert kept and kept[0].scope == "standing" and kept[0].widens
    assert kept[0].user_words == ["yesterday I only wanted you to listen", "from now on, when I vent, please just listen"]


def test_withdrawal_needs_the_users_words_or_a_supported_conclusion_against_it():
    pref = shown()
    strat = shown("L2", kind="strategy", user_words=(), support_from=("message:a", "message:b"))
    b = bundle(lessons=[pref, strat])
    # A preference is theirs: only their words withdraw it. An inference with only an unsupported reading against
    # it is a counterexample to reason about, not an erase.
    out = L.check({"changes": [change(op="withdraw", lesson="L1", conclusion_cites=["a2"]),
                               change(op="withdraw", lesson="L2", conclusion_cites=["m1"])]}, b)
    assert [(c.lesson_ref, c.op) for c in out.changes] == [("L1", "counter"), ("L2", "counter")]
    assert sum("recorded as a counterexample" in d["why"] for d in out.dropped) == 2
    # Their words withdraw the preference; a supported conclusion against the inference withdraws it at once
    # (strong contradictory evidence doesn't wait for a number of repetitions).
    out = L.check({"changes": [change(op="withdraw", lesson="L1", user_quote="please just listen",
                                      conclusion_cites=["m3"]),
                               change(op="withdraw", lesson="L2", conclusion_cites=["a2"])]}, b)
    assert [(c.lesson_ref, c.op) for c in out.changes] == [("L1", "withdraw"), ("L2", "withdraw")]


def test_withdrawn_lessons_stay_withdrawn_and_older_evidence_cannot_bring_one_back():
    gone = shown("W1", status="withdrawn", withdrawn_occasion_at=NOW)
    old = bundle(withdrawn=[gone], at=NOW - timedelta(hours=1))
    out = L.check({"changes": [change(op="support", lesson="W1", conclusion_cites=["a1"]),
                               change(user_quote="please just listen", conclusion_cites=["m3"],
                                      same_as_withdrawn="W1")]}, old)
    assert out.changes == []
    assert any("stay withdrawn" in d["why"] for d in out.dropped)
    assert any("no newer than its withdrawal" in d["why"] for d in out.dropped)
    newer = bundle(withdrawn=[gone], at=NOW + timedelta(hours=1))
    out = L.check({"changes": [change(user_quote="please just listen", conclusion_cites=["m3"],
                                      same_as_withdrawn="W1")]}, newer)
    assert [c.op for c in out.changes] == ["create"]


def test_nothing_is_cut_to_fit_and_narrowing_only_adds_exceptions():
    b = bundle(lessons=[shown()])
    long = change(user_quote="please just listen", conclusion_cites=["m3"], exceptions=["x" * 300])
    many = change(user_quote="please just listen", conclusion_cites=["m3"], exceptions=list("abcde"))
    assert L.check({"changes": [long, many]}, b).changes == []
    narrow = change(op="narrow", lesson="L1", exceptions=["when they've asked for a plan"], conclusion_cites=["a1"])
    out = L.check({"changes": [narrow]}, b).changes[0]
    assert out.exceptions == ["when they ask for advice", "when they've asked for a plan"]
    assert out.statement == "Listen rather than offer fixes" and out.expected_revision == 1
    # Revising what a preference says needs their own words; a second change to one lesson in a reading is dropped.
    out = L.check({"changes": [change(op="revise", lesson="L1", statement="Ask what they need", conclusion_cites=["a2"]),
                               change(op="support", lesson="L1", conclusion_cites=["a1"]),
                               change(op="counter", lesson="L1", conclusion_cites=["a1"])]}, b)
    assert [c.op for c in out.changes] == ["support"]
    assert any("user's own words" in d["why"] for d in out.dropped) and any("second change" in d["why"]
                                                                          for d in out.dropped)


def views():
    return (L.LessonView("L1", str(uuid.uuid4()), 2, "preference", "standing", "Keep answers short",
                         "in general", ("when they ask for a detailed explanation",), ("keep it short please",)),
            L.LessonView("L2", str(uuid.uuid4()), 1, "strategy", "situation", "Explain coding problems thoroughly",
                         "when they bring a coding problem", (), (), 3, conflicts=("L3",)),
            L.LessonView("L3", str(uuid.uuid4()), 1, "strategy", "situation", "Give the short fix first",
                         "when they bring a coding problem", (), (), 2, conflicts=("L2",)))


def test_the_decision_input_shows_lessons_whole_only_to_a_rendering_trained_for_them():
    live = S.Live(trigger="user_message", domain="chat", now=NOW, lines=(), new_message="can you help with a bug",
                  lessons=views(), lessons_left_out=(("x", 1, "cut by the budget"),))
    situation = S.snapshot(live)
    plain = S.render(situation, "memory-v1")
    assert "LESSONS" not in plain and not situation.lessons_shown and situation.lesson_refs == []
    text = S.render(situation, L.RENDERING)
    assert situation.lessons_shown and situation.lesson_refs == ["L1", "L2", "L3"]
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("LESSONS"))
    assert lines[at - 1].startswith("MEMORY") and lines[at + 6].startswith("REQUESTS FROM THE USER")
    assert ('[L1] they have said they want (their words: "keep it short please"): Keep answers short. Applies: in '
            'general. Except: when they ask for a detailed explanation.') in text
    assert "[L2] she has found, from 3 occasions (her inference)" in text
    assert "- [L2] and [L3] point different ways here and nothing has settled which holds." in text
    assert "1 other lesson of hers didn't fit" in text
    # Without lessons the lessons rendering is exactly memory-v1.
    bare = S.snapshot(S.Live(trigger="user_message", domain="chat", now=NOW, lines=(), new_message="hi"))
    assert S.render(bare, L.RENDERING) == S.render(bare, "memory-v1")


def test_its_account_of_each_lesson_is_parsed_and_checked():
    out = json.dumps({"intention": "x", "lessons_weighed": [
        {"lesson": "L1", "use": "applied", "why": "short"}, {"lesson": "L1", "use": "unclear", "why": "again"},
        {"lesson": "L9", "use": "applied", "why": "?"}, {"lesson": "L2", "use": "ignored", "why": "?"}]})
    weighed, problems = L.parse_weighed(out, ["L1", "L2"])
    assert weighed == [{"lesson": "L1", "use": "applied", "why": "short"}]
    assert "L1 weighed twice" in problems and "not weighed: L2" in problems and len(problems) == 4


def teacher_answer(**kw) -> dict:
    base = dict(S.DEFAULTS, intention="listen to them", communication="respond", what_to_communicate="that sounds hard",
                move="react_briefly", about=["M"], explanation="They are venting.",
                dials=dict(length="a_little", warmth="somewhat", playfulness="none", directness="somewhat"))
    base.update(kw)
    return base


@pytest.fixture
def teacher(monkeypatch, tmp_path):
    settings = svc.get_settings()
    monkeypatch.setattr(settings, "cognition_decision_provider", "teacher")
    monkeypatch.setattr(settings, "cognition_teacher_data_path", str(tmp_path / "live_teacher.jsonl"))
    monkeypatch.setattr(settings, "cognition_student_log_path", str(tmp_path / "summary.jsonl"))
    return settings


def test_the_teacher_gets_the_guide_and_the_account_field_only_with_lessons(teacher):
    seen = []

    def ask(model, text):
        seen.append(model)
        refs = model["schema"]["properties"].get("lessons_weighed", {}).get("items", {}) \
            .get("properties", {}).get("lesson", {}).get("enum", [])
        return json.dumps(teacher_answer(lessons_weighed=[{"lesson": r, "use": "not_relevant", "why": "no"}
                                                          for r in refs]))

    base = dict(trigger="user_message", domain="chat", now=NOW, lines=(), new_message="ugh, my landlord again")
    bare = svc.decide(S.Live(**base), ask=ask)
    assert bare.ok and not bare.lessons_received and seen[0]["rendering"] == "memory-v1"
    assert "lessons_weighed" not in seen[0]["schema"]["properties"] and L.GUIDE not in seen[0]["system"]
    given = svc.decide(S.Live(**base, lessons=views()), ask=ask)
    assert given.ok, given.problems
    model = seen[1]
    assert model["rendering"] == L.RENDERING and L.GUIDE in model["system"]
    assert model["schema"]["properties"]["lessons_weighed"]["items"]["properties"]["lesson"]["enum"] == ["L1", "L2", "L3"]
    assert "lessons_weighed" in model["schema"]["required"]
    assert given.lessons_received and [w["lesson"] for w in given.lessons_weighed] == ["L1", "L2", "L3"]
    # The decision contract is unchanged: the account isn't part of the decision.
    assert "lessons_weighed" not in given.decision
    assert given.teacher_capture["rendering"] == L.RENDERING
    assert [x["ref"] for x in given.teacher_capture["lessons"]["lessons"]] == ["L1", "L2", "L3"]


def test_a_local_model_without_lessons_in_its_rendering_is_recorded_as_not_receiving_them(monkeypatch):
    monkeypatch.setattr(svc, "manifest", lambda: {"name": "qwen-4", "system": "s", "rendering": "memory-v1"})
    outcome = svc.decide(S.Live(trigger="user_message", domain="chat", now=NOW, lines=(), new_message="hi",
                                lessons=views()),
                         ask=lambda model, text: json.dumps(teacher_answer()))
    assert not outcome.lessons_received and "rendering doesn't show them" in outcome.lessons_reason
    assert "LESSONS" not in outcome.situation.text


# --------------------------------------------------------------------------- #
# Database fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    settings = LA.get_settings().model_copy(update={
        "learning_assessment_settle_seconds": 0, "learning_assessment_log_path": str(tmp_path / "assess.jsonl"),
        "learning_lessons_log_path": str(tmp_path / "lessons.jsonl"), "lessons_supplied_max": 5})
    holder = {"s": settings}
    for module in (LA, LL, ML):
        monkeypatch.setattr(module, "get_settings", lambda: holder["s"])

    def configure(**updates):
        holder["s"] = holder["s"].model_copy(update=updates)

    db_env.configure_learning = configure
    monkeypatch.setattr(ML, "_embed", lambda text: None)  # no embeddings: lexical ranking
    return db_env


def say(db, conversation, role, text, *, turn_id=None) -> ChatMessage:
    conversation = db.get(Conversation, conversation.id)
    return chat_service.add_message(db, conversation=conversation, role=role, content=text,
                                    turn_id=turn_id, status="completed")


def setup(env):
    with env.db() as db:
        LA.started_at(db)
        db.commit()
        user = create_user(db)
        conversation = create_conversation(db, user)
    return user, conversation


class Evaluator:
    name = "assessment-v1:scripted"

    def __init__(self, raw):
        self.raw = raw

    def __call__(self, text):
        raw = self.raw(text) if callable(self.raw) else self.raw
        return A.Answer(json.loads(json.dumps(raw)), 1.0, 10, 5)


class Builder:
    """Scripted lesson builder: a dict, or a callable of (text, call number)."""

    name = "lessons-v1:scripted"

    def __init__(self, answer):
        self.answer, self.texts = answer, []

    def __call__(self, text):
        self.texts.append(text)
        raw = self.answer(text, len(self.texts)) if callable(self.answer) else self.answer
        if isinstance(raw, Exception):
            return L.Answer(None, 1.0, error=str(raw))
        return L.Answer(json.loads(json.dumps(raw)), 1.0, 10, 5)


def ref_of(text: str, needle: str) -> str:
    """The ref the rendered bundle gave to the first conversation line containing ``needle`` (an ``a`` conclusion
    when the needle is "the user stated")."""
    prefix = "a" if needle == "the user stated" else "m"
    line = next(line for line in text.splitlines() if needle in line and line.startswith(prefix))
    return line.split()[0]


def assessed(env, user, conversation, *, venting="my boss yelled at me again",
             reply="Here are five ways to handle a difficult boss.",
             correction="When I'm venting, please just listen. Don't jump to fixes.", stated=True, cause=True):
    """One exchange and a correction, assessed by a scripted evaluator: returns (assessment id, correction msg)."""
    with env.db() as db:
        turn = uuid.uuid4()
        say(db, conversation, "user", venting, turn_id=turn)
        her = say(db, conversation, "assistant", reply, turn_id=turn)
        msg = say(db, conversation, "user", correction, turn_id=uuid.uuid4())
        row, _ = LA.open_assessment(db, user_id=user.id, kind="correction", key=f"message:{msg.id}",
                                    anchor_at=msg.created_at, conversation_id=conversation.id,
                                    anchor_message_id=msg.id, trigger={"why": "Student 1 read it as correcting her"})
        db.commit()
        aid = row.id
    m, t = f"m{msg.conversation_sequence}", f"t{her.conversation_sequence}"
    # A cause supported on its component's own record: the user's own words (nothing on her side).
    raw = {"feedback": "yes", "targets": [{"ref": t, "also": [], "status": "supported", "cites": [m],
                                           "why": "about her advice"}],
           "causes": [{"target": t, "component": "user_side", "status": "supported", "cites": [m],
                       "why": "what they wanted"}] if cause else [],
           "user_stated": [{"kind": "preference", "quote": correction.split(".")[0], "cites": [m]}] if stated else [],
           "dimensions": [], "choice_vs_outcome": [], "missing": [],
           "reopens": [], "next": {"step": "conclude", "look": [], "why": "enough"}, "explanation": "they wanted listening"}
    with env.db() as db:
        claimed = LA.claim(db, soon(hours=1))
    with env.db() as db:
        assert LA.assess(db, claimed, evaluator=Evaluator(raw)) == "revised"
    return aid, msg


def run_lessons(env, builder, *, times=1):
    outcomes = []
    for _ in range(times):
        with env.db() as db:
            claimed = LL.claim(db, soon(hours=1))
        if claimed is None:
            outcomes.append(None)
            continue
        with env.db() as db:
            outcomes.append(LL.process(db, *claimed, builder=builder, embed=lambda text: None))
    return outcomes


def preference(text, **kw):
    """The scripted builder's answer: one preference from the correction line."""
    quote = kw.pop("quote", "When I'm venting, please just listen")
    return {"changes": [change(user_quote=quote, conclusion_cites=[ref_of(text, quote)], **kw)],
            "explanation": "a stated preference"}


def capture(user, conversation=None) -> learning_capture.Ctx:
    return learning_capture.Ctx(user_id=user.id, attempt_id=uuid.uuid4(), trigger="user_message",
                                conversation_id=conversation.id if conversation else None)


# --------------------------------------------------------------------------- #
# The walkthrough
# --------------------------------------------------------------------------- #


def test_a_correction_informs_the_next_choice_now_and_a_fresh_conversation_later(env, teacher):
    user, first = setup(env)
    aid, msg = assessed(env, user, first)
    with env.db() as db:
        job = db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == aid))
        assert job is not None and job.assessment_revision == 1 and job.state == "pending"
        # Immediately, before any lesson exists: the next decision in that conversation has the correction in view.
        live = svc.gather(db, user_id=user.id, conversation_id=first.id, trigger_message_id=None,
                          message="and now he wants me to work Saturday", zone="UTC")
    assert live.lessons == () and live.lessons_note == "no lessons with live support"
    prompts = []

    def ask(model, text):
        prompts.append(text)
        refs = model["schema"]["properties"].get("lessons_weighed", {}).get("items", {}) \
            .get("properties", {}).get("lesson", {}).get("enum", [])
        return json.dumps(teacher_answer(lessons_weighed=[{"lesson": r, "use": "applied", "why": "they're venting"}
                                                          for r in refs]))

    now = svc.decide(live, ask=ask, capture=capture(user, first))
    assert now.ok and "please just listen" in prompts[0] and "LESSONS" not in prompts[0]

    # Learning reads the assessment, memory keeps the lesson.
    builder = Builder(lambda text, n: preference(text, scope="situation", applies_when="when they are venting"))
    assert run_lessons(env, builder) == ["changed"]
    assert "the user stated (preference)" in builder.texts[0] and "(none)" in builder.texts[0]
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
        assert (lesson.kind, lesson.origin, lesson.status, lesson.revision) == ("preference", "stated", "accepted", 1)
        assert lesson.user_words == ["When I'm venting, please just listen"]
        evidence = db.scalar(select(MemoryLessonEvidence).where(MemoryLessonEvidence.lesson_id == lesson.id))
        assert evidence.occasion == f"message:{msg.id}" and f"message:{msg.id}" in evidence.refs
        use = db.scalar(select(LearningAssessmentUse).where(LearningAssessmentUse.assessment_id == aid))
        assert (use.consumer, use.ref) == ("lessons", f"lesson:{lesson.id}")
        lesson_id = lesson.id

    # A fresh conversation, the correction nowhere in it: the decision is given the lesson and accounts for it.
    with env.db() as db:
        second = create_conversation(db, db.get(M.User, user.id))
        say(db, second, "user", "hey", turn_id=uuid.uuid4())
        later = svc.gather(db, user_id=user.id, conversation_id=second.id, trigger_message_id=None,
                           message="ugh, my landlord ignored the leak again", zone="UTC")
    assert [v.id for v in later.lessons] == [str(lesson_id)]
    assert all("please just listen" not in line.text for line in later.lines)
    ctx = capture(user, second)
    outcome = svc.decide(later, ask=ask, capture=ctx)
    assert outcome.ok and outcome.lessons_received
    assert f"lesson:{lesson_id}" in outcome.capture.sources
    assert {"ref": f"lesson:{lesson_id}", "revision": 1, "shown": True} in outcome.capture.input_refs
    assert "LESSONS" in prompts[1] and "When I'm venting, please just listen" in prompts[1]
    with env.db() as db:
        row = db.scalar(select(MemoryLessonUse).where(MemoryLessonUse.attempt_id == ctx.attempt_id))
        assert (row.received, row.revision, row.ref, row.reported) == (True, 1, "L1", "applied")
        case, sentence = ML.explain(row)
        assert case == "applied" and "not proof of influence" in sentence


def test_an_inference_is_as_certain_as_the_builder_judged_and_rereading_one_is_not_new_support(env):
    user, conversation = setup(env)

    def strategy(text, n):
        stated = ref_of(text, "the user stated")
        lesson = next((line.split()[0] for line in text.splitlines() if line.startswith("L1 ")), None)
        if lesson is None:
            return {"changes": [change(kind="strategy", statement="Ask before offering fixes",
                                       conclusion_cites=[stated], applicability_cites=[stated], certainty="tentative")],
                    "explanation": "a pattern, maybe"}
        # Every later reading judges it established: code lets that count only from new support.
        return {"changes": [change(op="support", lesson=lesson, conclusion_cites=[stated], certainty="established")],
                "explanation": "again"}

    first, _ = assessed(env, user, conversation, correction="I just wanted to vent. Not a list of fixes.")
    run_lessons(env, Builder(strategy))
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
        assert (lesson.origin, lesson.status) == ("inferred", "hypothesis") and "a guess" in lesson.status_reason
        assert ML.for_decision(db, user.id, "anything").views == ()  # accepted-only retrieval leaves the guess out
        # Retrieval that asks for tentative lessons (every consumer since post-audit 3) has it, labelled a guess.
        tentative = ML.for_decision(db, user.id, "anything", include_tentative=True)
        assert [v.status for v in tentative.views] == ["hypothesis"]
        assert "her inference, a guess" in L.decision_lines(tentative.views)[1]
    # The same exchange read again (a new revision of its assessment) is not a second occasion.
    with env.db() as db:
        LA.add_note(db, user.id, first, "yes, that's right")
    with env.db() as db:
        claimed = LA.claim(db, soon(hours=1))
    raw = None
    with env.db() as db:
        row = db.get(LearningAssessment, first)
        rev = db.scalar(select(M.LearningAssessmentRevision).where(M.LearningAssessmentRevision.assessment_id == first))
        raw = {k: rev.result[k] for k in ("feedback", "targets", "causes", "user_stated", "dimensions",
                                          "choice_vs_outcome", "missing", "follow_up", "reopens", "next",
                                          "explanation")}
    with env.db() as db:
        LA.assess(db, claimed, evaluator=Evaluator(raw))
    run_lessons(env, Builder(strategy))
    with env.db() as db:
        lesson = db.get(MemoryLesson, lesson.id)
        assert lesson.status == "hypothesis" and lesson.revision == 1
        job = db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == first,
                                                        LearningLessonJob.assessment_revision == 2))
        assert job.outcome == "changed" and any("already counted" in d["why"] for d in job.result["dropped"])
    # A second, different exchange, judged established: accepted, as a new revision.
    assessed(env, user, conversation, correction="Again, I wanted to vent. Not fixes.")
    run_lessons(env, Builder(strategy))
    with env.db() as db:
        lesson = db.get(MemoryLesson, lesson.id)
        assert (lesson.status, lesson.revision) == ("accepted", 2)
        assert [v.statement for v in ML.for_decision(db, user.id, "x").views] == ["Ask before offering fixes"]
        ops = [r.op for r in db.scalars(select(MemoryLessonRevision).where(MemoryLessonRevision.lesson_id == lesson.id)
                                        .order_by(MemoryLessonRevision.revision))]
        assert ops == ["create", "support"]


def test_an_episode_correction_is_kept_but_never_supplied(env):
    user, conversation = setup(env)
    assessed(env, user, conversation, correction="Yesterday I only wanted you to listen. That's all.")
    run_lessons(env, Builder(lambda text, n: preference(text, quote="Yesterday I only wanted you to listen",
                                                        scope="episode")))
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
        assert lesson.status == "episode_only" and "not a standing preference" in lesson.status_reason
        assert ML.for_decision(db, user.id, "my day was awful").views == ()


def test_a_disclaimed_preference_is_withdrawn_and_stale_work_cannot_restore_it(env):
    user, conversation = setup(env)
    old_aid, _ = assessed(env, user, conversation, correction="Keep your answers short. I mean it.")
    run_lessons(env, Builder(lambda text, n: preference(text, quote="Keep your answers short", scope="standing",
                                                        statement="Keep answers short", applies_when="in general",
                                                        exceptions=[])))
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
    # The user says they no longer want it.
    assessed(env, user, conversation, correction="Actually I don't need short answers anymore. Go long.")

    def withdraw(text, n):
        if "don't need short answers" not in text:
            return {"changes": [], "explanation": "nothing"}
        quote = "Actually I don't need short answers anymore"
        return {"changes": [change(op="withdraw", lesson="L1", user_quote=quote,
                                   conclusion_cites=[ref_of(text, quote)])], "explanation": "they disclaimed it"}

    run_lessons(env, Builder(withdraw))
    with env.db() as db:
        lesson = db.get(MemoryLesson, lesson.id)
        assert lesson.status == "withdrawn" and "don't need short answers" in lesson.status_reason
        assert ML.for_decision(db, user.id, "tell me about whales").views == ()
    # The old exchange is read again (its assessment got new evidence): its support of the withdrawn lesson, or a
    # new lesson saying the same from that older evidence, is refused.
    with env.db() as db:
        LA.add_note(db, user.id, old_aid, "short, yes")
    with env.db() as db:
        claimed = LA.claim(db, soon(hours=1))
        rev = db.scalar(select(M.LearningAssessmentRevision).where(M.LearningAssessmentRevision.assessment_id == old_aid))
        raw = {k: rev.result[k] for k in ("feedback", "targets", "causes", "user_stated", "dimensions",
                                          "choice_vs_outcome", "missing", "follow_up", "reopens", "next",
                                          "explanation")}
    with env.db() as db:
        LA.assess(db, claimed, evaluator=Evaluator(raw))

    def stale(text, n):
        quote = "Keep your answers short"
        return {"changes": [change(op="support", lesson="W1", conclusion_cites=[ref_of(text, quote)]),
                            change(user_quote=quote, conclusion_cites=[ref_of(text, quote)], scope="standing",
                                   same_as_withdrawn="W1")], "explanation": "short again"}

    builder = Builder(stale)
    run_lessons(env, builder)
    assert "W1 (preference, withdrawn" in builder.texts[0]
    with env.db() as db:
        assert db.get(MemoryLesson, lesson.id).status == "withdrawn"
        assert len(list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == user.id)))) == 1
        job = db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == old_aid,
                                                        LearningLessonJob.assessment_revision == 2))
        assert job.outcome == "no_change" and len(job.result["dropped"]) == 2


def test_a_reading_of_a_revision_that_was_superseded_is_never_applied(env):
    user, conversation = setup(env)
    aid, _ = assessed(env, user, conversation)
    with env.db() as db:
        row = db.get(LearningAssessment, aid)
        row.revision = 2  # revised meanwhile (its own job will read it)
        db.commit()
    assert run_lessons(env, Builder(lambda text, n: preference(text))) == ["superseded"]
    with env.db() as db:
        assert db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id)) is None


def test_a_lesson_changed_while_the_builder_read_makes_it_read_again(env):
    user, conversation = setup(env)
    assessed(env, user, conversation)
    run_lessons(env, Builder(lambda text, n: preference(text)))
    assessed(env, user, conversation, correction="Seriously, when I'm venting, please just listen. Thanks.")
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))

    def racing(text, n):
        if n == 1:  # someone else revises the lesson while this reading is in flight
            with env.db() as other:
                row = other.get(MemoryLesson, lesson.id)
                row.revision += 1
                other.commit()
        return {"changes": [change(op="support", lesson="L1", conclusion_cites=[ref_of(text, "please just listen")])],
                "explanation": "again"}

    builder = Builder(racing)
    assert run_lessons(env, builder) == ["changed"]
    assert len(builder.texts) == 2 and "revision 2" in builder.texts[1]


def test_counterevidence_narrows_a_lesson_and_one_bad_result_does_not_erase_it(env):
    user, conversation = setup(env)
    assessed(env, user, conversation)
    run_lessons(env, Builder(lambda text, n: preference(text)))
    assessed(env, user, conversation, venting="this bug is driving me mad, can you fix it",
             reply="That sounds so frustrating.", correction="No, I wanted the fix. I asked for it.")

    def narrow(text, n):
        ref = ref_of(text, "No, I wanted the fix")
        return {"changes": [change(op="narrow", lesson="L1", exceptions=["when they ask her to fix something"],
                                   conclusion_cites=[ref, ref_of(text, "the user stated")]),
                            ], "explanation": "an exception"}

    run_lessons(env, Builder(narrow))
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
        assert lesson.status == "accepted" and lesson.revision == 2
        assert lesson.exceptions == ["when they ask for advice", "when they ask her to fix something"]
        roles = sorted(e.role for e in db.scalars(select(MemoryLessonEvidence).where(
            MemoryLessonEvidence.lesson_id == lesson.id)))
        assert roles == ["counter", "support"]
        detail = ML.detail(db, user.id, lesson.id)
        assert [r["op"] for r in detail["revisions"]] == ["narrow", "create"]
        assert detail["revisions"][1]["exceptions"] == ["when they ask for advice"]  # the old revision kept whole


def test_a_revised_assessment_stops_counting_until_read_again(env):
    user, conversation = setup(env)

    def strategy(text, n):
        stated = ref_of(text, "the user stated")
        return {"changes": [change(kind="strategy", statement="Ask before offering fixes", certainty="established",
                                   conclusion_cites=[stated], applicability_cites=[stated])], "explanation": "x"}

    aid, _ = assessed(env, user, conversation, correction="I just wanted to vent. Not a list of fixes.")
    run_lessons(env, Builder(strategy))
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
        assert lesson.status == "accepted"
        LA.add_note(db, user.id, aid, "honestly the fixes were fine")
    with env.db() as db:
        claimed = LA.claim(db, soon(hours=1))
    with env.db() as db:  # the revision no longer supports anything: its conclusions changed
        LA.assess(db, claimed, evaluator=Evaluator({
            "feedback": "unclear", "targets": [], "causes": [], "user_stated": [], "dimensions": [],
            "choice_vs_outcome": [], "missing": [],
            "reopens": [], "next": {"step": "conclude", "look": [], "why": "x"}, "explanation": "changed"}))
    with env.db() as db:
        lesson = db.get(MemoryLesson, lesson.id)
        # The judgment stays the builder's; with no live support it is not supplied until read again.
        assert lesson.status == "accepted" and lesson.status_reason == ML.UNSUPPORTED and lesson.revision == 2
        assert ML.for_decision(db, user.id, "fixes", include_tentative=True).views == ()
        assert db.scalar(select(MemoryLessonRevision.op).where(MemoryLessonRevision.lesson_id == lesson.id,
                                                               MemoryLessonRevision.revision == 2)) == "reconsider"
        use = db.scalar(select(LearningAssessmentUse).where(LearningAssessmentUse.assessment_id == aid))
        assert use.state == "reconsider"
        # Its new revision is read (a lesson rests on it), and the builder may support it again or not.
        job = db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == aid,
                                                        LearningLessonJob.assessment_revision == 2))
        assert job is not None and "rests on an earlier revision" in job.reason
    builder = Builder({"changes": [], "explanation": "nothing holds now"})
    run_lessons(env, builder)
    assert "it rested on revision 1 of this same assessment, which has been revised" in builder.texts[0]


def test_retrieval_is_bounded_whole_and_keeps_conflicts_together(env):
    user, _ = setup(env)
    env.configure_learning(lessons_supplied_max=3)
    with env.db() as db:
        rows = []
        for statement, applies in [("Keep answers short", "in general"),
                                   ("Explain coding problems thoroughly", "when they bring a coding bug"),
                                   ("Give the short fix first", "when they bring a coding bug"),
                                   ("Talk about whales with enthusiasm", "when they mention whales"),
                                   ("Use metric units", "when giving measurements"),
                                   ("Avoid spoilers for films", "when discussing films")]:
            row = MemoryLesson(user_id=user.id, kind="strategy", origin="inferred", scope="situation",
                               status="accepted", statement=statement, applies_when=applies,
                               exceptions=["unless they ask"] if "whales" in statement else [])
            db.add(row)
            rows.append(row)
        db.flush()
        rows[1].conflicts_with, rows[2].conflicts_with = [str(rows[2].id)], [str(rows[1].id)]
        guess = MemoryLesson(user_id=user.id, kind="strategy", origin="inferred", scope="situation",
                             status="hypothesis", statement="Coding bug guess", applies_when="coding bug")
        db.add(guess)
        db.flush()
        for i, row in enumerate([*rows, guess]):  # an inference is supplied only with live support
            db.add(MemoryLessonEvidence(lesson_id=row.id, user_id=user.id, role="support", occasion=f"test:{i}",
                                        occasion_at=NOW, refs=[f"message:{uuid.uuid4()}"], added_revision=1))
        db.commit()
        supply = ML.for_decision(db, user.id, "I have a coding bug about whales")
    shown_now = [v.statement for v in supply.views]
    assert len(shown_now) == 3 and supply.ranked_by == "lexical" and supply.accepted == 6
    assert {"Explain coding problems thoroughly", "Give the short fix first"} <= set(shown_now)
    assert "Talk about whales with enthusiasm" in shown_now
    whales = next(v for v in supply.views if "whales" in v.statement)
    assert whales.exceptions == ("unless they ask",)  # whole, never cut
    pair = [v for v in supply.views if "coding" in v.applies_when]
    assert pair[0].conflicts == (pair[1].ref,) and pair[1].conflicts == (pair[0].ref,)
    assert len(supply.left_out) == 3 and all(why.startswith("cut by the budget") for _, _, why in supply.left_out)
    assert "Coding bug guess" not in shown_now
    # A counterpart that ranks low is brought in beside its lesson, replacing the lowest-ranked one not in a conflict.
    with env.db() as db:
        rows[3] = db.get(MemoryLesson, rows[3].id)
        rows[4] = db.get(MemoryLesson, rows[4].id)
        rows[3].conflicts_with, rows[4].conflicts_with = [str(rows[4].id)], [str(rows[3].id)]
        db.commit()
        supply = ML.for_decision(db, user.id, "whales whales enthusiasm coding", budget=2)
    assert [v.statement for v in supply.views] == ["Talk about whales with enthusiasm", "Use metric units"]
    assert supply.views[0].conflicts == ("L2",) and supply.views[1].conflicts == ("L1",)


def test_inspection_tells_missing_retrieval_from_misunderstanding_and_from_judgment(env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.core.config import get_settings
    from app.db.session import get_db
    from app.main import app

    user, _ = setup(env)
    with env.db() as db:
        other = create_user(db)
        ids = []
        for statement in ("Keep answers short", "Listen when they vent", "Use metric units", "Avoid spoilers"):
            row = MemoryLesson(user_id=user.id, kind="preference", origin="stated", scope="standing",
                               status="accepted", statement=statement, applies_when="in general",
                               user_words=[statement.lower()])
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
        supply = ML.for_decision(db, user.id, "q", budget=3)
    attempt = uuid.uuid4()
    weighed = [{"lesson": "L1", "use": "applied", "why": "kept it short"},
               {"lesson": "L2", "use": "outweighed", "why": "they asked for advice outright"}]
    assert ML.record_uses(user.id, attempt, supply, consumer="student_2", received=True, weighed=weighed,
                          decision_status="ok") == 4
    # The same decision recorded again adds nothing.
    assert ML.record_uses(user.id, attempt, supply, consumer="student_2", received=True, weighed=weighed,
                          decision_status="ok") == 4
    local = uuid.uuid4()
    ML.record_uses(user.id, local, supply, consumer="student_2", received=False,
                   reason="this decision model's input has no lessons (its rendering doesn't show them)")
    who = {"user": user}

    def db_dep():
        with env.db() as db:
            yield db

    monkeypatch.setattr(get_settings(), "owner_emails", f"{user.email},{other.email}")
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[deps.get_current_user] = lambda: who["user"]
    try:
        client = TestClient(app)
        cases = {u["ref"] or "left": u["case"] for u in client.get(f"/api/learning/adaptation/decisions/{attempt}")
                 .json()["lessons"]}
        assert cases == {"L1": "applied", "L2": "set_aside", "L3": "no_account", "left": "not_received"}
        local_cases = {u["case"] for u in client.get(f"/api/learning/adaptation/decisions/{local}").json()["lessons"]}
        assert local_cases == {"not_received"}
        listing = client.get("/api/learning/adaptation/lessons").json()
        assert listing["counts"] == {"accepted": 4} and len(listing["items"]) == 4
        detail = client.get(f"/api/learning/adaptation/lessons/{ids[1]}").json()
        used = {u["attempt_id"]: u for u in detail["uses"]}
        assert "can be sound judgment" in used[str(attempt)]["explanation"]
        assert "Look at memory, retrieval or delivery" in used[str(local)]["explanation"]
        assert client.get("/api/learning/adaptation/lessons?status=bogus").status_code == 422
        who["user"] = other  # also an owner: still only their own lessons
        assert client.get(f"/api/learning/adaptation/lessons/{ids[1]}").status_code == 404
        assert client.get(f"/api/learning/adaptation/decisions/{attempt}").json()["lessons"] == []
    finally:
        app.dependency_overrides.clear()


def test_forgetting_the_messages_a_lesson_rests_on_removes_it(env):
    from app.services import memory_reset

    user, conversation = setup(env)
    aid, msg = assessed(env, user, conversation)
    run_lessons(env, Builder(lambda text, n: preference(text)))
    with env.db() as db:
        lesson = db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id))
        # A later decision's raw capture that showed the lesson (its lesson:<id> source).
        attempt = uuid.uuid4()
        db.add(M.DecisionAttempt(id=attempt, user_id=user.id, learner="student_2", trigger="user_message",
                                 input={"prompt": "LESSONS ..."}, status="ok", started_at=NOW,
                                 expires_at=NOW + timedelta(days=30)))
        db.flush()
        db.add(M.DecisionAttemptSource(attempt_id=attempt, ref=f"lesson:{lesson.id}", user_id=user.id))
        db.commit()
        assert ML.drop_for(db, user.id, [f"message:{uuid.uuid4()}"]) == 0
        assert ML.drop_for(db, user.id, [f"message:{msg.id}"]) == 1
        db.commit()
        assert db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id)) is None
        assert db.get(M.DecisionAttempt, attempt) is None
    assessed(env, user, conversation, correction="When I'm venting, please just listen. Really.")
    run_lessons(env, Builder(lambda text, n: preference(text)))
    with env.db() as db:
        memory_reset.reset(db, user_ids=[user.id], scopes=["lessons"])
        assert db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id)) is None
        assert db.scalar(select(LearningLessonJob).where(LearningLessonJob.user_id == user.id)) is None


def test_nothing_new_is_queued_when_learning_lessons_is_off_and_nothing_is_supplied_in_the_baseline(env, teacher):
    user, conversation = setup(env)
    env.configure_learning(learning_lessons_enabled=False)
    aid, _ = assessed(env, user, conversation)
    with env.db() as db:
        assert db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == aid)) is None
        db.add(MemoryLesson(user_id=user.id, kind="preference", origin="stated", scope="standing", status="accepted",
                            statement="Keep answers short", applies_when="in general", user_words=["short"]))
        db.commit()
    settings = svc.get_settings()
    original = settings.lessons_supply_enabled
    try:
        settings.lessons_supply_enabled = False
        with env.db() as db:
            live = svc.gather(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=None,
                              message="hi", zone="UTC")
        assert live.lessons == () and "baseline" in live.lessons_note
        settings.lessons_supply_enabled = True
        with env.db() as db:
            live = svc.gather(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=None,
                              message="hi", zone="UTC")
        assert [v.statement for v in live.lessons] == ["Keep answers short"]
    finally:
        settings.lessons_supply_enabled = original


def test_a_failed_reading_retries_and_never_becomes_a_lesson(env):
    user, conversation = setup(env)
    env.configure_learning(learning_lessons_max_attempts=2)
    assessed(env, user, conversation)
    builder = Builder(RuntimeError("model down"))
    assert run_lessons(env, builder, times=2) == ["pending", "failed"]
    with env.db() as db:
        assert db.scalar(select(MemoryLesson).where(MemoryLesson.user_id == user.id)) is None


def test_model_constants_match_the_pure_contract():
    assert M.LESSON_KINDS == L.KINDS and M.LESSON_SCOPES == L.SCOPES and M.LESSON_STATUSES == L.STATUSES
    assert M.LESSON_REPORTED == L.REPORTED and set(L.OPS) <= set(M.LESSON_OPS)
