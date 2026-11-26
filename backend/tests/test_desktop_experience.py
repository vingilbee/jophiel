"""Her desktop actions as experience: capture records on the turn, observations, memory.

Needs ``TEST_DATABASE_URL``.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import ChatMessage, Conversation, DecisionExecution, MemoryEvent, Observation, ObservationProcessing
from app.services import chat as chat_service, desktop_control, learning_capture, observation_memory
from app.services.desktop_control import Outcome, TurnRef
from db_support import create_conversation, create_user, db_env, database  # noqa: F401
from tests.desktop_support import isolated_redis  # noqa: F401  (fixture)


@pytest.fixture
def env(db_env):
    return db_env


def say(db, conversation, role, text, *, turn_id=None) -> ChatMessage:
    conversation = db.get(Conversation, conversation.id)
    return chat_service.add_message(db, conversation=conversation, role=role, content=text, turn_id=turn_id,
                                    status="completed")


@pytest.fixture
def capture(env, tmp_path):  # noqa: F811
    svc = learning_capture.CaptureService(base_delay=0.01, max_delay=0.05, max_tries=3,
                                          gap_path=str(tmp_path / "gaps.jsonl"))
    before = learning_capture.install(svc)
    yield svc
    svc.flush(5)
    learning_capture.install(before)


def test_a_turn_of_desktop_work_reaches_capture_observation_and_memory(env, capture, isolated_redis):
    with env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid.uuid4()
        asked = say(db, conversation, "user", "save my notes in notepad", turn_id=turn_id)
        say(db, conversation, "assistant", "Saved it as notes.txt.", turn_id=turn_id)
        turn = TurnRef(turn_id=turn_id, conversation_id=conversation.id, message_id=asked.id)
        now = datetime.now(timezone.utc)
        shot = Outcome("a" * 32, "done", screen={"region": [0, 0, 1920, 1080], "size": [1280, 720],
                                               "screen": [0, 0, 1920, 1080]},
                       foreground_process="notepad.exe", foreground_title="Untitled - Notepad", ended_at=now)
        desktop_control.remember(db, user.id, tool="screenshot", intended={}, outcome=shot, turn=turn, step=1)
        failed = Outcome("b" * 32, "failed", "Windows blocked the input", foreground_process="notepad.exe",
                         ended_at=now)
        desktop_control.remember(db, user.id, tool="click", intended={"x": 10, "y": 20, "button": "left"},
                                 outcome=failed, turn=turn, step=2)
        user_id = user.id
    capture.flush(5)

    with env.db() as db:
        executions = list(db.scalars(select(DecisionExecution).where(DecisionExecution.turn_id == turn_id)
                                     .order_by(DecisionExecution.started_at)))
        assert [(e.kind, e.result) for e in executions] == [("desktop_action", "succeeded"),
                                                            ("desktop_action", "failed")]
        assert executions[1].deviation == "Windows blocked the input"
        assert executions[1].intended["tool"] == "click"

        seen = list(db.scalars(select(Observation).where(Observation.user_id == user_id,
                                                         Observation.source == "desktop").order_by(Observation.id)))
        assert [o.payload["status"] for o in seen] == ["done", "failed"]
        assert seen[0].payload["app_changed"] is True and "Untitled - Notepad" in seen[0].summary
        consumers = set(db.scalars(select(ObservationProcessing.consumer)
                                   .where(ObservationProcessing.observation_id == seen[1].id)))
        assert consumers == {"memory"}

        # Memory: the new window and the failure are kept; bound to the context in focus.
        observation_memory.process(db, datetime.now(timezone.utc), time.monotonic() + 10, user_id=user_id)
        kinds = set(db.scalars(select(MemoryEvent.kind).where(MemoryEvent.user_id == user_id)))
        assert {"desktop_seen", "action_failed"} <= kinds
