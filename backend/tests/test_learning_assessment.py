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
    ObservationProcessing,
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
            "choice_vs_outcome": [], "missing": [], "reopens": [], "next": {"step": "conclude", "look": [], "why": "enough"},
            "explanation": "scripted", "review": {"decision": "examine", "why": "scripted"}, "findings": [],
            "requests": [], "inquiry": {"question": "", "provisional": "", "alternatives": [], "unknown": "",
                                        "resume_when": ""},
            "follow_up": {"propose": False, "kind": "question", "text": "", "resolves": ""}}
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


def _bundle(*, mind_captured=True, delivery=True) -> A.Evidence:
    ev = A.Evidence(trigger="The user's message m3 (memory read it as correcting an earlier statement).",
                    trigger_refs=("m3",))
    ev.add(A.Item("m1", "user", "message:u1", ("message:u1",), 'm1 [user] "what should I cook tonight"',
                  words="what should I cook tonight"))
    ev.add(A.Item("m2", "her", "turn:T", ("message:h1",), 'm2 [columbina, t2] "Try a curry."', target="t2",
                  words="Try a curry."))
    ev.add(A.Item("m3", "user", "message:u2", ("message:u2",),
                  'm3 [user] "you got it, but I just wanted to vent, not advice"',
                  words="you got it, but I just wanted to vent, not advice"))
    ev.targets["t2"] = A.Target("t2", "turn", "her reply m2", "turn:T", ("turn:T",))
    ev.add(A.Item("t2.m", "record", "turn:T", ("attempt:m",), "her mind's evaluation (ok)", target="t2",
                  learner="mind", captured=mind_captured))
    if delivery:
        ev.add(A.Item("c1", "fact", "turn:T", ("execution:r",), "delivery: succeeded", target="t2",
                      fact_kind="delivery"))
    return ev


def test_unknown_refs_are_dropped_and_a_correction_alone_never_establishes_the_cause():
    ev = _bundle()
    raw = answer(targets=[target("t2", cites=["m3", "m2"]), target("t9", cites=["m3"])],
                 causes=[cause("t2", "mind", cites=["m3"]),               # only the user's correction
                         cause("t2", "delivery", cites=["c1", "m3"]),     # its own record: kept
                         cause("t2", "timing", cites=["zz9"])])
    checked = A.check(raw, ev)
    result = checked.result
    assert [t["ref"] for t in result["targets"]] == ["t2"] and result["targets"][0]["status"] == "supported"
    by = {c["component"]: c for c in result["causes"]}
    assert by["mind"]["status"] == "plausible" and by["mind"]["basis"] == "not_its_own_record"
    assert by["delivery"]["status"] == "supported"
    assert by["timing"]["status"] == "unresolved"  # its only cite was unknown: no evidence left
    # Her mind's own record cited: a supported cause.
    own = A.check(answer(targets=[target("t2", cites=["m3", "m2"])], causes=[cause("t2", "mind", cites=["t2.m", "m3"])]),
                  ev).result
    assert own["causes"][0]["status"] == "supported"
    assert any("t9" in d["what"] for d in checked.dropped) and any("zz9" in d["what"] for d in checked.dropped)


def test_missing_capture_is_unresolved_with_what_is_missing_not_contradicted():
    ev = _bundle(mind_captured=False)
    raw = answer(targets=[target("t2", cites=["m2", "m3"])],
                 causes=[cause("t2", "mind", cites=["t2.m"]), cause("t2", "delivery", "contradicted", cites=["m3"])],
                 user_stated=[{"kind": "preference", "quote": "I just wanted to vent, not advice", "cites": ["m3"]}])
    result = A.check(raw, ev).result
    by = {c["component"]: c for c in result["causes"]}
    assert by["mind"]["status"] == "unresolved" and by["mind"]["basis"] == "missing_capture"
    assert by["delivery"]["status"] == "contradicted"  # conflicting evidence stays distinct from missing
    assert any("her mind's evaluation for t2" in m["what"] for m in result["missing"])
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
                              "cites": ["t2.m", "m3"], "note": "they said so"}],
                 choice_vs_outcome=[{"target": "t2", "choice": "poor", "outcome": "good", "cites": ["t2.m", "m3"],
                                     "note": "lucky"}])
    result = A.check(raw, ev).result
    dims = {d["dimension"]: d for d in result["dimensions"]}
    assert dims["relationship"]["value"] == "unknown" and dims["relationship"]["status"] == "unresolved"
    assert dims["understanding"]["value"] == "good" and dims["understanding"]["status"] == "supported"
    assert result["choice_vs_outcome"][0]["choice"] == "poor" and result["choice_vs_outcome"][0]["outcome"] == "good"
    # Without the decision record, choice quality can't be judged.
    no_record = A.check(raw, _bundle(mind_captured=False)).result
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
        "learning_assessment_settle_seconds": 0, "learning_assessment_log_path": str(tmp_path / "assess.jsonl")})
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


