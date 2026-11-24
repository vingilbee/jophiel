"""A user message stops a wake her background mind raised on its own, so it's answered next.

Seen live 2026-11-24 in a game: "follow me" waited 11 s and another message 14 s in the queue behind her thought
wakes (one conversation's jobs run one at a time). Wakes she scheduled, or that a promise waits for, run on."""

import uuid
from datetime import datetime, timedelta, timezone

from app.db.models import ChatJob, ChatMessage, Conversation, CoordinatorWork
from app.services import chat as chat_service
from app.workers import chat as chat_worker
from tests.db_support import database  # noqa: F401  (fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: F401  (fixtures)


def _wake(db, user, conversation, *, autonomous: bool) -> ChatJob:
    work = CoordinatorWork(request_id=uuid.uuid4(), user_id=user.id, conversation_id=conversation.id,
                           kind="mind", status="completed", input={"autonomous": autonomous},
                           deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                           idempotency_key=str(uuid.uuid4()))
    db.add(work)
    db.flush()
    job = ChatJob(turn_id=uuid.uuid4(), user_id=user.id, conversation_id=conversation.id, status="processing",
                  payload={"phase": "follow_up", "work_id": str(work.id), "request_id": str(work.request_id)})
    db.add(job)
    db.commit()
    return job


def test_a_user_message_stops_her_own_thought_wake_but_not_a_scheduled_one(db, alice, monkeypatch):
    cancelled: list = []
    monkeypatch.setattr(chat_worker, "request_cancel", lambda turn_id: cancelled.append(turn_id))
    monkeypatch.setattr(chat_service, "wake_chat_worker", lambda job_id: None)
    conversation = Conversation(user_id=alice.id)
    db.add(conversation)
    db.commit()
    thought = _wake(db, alice, conversation, autonomous=True)
    scheduled = _wake(db, alice, conversation, autonomous=False)
    message = ChatMessage(conversation_id=conversation.id, role="user", content="follow me")
    db.add(message)
    db.commit()
    chat_service.enqueue_job(db, user_id=alice.id, conversation=conversation, turn_id=uuid.uuid4(),
                             user_message_id=message.id, payload={"content": "follow me"})
    assert cancelled == [thought.turn_id] and scheduled.turn_id not in cancelled
    # A wake's own job (no user message) stops nothing.
    cancelled.clear()
    chat_service.enqueue_job(db, user_id=alice.id, conversation=conversation, turn_id=uuid.uuid4(),
                             payload={"phase": "follow_up"})
    assert cancelled == []
