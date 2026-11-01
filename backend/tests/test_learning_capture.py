"""Learning step 1, capture and ownership (docs/learning-system/01-implementation.md, "Completion evidence").

Real PostgreSQL (TEST_DATABASE_URL); no model calls: Student 1's reading and Student 2's answer are scripted, the
rest of the pipeline runs. One conversation chain and one game attempt end to end, then failures: delivery,
execution and unknown results, a storage outage while replies continue, restart without duplicates, retention and
source deletion, training examples that outlive their sources, corrections, explicit deletion, and user boundaries.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.ai.state.contracts import CacheEntry, MemoryContextView, Projection
from app.db.models import (
    CaptureGap, Conversation, ChatMessage, DecisionAttempt, DecisionAttemptSource, DecisionExecution, SemanticMemory,
    TrainingExample, TrainingExampleDeletion,
)
from app.services import (
    capture_chains, chat as chat_service, cognitive_live, learning_capture, shared_state, student_two,
    training_examples,
)
from db_support import create_conversation, create_user, db_env, database  # noqa: F401

DIALS = {"length": "a_little", "warmth": "somewhat", "playfulness": "none", "directness": "somewhat"}
SAY_HI = dict(intention="say hello back", work="no_change", communication="respond", what_to_communicate="hi there",
              move="react_briefly", dials=DIALS, about=["M"])
NOWHERE = "postgresql+psycopg://nobody@127.0.0.1:1/no_database"


@pytest.fixture
def capture(db_env, tmp_path):
    """A real writer with quick retries, installed for the test; the gap file in tmp."""
    svc = learning_capture.CaptureService(base_delay=0.01, max_delay=0.05, max_tries=3,
                                          gap_path=str(tmp_path / "gaps.jsonl"))
    before = learning_capture.install(svc)
    yield svc
    svc.flush(5)
    learning_capture.install(before)


from app.ai.Cognitive.simple.contracts import ACTS  # noqa: E402

READING = {"understand": {"acts": {a: a == "small_talk" for a in ACTS}, "tone": "friendly", "request": "no_request",
                          "about_her": False, "sure": True, "appraisal": None, "feelings": []},
           "acts": {"small_talk": 0.91}, "tone": {"friendly": 0.8}, "request": {"no_request": 0.9},
           "experimental": {"about_her": False, "sure": True, "appraisal": None, "feelings": []}}


@pytest.fixture
def pipeline(capture, monkeypatch, tmp_path):
    """The reply pipeline with scripted learners: Student 1 reads READING, Student 2 (the local provider) answers
    ``state["answer"]``; memory's check returns one fact. Everything else is the real code on the test DB."""
    state = {"answer": json.dumps(SAY_HI), "reading_fails": False, "fact": None, "texts": []}

    def read(bundle, model_dir):
        if state["reading_fails"]:
            raise RuntimeError("student 1 weights missing")
        return READING

    monkeypatch.setattr(cognitive_live, "read", read)
    monkeypatch.setattr(student_two, "manifest", lambda: {"name": "qwen-test", "system": "SYSTEM TEXT",
                                                         "rendering": None})

    def ask(model, text):
        state["texts"].append(text)
        return state["answer"]

    monkeypatch.setattr(student_two, "_ask", ask)
    monkeypatch.setattr(shared_state, "_student_version", lambda: "s1:test")

    def memory(user_id, conversation_id, trigger_id, message, zone):
        entries = ()
        if state["fact"] is not None:
            entries = (CacheEntry(f"fact:{state['fact']}", "f" * 40, "current", content="They like tea."),)
        return MemoryContextView(Projection("memory", "available"), entries, (), "matched", "", "- They like tea."), None

    monkeypatch.setattr(shared_state, "_memory", memory)
    settings = shared_state.get_settings().model_copy(update={"shared_state_log_path": str(tmp_path / "ss.jsonl"),
                                                              "cognition_student_log_path": str(tmp_path / "s2.jsonl")})
    monkeypatch.setattr(shared_state, "get_settings", lambda: settings)
    monkeypatch.setattr(student_two, "get_settings", lambda: settings)
    return state


def _say(db, conversation, role, content, **kw) -> ChatMessage:
    return chat_service.add_message(db, conversation=conversation, role=role, content=content, **kw)