def capture_turn(db, user, conversation, turn_id, her: ChatMessage, asked: ChatMessage | None, *, mind=True,
                 result="succeeded", deviation=None):
    """The records capture would have saved for a turn (one evaluation of her mind and its delivery), written
    directly."""
    mind_id = uuid.uuid5(turn_id, "mind:1")
    started = NOW - timedelta(minutes=5)
    if mind:
        db.add(DecisionAttempt(id=mind_id, user_id=user.id, learner="mind", trigger="user_message", provider="openai",
                               turn_id=turn_id, conversation_id=conversation.id,
                               source_message_id=asked.id if asked else None, started_at=started, produced_at=started,
                               expires_at=NOW + timedelta(days=30), status="ok",
                               input={"reason": "user_message", "message": asked.content if asked else "",
                                      "memory": {"status": "no_match", "refs": []}},
                               output={"reply": her.content, "silent": None, "tools": []}))
    db.add(DecisionExecution(id=execution_id_for(turn_id, "reply"), user_id=user.id, attempt_id=mind_id,
                             turn_id=turn_id, kind="reply", executor="mouth:web", conversation_id=conversation.id,
                             assistant_message_id=her.id, intended={"communication": "speak", "origin": "mind"},
                             executed={"outcome": "completed"}, result=result, deviation=deviation, ended_at=started,
                             expires_at=NOW + timedelta(days=30)))
    db.commit()
    return mind_id


def exchange(db, user, conversation, question, reply, **kw):
    """One user message and her captured reply. Returns (user message, her message, turn id, her mind's attempt)."""
    turn_id = uuid.uuid4()
    asked = say(db, conversation, "user", question, turn_id=turn_id)
    her = say(db, conversation, "assistant", reply, turn_id=turn_id)
    ids = capture_turn(db, user, conversation, turn_id, her, asked, **kw)
    return asked, her, turn_id, ids


def correction(db, user, conversation, text):
    msg = say(db, conversation, "user", text, turn_id=uuid.uuid4())
    row, _ = LA.open_assessment(db, user_id=user.id, kind="correction", key=f"message:{msg.id}",
                                anchor_at=msg.created_at, conversation_id=conversation.id, anchor_message_id=msg.id,
                                trigger={"why": "the user corrected her"})
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


