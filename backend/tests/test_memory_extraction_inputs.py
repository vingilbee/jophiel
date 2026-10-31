"""Extraction must distinguish empty success from failure and follow explicit turn links."""
import uuid
from types import SimpleNamespace

import pytest

from app.db.models import ChatJob, MemoryJob, SemanticMemory
from app.schemas.long_term_memory import MemoryExtraction
from app.services import chat, long_term_memory as memory
from tests.db_support import create_user, database  # noqa: F401


@pytest.mark.parametrize("status,parsed", [("completed", None), ("incomplete", MemoryExtraction())])
def test_unusable_extraction_is_an_error(monkeypatch, status, parsed):
    response = SimpleNamespace(status=status, output_parsed=parsed)
    monkeypatch.setattr(memory, "_client", lambda: SimpleNamespace(
        responses=SimpleNamespace(parse=lambda **kwargs: response)))
    with pytest.raises(ValueError, match="completed structured result"):
        memory._extract("Ada prefers rooibos", "Got it")


def test_valid_empty_extraction_succeeds(monkeypatch):
    response = SimpleNamespace(status="completed", output_parsed=MemoryExtraction())
    monkeypatch.setattr(memory, "_client", lambda: SimpleNamespace(
        responses=SimpleNamespace(parse=lambda **kwargs: response)))
    assert memory._extract("Hello", "Hello").memories == []


@pytest.mark.parametrize("linked_job", [False, True])
def test_overlapping_voice_turn_uses_original_message(database, linked_job):
    with database() as db:
        user = create_user(db)
        conversation = chat.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
        turn = uuid.uuid4()
        first = chat.add_message(db, conversation=conversation, role="user", content="Ada prefers rooibos",
                                 turn_id=turn)
        chat.add_message(db, conversation=conversation, role="user", content="I prefer ginger",
                         turn_id=uuid.uuid4())
        reply_turn = uuid.uuid4() if linked_job else turn
        reply = chat.add_message(db, conversation=conversation, role="assistant", content="Got it. More words",
                                 turn_id=reply_turn)
        reply.heard_chars = len("Got it.")
        if linked_job:
            db.add(ChatJob(user_id=user.id, conversation_id=conversation.id, turn_id=reply_turn,
                           user_message_id=first.id, payload={}))
        job = MemoryJob(user_id=user.id, conversation_id=conversation.id, assistant_message_id=reply.id)
        db.add(job)
        db.commit()
        source, answer, source_id = memory._turn_for_job(db, job)
        assert source_id == first.id and source == first.content
        assert "More words" not in answer


def test_missing_pair_retries_and_fails_instead_of_learning_from_latest(database, monkeypatch):
    monkeypatch.setattr(memory, "_extract", lambda *args: pytest.fail("Must not guess a source"))
    with database() as db:
        user = create_user(db)
        conversation = chat.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
        chat.add_message(db, conversation=conversation, role="user", content="Unrelated", turn_id=uuid.uuid4())
        reply = chat.add_message(db, conversation=conversation, role="assistant", content="Answer",
                                 turn_id=uuid.uuid4())
        job = MemoryJob(user_id=user.id, conversation_id=conversation.id, assistant_message_id=reply.id)
        db.add(job)
        db.commit()
        for expected in ("pending", "pending", "failed"):
            assert memory.process_job(db, job.id) is False
            db.refresh(job)
            assert job.status == expected
            assert "source user message" in job.last_error


def test_selected_context_keeps_full_text(database):
    with database() as db:
        user = create_user(db)
        conversation = chat.get_or_create_conversation(db, user_id=user.id, conversation_id=None)
        full = "Background detail. " * 40 + "Ada means my sister."
        chat.add_message(db, conversation=conversation, role="user", content=full, turn_id=uuid.uuid4())
        current = chat.add_message(db, conversation=conversation, role="user", content="She prefers rooibos",
                                   turn_id=uuid.uuid4())
        fact = "Preference detail. " * 20 + "Only when travelling."
        db.add(SemanticMemory(user_id=user.id, normalized_key="preference:ada", kind="preference",
                              content=fact, importance=0.7, confidence=1.0))
        db.commit()
        assert memory._earlier_messages(db, conversation.id, current.id) == ["USER: " + full]
        assert memory._known(db, user.id) == ["preference:ada: " + fact]
