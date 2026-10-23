"""Step 5 routing through real durable coordinator and mouth; scripted cognition only."""
import uuid

import pytest
from sqlalchemy import select

from app.ai.state.decisions import ResultDecision, RESULT_GAP
from app.db.models import ChatMessage, CoordinatorOutput, CoordinatorWork
from app.services import autonomous_speech, cognition_decisions, coordinator
from app.services import chat as chat_service
from test_coordinator import env, _run_chat  # noqa: F401
from db_support import database, db_env  # noqa: F401


def propose(env, monkeypatch, event=None):
    monkeypatch.setattr(autonomous_speech.agent_say, "user_spoke_since", lambda *a: False)
    monkeypatch.setattr(autonomous_speech.agent_say, "allowed", lambda *a, **k: (True, ""))
    monkeypatch.setattr(autonomous_speech.desktop_preview, "redis_client", lambda: None)
    with env.db() as db:
        result = autonomous_speech.submit(
            db, user_id=env.user.id, activity_id=env.conversation.id, source="conversation",
            event_id=event or uuid.uuid4(), text="A completed autonomous thought", reasons=["thought: test"],
            commentary_mode="occasional")
        assert result.startswith("Proposed")
        return db.scalar(select(CoordinatorWork))


@pytest.mark.parametrize("action", ["respond", "ask", "defer", "silent", None])
def test_proposal_cognition_mouth_and_receipt(env, monkeypatch, action):
    calls = []
    def decide(inp):
        calls.append(inp)
        return ResultDecision(action, "stub" if action else "unsupported", bool(action),
                              missing_capability=None if action else RESULT_GAP)
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", decide)
    work = propose(env, monkeypatch)
    assert coordinator.execute_once()
    assert len(calls) == 1 and calls[0].trigger_ref.startswith("event:")
    assert env.calls["think"] == []
    if action in ("respond", "ask"):
        assert _run_chat(env)
        assert env.announced
        assert "autonomous thought" in coordinator.render_result(coordinator.follow_up_still_valid(work.id)[1])
    else:
        assert not _run_chat(env)
    with env.db() as db:
        output = db.scalar(select(CoordinatorOutput))
        assert output.decision == (action or "blocked")
        assert output.status == ("delivered" if action in ("respond", "ask") else "not_sent")
    assert not coordinator.execute_once()


def test_duplicate_proposal_and_new_user_fence(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda _: ResultDecision("respond", "stub", True))
    event = uuid.uuid4()
    work = propose(env, monkeypatch, event)
    assert propose(env, monkeypatch, event).id == work.id
    assert coordinator.execute_once()
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        chat_service.add_message(db, conversation=conversation, role="user", content="Different topic now")
    assert coordinator.follow_up_still_valid(work.id) == (False, None)
    assert _run_chat(env)
    assert not env.announced
    with env.db() as db:
        assert not list(db.scalars(select(ChatMessage).where(ChatMessage.role == "assistant")))
        assert db.scalar(select(CoordinatorOutput)).status == "cancelled"


def test_recover_accepted_proposal_after_restart(env, monkeypatch):
    work = propose(env, monkeypatch)
    with env.db() as db:
        db.get(CoordinatorWork, work.id).status = "completed"
        db.commit()
    assert coordinator.execute_once()
    with env.db() as db:
        assert db.scalar(select(CoordinatorOutput)).decision == "blocked"


def test_new_message_during_mouth_generation_prevents_announcement(env, monkeypatch):
    from app.workers import chat as worker
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda _: ResultDecision("respond", "stub", True))
    propose(env, monkeypatch)
    coordinator.execute_once()
    async def mouth(**kwargs):
        yield {"type": "token", "content": "An obsolete thought"}
        with env.db() as db:
            conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
            chat_service.add_message(db, conversation=conversation, role="user", content="Stop, new topic")
    monkeypatch.setattr(worker, "stream_chat", mouth)
    assert _run_chat(env)
    assert not env.announced
    with env.db() as db:
        assert db.scalar(select(CoordinatorOutput)).status == "cancelled"
        assert not list(db.scalars(select(ChatMessage).where(ChatMessage.role == "assistant")))


@pytest.mark.parametrize("guard", ["expired", "quiet", "recent_user"])
def test_delivery_guards_keep_proposal_private(env, monkeypatch, guard):
    from datetime import timedelta
    work = propose(env, monkeypatch)
    if guard == "expired":
        with env.db() as db:
            db.get(CoordinatorWork, work.id).deadline_at = coordinator._now() - timedelta(seconds=1)
            db.commit()
    elif guard == "quiet":
        monkeypatch.setattr(autonomous_speech.agent_say, "allowed", lambda *a, **k: (False, "quiet"))
    else:
        monkeypatch.setattr(autonomous_speech.agent_say, "user_spoke_since", lambda *a: True)
    coordinator.execute_once()
    assert not _run_chat(env)
    with env.db() as db:
        assert db.scalar(select(CoordinatorOutput)).decision == "rejected"