def _turn(db_env, user, conversation, text, *, turn_id=None, job=None, reply="hi there", outcome="completed"):
    """One user turn through the real preparation, then the reply stored and finished as the worker does."""
    with db_env.db() as db:
        conversation = db.get(Conversation, conversation.id)
        message = _say(db, conversation, "user", text)
        history = [{"role": m.role, "content": m.content} for m in db.scalars(select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.conversation_sequence))]
    turn_id = turn_id or uuid.uuid4()
    turn = shared_state.prepare(user_id=user.id, conversation_id=conversation.id, user_message_id=message.id,
                                history=history, message=text, zone="UTC", turn_id=turn_id,
                                reply_message_id=uuid.uuid5(turn_id, "assistant"), job=job)
    stored = None
    if reply and outcome in ("completed", "partial", "failed"):
        with db_env.db() as db:
            stored = _say(db, db.get(Conversation, conversation.id), "assistant", reply, message_id=uuid.uuid5(turn_id, "assistant"),
                          turn_id=turn_id, status=outcome).id
    shared_state.finish(turn, outcome=outcome, reply=reply, assistant_message_id=stored)
    return turn, message, turn_id


# --------------------------------------------------------------------------- #
# One conversation chain
# --------------------------------------------------------------------------- #


def test_a_conversation_turn_is_captured_end_to_end_without_later_information(db_env, capture, pipeline):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        _say(db, conversation, "user", "I spent the day in Lisbon")
        _say(db, conversation, "assistant", "That sounds lovely.")
        fact = SemanticMemory(user_id=user.id, kind="preference", normalized_key="drink", content="They like tea.")
        db.add(fact)
        db.commit()
        pipeline["fact"] = fact.id
    turn, message, turn_id = _turn(db_env, user, conversation, "hello again")
    assert capture.flush(5)
    # Something said after the decision never reaches what was captured for it.
    with db_env.db() as db:
        _say(db, db.get(Conversation, conversation.id), "user", "LATER: this came after")
        chain = capture_chains.turn(db, user.id, turn_id, blobs=True)
    assert chain["complete"], chain["missing"]
    s1, s2 = (next(a for a in chain["attempts"] if a["learner"] == name) for name in ("student_1", "student_2"))
    # Student 1: the exact bundle it read and its soft outputs; its id was given before it ran.
    assert s1["prior_attempt_id"] is None and s1["turn_id"] == str(turn_id)
    assert s1["input"]["bundle"]["message"] == "hello again"
    assert [m["text"] for m in s1["input"]["bundle"]["window"]] == ["I spent the day in Lisbon", "That sounds lovely."]
    assert s1["output"]["understand"]["tone"] == "friendly" and s1["versions"]["student"] == "s1:test"
    assert {r["ref"] for r in s1["input_refs"]} == {f"fact:{pipeline['fact']}"}
    # Student 2: names Student 1 upstream; exact prompt, the system text resolved from its blob, generation settings.
    assert s2["upstream"]["student_1"] == s1["id"] and s2["id"] == turn.student.decision_id
    assert s2["input"]["system"] == "SYSTEM TEXT" and s2["input"]["prompt"] == pipeline["texts"][0]
    assert "Lisbon" in s2["input"]["prompt"] and "LATER" not in json.dumps(s2["input"])
    assert s2["settings"]["temperature"] == 0.0 and s2["output"]["decision"]["communication"] == "respond"
    assert set(s2["absent"]) >= {"prediction", "alternatives", "selection_probabilities"}
    assert chain["leaks"] == {}
    # Intended (Student 2's choice) next to what executed: the effect's owner and the reply's delivery.
    effect = next(e for e in chain["executions"] if e["kind"] == "effect")
    reply = next(e for e in chain["executions"] if e["kind"] == "reply")
    assert effect["intended"]["work"] == "no_change" and effect["result"] == "succeeded"
    assert reply["intended"]["communication"] == "respond" and reply["result"] == "succeeded"
    assert reply["attempt_id"] == s2["id"] and reply["executed"]["reply_chars"] == len("hi there")
    with db_env.db() as db:
        sources = set(db.scalars(select(DecisionAttemptSource.ref).where(
            DecisionAttemptSource.attempt_id == uuid.UUID(s2["id"]))))
    assert f"message:{message.id}" in sources and f"fact:{pipeline['fact']}" in sources


