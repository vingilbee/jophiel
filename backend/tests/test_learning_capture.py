"""Learning step 1, capture and ownership (docs/learning-system/01-implementation.md, "Completion evidence").

Real PostgreSQL (TEST_DATABASE_URL); no model calls: the evaluation of her mind is prepared and recorded by the real
code (``services/mind_turn.py``) with a scripted memory check; the model's run is stood in by what it would leave on
the evaluation (its prompt, tools, reply). One conversation chain and one game attempt end to end, then failures:
delivery, execution and unknown results, a storage outage while replies continue, restart without duplicates,
retention and source deletion, and user boundaries.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    CaptureGap, Conversation, ChatMessage, DecisionAttempt, DecisionAttemptSource, DecisionExecution, SemanticMemory,
)
from app.services import capture_chains, chat as chat_service, learning_capture, mind_turn, recall_references
from db_support import create_conversation, create_user, db_env, database  # noqa: F401

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


@pytest.fixture
def pipeline(capture, monkeypatch, tmp_path):
    """The evaluation with a scripted memory check (memory returns ``state["fact"]`` when set, or fails when
    ``state["recall_fails"]``). Everything else is the real code on the test DB."""
    state = {"fact": None, "recall_fails": False}

    def for_reply(user_id, conversation_id, user_message_id, query, zone, **_):
        from app.ai.recall.contracts import RecallResult
        from app.db.session import SessionLocal
        from app.services import recall

        if state["recall_fails"]:
            result = RecallResult.failed("search_failed")
        elif state["fact"] is not None:
            with SessionLocal() as db:
                result = recall.read(db, user_id, f"fact:{state['fact']}")
        else:
            result = RecallResult("no_match")
        now = datetime.now(timezone.utc)
        turn = recall_references.TurnRecall(user_id, conversation_id, "UTC", now, None, result,
                                            "- They like tea." if result.records else "", None,
                                            user_message_id=user_message_id)
        turn.supply(result.records)
        return turn

    monkeypatch.setattr(recall_references, "for_reply", for_reply)
    settings = mind_turn.get_settings().model_copy(update={"mind_turn_log_path": str(tmp_path / "turns.jsonl")})
    monkeypatch.setattr(mind_turn, "get_settings", lambda: settings)
    return state


def _say(db, conversation, role, content, **kw) -> ChatMessage:
    return chat_service.add_message(db, conversation=conversation, role=role, content=content, **kw)


def _turn(db_env, user, conversation, text, *, turn_id=None, job=None, reply="hi there", outcome="completed",
          silent=None, tools=()):
    """One user message through the real preparation, the model's run stood in (prompt, tools, reply), then the
    reply stored and the evaluation finished as the worker does."""
    with db_env.db() as db:
        conversation = db.get(Conversation, conversation.id)
        message = _say(db, conversation, "user", text)
    turn_id = turn_id or uuid.uuid4()
    ev = mind_turn.prepare_message(user_id=user.id, conversation_id=conversation.id, user_message_id=message.id,
                                   message=text, zone="UTC", turn_id=turn_id, job=job)
    ev.prompt, ev.model, ev.silent = "SYSTEM TEXT", "gpt-test", silent
    ev.actions = [{"tool": t, "status": "success", "result": "ok"} for t in tools]
    stored = None
    if reply and outcome in ("completed", "partial", "failed"):
        with db_env.db() as db:
            stored = _say(db, db.get(Conversation, conversation.id), "assistant", reply,
                          message_id=uuid.uuid5(turn_id, "assistant"), turn_id=turn_id, status=outcome).id
    mind_turn.finish(ev, outcome=outcome, reply=reply, assistant_message_id=stored,
                     error="boom" if outcome == "failed" else None)
    return ev, message, turn_id


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
    ev, message, turn_id = _turn(db_env, user, conversation, "hello again", tools=["search_memories"])
    assert capture.flush(5)
    # Something said after the evaluation never reaches what was captured for it.
    with db_env.db() as db:
        _say(db, db.get(Conversation, conversation.id), "user", "LATER: this came after")
        chain = capture_chains.turn(db, user.id, turn_id, blobs=True)
    assert chain["complete"], chain["missing"]
    assert [a["learner"] for a in chain["attempts"]] == ["mind"]  # one evaluation: no student, no second stage
    mind = chain["attempts"][0]
    # Its id was given before it ran; what it read (the system text resolved from its blob, memory) and what it did.
    assert mind["id"] == str(ev.capture.attempt_id) and mind["turn_id"] == str(turn_id)
    assert mind["prior_attempt_id"] is None and mind["trigger"] == "user_message"
    assert mind["input"]["system"] == "SYSTEM TEXT" and mind["input"]["message"] == "hello again"
    assert mind["input"]["memory"]["status"] == "matched" and "LATER" not in json.dumps(mind["input"])
    assert {r["ref"] for r in mind["input_refs"]} == {f"fact:{fact.id}"}
    assert mind["output"]["reply"] == "hi there" and mind["output"]["silent"] is None
    assert [t["tool"] for t in mind["output"]["tools"]] == ["search_memories"]
    assert set(mind["absent"]) >= {"prediction", "alternatives", "selection_probabilities"}
    assert chain["leaks"] == {}
    # What she chose next to what was delivered.
    reply = next(e for e in chain["executions"] if e["kind"] == "reply")
    assert reply["intended"]["communication"] == "speak" and reply["result"] == "succeeded"
    assert reply["attempt_id"] == mind["id"] and reply["executed"]["reply_chars"] == len("hi there")
    with db_env.db() as db:
        sources = set(db.scalars(select(DecisionAttemptSource.ref).where(
            DecisionAttemptSource.attempt_id == uuid.UUID(mind["id"]))))
    assert f"message:{message.id}" in sources and f"fact:{fact.id}" in sources


def test_failures_are_known_silence_is_chosen_and_unknown_results_stay_unknown(db_env, capture, pipeline):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
    # Memory's search fails and the reply fails: each is a record of that, never an empty memory.
    pipeline["recall_fails"] = True
    _, _, failed_turn = _turn(db_env, user, conversation, "are you there?", reply="I couldn't", outcome="failed")
    pipeline["recall_fails"] = False
    # Silence chosen: nothing sent, recorded as not sent (not as a failure).
    _, _, silent_turn = _turn(db_env, user, conversation, "ok", reply="", outcome="silent", silent="nothing to add")
    # A cut-off reply is a deviation; a WhatsApp reply has no result until the channel settles it.
    _, _, partial_turn = _turn(db_env, user, conversation, "tell me a story", reply="Once upon", outcome="partial")
    assert capture.flush(5)
    with db_env.db() as db:
        failed = capture_chains.turn(db, user.id, failed_turn)
        silent = capture_chains.turn(db, user.id, silent_turn)
        partial = capture_chains.turn(db, user.id, partial_turn)
        hearing = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == partial_turn))
        chat_service.record_heard(db, user_id=user.id, turn_id=partial_turn, heard_chars=4)
    mind = failed["attempts"][0]
    assert mind["status"] == "error" and mind["error"] == "boom" and mind["input"]["memory"]["status"] == "failed"
    assert next(e for e in failed["executions"] if e["kind"] == "reply")["result"] == "failed"
    quiet = next(e for e in silent["executions"] if e["kind"] == "reply")
    assert quiet["result"] == "not_sent" and quiet["intended"]["communication"] == "silent"
    assert silent["attempts"][0]["output"]["silent"] == "nothing to add"
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
        ev, _, turn_id = _turn(db_env, user, conversation, "still there?")
        assert ev.capture is not None  # the reply went on without storage
        assert svc.flush(10)
    finally:
        learning_capture.install(before)
    stats = svc.stats()
    # Her mind's evaluation and its delivery: two records, each retried then given up as a gap.
    assert stats["written"] == 0 and stats["retried"] >= 2 and stats["gaps"] >= 2
    # The database was the thing down: gaps fell back to the file, content-free.
    lines = [json.loads(line) for line in (tmp_path / "gaps.jsonl").read_text().splitlines()]
    assert {line["reason"] for line in lines} == {"retries_exhausted"}
    assert {line["label"] for line in lines} >= {"mind", "reply"}
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
    learning_capture.attempt(ctx, learner="mind", provider="openai", model="m", input={"message": "a"},
                             output=None, status="unavailable", started_at=datetime.now(timezone.utc), produced_at=None)
    second = uuid.uuid4()
    learning_capture.attempt(learning_capture.Ctx(user_id=user.id, attempt_id=second, trigger="user_message"),
                             learner="mind", provider="openai", model="m", input={}, output=None,
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
    first_id = learning_capture.attempt_id_for(turn_id, "mind", 1)
    learning_capture.attempt(learning_capture.Ctx(user_id=user.id, attempt_id=first_id, trigger="user_message"),
                             learner="mind", provider="openai", model="x", input={"message": "replayed"},
                             output=None, status="error", started_at=datetime.now(timezone.utc), produced_at=None)
    # The job is reclaimed: attempt 2, linked to attempt 1.
    job.attempts = 2
    with db_env.db() as db:
        message = db.scalar(select(ChatMessage).where(ChatMessage.content == "first try"))
    ev = mind_turn.prepare_message(user_id=user.id, conversation_id=conversation.id, user_message_id=message.id,
                                   message="first try", zone="UTC", turn_id=turn_id, job=job)
    ev.prompt = "SYSTEM TEXT"
    mind_turn.finish(ev, outcome="cancelled", reply="", assistant_message_id=None)
    assert capture.flush(5)
    with db_env.db() as db:
        rows = list(db.scalars(select(DecisionAttempt).where(DecisionAttempt.turn_id == turn_id)))
        first = db.get(DecisionAttempt, first_id)
        replies = db.scalar(select(func.count()).select_from(DecisionExecution).where(
            DecisionExecution.turn_id == turn_id, DecisionExecution.kind == "reply"))
    assert len(rows) == 2 and first.input["message"] != "replayed"  # the first save won; the replay inserted nothing
    retried = next(r for r in rows if r.prior_attempt_id is not None)
    assert retried.prior_attempt_id == first_id
    assert replies == 1  # one delivery record for the turn, whichever attempt finished it


# --------------------------------------------------------------------------- #
# Retention, source deletion, access
# --------------------------------------------------------------------------- #


def _mind_attempt(db_env, user, conversation, message, *, fact_id=None) -> uuid.UUID:
    attempt_id = uuid.uuid4()
    refs = (f"message:{message.id}",) + ((f"fact:{fact_id}",) if fact_id else ())
    learning_capture.attempt(
        learning_capture.Ctx(user_id=user.id, attempt_id=attempt_id, trigger="user_message",
                             conversation_id=conversation.id, source_message_id=message.id, sources=refs),
        learner="mind", provider="openai", model="gpt-6-luna",
        input={"reason": "user_message", "message": message.content, "system": None}, blobs={"system": "SYSTEM"},
        output={"reply": "hi there", "silent": None, "tools": []}, status="ok",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=1), produced_at=datetime.now(timezone.utc))
    return attempt_id


def test_retention_and_forgetting_remove_raw_copies(db_env, capture):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        early = _say(db, conversation, "user", "something to forget")
        fact = SemanticMemory(user_id=user.id, kind="fact", normalized_key="pet", content="They have a cat.")
        db.add(fact)
        db.commit()
    old = _mind_attempt(db_env, user, conversation, early)
    about_fact = _mind_attempt(db_env, user, conversation, early, fact_id=fact.id)
    assert capture.flush(5)
    with db_env.db() as db:
        # Retention: past expiry, the raw capture is pruned.
        db.get(DecisionAttempt, old).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert learning_capture.prune(db)["attempts"] == 1
        # Forgetting a memory removes raw copies made from it (in the forget's transaction).
        assert learning_capture.drop_raw_for(db, user.id, [f"fact:{fact.id}"]) == 1
        db.commit()
        assert db.scalar(select(func.count()).select_from(DecisionAttempt).where(
            DecisionAttempt.user_id == user.id)) == 0
    assert about_fact


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
    _mind_attempt(db_env, user, conversation, message)
    assert capture.flush(5)

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
        assert overview["attempts"]["mind"] == 2 and overview["process"]["written"] >= 3
        assert "training_examples" not in overview
        chain = client.get(f"/api/learning/capture/turns/{turn_id}").json()
        assert chain["complete"] and {a["learner"] for a in chain["attempts"]} == {"mind"}
        # Training examples are retired: the routes are gone.
        assert client.get("/api/learning/training-examples").status_code == 404
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=other.id, email=other.email)
        assert client.get(f"/api/learning/capture/turns/{turn_id}").status_code == 404


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
    paths = ["/api/learning/capture", "/api/learning/lessons", "/api/learning/skills", "/api/mind/thoughts",
             "/api/mind/state", "/api/memory/working"]
    with TestClient(app) as client:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=guest.id, email=guest.email)
        for path in paths:
            assert client.get(path).status_code == 404, path
        assert client.patch(f"/api/learning/lessons/{uuid.uuid4()}", json={"status": "retired"}).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner.id, email=owner.email)
        assert client.get("/api/mind/state").status_code == 200
        assert client.get("/api/learning/capture").status_code == 200
    assert UserOut.model_validate(owner).is_owner and not UserOut.model_validate(guest).is_owner
    monkeypatch.setattr(get_settings(), "owner_emails", "")
    assert get_settings().is_owner(guest.email) and not get_settings().is_owner(owner.email)