def test_bad_wording_is_her_minds_and_cites_its_own_record(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, turn_id, mind = exchange(db, user, conversation, "I had an awful day at work",
                                             "Have you considered quitting?")
        msg, aid = correction(db, user, conversation, "you understood me fine, it just came out really cold")
    t = f"t{her.conversation_sequence}"
    script = Script(answer(targets=[target(t, cites=[f"m{msg.conversation_sequence}", f"m{her.conversation_sequence}"])],
                           causes=[cause(t, "mind", cites=[f"m{her.conversation_sequence}", f"{t}.m"]),
                                   cause(t, "delivery", "contradicted", cites=[f"m{msg.conversation_sequence}"])],
                           dimensions=[{"dimension": "understanding", "target": t, "value": "good",
                                        "status": "supported", "cites": [f"{t}.m", f"m{msg.conversation_sequence}"],
                                        "note": "they said so"}]))
    assert run(env, script) == ["revised"]
    row, rev = latest(env, aid)
    assert row.state == "done" and row.outcome == "concluded" and row.revision == 1
    causes = {c["component"]: c["status"] for c in rev.result["causes"]}
    assert causes == {"mind": "supported", "delivery": "contradicted"}
    # What it read: the conversation, her turn with its record, the delivery fact with what it establishes.
    text = script.texts[0]
    assert "Have you considered quitting?" in text and f"{t}.m her mind's evaluation (ok)" in text
    assert "woken by user_message" in text
    assert "Establishes: what happened to the reply" in text and "Does not establish" in text
    with env.db() as db:
        links = {(li.role, li.ref, li.component) for li in db.scalars(select(LearningAssessmentLink).where(
            LearningAssessmentLink.assessment_id == aid))}
    assert ("target", f"attempt:{mind}", "") in links and ("target", f"turn:{turn_id}", "") in links
    assert ("cause", f"attempt:{mind}", "mind") in links


def test_a_delivery_failure_does_not_become_a_choice_or_interpretation_error(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, turn_id, mind = exchange(db, user, conversation, "what's the capital of Peru?", "Lima",
                                                 result="failed")
        LA.sweep(db, soon(seconds=1))
        row = db.scalar(select(LearningAssessment).where(LearningAssessment.trigger_key == f"reply:{turn_id}"))
        assert row.trigger_kind == "execution"
        aid = row.id
    t = f"t{her.conversation_sequence}"

    def evaluate(text):
        fact = next(line.split()[0] for line in text.splitlines() if "delivery (mouth:web): failed" in line)
        return answer(targets=[target(t, cites=[fact])],
                      causes=[cause(t, "delivery", cites=[fact]), cause(t, "mind", cites=[fact])])

    run(env, Script(evaluate))
    _, rev = latest(env, aid)
    causes = {c["component"]: c["status"] for c in rev.result["causes"]}
    assert causes == {"delivery": "supported", "mind": "plausible"}
    # The sweep found it once: a second sweep adds nothing.
    with env.db() as db:
        LA.sweep(db, soon(seconds=2))
        assert len(list(db.scalars(select(LearningAssessment).where(LearningAssessment.user_id == user.id)))) == 1


def test_unwanted_advice_is_her_choice_and_a_revision_supersedes_it(env):
    user, conversation = setup(env)
    with env.db() as db:
        asked, her, turn_id, mind = exchange(db, user, conversation, "my boss yelled at me again",
                                             "Here are five ways to handle a difficult boss.")
        msg, aid = correction(db, user, conversation, "you got it, but I wanted you to just listen, not fix it")
    t, m = f"t{her.conversation_sequence}", f"m{msg.conversation_sequence}"
    script = Script(answer(
        targets=[target(t, cites=[m, f"m{her.conversation_sequence}"])],
        causes=[cause(t, "mind", cites=[f"{t}.m", m])],
        user_stated=[{"kind": "preference", "quote": "I wanted you to just listen, not fix it", "cites": [m]},
                     {"kind": "preference", "quote": "I hate advice", "cites": [m]}]))
    run(env, script)
    row, rev = latest(env, aid)
    assert {c["component"]: c["status"] for c in rev.result["causes"]} == {"mind": "supported"}
    assert [s["quote"] for s in rev.result["user_stated"]] == ["I wanted you to just listen, not fix it"]
    assert any("I hate advice" in d["what"] for d in rev.dropped)

    # New evidence revises it: the user's note says the advice was fine. The old revision is kept, superseded.
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
    assert first.superseded_by == 2 and first.superseded_at is not None and first.result["causes"]  # history kept


def test_delayed_feedback_about_an_earlier_turn_and_about_a_sequence(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, first, first_turn, first_mind = exchange(db, user, conversation, "recommend a book",
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
        found = LA.for_ref(db, user.id, f"attempt:{first_mind}")
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
        choice_vs_outcome=[{"target": ta, "choice": "poor", "outcome": "good", "cites": [f"{ta}.m", m],
                            "note": "it worked out by luck"}],
        next={"step": "need_missing", "look": [], "why": "which remark they mean"},
        missing=[{"what": "which remark they meant", "needed_for": "the target"}],
        follow_up={"propose": True, "kind": "question", "text": "Which thing I said felt weird?",
                   "resolves": "which remark they meant"})))
    row, rev = latest(env, aid)
    assert row.outcome == "unresolved" and row.target_status == "plausible" and row.stop_reason == "missing_evidence"
    assert rev.result["causes"][0]["status"] == "unresolved"  # "unclear" is never a supported cause
    assert rev.result["choice_vs_outcome"][0] | {"choice": "poor", "outcome": "good"} == rev.result["choice_vs_outcome"][0]
    # The question is a proposal kept by memory as an open item of hers: her mind decides whether to ask it.
    from app.db.models import WorkingItem
    with env.db() as db:
        items = list(db.scalars(select(WorkingItem).where(WorkingItem.user_id == user.id, WorkingItem.kind == "question")))
    assert len(items) == 1 and items[0].owner == "columbina" and items[0].epistemic == "inferred"
    assert items[0].text == "Which thing I said felt weird?" and f"assessment {aid}" in items[0].reason
    assert rev.result["follow_up"]["item"] == f"item:{items[0].id}"


def test_incomplete_capture_withholds_attribution_until_the_capture_arrives(env):
    user, conversation = setup(env)
    with env.db() as db:
        _, her, turn_id, mind = exchange(db, user, conversation, "remind me what we said",
                                         "We said Tuesday.", mind=False)
        msg, aid = correction(db, user, conversation, "no, I meant the venue, not the day")
    t, m = f"t{her.conversation_sequence}", f"m{msg.conversation_sequence}"
    blame = answer(targets=[target(t, cites=[m, f"m{her.conversation_sequence}"])],
                   causes=[cause(t, "mind", cites=[f"{t}.m"])],
                   user_stated=[{"kind": "intended_meaning", "quote": "I meant the venue, not the day", "cites": [m]}])
    script = Script(blame)
    run(env, script)
    assert "her mind's evaluation: not captured (never saved)" in script.texts[0]
    row, rev = latest(env, aid)
    mind_cause = next(c for c in rev.result["causes"] if c["component"] == "mind")
    assert mind_cause["status"] == "unresolved" and mind_cause["basis"] == "missing_capture"
    assert rev.result["user_stated"][0]["status"] == "supported" and row.outcome == "concluded"
    assert row.awaiting == [f"attempt:{mind}"]

    # The capture arrives late: the sweep attaches it and the assessment is revised with it.
    with env.db() as db:
        db.add(DecisionAttempt(id=mind, user_id=user.id, learner="mind", trigger="user_message",
                               turn_id=turn_id, conversation_id=conversation.id, input={"reason": "user_message"},
                               output={"reply": "We said Tuesday.", "tools": []}, status="ok", started_at=NOW,
                               expires_at=NOW + timedelta(days=1)))
        db.commit()
        assert LA.sweep(db, soon(seconds=3))["arrived"] == 1
    run(env, script)
    row, rev = latest(env, aid)
    assert row.revision == 2 and rev.reason == f"new evidence: attempt:{mind}" and row.awaiting == []
    assert next(c for c in rev.result["causes"] if c["component"] == "mind")["status"] == "supported"


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


def test_every_completed_exchange_is_a_review_opportunity_and_labels_are_only_signals(env):
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "hello", "Hi!")
        corrected = say(db, conversation, "user", "no, that's not what I asked")
        complaint = say(db, conversation, "user", "you keep interrupting me")
        ordinary = say(db, conversation, "user", "anyway, how are you")
        unread = say(db, conversation, "user", "fine")
        for m in (corrected, complaint, ordinary, unread):
            _observe(db, user, m)
        assert LA.triage(db, soon(seconds=1)) == 4
        rows = list(db.scalars(select(LearningAssessment)))
        receipts = list(db.scalars(select(ObservationProcessing).where(ObservationProcessing.consumer == "learning")))
        # No code verdict and no sampling: one open stretch takes every exchange.
        assert [(r.trigger_kind, r.trigger["from_seq"], r.trigger["up_to_seq"]) for r in rows] == [
            ("review", corrected.conversation_sequence, unread.conversation_sequence)]
        assert sorted(r.status for r in receipts) == ["applied"] * 4
        assert LA.triage(db, soon(seconds=2)) == 0  # settled receipts are never triaged again
    script = Script(answer(review={"decision": "leave", "why": "small talk and a passing gripe"}, feedback="no",
                           targets=[target(f"t{corrected.conversation_sequence - 1}", cites=[f"m{corrected.conversation_sequence}"])]))
    run(env, script)
    text = script.texts[0]
    # Nothing labels the messages for the reviewer: no interpretation stage reads them first.
    assert "as correcting her" not in text and "as complaining" not in text
    assert "review opportunity over a stretch of the conversation" in text and '"fine"' in text
    row, rev = latest(env, rows[0].id)
    # Left alone: a record that it was looked at, with nothing concluded and nothing queued for lessons.
    assert (row.outcome, row.stop_reason, row.inquiry) == ("left", "left", {})
    assert rev.result["targets"] == [] and any("chose to leave it" in d["why"] for d in rev.dropped)
    with env.db() as db:
        from app.db.models import LearningLessonJob
        assert db.scalar(select(LearningLessonJob).where(LearningLessonJob.assessment_id == row.id)) is None