def test_failures_are_known_and_unknown_results_stay_unknown(db_env, capture, pipeline):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    # Student 1 fails, Student 2 answers something invalid, and the reply fails: each is a record of that.
    pipeline["reading_fails"] = True
    pipeline["answer"] = "not json at all"
    _, _, failed_turn = _turn(db_env, user, conversation, "are you there?", reply="I couldn't", outcome="failed")
    # A cut-off reply is a deviation; a WhatsApp reply has no result until the channel settles it.
    pipeline["reading_fails"], pipeline["answer"] = False, json.dumps(SAY_HI)
    _, _, partial_turn = _turn(db_env, user, conversation, "tell me a story", reply="Once upon", outcome="partial")
    assert capture.flush(5)
    with db_env.db() as db:
        failed = capture_chains.turn(db, user.id, failed_turn)
        partial = capture_chains.turn(db, user.id, partial_turn)
        hearing = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == partial_turn))
        chat_service.record_heard(db, user_id=user.id, turn_id=partial_turn, heard_chars=4)
    s1 = next(a for a in failed["attempts"] if a["learner"] == "student_1")
    s2 = next(a for a in failed["attempts"] if a["learner"] == "student_2")
    assert s1["status"] == "error" and "weights missing" in s1["error"] and s1["output"] is None
    assert s2["status"] == "invalid" and s2["output"]["raw"] == "not json at all"
    assert s2["upstream"]["student_1_status"] == "failed"
    assert not any(e["kind"] == "effect" for e in failed["executions"])  # an invalid decision has no effect
    reply = next(e for e in failed["executions"] if e["kind"] == "reply")
    assert reply["result"] == "failed" and reply["intended"]["origin"] == "operational_fallback"
    assert next(e for e in partial["executions"] if e["kind"] == "reply")["result"] == "deviated"
    learning_capture.reply(user_id=user.id, attempt_id=None, turn_id=uuid.uuid4(), conversation_id=conversation.id,
                           outcome="completed", assistant_message_id=hearing.id, reply_chars=9, intended=None,
                           channel="whatsapp", voice=False)
    assert capture.flush(5)
    with db_env.db() as db:
        rows = list(db.scalars(select(DecisionExecution).where(DecisionExecution.assistant_message_id == hearing.id)))
    by_kind = {r.kind: r for r in rows}
    assert by_kind["heard"].result == "deviated" and by_kind["heard"].executed["heard_chars"] == 4
    assert any(r.kind == "reply" and r.result == "unknown" and r.executor == "mouth:whatsapp" for r in rows)


# --------------------------------------------------------------------------- #
# One game attempt
# --------------------------------------------------------------------------- #


def _pack_session(db, user):
    from app.db.models import DesktopEnvironment, DesktopSession

    env = DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                             allowed_profile_ids=["minecraft_pack"], status="online")
    db.add(env)
    db.flush()
    session = DesktopSession(owner_user_id=user.id, environment_id=env.id, profile_id="minecraft_pack",
                             profile_revision=1, state="running", control_owner="gameplay", max_duration_seconds=3600)
    db.add(session)
    db.commit()
    return session


def test_a_game_decision_is_linked_to_what_the_skill_actually_did(db_env, capture):
    from test_skill_driver import Harness, ScriptedModel, _decide, _view

    from app.workers import desktop

    with db_env.db() as db:
        user = create_user(db)
        session = _pack_session(db, user)
    model = ScriptedModel(_decide("mine", {"block": "oak_log", "count": 3}),
                          _decide("craft", {"item": "planks"}), _decide("mine", {"block": "stone"}))
    harness = Harness(model, view=_view(session_id=session.id))
    harness.run.session_id = session.id
    harness.run.deps.capture = desktop._hands_capture(session.id)
    harness.steps(3)  # decide mine -> it runs -> it finishes
    harness.pack.fail_reasons = ["no recipe"]
    harness.steps(3)  # decide craft -> runs -> fails
    harness.steps(1)  # decides mine stone; the run is still going when play stops: no result seen
    assert capture.flush(5)
    with db_env.db() as db:
        attempts = list(db.scalars(select(DecisionAttempt).where(DecisionAttempt.game_session_id == session.id)
                                   .order_by(DecisionAttempt.started_at)))
        chains = [capture_chains.attempt(db, user.id, a.id) for a in attempts]
    assert [a.learner for a in attempts] == ["hands", "hands", "hands"]
    first = chains[0]
    assert first["attempts"][0]["input"]["text"] == model.prompts[0]
    assert "mine(" in first["attempts"][0]["input"]["system"]  # the system prompt, resolved from its blob
    run = first["executions"][0]
    assert run["intended"]["skill"] == "mine" and run["intended"]["params"]["block"] == "oak_log"
    assert run["result"] == "succeeded" and run["executed"]["status"] == "done" and first["complete"]
    assert chains[1]["executions"][0]["result"] == "failed" and "no recipe" in chains[1]["executions"][0]["deviation"]
    assert chains[2]["executions"][0]["result"] == "unknown"  # running when we looked: unknown, never a guess
    with db_env.db() as db:
        from app.db.models import GameExperience
        payloads = [e.payload for e in db.scalars(select(GameExperience).where(GameExperience.session_id == session.id))]
    assert not payloads or all("attempt_id" in p for p in payloads if p.get("skill") in ("mine", "craft"))


