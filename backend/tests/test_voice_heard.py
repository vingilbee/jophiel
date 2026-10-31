"""Interrupted spoken replies: the model and memory see only what was heard."""

from __future__ import annotations

import uuid

import pytest

from app.services import chat as chat_service
from app.services import heard
from db_support import create_conversation, create_user, database, db_env  # noqa: F401

REPLY = "Two large eggs have twelve grams of protein. That is a solid breakfast. Add toast for carbs."


@pytest.mark.parametrize(("heard_chars", "expected"), [
    (None, REPLY),
    (len(REPLY), REPLY),
    (45, "Two large eggs have twelve grams of protein. " + heard.INTERRUPTED_MARKER),
    # Mid-word ("break|fast") snaps back to the last whole word.
    (66, "Two large eggs have twelve grams of protein. That is a solid " + heard.INTERRUPTED_MARKER),
    (0, heard.INTERRUPTED_MARKER),
    (2, heard.INTERRUPTED_MARKER),  # "Tw" is not a word they heard
])
def test_heard_text(heard_chars, expected) -> None:
    assert heard.heard_text(REPLY, heard_chars) == expected


def test_heard_cut_never_splits_a_word() -> None:
    for n in range(len(REPLY)):
        cut = heard.heard_cut(REPLY, n)
        # Either exactly where asked (already a word boundary) or back at the
        # start of the word being spoken.
        assert cut == n or cut == 0 or (cut < n and REPLY[cut - 1].isspace())


def test_clause_spans_map_back_onto_the_raw_reply() -> None:
    raw = "Eggs are great. [[sticker:yum]] Two of them have twelve grams.\n\nWant toast?"
    clauses = ["Eggs are great.", "Two of them have twelve grams.", "Want toast?"]
    end = 0
    spans = []
    for clause in clauses:
        start, end = end, heard.raw_end_of_spoken(raw, end, clause)
        spans.append(raw[start:end].strip())
    assert spans == ["Eggs are great.", "[[sticker:yum]] Two of them have twelve grams.", "Want toast?"]
    assert end == len(raw)


def test_history_and_memory_use_the_heard_part(db_env) -> None:
    from app.services.long_term_memory import _turn_for_job
    from types import SimpleNamespace

    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        turn_id = uuid.uuid4()
        chat_service.add_message(db, conversation=conversation, role="user",
                                 content="Protein in eggs?", turn_id=turn_id)
        assistant = chat_service.add_message(db, conversation=conversation, role="assistant",
                                             content=REPLY, turn_id=turn_id)
        assert chat_service.record_heard(db, user_id=user.id, turn_id=turn_id, heard_chars=45)
        # A later, larger report never moves the cut forward again.
        assert chat_service.record_heard(db, user_id=user.id, turn_id=turn_id, heard_chars=80)
        # Someone else's reply is never touched.
        assert not chat_service.record_heard(db, user_id=uuid.uuid4(), turn_id=turn_id, heard_chars=1)

        history = chat_service.get_history(db, conversation=conversation)
        expected = "Two large eggs have twelve grams of protein. " + heard.INTERRUPTED_MARKER
        assert history[-1] == {"role": "assistant", "content": expected}
        db.refresh(assistant)
        assert assistant.content == REPLY and assistant.heard_chars == 45

        job = SimpleNamespace(assistant_message_id=assistant.id, conversation_id=conversation.id, user_id=user.id)
        assert _turn_for_job(db, job)[1] == expected


def test_record_heard_waits_for_the_reply(db_env) -> None:
    with db_env.db() as db:
        user = create_user(db)
        assert not chat_service.record_heard(db, user_id=user.id, turn_id=uuid.uuid4(), heard_chars=3)