def test_a_left_stretch_takes_the_next_exchange_and_a_concluded_one_starts_the_next(env):
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "hi", "Hello!")
        first = say(db, conversation, "user", "lol you're funny")
        _observe(db, user, first)
        LA.triage(db, soon(seconds=1))
        aid = db.scalar(select(LearningAssessment.id))
    run(env, Script(answer(review={"decision": "leave", "why": "nothing yet"})))
    with env.db() as db:
        exchange(db, user, conversation, "ok but seriously, the deadline", "Let's look at it.")
        second = say(db, conversation, "user", "thanks, that helps")
        _observe(db, user, second)
        LA.triage(db, soon(seconds=2))
        rows = list(db.scalars(select(LearningAssessment)))
    assert [r.id for r in rows] == [aid] and rows[0].state == "pending"
    assert rows[0].trigger["up_to_seq"] == second.conversation_sequence
    script = Script(answer(findings=[{"statement": "they joke first and get serious with 'seriously'",
                                      "form": "how they mean things", "status": "plausible",
                                      "cites": [f"m{first.conversation_sequence}"], "applies_when": "chat",
                                      "alternatives": ["it was one joke"], "relates_to": []}],
                           review={"decision": "examine", "why": "a possible pattern"}))
    run(env, script)
    assert "lol you're funny" in script.texts[0] and "thanks, that helps" in script.texts[0]
    with env.db() as db:
        row = db.get(LearningAssessment, aid)
        row.outcome = "concluded"  # as if this reading had concluded
        db.commit()
        third = say(db, conversation, "user", "anyway")
        _observe(db, user, third)
        LA.triage(db, soon(seconds=3))
        rows = list(db.scalars(select(LearningAssessment).order_by(LearningAssessment.created_at)))
    assert len(rows) == 2 and rows[1].trigger["from_seq"] == second.conversation_sequence + 1