# --------------------------------------------------------------------------- #
# Storage outage, retries, gaps, restart
# --------------------------------------------------------------------------- #


def test_an_outage_never_stops_replies_retries_are_bounded_and_gaps_are_visible(db_env, pipeline, tmp_path):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    down = sessionmaker(create_engine(NOWHERE, connect_args={"connect_timeout": 1}))
    svc = learning_capture.CaptureService(session_factory=down, base_delay=0.01, max_delay=0.02, max_tries=2,
                                          gap_path=str(tmp_path / "gaps.jsonl"))
    before = learning_capture.install(svc)
    try:
        turn, _, turn_id = _turn(db_env, user, conversation, "still there?")
        assert turn.student.ok and turn.immediate_action == "respond"  # the reply went on without storage
        assert svc.flush(10)
    finally:
        learning_capture.install(before)
    stats = svc.stats()
    assert stats["written"] == 0 and stats["retried"] >= 3 and stats["gaps"] >= 4
    # The database was the thing down: gaps fell back to the file, content-free.
    lines = [json.loads(line) for line in (tmp_path / "gaps.jsonl").read_text().splitlines()]
    assert {line["reason"] for line in lines} == {"retries_exhausted"}
    assert {line["label"] for line in lines} >= {"student_1", "student_2", "reply"}
    assert all("hi there" not in json.dumps(line) for line in lines)
    with db_env.db() as db:
        assert capture_chains.turn(db, user.id, turn_id) is None  # nothing saved; never shown as a chain


def test_a_full_queue_and_a_deleted_source_are_gaps_in_the_database(db_env, capture, tmp_path):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    small = learning_capture.CaptureService(queue_max=1, base_delay=0.01, gap_path=str(tmp_path / "g.jsonl"))
    small._start_locked = lambda: None  # hold the writer: the queue fills
    ctx = learning_capture.Ctx(user_id=user.id, attempt_id=uuid.uuid4(), trigger="user_message",
                               conversation_id=conversation.id)
    learning_capture.install(small)
    learning_capture.attempt(ctx, learner="student_2", provider="teacher", model="m", input={"prompt": "a"},
                             output=None, status="unavailable", started_at=datetime.now(timezone.utc), produced_at=None)
    second = uuid.uuid4()
    learning_capture.attempt(learning_capture.Ctx(user_id=user.id, attempt_id=second, trigger="user_message"),
                             learner="student_2", provider="teacher", model="m", input={}, output=None,
                             status="error", started_at=datetime.now(timezone.utc), produced_at=None)
    assert small.counts["gaps"] == 1
    # The conversation is deleted before the writer runs: the attempt can't be saved, and that's a gap too.
    with db_env.db() as db:
        chat_service.delete_conversation(db, user_id=user.id, conversation_id=conversation.id)
    del small._start_locked
    with small._cond:
        small._start_locked()
    assert small.flush(5)
    learning_capture.install(capture)
    with db_env.db() as db:
        gaps = {g.record_id: g.reason for g in db.scalars(select(CaptureGap).where(CaptureGap.user_id == user.id))}
        assert db.get(DecisionAttempt, ctx.attempt_id) is None
    assert gaps == {second: "queue_full", ctx.attempt_id: "source_gone"}


