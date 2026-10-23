"""One real chat-worker turn (``workers/chat._generate``) with a deterministic mocked agent, on the
disposable schema from ``db_support``."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db.models import ChatJob, ChatMessage
from app.services import chat as chat_service
from app.workers import chat as chat_worker
from db_support import create_conversation, create_user

REPLY = ("Two large eggs ", "have about ", "12 g of protein.")


@pytest.fixture
def live_turn(db_env, monkeypatch):
    """Run the real chat-worker `_generate` with a deterministic mocked agent."""

    published: list[dict] = []
    agent_inputs: list[tuple] = []

    async def fake_stream_chat(*, messages, context, **_):
        agent_inputs.append((messages, dataclasses.replace(context, user_id=None)))
        for token in REPLY:
            yield {"type": "token", "content": token}

    async def no_token(db, *, user_id):
        return None

    monkeypatch.setattr(chat_worker, "SessionLocal", db_env.db)
    monkeypatch.setattr(chat_worker, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_worker, "publish", lambda turn_id, event: published.append(event))
    monkeypatch.setattr(chat_worker, "_cancelled", lambda turn_id: False)
    monkeypatch.setattr(chat_worker.google, "get_access_token", no_token)
    consolidation = Mock()
    monkeypatch.setattr(chat_worker.memory_service, "schedule_consolidation", consolidation)

    def run(content: str = "How much protein is in two eggs?"):
        published.clear()
        with db_env.db() as db:
            user = create_user(db)
            conversation = create_conversation(db, user)
            turn_id = uuid.uuid4()
            message = chat_service.add_message(db, conversation=conversation, role="user", content=content,
                                               turn_id=turn_id)
            chat_service.enqueue_job(db, user_id=user.id, conversation=conversation, turn_id=turn_id,
                                     user_message_id=message.id, payload={"content": content}, commit=False)
            db.commit()
            job = chat_service.claim_next_job(db, lease_seconds=60)
        asyncio.run(chat_worker._generate(job))
        with db_env.db() as db:
            job = db.get(ChatJob, job.id)
            assistant = db.scalar(select(ChatMessage).where(ChatMessage.turn_id == turn_id,
                                                            ChatMessage.role == "assistant"))
        normalized = [
            {key: value for key, value in event.items() if key not in {"turn_id", "message_id"}}
            for event in published
        ]
        return {
            "events": normalized, "job": (job.status, job.last_error),
            "assistant": (assistant.content, assistant.status), "agent_input": agent_inputs[-1],
            "turn_id": turn_id, "consolidations": consolidation.call_count,
        }

    return run