def test_a_reply_the_user_never_answered_gets_its_opportunity_after_a_pause(env):
    user, conversation = setup(env)
    env.configure_learning(learning_review_pause_seconds=60)
    with env.db() as db:
        _, her, _, _ = exchange(db, user, conversation, "tell me about tides", "The moon pulls the oceans.")
        assert LA.sweep(db, soon(seconds=1))["pauses"] == 0  # not quiet long enough yet
        assert LA.sweep(db, soon(minutes=5))["pauses"] == 1
        assert LA.sweep(db, soon(minutes=6))["pauses"] == 0  # covered now
        row = db.scalar(select(LearningAssessment))
    assert row.trigger_kind == "review" and row.anchor_message_id == her.id
    assert row.trigger["up_to_seq"] == her.conversation_sequence


def test_a_memory_correction_is_a_signal_on_the_stretch(env):
    user, conversation = setup(env)
    with env.db() as db:
        exchange(db, user, conversation, "my sister lives in Porto", "Porto, lovely.")
        msg = say(db, conversation, "user", "sorry, I said Porto but she lives in Lisbon")
        _observe(db, user, msg)
        LA.triage(db, soon(seconds=1))
        row = db.scalar(select(LearningAssessment))
        db.add(MemoryCorrection(user_id=user.id, source_message_id=msg.id, subject="sister's city",
                                previous_value="Porto", new_value="Lisbon", kind="mistaken",
                                user_words="she lives in Lisbon", said_at=msg.created_at, fingerprint="f" * 32))
        db.commit()
        assert LA.sweep(db, soon(seconds=1))["corrections"] == 1
        assert LA.sweep(db, soon(seconds=2))["corrections"] == 0
        rows = list(db.scalars(select(LearningAssessment)))
    assert [r.id for r in rows] == [row.id] and rows[0].trigger_kind == "review"
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
    assert any("planner decision is missing" in m["what"] for m in rev.result["missing"])


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
    good = answer(targets=[target("t2", cites=["m3", "m2"])], causes=[cause("t2", "mind", cites=["m2", "t2.m"])])
    # Delivery cited with its own record ("c1", the delivery fact): code keeps it supported, the eval calls it blame.
    blame = answer(targets=[target("t2", cites=["m3", "m2"])], causes=[cause("t2", "delivery", cites=["c1"])])
    shrug = answer(targets=[target("t2", "unresolved")], next={"step": "need_missing", "look": [], "why": "?"})

    def verdict(raw):
        return E.score(bad, {"result": A.check(raw, bad.ev).result})

    assert verdict(good)["passed"]
    assert any("unsupported blame: delivery" in p for p in verdict(blame)["problems"])
    assert not verdict(shrug)["passed"]  # always answering "unresolved" fails
    for case in cases.values():
        assert A.render(case.ev).startswith("# What started this")