def test_restart_reuses_identity_and_a_reclaimed_job_is_a_linked_new_attempt(db_env, capture, pipeline):
    from types import SimpleNamespace

    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    turn_id = uuid.uuid4()
    job = SimpleNamespace(id=uuid.uuid4(), attempts=1, status="running", lease_expires_at=None)
    _turn(db_env, user, conversation, "first try", turn_id=turn_id, job=job, reply="", outcome="cancelled")
    # The same job attempt captured again (a crash after capture, before the job finished): no duplicates.
    s1 = learning_capture.attempt_id_for(turn_id, "student_1", 1)
    learning_capture.attempt(learning_capture.Ctx(user_id=user.id, attempt_id=s1, trigger="user_message"),
                             learner="student_1", provider="local", model="x", input={"bundle": "replayed"},
                             output=None, status="error", started_at=datetime.now(timezone.utc), produced_at=None)
    # The job is reclaimed: attempt 2 of each learner, linked to attempt 1.
    job.attempts = 2
    with db_env.db() as db:
        message = db.scalar(select(ChatMessage).where(ChatMessage.content == "first try"))
        history = [{"role": "user", "content": "first try"}]
    turn = shared_state.prepare(user_id=user.id, conversation_id=conversation.id, user_message_id=message.id,
                                history=history, message="first try", zone="UTC", turn_id=turn_id,
                                reply_message_id=uuid.uuid5(turn_id, "assistant"), job=job)
    shared_state.finish(turn, outcome="cancelled", reply="", assistant_message_id=None)
    assert capture.flush(5)
    with db_env.db() as db:
        rows = list(db.scalars(select(DecisionAttempt).where(DecisionAttempt.turn_id == turn_id)))
        first = db.get(DecisionAttempt, s1)
        replies = db.scalar(select(func.count()).select_from(DecisionExecution).where(
            DecisionExecution.turn_id == turn_id, DecisionExecution.kind == "reply"))
    assert len(rows) == 4 and first.input["bundle"] != "replayed"  # the first save won; the replay inserted nothing
    retried = {r.learner: r for r in rows if r.prior_attempt_id is not None}
    assert retried["student_1"].prior_attempt_id == s1
    assert retried["student_2"].upstream["student_1"] == str(retried["student_1"].id)
    assert replies == 1  # one delivery record for the turn, whichever attempt finished it


# --------------------------------------------------------------------------- #
# Retention, source deletion, training examples, corrections, deletion, access
# --------------------------------------------------------------------------- #


def _teacher_attempt(db_env, user, conversation, message, *, fact_id=None) -> uuid.UUID:
    attempt_id = uuid.uuid4()
    refs = (f"message:{message.id}",) + ((f"fact:{fact_id}",) if fact_id else ())
    learning_capture.attempt(
        learning_capture.Ctx(user_id=user.id, attempt_id=attempt_id, trigger="user_message",
                             conversation_id=conversation.id, source_message_id=message.id, sources=refs),
        learner="student_2", provider="teacher", model="gpt-6-luna",
        input={"prompt": f"NEW MESSAGE: {message.content}", "snapshot": {"domain": "chat"}, "rendering": "memory-v1",
               "system": None}, blobs={"system": "TEACHER SYSTEM"},
        output={"raw": json.dumps(SAY_HI), "decision": SAY_HI, "problems": []}, status="ok",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=1), produced_at=datetime.now(timezone.utc),
        versions={"snapshot_version": 5})
    return attempt_id


def test_examples_are_derived_and_outlive_their_source_and_raw_capture(db_env, capture, tmp_path, monkeypatch):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message = _say(db, conversation, "user", "my cat is called Miso")
    attempt_id = _teacher_attempt(db_env, user, conversation, message)
    assert capture.flush(5)
    with db_env.db() as db:
        assert training_examples.derive_pending(db) == 1
        assert training_examples.derive_pending(db) == 0  # once per attempt
        example = db.scalar(select(TrainingExample).where(TrainingExample.attempt_id == attempt_id))
        assert example.input["prompt"].endswith("Miso") and example.target["decision"]["communication"] == "respond"
        assert example.review_status == "unreviewed" and example.eligible
        # The source conversation is deleted: the raw capture goes with it, the example stays (self-contained).
        chat_service.delete_conversation(db, user_id=user.id, conversation_id=conversation.id)
        assert db.get(DecisionAttempt, attempt_id) is None
        db.expire_all()
        kept = db.get(TrainingExample, example.id)
        assert kept is not None and kept.input["prompt"].endswith("Miso")
    # The setting off: nothing new is derived.
    with db_env.db() as db:
        conversation = create_conversation(db, user)
        message = _say(db, conversation, "user", "hello")
    _teacher_attempt(db_env, user, conversation, message)
    assert capture.flush(5)
    settings = training_examples.get_settings().model_copy(update={"learning_derive_examples": False})
    monkeypatch.setattr(training_examples, "get_settings", lambda: settings)
    with db_env.db() as db:
        assert training_examples.derive_pending(db) == 0


