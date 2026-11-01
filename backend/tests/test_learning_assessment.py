"""Learning step 2, evidence and attribution (docs/learning-system/02-implementation.md, "Completion evidence").

The fixed cases of docs/learning-system/02-evidence-and-attribution.md with a scripted evaluator: what code keeps,
drops and downgrades, how targets and causes stay apart, what's missing versus contradicted, revisions and their
dependents, stopping, reprocessing, triggers, forgetting and the owner boundary. Pure checks run without a
database; the rest need real PostgreSQL (TEST_DATABASE_URL). No model calls: the real evaluator's quality is
measured by ``scripts/learning_assessment_eval.py``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai.learning import assessment as A
from app.db.models import (
    ChatMessage, Conversation, DecisionAttempt, DecisionExecution, LearningAssessment, LearningAssessmentEvidence,
    LearningAssessmentLink, LearningAssessmentRevision, LearningAssessmentUse, MemoryCorrection, Observation,
    ObservationProcessing, TrainingExample,
)
from app.services import chat as chat_service
from app.services import learning_assessment as LA
from app.services import observation as observation_service
from app.services.learning_capture import execution_id_for
from db_support import create_conversation, create_user, db_env, database  # noqa: F401

NOW = datetime.now(timezone.utc)


def soon(**delta) -> datetime:
    """A moment after now (records' server times are the database's now, later than NOW)."""
    return datetime.now(timezone.utc) + timedelta(**delta)


# --------------------------------------------------------------------------- #
# Scripted evaluator
# --------------------------------------------------------------------------- #


def answer(**kw) -> dict:
    base = {"feedback": "yes", "targets": [], "causes": [], "user_stated": [], "dimensions": [],
            "choice_vs_outcome": [], "missing": [], "clarification": {"propose": False, "question": ""},
            "reopens": [], "next": {"step": "conclude", "look": [], "why": "enough"}, "explanation": "scripted"}
    base.update(kw)
    return base


def target(ref, status="supported", cites=(), also=(), why="it is about this"):
    return {"ref": ref, "also": list(also), "status": status, "cites": list(cites), "why": why}


def cause(ref, component, status="supported", cites=(), why="because"):
    return {"target": ref, "component": component, "status": status, "cites": list(cites), "why": why}


class Script:
    """Answers in order (the last one repeats); a callable gets the rendered text."""

    name = "assessment-v1:scripted"

    def __init__(self, *answers):
        self.answers, self.texts = list(answers), []

    def __call__(self, text):
        self.texts.append(text)
        raw = self.answers[min(len(self.texts) - 1, len(self.answers) - 1)]
        if callable(raw):
            raw = raw(text)
        if isinstance(raw, Exception):
            return A.Answer(None, 1.0, error=str(raw))
        return A.Answer(json.loads(json.dumps(raw)), 1.0, 10, 5)


# --------------------------------------------------------------------------- #
# Pure checks (no database)
# --------------------------------------------------------------------------- #


def _bundle(*, s2_captured=True, delivery=True) -> A.Evidence:
    ev = A.Evidence(trigger="The user's message m3 (Student 1 read it as correcting her).", trigger_refs=("m3",))
    ev.add(A.Item("m1", "user", "message:u1", ("message:u1",), 'm1 [user] "what should I cook tonight"',
                  words="what should I cook tonight"))
    ev.add(A.Item("m2", "her", "turn:T", ("message:h1",), 'm2 [columbina, t2] "Try a curry."', target="t2",
                  words="Try a curry."))
    ev.add(A.Item("m3", "user", "message:u2", ("message:u2",),
                  'm3 [user] "you got it, but I just wanted to vent, not advice"',
                  words="you got it, but I just wanted to vent, not advice"))
    ev.targets["t2"] = A.Target("t2", "turn", "her reply m2", "turn:T", ("turn:T",))
    ev.add(A.Item("t2.s1", "record", "turn:T", ("attempt:s1",), "Student 1's reading (ok)", target="t2",
                  learner="student_1"))
    ev.add(A.Item("t2.s2", "record", "turn:T", ("attempt:s2",), "Student 2's decision", target="t2",
                  learner="student_2", captured=s2_captured))
    if delivery:
        ev.add(A.Item("c1", "fact", "turn:T", ("execution:r",), "delivery: succeeded", target="t2",
                      fact_kind="delivery"))
    return ev


def test_unknown_refs_are_dropped_and_a_correction_alone_never_establishes_the_cause():
    ev = _bundle()
    raw = answer(targets=[target("t2", cites=["m3", "m2"]), target("t9", cites=["m3"])],
                 causes=[cause("t2", "student_1", cites=["m3"]),           # only the user's correction
                         cause("t2", "student_2", cites=["t2.s2", "m3"]),  # its own record: kept
                         cause("t2", "mouth", cites=["zz9"])])
    checked = A.check(raw, ev)
    result = checked.result
    assert [t["ref"] for t in result["targets"]] == ["t2"] and result["targets"][0]["status"] == "supported"
    by = {c["component"]: c for c in result["causes"]}
    assert by["student_1"]["status"] == "plausible" and by["student_1"]["basis"] == "not_its_own_record"
    assert by["student_2"]["status"] == "supported"
    assert by["mouth"]["status"] == "unresolved"  # its only cite was unknown: no evidence left
    assert any("t9" in d["what"] for d in checked.dropped) and any("zz9" in d["what"] for d in checked.dropped)


def test_missing_capture_is_unresolved_with_what_is_missing_not_contradicted():
    ev = _bundle(s2_captured=False)
    raw = answer(targets=[target("t2", cites=["m2", "m3"])],
                 causes=[cause("t2", "student_2", cites=["t2.s2"]), cause("t2", "mouth", cites=["m2"]),
                         cause("t2", "student_1", "contradicted", cites=["m3"])],
                 user_stated=[{"kind": "preference", "quote": "I just wanted to vent, not advice", "cites": ["m3"]}])
    result = A.check(raw, ev).result
    by = {c["component"]: c for c in result["causes"]}
    assert by["student_2"]["status"] == "unresolved" and by["student_2"]["basis"] == "missing_capture"
    # Without Student 2's decision, wording can't be told apart from choice.
    assert by["mouth"]["status"] == "unresolved" and by["mouth"]["basis"] == "missing_capture"
    assert by["student_1"]["status"] == "contradicted"  # conflicting evidence stays distinct from missing
    assert any("Student 2's decision for t2" in m["what"] for m in result["missing"])
    # The stated preference is established anyway: the user is the authority on it.
    assert result["user_stated"][0]["status"] == "supported"
    assert A.summarize(result) == {"target_status": "supported", "cause_status": "contradicted",
                                   "outcome": "concluded"}


def test_quotes_must_be_the_users_exact_words_and_delivery_needs_its_record():
    ev = _bundle(delivery=False)
    raw = answer(user_stated=[{"kind": "preference", "quote": "never give me advice", "cites": ["m3"]},
                              {"kind": "intended_meaning", "quote": "Try a curry", "cites": ["m2"]}],
                 causes=[cause("t2", "delivery", cites=["m2"])])
    result = A.check(raw, ev).result
    assert result["user_stated"] == []  # invented words, and her own words, are never the user's
    assert result["causes"][0]["status"] == "unresolved" and "no delivery record" in result["missing"][0]["what"]


def test_dimensions_are_unknown_unless_cited_and_choice_is_judged_apart_from_outcome():
    ev = _bundle()
    raw = answer(dimensions=[{"dimension": "relationship", "target": "t2", "value": "poor", "status": "supported",
                              "cites": [], "note": ""},
                             {"dimension": "understanding", "target": "t2", "value": "good", "status": "supported",
                              "cites": ["t2.s1", "m3"], "note": "they said so"}],
                 choice_vs_outcome=[{"target": "t2", "choice": "poor", "outcome": "good", "cites": ["t2.s2", "m3"],
                                     "note": "lucky"}])
    result = A.check(raw, ev).result
    dims = {d["dimension"]: d for d in result["dimensions"]}
    assert dims["relationship"]["value"] == "unknown" and dims["relationship"]["status"] == "unresolved"
    assert dims["understanding"]["value"] == "good" and dims["understanding"]["status"] == "supported"
    assert result["choice_vs_outcome"][0]["choice"] == "poor" and result["choice_vs_outcome"][0]["outcome"] == "good"
    # Without the decision record, choice quality can't be judged.
    no_record = A.check(raw, _bundle(s2_captured=False)).result
    assert no_record["choice_vs_outcome"][0]["choice"] == "unclear"


def test_citing_one_event_many_times_is_one_independent_piece_of_evidence():
    ev = _bundle()
    ev.add(A.Item("c2", "fact", "message:u2", ("correction:x", "message:u2"), "memory correction of m3",
                  fact_kind="correction"))
    raw = answer(targets=[target("t2", cites=["m3", "m3", "c2", "m2"])])
    t = A.check(raw, ev).result["targets"][0]
    assert t["cites"] == ["m3", "c2", "m2"] and t["independent"] == 2  # the user's message, her turn
    # Rewording is not a different conclusion.
    reworded = answer(targets=[target("t2", cites=["m3", "m2"], why="entirely different words")],
                      explanation="other words")
    assert A.fingerprint(A.check(raw, ev).result) == A.fingerprint(A.check(reworded, ev).result)


def test_a_supported_target_must_cite_something_of_the_target():
    ev = _bundle()
    result = A.check(answer(targets=[target("t2", cites=["m3"])]), ev).result
    assert result["targets"][0]["status"] == "plausible"


# --------------------------------------------------------------------------- #
# Database fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    settings = LA.get_settings().model_copy(update={
        "learning_assessment_settle_seconds": 0, "learning_assessment_triage_wait_seconds": 120,
        "learning_assessment_log_path": str(tmp_path / "assess.jsonl"), "learning_assessment_sample_rate": 0.0})
    holder = {"s": settings}
    monkeypatch.setattr(LA, "get_settings", lambda: holder["s"])

    def configure(**updates):
        holder["s"] = holder["s"].model_copy(update=updates)

    db_env.configure_learning = configure
    return db_env


def say(db, conversation, role, text, *, turn_id=None, status="completed", heard=None, at=None) -> ChatMessage:
    conversation = db.get(Conversation, conversation.id)
    msg = chat_service.add_message(db, conversation=conversation, role=role, content=text, turn_id=turn_id,
                                   status=status)
    if heard is not None or at is not None:
        msg.heard_chars = heard
        if at is not None:
            msg.created_at = at
        db.commit()
    return msg


DECISION = {"intention": "help them choose dinner", "communication": "respond", "move": "answer_explain",
            "what_to_communicate": "suggest a curry", "work": "no_change"}


def capture_turn(db, user, conversation, turn_id, her: ChatMessage, asked: ChatMessage | None, *, s1=True, s2=True,
                 acts=None, result="succeeded", deviation=None, provider="teacher"):
    """The records step 1 would have captured for a turn, written directly."""
    s1_id, s2_id = uuid.uuid5(turn_id, "student_1:1"), uuid.uuid5(turn_id, "student_2:1")
    started = NOW - timedelta(minutes=5)
    common = dict(user_id=user.id, turn_id=turn_id, conversation_id=conversation.id,
                  source_message_id=asked.id if asked else None, started_at=started, produced_at=started,
                  expires_at=NOW + timedelta(days=30), status="ok")
    if s1:
        understand = {"acts": {a: a in (acts or ["asking"]) for a in ("asking", "correcting_her", "complaining")},
                      "tone": "friendly", "request": "talk_only", "sure": True}
        db.add(DecisionAttempt(id=s1_id, learner="student_1", trigger="user_message", provider="local",
                               input={"bundle": {"message": asked.content if asked else ""}},
                               output={"understand": understand}, **common))
    if s2:
        db.add(DecisionAttempt(id=s2_id, learner="student_2", trigger="user_message", provider=provider,
                               upstream={"student_1": str(s1_id)},
                               input={"prompt": f"SITUATION for {turn_id}", "snapshot": {}},
                               output={"decision": DECISION}, **common))
    db.add(DecisionExecution(id=execution_id_for(turn_id, "reply"), user_id=user.id, attempt_id=s2_id,
                             turn_id=turn_id, kind="reply", executor="mouth:web", conversation_id=conversation.id,
                             assistant_message_id=her.id, intended=DECISION, executed={"outcome": "completed"},
                             result=result, deviation=deviation, ended_at=started, expires_at=NOW + timedelta(days=30)))
    db.commit()
    return s1_id, s2_id


def exchange(db, user, conversation, question, reply, **kw):
    """One user message and her captured reply. Returns (user message, her message, turn id, (s1, s2))."""
    turn_id = uuid.uuid4()
    asked = say(db, conversation, "user", question, turn_id=turn_id)
    her = say(db, conversation, "assistant", reply, turn_id=turn_id)
    ids = capture_turn(db, user, conversation, turn_id, her, asked, **kw)
    return asked, her, turn_id, ids


def correction(db, user, conversation, text):
    msg = say(db, conversation, "user", text, turn_id=uuid.uuid4())
    row, _ = LA.open_assessment(db, user_id=user.id, kind="correction", key=f"message:{msg.id}",
                                anchor_at=msg.created_at, conversation_id=conversation.id, anchor_message_id=msg.id,
                                trigger={"why": "Student 1 read it as correcting her"})
    db.commit()
    return msg, row.id


def run(env, script, *, times=1):
    outcomes = []
    for _ in range(times):
        with env.db() as db:
            claimed = LA.claim(db, soon(hours=1))
        if claimed is None:
            outcomes.append(None)
            continue
        with env.db() as db:
            outcomes.append(LA.assess(db, claimed, evaluator=script))
    return outcomes


def latest(env, assessment_id):
    with env.db() as db:
        row = db.get(LearningAssessment, assessment_id)
        rev = db.scalar(select(LearningAssessmentRevision).where(
            LearningAssessmentRevision.assessment_id == assessment_id,
            LearningAssessmentRevision.revision == row.revision))
        return row, rev


def setup(env):
    with env.db() as db:
        LA.started_at(db)  # assessment began before anything here (the sweep never imports older records)
        db.commit()
        user = create_user(db)
        conversation = create_conversation(db, user)
    return user, conversation


# --------------------------------------------------------------------------- #
# The fixed cases
# --------------------------------------------------------------------------- #


def test_correct_interpretation_with_bad_wording_blames_the_wording_only(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, turn_id, (s1, s2) = exchange(db, user, conversation, "I had an awful day at work",
                                                 "Have you considered quitting?")
        msg, aid = correction(db, user, conversation, "you understood me fine, it just came out really cold")
    t = f"t{her.conversation_sequence}"
    script = Script(answer(targets=[target(t, cites=[f"m{msg.conversation_sequence}", f"m{her.conversation_sequence}"])],
                           causes=[cause(t, "mouth", cites=[f"m{her.conversation_sequence}", f"{t}.s2"]),
                                   cause(t, "student_1", "contradicted", cites=[f"m{msg.conversation_sequence}"])],
                           dimensions=[{"dimension": "understanding", "target": t, "value": "good",
                                        "status": "supported", "cites": [f"{t}.s1", f"m{msg.conversation_sequence}"],
                                        "note": "they said so"}]))
    assert run(env, script) == ["revised"]
    row, rev = latest(env, aid)
    assert row.state == "done" and row.outcome == "concluded" and row.revision == 1
    causes = {c["component"]: c["status"] for c in rev.result["causes"]}
    assert causes == {"mouth": "supported", "student_1": "contradicted"}
    # What it read: the conversation, her turn with its records, the delivery fact with what it establishes.
    text = script.texts[0]
    assert "Have you considered quitting?" in text and f"{t}.s2 Student 2's decision (ok)" in text
    assert "Establishes: what happened to the reply" in text and "Does not establish" in text
    with env.db() as db:
        links = {(li.role, li.ref, li.component) for li in db.scalars(select(LearningAssessmentLink).where(
            LearningAssessmentLink.assessment_id == aid))}
    assert ("target", f"attempt:{s2}", "") in links and ("target", f"turn:{turn_id}", "") in links
    assert ("cause", f"turn:{turn_id}", "mouth") in links and ("cause", f"attempt:{s1}", "student_1") in links


def test_a_delivery_failure_does_not_become_a_choice_or_interpretation_error(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, turn_id, (s1, s2) = exchange(db, user, conversation, "what's the capital of Peru?", "Lima",
                                                 result="failed")
        LA.sweep(db, soon(seconds=1))
        row = db.scalar(select(LearningAssessment).where(LearningAssessment.trigger_key == f"reply:{turn_id}"))
        assert row.trigger_kind == "execution"
        aid = row.id
    t = f"t{her.conversation_sequence}"

    def evaluate(text):
        fact = next(line.split()[0] for line in text.splitlines() if "delivery (mouth:web): failed" in line)
        return answer(targets=[target(t, cites=[fact])],
                      causes=[cause(t, "delivery", cites=[fact]), cause(t, "student_2", cites=[fact]),
                              cause(t, "student_1", cites=[fact])])

    run(env, Script(evaluate))
    _, rev = latest(env, aid)
    causes = {c["component"]: c["status"] for c in rev.result["causes"]}
    assert causes == {"delivery": "supported", "student_2": "plausible", "student_1": "plausible"}
    # The sweep found it once: a second sweep adds nothing.
    with env.db() as db:
        LA.sweep(db, soon(seconds=2))
        assert len(list(db.scalars(select(LearningAssessment).where(LearningAssessment.user_id == user.id)))) == 1


def test_unwanted_advice_despite_understanding_reaches_student_2_and_flags_its_training_example(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, turn_id, (s1, s2) = exchange(db, user, conversation, "my boss yelled at me again",
                                                 "Here are five ways to handle a difficult boss.")
        db.add(TrainingExample(user_id=user.id, learner="student_2", attempt_id=s2, label_source="teacher_decision",
                               input={"prompt": "x"}, target={"decision": DECISION}, decided_at=NOW))
        db.commit()
        msg, aid = correction(db, user, conversation, "you got it, but I wanted you to just listen, not fix it")
    t, m = f"t{her.conversation_sequence}", f"m{msg.conversation_sequence}"
    script = Script(answer(
        targets=[target(t, cites=[m, f"m{her.conversation_sequence}"])],
        causes=[cause(t, "student_2", cites=[f"{t}.s2", m]), cause(t, "student_1", "contradicted", cites=[m])],
        user_stated=[{"kind": "preference", "quote": "I wanted you to just listen, not fix it", "cites": [m]},
                     {"kind": "preference", "quote": "I hate advice", "cites": [m]}]))
    run(env, script)
    row, rev = latest(env, aid)
    assert {c["component"]: c["status"] for c in rev.result["causes"]} == {"student_2": "supported",
                                                                           "student_1": "contradicted"}
    assert [s["quote"] for s in rev.result["user_stated"]] == ["I wanted you to just listen, not fix it"]
    assert any("I hate advice" in d["what"] for d in rev.dropped)
    with env.db() as db:
        example = db.scalar(select(TrainingExample).where(TrainingExample.attempt_id == s2))
        use = db.scalar(select(LearningAssessmentUse).where(LearningAssessmentUse.assessment_id == aid))
    assert not example.eligible and example.review_status == "needs_review" and str(aid) in example.ineligible_reason
    assert use.consumer == "training_examples" and use.state == "current" and use.revision == 1

    # New evidence revises it: the user's note says the advice was fine. The old revision is kept, superseded; the
    # flag it led to is told to reconsider (its owner decides; the example stays under review).
    with env.db() as db:
        LA.add_note(db, user.id, aid, "actually the advice was fine, I was just tired")
    run(env, Script(answer(targets=[target(t, cites=["n1", f"m{her.conversation_sequence}"])],
                           causes=[cause(t, "user_side", cites=["n1"])],
                           user_stated=[{"kind": "intended_meaning", "quote": "I was just tired", "cites": ["n1"]}])))
    row, rev = latest(env, aid)
    assert row.revision == 2 and rev.reason.startswith("new evidence: note:")
    with env.db() as db:
        first = db.scalar(select(LearningAssessmentRevision).where(LearningAssessmentRevision.assessment_id == aid,
                                                                   LearningAssessmentRevision.revision == 1))
        use = db.scalar(select(LearningAssessmentUse).where(LearningAssessmentUse.assessment_id == aid))
        example = db.scalar(select(TrainingExample).where(TrainingExample.attempt_id == s2))
    assert first.superseded_by == 2 and first.superseded_at is not None and first.result["causes"]  # history kept
    assert use.state == "reconsider" and "no longer supports" in use.reason
    assert example.review_status == "needs_review" and "was revised" in example.ineligible_reason


def test_delayed_feedback_about_an_earlier_turn_and_about_a_sequence(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, first, first_turn, (_, first_s2) = exchange(db, user, conversation, "recommend a book",
                                                       "You might love The Remains of the Day by Ishiguro.")
        _, second, _, _ = exchange(db, user, conversation, "and a film?", "Try Paterson.")
        _, third, _, _ = exchange(db, user, conversation, "thanks", "Anytime!")
        msg, aid = correction(db, user, conversation,
                              'about "The Remains of the Day by Ishiguro" - I told you I read it already')
    t1, t2, t3 = (f"t{m.conversation_sequence}" for m in (first, second, third))

    def evaluate(text):
        quote = next(line.split()[0] for line in text.splitlines() if line.strip().startswith("c") and "quote:" in line)
        assert quote and f"from m{first.conversation_sequence}" in text
        return answer(targets=[target(t1, cites=[quote, f"m{msg.conversation_sequence}"]),
                               target(t2, "plausible", cites=[f"m{msg.conversation_sequence}"], also=[t3])])

    run(env, Script(evaluate))
    _, rev = latest(env, aid)
    by = {t["ref"]: t for t in rev.result["targets"]}
    # Not the latest reply: the earlier turn the user quoted.
    assert by[t1]["status"] == "supported" and f"turn:{first_turn}" in by[t1]["target_roots"]
    assert by[t2]["status"] == "plausible" and by[t2]["also"] == [t3]
    with env.db() as db:
        found = LA.for_ref(db, user.id, f"attempt:{first_s2}")
        targets = set(db.scalars(select(LearningAssessmentLink.ref).where(
            LearningAssessmentLink.assessment_id == aid, LearningAssessmentLink.role == "target")))
    assert [f["id"] for f in found] == [str(aid)]
    assert f"message:{third.id}" in targets  # the sequence's other turn is linked too


def test_ambiguous_targets_stay_ambiguous_and_lucky_success_is_kept_apart_from_choice(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, a, _, _ = exchange(db, user, conversation, "should I text him?", "Maybe wait a day.")
        _, b, _, _ = exchange(db, user, conversation, "he texted first!", "See, waiting worked.")
        msg, aid = correction(db, user, conversation, "that was a weird thing to say")
    ta, tb, m = f"t{a.conversation_sequence}", f"t{b.conversation_sequence}", f"m{msg.conversation_sequence}"
    run(env, Script(answer(
        targets=[target(ta, "plausible", cites=[m]), target(tb, "plausible", cites=[m])],
        causes=[cause(ta, "unclear", "supported", cites=[m])],
        choice_vs_outcome=[{"target": ta, "choice": "poor", "outcome": "good", "cites": [f"{ta}.s2", m],
                            "note": "it worked out by luck"}],
        next={"step": "need_missing", "look": [], "why": "which remark they mean"},
        missing=[{"what": "which remark they meant", "needed_for": "the target"}],
        clarification={"propose": True, "question": "Which thing I said felt weird?"})))
    row, rev = latest(env, aid)
    assert row.outcome == "unresolved" and row.target_status == "plausible" and row.stop_reason == "missing_evidence"
    assert rev.result["causes"][0]["status"] == "unresolved"  # "unclear" is never a supported cause
    assert rev.result["choice_vs_outcome"][0] | {"choice": "poor", "outcome": "good"} == rev.result["choice_vs_outcome"][0]
    assert rev.result["clarification"]["question"] == "Which thing I said felt weird?"


def test_incomplete_capture_withholds_attribution_until_the_capture_arrives(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, her, turn_id, (s1, s2) = exchange(db, user, conversation, "remind me what we said",
                                             "We said Tuesday.", s2=False)
        msg, aid = correction(db, user, conversation, "no, I meant the venue, not the day")
    t, m = f"t{her.conversation_sequence}", f"m{msg.conversation_sequence}"
    blame = answer(targets=[target(t, cites=[m, f"m{her.conversation_sequence}"])],
                   causes=[cause(t, "student_2", cites=[f"{t}.s2"]), cause(t, "student_1", "plausible", cites=[m])],
                   user_stated=[{"kind": "intended_meaning", "quote": "I meant the venue, not the day", "cites": [m]}])
    script = Script(blame)
    run(env, script)
    assert "Student 2's decision: not captured (never saved)" in script.texts[0]
    row, rev = latest(env, aid)
    s2_cause = next(c for c in rev.result["causes"] if c["component"] == "student_2")
    assert s2_cause["status"] == "unresolved" and s2_cause["basis"] == "missing_capture"
    assert rev.result["user_stated"][0]["status"] == "supported" and row.outcome == "concluded"
    assert row.awaiting == [f"attempt:{s2}"]

    # The capture arrives late: the sweep attaches it and the assessment is revised with it.
    with env.db() as db:
        db.add(DecisionAttempt(id=s2, user_id=user.id, learner="student_2", trigger="user_message",
                               turn_id=turn_id, conversation_id=conversation.id, input={"prompt": "late"},
                               output={"decision": DECISION}, status="ok", started_at=NOW, expires_at=NOW + timedelta(days=1)))
        db.commit()
        assert LA.sweep(db, soon(seconds=3))["arrived"] == 1
    run(env, script)
    row, rev = latest(env, aid)
    assert row.revision == 2 and rev.reason == f"new evidence: attempt:{s2}" and row.awaiting == []
    assert next(c for c in rev.result["causes"] if c["component"] == "student_2")["status"] == "supported"


def test_repeated_reflection_adds_no_revision_and_no_independent_evidence(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, her, _, _ = exchange(db, user, conversation, "do you like jazz?", "I adore it.")
        msg, aid = correction(db, user, conversation, "you always say that")
    t, m = f"t{her.conversation_sequence}", f"m{msg.conversation_sequence}"
    script = Script(answer(targets=[target(t, cites=[m, m, m, f"m{her.conversation_sequence}"])]))
    run(env, script)
    _, rev = latest(env, aid)
    assert rev.independent == 2
    # Reflecting again on the same evidence: no new revision, nothing more counted.
    with env.db() as db:
        row = db.get(LearningAssessment, aid)
        LA._reopen(row, NOW, "asked to think again")
        db.commit()
    assert run(env, script) == ["unchanged"]
    row, rev = latest(env, aid)
    assert row.revision == 1 and row.state == "done" and rev.independent == 2
    # Reprocessing the trigger attaches nothing twice.
    with env.db() as db:
        _, changed = LA.open_assessment(db, user_id=user.id, kind="correction", key=f"message:{msg.id}",
                                        anchor_at=msg.created_at, trigger={}, conversation_id=conversation.id,
                                        anchor_message_id=msg.id, evidence=[("correction:x", "", {})])
        _, again = LA.open_assessment(db, user_id=user.id, kind="correction", key=f"message:{msg.id}",
                                      anchor_at=msg.created_at, trigger={}, conversation_id=conversation.id,
                                      anchor_message_id=msg.id, evidence=[("correction:x", "", {})])
        db.commit()
        rows = list(db.scalars(select(LearningAssessmentEvidence).where(LearningAssessmentEvidence.assessment_id == aid)))
    assert changed and not again and len(rows) == 1


def test_stuck_reasoning_stops_without_a_forced_conclusion(env):
    user, conversation = setup(env)
    env.configure_learning(learning_assessment_window_messages=2)
    with env.db() as db:
        for i in range(4):
            exchange(db, user, conversation, f"question {i}", f"answer {i}")
        msg, aid = correction(db, user, conversation, "that's not right")
    keep_looking = answer(targets=[], next={"step": "look_further", "look": ["earlier"], "why": "more context"})
    script = Script(keep_looking)
    run(env, script)
    row, rev = latest(env, aid)
    # The window doubled until the start of the conversation; then there was nothing more to look at.
    assert len(script.texts) == len(rev.rounds) and 2 <= len(rev.rounds) <= 4
    assert row.stop_reason == "stuck" and row.outcome == "unresolved"
    assert "question 0" in script.texts[-1] and "question 0" not in script.texts[0]
    # Asking for the same thing again adds nothing new: stuck at once.
    with env.db() as db:
        msg2, aid2 = correction(db, user, conversation, "still wrong")
    same = Script(answer(next={"step": "look_further", "look": ["later"], "why": "maybe"}))
    run(env, same)
    row2, rev2 = latest(env, aid2)
    assert len(same.texts) == 1 and row2.stop_reason == "stuck"
    assert any("later" in d["what"] for d in rev2.dropped)


def test_an_evaluator_failure_retries_and_never_becomes_a_conclusion(env):
    user, conversation = setup(env)
    env.configure_learning(learning_assessment_max_attempts=2)
    with env.db() as db:
        exchange(db, user, conversation, "hi", "hello")
        _, aid = correction(db, user, conversation, "you misread me")
    failing = Script(RuntimeError("model down"))
    assert run(env, failing) == ["pending"]
    row, _ = latest(env, aid)
    assert row.revision == 0 and row.last_error and row.outcome is None
    with env.db() as db:
        db.get(LearningAssessment, aid).next_attempt_at = NOW
        db.commit()
    assert run(env, failing) == ["failed"]
    row, _ = latest(env, aid)
    assert row.state == "failed" and row.revision == 0


def test_an_open_assessment_is_reopened_by_later_evidence(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, her, _, _ = exchange(db, user, conversation, "plan my weekend", "Hike on Saturday?")
        first, first_id = correction(db, user, conversation, "hmm no")
    run(env, Script(answer(feedback="unclear", next={"step": "need_missing", "look": [], "why": "too vague"})))
    row, _ = latest(env, first_id)
    assert row.outcome == "unresolved"
    with env.db() as db:
        second, second_id = correction(db, user, conversation, "I meant: I hate hiking, I told you")
    m2 = f"m{second.conversation_sequence}"
    run(env, Script(answer(targets=[target(f"t{her.conversation_sequence}", cites=[m2, f"m{her.conversation_sequence}"])],
                           reopens=[{"ref": "p1", "cites": [m2], "why": "this explains the earlier no"}])))
    row, _ = latest(env, first_id)
    assert row.state == "pending" and row.epoch >= 1
    with env.db() as db:
        refs = set(db.scalars(select(LearningAssessmentEvidence.ref).where(
            LearningAssessmentEvidence.assessment_id == first_id)))
    assert refs == {f"message:{second.id}"}
    script = Script(answer(targets=[target(f"t{her.conversation_sequence}", cites=[m2])]))
    run(env, script)
    assert "I hate hiking" in script.texts[0]  # the later message is shown in the earlier assessment now
    row, rev = latest(env, first_id)
    assert row.revision == 2 and rev.reason == f"new evidence: message:{second.id}"


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


def _observe(db, user, msg: ChatMessage):
    observation_service.record(db, observation_service.Report(
        user_id=user.id, source="conversation", source_instance=str(msg.conversation_id),
        source_event_key=f"message:{msg.id}", kind="user_message",
        payload={"message_id": str(msg.id), "sequence": msg.conversation_sequence, "channel": "web",
                 "chars": len(msg.content)}, message_id=msg.id))
    db.commit()


def _student_1(db, user, msg, acts):
    understand = {"acts": {a: True for a in acts}, "tone": "upset", "request": "no_request"}
    db.add(DecisionAttempt(id=uuid.uuid4(), user_id=user.id, learner="student_1", trigger="user_message",
                           source_message_id=msg.id,
                           conversation_id=msg.conversation_id, input={"bundle": {}}, output={"understand": understand},
                           status="ok", started_at=NOW, expires_at=NOW + timedelta(days=1)))
    db.commit()


def test_triage_assesses_corrections_and_complaints_waits_for_the_reading_and_samples_the_rest(env):
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "hello", "Hi!")
        corrected = say(db, conversation, "user", "no, that's not what I asked")
        complaint = say(db, conversation, "user", "you keep interrupting me")
        ordinary = say(db, conversation, "user", "anyway, how are you")
        unread = say(db, conversation, "user", "fine")
        _student_1(db, user, corrected, ["correcting_her"])
        _student_1(db, user, complaint, ["complaining"])
        _student_1(db, user, ordinary, ["small_talk"])
        for m in (corrected, complaint, ordinary, unread):
            _observe(db, user, m)
        assert LA.triage(db, soon(seconds=1)) == 4
        kinds = {r.trigger_key: r.trigger_kind for r in db.scalars(select(LearningAssessment))}
        receipts = {r.observation_id: r for r in db.scalars(select(ObservationProcessing).where(
            ObservationProcessing.consumer == "learning"))}
    assert kinds == {f"message:{corrected.id}": "correction", f"message:{complaint.id}": "complaint"}
    statuses = sorted((r.status, r.attempts) for r in receipts.values())
    # The message Student 1 hasn't been captured for yet waits (not a failure); the ordinary one isn't sampled.
    assert statuses == [("applied", 0), ("applied", 0), ("retry", 0), ("skipped", 0)]
    env.configure_learning(learning_assessment_sample_rate=1.0, learning_assessment_triage_wait_seconds=0)
    with env.db() as db:
        LA.triage(db, soon(minutes=5))
        row = db.scalar(select(LearningAssessment).where(LearningAssessment.trigger_key == f"message:{unread.id}"))
    assert row.trigger_kind == "sample" and "wasn't captured" in row.trigger["why"]


def test_a_memory_correction_joins_the_messages_assessment(env):
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "my sister lives in Porto", "Porto, lovely.")
        msg = say(db, conversation, "user", "sorry, I said Porto but she lives in Lisbon")
        row, _ = LA.open_assessment(db, user_id=user.id, kind="sample", key=f"message:{msg.id}",
                                    anchor_at=msg.created_at, conversation_id=conversation.id, anchor_message_id=msg.id,
                                    trigger={"why": "sampled"})
        db.commit()
        db.add(MemoryCorrection(user_id=user.id, source_message_id=msg.id, subject="sister's city",
                                previous_value="Porto", new_value="Lisbon", kind="mistaken",
                                user_words="she lives in Lisbon", said_at=msg.created_at, fingerprint="f" * 32))
        db.commit()
        assert LA.sweep(db, soon(seconds=1))["corrections"] == 1
        assert LA.sweep(db, soon(seconds=2))["corrections"] == 0
        row = db.get(LearningAssessment, row.id)
    assert row.trigger_kind == "correction" and row.trigger["was"] == "sample"
    script = Script(answer(feedback="no"))
    run(env, script)
    assert 'memory correction: memory read' in script.texts[0] and "Does not establish: whose mistake" in script.texts[0]


def test_a_failed_skill_run_targets_her_hands_and_repeats_join_one_assessment(env):
    from app.db.models import DesktopEnvironment, DesktopSession, GameExperience

    user, _ = setup(env)
    with env.db() as db:
        environment = DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                                         allowed_profile_ids=["minecraft_pack"], status="online")
        db.add(environment)
        db.flush()
        session = DesktopSession(owner_user_id=user.id, environment_id=environment.id, profile_id="minecraft_pack",
                                 profile_revision=1, state="running", control_owner="gameplay",
                                 max_duration_seconds=3600)
        db.add(session)
        db.commit()
        hands = uuid.uuid4()
        db.add(DecisionAttempt(id=hands, user_id=user.id, learner="hands", trigger="hands_step",
                               game_session_id=session.id, input={"text": "step s1: get iron"},
                               output={"action": ["mine", {"block": "iron_ore"}]}, status="ok", started_at=NOW,
                               expires_at=NOW + timedelta(days=1)))
        experiences = []
        for _ in range(2):
            e = GameExperience(user_id=user.id, session_id=session.id, game="minecraft", kind="skill_outcome",
                               payload={"skill": "mine", "params": {"block": "iron_ore"}, "status": "failed",
                                        "reason": "needs a stone pickaxe", "step_id": "s1", "attempt_id": str(hands),
                                        "data": {"counted": {"gained": {}}}})
            db.add(e)
            db.flush()
            experiences.append(e)
            observation_service.record(db, observation_service.Report(
                user_id=user.id, source="minecraft_pack", source_instance=str(session.id),
                source_event_key=f"experience:{e.id}", kind="skill_outcome", session_id=session.id,
                experience_id=e.id, payload={"experience_id": e.id, "skill": "mine", "status": "failed",
                                             "reason": "needs a stone pickaxe", "step_id": "s1"}))
        db.commit()
        LA.triage(db, soon(seconds=1))
        rows = list(db.scalars(select(LearningAssessment).where(LearningAssessment.user_id == user.id)))
    assert len(rows) == 1 and rows[0].trigger_key == f"skill:{session.id}:s1:mine"
    script = Script(lambda text: answer(targets=[target("h1", cites=["h1.d", f"g{experiences[0].id}"])],
                                        causes=[cause("h1", "hands", cites=["h1.d"]),
                                                cause("h1", "planner", cites=[f"g{experiences[0].id}"])]))
    run(env, script)
    _, rev = latest(env, rows[0].id)
    assert "her hands' decision (ok): chose ['mine', {'block': 'iron_ore'}]" in script.texts[0]
    assert "needs a stone pickaxe" in script.texts[0] and "counted while it ran" in script.texts[0]
    causes = {c["component"]: c for c in rev.result["causes"]}
    # The planner's decisions aren't captured: a cause on it can be proposed, never established.
    assert causes["hands"]["status"] == "supported" and causes["planner"]["status"] == "unresolved"
    assert any("planner's decisions aren't captured" in m["what"] for m in rev.result["missing"])


# --------------------------------------------------------------------------- #
# Forgetting, deletion, the owner boundary
# --------------------------------------------------------------------------- #


def test_forgetting_and_deleting_a_conversation_remove_its_assessments(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, _, _ = exchange(db, user, conversation, "my secret is X", "Noted.")
        _, aid = correction(db, user, conversation, "don't call it a secret")
        assert LA.drop_for(db, user.id, [f"message:{asked.id}"]) == 1
        db.commit()
        assert db.get(LearningAssessment, aid) is None
        # An earlier assessment that looked at later messages (one of them forgotten, never cited) goes too.
        _, early = correction(db, user, conversation, "hmm")
        _, unrelated = correction(db, user, conversation, "ok")
        later = say(db, conversation, "user", "my other secret is Y")
    looks = Script(answer(next={"step": "look_further", "look": ["later"], "why": "what came next"}),
                   answer(next={"step": "conclude", "look": [], "why": "enough"}))
    with env.db() as db:
        LA.attach(db, db.get(LearningAssessment, unrelated), [("note:x", "", {"text": "fine"})])
        db.get(LearningAssessment, unrelated).next_attempt_at = soon(days=1)  # not run: it never looked later
        db.commit()
    run(env, looks)
    with env.db() as db:
        assert "my other secret is Y" in looks.texts[1]
        assert LA.drop_for(db, user.id, [f"message:{later.id}"]) == 1
        db.commit()
        assert db.get(LearningAssessment, early) is None and db.get(LearningAssessment, unrelated) is not None
        _, aid = correction(db, user, conversation, "again")
        _, aid = correction(db, user, conversation, "again")
    with env.db() as db:
        chat_service.delete_conversation(db, user_id=user.id, conversation_id=conversation.id)
    with env.db() as db:
        assert db.get(LearningAssessment, aid) is None


def test_the_api_is_the_owners_and_a_note_reopens_the_assessment(env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.db.session import get_db
    from app.main import app

    owner, conversation = setup(env)
    with env.db() as db:
        other = create_user(db)
        exchange(db, owner, conversation, "hi", "hello")
        _, aid = correction(db, owner, conversation, "wrong")
    run(env, Script(answer(feedback="unclear", next={"step": "need_missing", "look": [], "why": "vague"})))
    who = {"user": owner}

    def db_dep():
        with env.db() as db:
            yield db

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "owner_emails", f"{owner.email},{other.email}")
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[deps.validate_origin] = lambda: True
    app.dependency_overrides[deps.get_current_user] = lambda: who["user"]
    try:
        client = TestClient(app)
        listing = client.get("/api/learning/assessments").json()
        assert [i["id"] for i in listing["items"]] == [str(aid)] and listing["items"][0]["outcome"] == "unresolved"
        detail = client.get(f"/api/learning/assessments/{aid}").json()
        assert detail["revisions"][0]["stop_reason"] == "missing_evidence" and detail["links"] == []
        noted = client.post(f"/api/learning/assessments/{aid}/notes", json={"text": "I meant the time"})
        assert noted.status_code == 200, noted.text
        assert noted.json()["state"] == "pending" and noted.json()["evidence"][0]["detail"]["text"] == "I meant the time"
        who["user"] = other  # also an owner here: still only their own records
        assert client.get(f"/api/learning/assessments/{aid}").status_code == 404
        assert client.get("/api/learning/assessments").json()["items"] == []
        assert client.post(f"/api/learning/assessments/{aid}/notes", json={"text": "x"}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_the_real_model_evals_scoring_catches_blame_misses_and_always_unresolved():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import learning_assessment_eval as E

    cases = {c.name: c for c in E.cases()}
    bad = cases["bad_wording"]
    good = answer(targets=[target("t2", cites=["m3", "m2"])], causes=[cause("t2", "mouth", cites=["m2", "t2.s2"])])
    blame = answer(targets=[target("t2", cites=["m3", "m2"])], causes=[cause("t2", "student_1", cites=["t2.s1"])])
    shrug = answer(targets=[target("t2", "unresolved")], next={"step": "need_missing", "look": [], "why": "?"})

    def verdict(raw):
        return E.score(bad, {"result": A.check(raw, bad.ev).result})

    assert verdict(good)["passed"]
    assert any("unsupported blame: student_1" in p for p in verdict(blame)["problems"])
    assert not verdict(shrug)["passed"]  # always answering "unresolved" fails
    for case in cases.values():
        assert A.render(case.ev).startswith("# What started this")