def test_the_sweep_resolves_attempts_and_executions_and_keeps_notes_and_legacy_shapes(env):
    """Awaited evidence is a typed reference resolved against its own store; an explanation or an unknown shape is
    kept as a note, never parsed as an id and never marked as received (the IndexError of 2026-11-13)."""
    from app.db.models import DecisionExecution, LearningAssessment, LearningAssessmentEvidence

    user, conversation = setup(env)
    arrived_attempt, missing_attempt, arrived_execution = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    legacy = ["trajectory missing or expired for this stretch", "attempt:not-a-uuid", "weird:entry", "x:y:z",
              {"odd": "shape"}]
    with env.db() as db:
        row = LearningAssessment(
            id=uuid.uuid4(), user_id=user.id, trigger_kind="correction", trigger_key=f"test:{uuid.uuid4()}",
            conversation_id=conversation.id, anchor_at=NOW, state="done", updated_at=NOW,
            awaiting=[f"attempt:{arrived_attempt}", f"attempt:{missing_attempt}",
                      f"execution:{arrived_execution}", *legacy],
            awaiting_notes=["capture window truncated at 100 attempts / 150 executions"])
        db.add(row)
        db.add(DecisionAttempt(id=arrived_attempt, user_id=user.id, learner="mind", trigger="user_message",
                               conversation_id=conversation.id, input={}, output={}, status="ok", started_at=NOW,
                               expires_at=NOW + timedelta(days=1)))
        db.add(DecisionExecution(id=arrived_execution, user_id=user.id, kind="reply", executor="mouth:web",
                                 result="succeeded", expires_at=NOW + timedelta(days=1)))
        db.commit()
        done = LA.sweep(db, soon(seconds=3))
        assert done["arrived"] == 1
        db.refresh(row)
        refs = {e.ref for e in db.scalars(select(LearningAssessmentEvidence).where(
            LearningAssessmentEvidence.assessment_id == row.id))}
    assert refs == {f"attempt:{arrived_attempt}", f"execution:{arrived_execution}"}
    assert row.awaiting == [f"attempt:{missing_attempt}"]  # only what arrived left
    assert row.awaiting_notes == ["capture window truncated at 100 attempts / 150 executions",
                                  *legacy[:4], json.dumps({"odd": "shape"})]
    assert row.state == "pending"  # reopened with the new evidence
    # Running again changes nothing: the missing attempt is still awaited, the notes aren't duplicated.
    with env.db() as db:
        assert LA.sweep(db, soon(seconds=4))["arrived"] == 0
        row = db.get(LearningAssessment, row.id)
    assert row.awaiting == [f"attempt:{missing_attempt}"] and len(row.awaiting_notes) == 6


def test_awaited_refs_are_typed_and_validated():
    some = uuid.uuid4()
    assert LA.awaited_ref(f"attempt:{some}") == ("attempt", some)
    assert LA.awaited_ref(f"execution:{some}") == ("execution", some)
    for entry in ("trajectory missing or expired for this stretch", "attempt:", "attempt:nope", f"lesson:{some}",
                  None, 3, {"attempt": str(some)}):
        assert LA.awaited_ref(entry) is None