def test_retention_and_forgetting_remove_raw_copies_only(db_env, capture):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        early = _say(db, conversation, "user", "something to forget")
        fact = SemanticMemory(user_id=user.id, kind="fact", normalized_key="pet", content="They have a cat.")
        db.add(fact)
        db.commit()
    old = _teacher_attempt(db_env, user, conversation, early)
    about_fact = _teacher_attempt(db_env, user, conversation, early, fact_id=fact.id)
    assert capture.flush(5)
    with db_env.db() as db:
        training_examples.derive_pending(db)
        # Retention: past expiry, the raw capture is pruned; its example isn't.
        db.get(DecisionAttempt, old).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert learning_capture.prune(db)["attempts"] == 1
        # Forgetting a memory removes raw copies made from it (in the forget's transaction).
        assert learning_capture.drop_raw_for(db, user.id, [f"fact:{fact.id}"]) == 1
        db.commit()
        assert db.scalar(select(func.count()).select_from(DecisionAttempt).where(
            DecisionAttempt.user_id == user.id)) == 0
        assert db.scalar(select(func.count()).select_from(TrainingExample).where(
            TrainingExample.user_id == user.id)) == 2
    assert about_fact


def test_a_correction_invalidates_and_explicit_deletion_removes_from_exports(db_env, capture, tmp_path, monkeypatch):
    with db_env.db() as db:
        user = create_user(db)
        other = create_user(db)
        conversation = create_conversation(db, user)
        message = _say(db, conversation, "user", "my sister lives in Porto")
        fact = SemanticMemory(user_id=user.id, kind="fact", normalized_key="sister_city",
                              content="Their sister lives in Porto.", source_message_id=message.id)
        db.add(fact)
        db.commit()
    corrected = _teacher_attempt(db_env, user, conversation, message, fact_id=fact.id)
    assert capture.flush(5)
    export = tmp_path / "live_teacher.jsonl"
    export.write_text(json.dumps({"decision_id": str(corrected), "user_id": str(user.id)}) + "\n"
                      + json.dumps({"decision_id": str(corrected), "user_id": str(other.id)}) + "\n"
                      + json.dumps({"decision_id": "someone-else", "user_id": str(user.id)}) + "\n")
    monkeypatch.setattr(training_examples, "exports", lambda: [str(export)])
    with db_env.db() as db:
        training_examples.derive_pending(db)
        # Corrected: ineligible until reviewed, kept for inspection; the source fact is gone and it still finds it.
        db.delete(db.get(SemanticMemory, fact.id))
        db.commit()
        assert training_examples.invalidate_for_sources(db, user.id, [f"fact:{fact.id}"], reason="corrected") == 1
        db.commit()
        example = db.scalar(select(TrainingExample).where(TrainingExample.attempt_id == corrected))
        assert not example.eligible and example.review_status == "needs_review"
        assert training_examples.eligible(db, user.id, "student_2") == []
        # Private: another user can neither see, review nor delete it.
        assert capture_chains.overview(db, other.id)["training_examples"] == {"eligible": 0, "ineligible": 0}
        assert training_examples.review(db, other.id, example.id, "accepted") is None
        assert training_examples.delete_examples(db, other.id, [example.id]) == 0
        # Deleted by its user: gone from training and the export (only their line), never derived again.
        assert training_examples.delete_examples(db, user.id, [example.id]) == 1
        assert db.get(TrainingExampleDeletion, example.id).attempt_id == corrected
    lines = [json.loads(line) for line in export.read_text().splitlines()]
    assert [line["user_id"] for line in lines if line["decision_id"] == str(corrected)] == [str(other.id)]
    assert len(lines) == 2
    with db_env.db() as db:
        db.get(DecisionAttempt, corrected)  # raw capture still there (own retention)
        assert training_examples.derive_pending(db) == 0


def test_another_users_chain_is_not_found(db_env, capture, pipeline):
    with db_env.db() as db:
        user = create_user(db)
        other = create_user(db)
        conversation = create_conversation(db, user)
    _, _, turn_id = _turn(db_env, user, conversation, "just between us")
    assert capture.flush(5)
    with db_env.db() as db:
        assert capture_chains.turn(db, user.id, turn_id) is not None
        assert capture_chains.turn(db, other.id, turn_id) is None
        attempt = db.scalar(select(DecisionAttempt.id).where(DecisionAttempt.turn_id == turn_id))
        assert capture_chains.attempt(db, other.id, attempt) is None


def test_the_api_shows_only_the_users_own_records(db_env, capture, pipeline, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, validate_origin
    from app.api.routes.learning import router
    from app.db.session import get_db

    with db_env.db() as db:
        user = create_user(db)
        other = create_user(db)
        conversation = create_conversation(db, user)
        message = _say(db, conversation, "user", "hello")
    # Both are owners here, to show each still sees only their own records (non-owners: the next test).
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "owner_emails", f"{user.email},{other.email}")
    _, _, turn_id = _turn(db_env, user, conversation, "how are you?")
    _teacher_attempt(db_env, user, conversation, message)
    assert capture.flush(5)
    with db_env.db() as db:
        training_examples.derive_pending(db)

    def session():
        with db_env.db() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[validate_origin] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user.id, email=user.email)
    with TestClient(app) as client:
        overview = client.get("/api/learning/capture").json()
        assert overview["attempts"]["student_2"] == 2 and overview["process"]["written"] >= 4
        chain = client.get(f"/api/learning/capture/turns/{turn_id}").json()
        assert chain["complete"] and {a["learner"] for a in chain["attempts"]} == {"student_1", "student_2"}
        examples = client.get("/api/learning/training-examples").json()
        assert len(examples) == 1
        example_id = examples[0]["id"]
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=other.id, email=other.email)
        assert client.get(f"/api/learning/capture/turns/{turn_id}").status_code == 404
        assert client.get("/api/learning/training-examples").json() == []
        assert client.delete(f"/api/learning/training-examples/{example_id}").status_code == 404
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user.id, email=user.email)
        assert client.patch(f"/api/learning/training-examples/{example_id}",
                            json={"review_status": "rejected"}).json()["eligible"] is False
        assert client.delete(f"/api/learning/training-examples/{example_id}").status_code == 204
        assert client.get("/api/learning/training-examples").json() == []


def test_only_the_owner_sees_her_insides(db_env, monkeypatch):
    """Learning, captures, training data, her mind and memory inspection: 404 for anyone but the owner, who can
    only talk with her. ``is_owner`` tells the app what to show."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, validate_origin
    from app.api.routes import learning, memory, mind
    from app.core.config import get_settings
    from app.db.session import get_db
    from app.schemas.user import UserOut

    with db_env.db() as db:
        owner, guest = create_user(db), create_user(db)
    monkeypatch.setattr(get_settings(), "owner_emails", f" {owner.email.upper()} ")
    monkeypatch.setattr(get_settings(), "minecraft_pack_owner_email", guest.email)  # used only when unset

    def session():
        with db_env.db() as db:
            yield db

    app = FastAPI()
    for router in (learning.router, memory.router, mind.router):
        app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[validate_origin] = lambda: True
    paths = ["/api/learning/capture", "/api/learning/training-examples", "/api/learning/lessons",
             "/api/learning/skills", "/api/mind/thoughts", "/api/memory/working"]
    with TestClient(app) as client:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=guest.id, email=guest.email)
        for path in paths:
            assert client.get(path).status_code == 404, path
        assert client.delete(f"/api/learning/training-examples/{uuid.uuid4()}").status_code == 404
        assert client.patch(f"/api/learning/lessons/{uuid.uuid4()}", json={"status": "retired"}).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner.id, email=owner.email)
        assert client.get("/api/learning/training-examples").status_code == 200
        assert client.get("/api/learning/capture").status_code == 200
    assert UserOut.model_validate(owner).is_owner and not UserOut.model_validate(guest).is_owner
    monkeypatch.setattr(get_settings(), "owner_emails", "")
    assert get_settings().is_owner(guest.email) and not get_settings().is_owner(owner.email)
