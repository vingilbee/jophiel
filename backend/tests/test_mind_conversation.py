"""Her mind in everyday chat (services/mind_conversation.py): the problems the sweep finds in the stores, what she
reads, when a thought no longer holds, and Student 2 seeing her chat thoughts. Real PostgreSQL
(``TEST_DATABASE_URL``); no model calls."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.ai.mind.contracts import Scope
from app.ai.mind.hard import Problem
from app.db.models import ChatMessage, MindThought, WorkingItem
from app.services import chat as chat_service
from app.services import cognitive_live, memory_manager, mind_conversation, student_two
from db_support import create_conversation, create_user, database, db_env  # noqa: F401


def _say(db, conversation, role, content, *, ago):
    message = chat_service.add_message(db, conversation=conversation, role=role, content=content,
                                       turn_id=uuid.uuid4())
    at = datetime.now(timezone.utc) - ago
    db.execute(update(ChatMessage).where(ChatMessage.id == message.id).values(created_at=at))
    conversation.updated_at = at
    db.commit()
    return message


def test_an_open_promise_of_hers_is_a_problem_in_the_conversation_she_made_it(db_env):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message = _say(db, conversation, "user", "Can you find me a good ramen place?", ago=timedelta(minutes=10))
        promise = memory_manager.note_commitment(db, user.id, "find a ramen place near their office",
                                                 source=memory_manager.Source("message", message.id))
        fresh = memory_manager.note_commitment(db, user.id, "send the recipe",
                                               source=memory_manager.Source("message", message.id))
        theirs = memory_manager.note_commitment(db, user.id, "send her the photos", owner="user",
                                                source=memory_manager.Source("message", message.id))
        no_message = memory_manager.note_commitment(db, user.id, "look up the date")
        db.execute(update(WorkingItem).where(WorkingItem.id.in_([promise.id, theirs.id, no_message.id]))
                   .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
        db.commit()
        found = mind_conversation.item_problems(db, datetime.now(timezone.utc))
    # Hers, owed to the user, older than a reply, said in a conversation: one problem, in that conversation.
    assert [(s, p.trigger) for s, p in found] == [(Scope(user.id, "conversation", conversation.id), "promise")]
    problem = found[0][1]
    assert "find a ramen place" in problem.text and problem.anchor["event"] == f"item:{promise.id}:1"
    assert fresh.id and theirs.id and no_message.id  # too new, the user's own, nowhere to say it


def test_quiet_time_is_found_once_per_silence_in_the_latest_conversation(db_env):
    with db_env.db() as db:
        user = create_user(db)
        older = create_conversation(db, user)
        _say(db, older, "user", "hi", ago=timedelta(hours=3))
        latest = create_conversation(db, user)
        _say(db, latest, "user", "Long day at the clinic.", ago=timedelta(minutes=30))
        _say(db, latest, "assistant", "That sounds draining.", ago=timedelta(minutes=29))
        found = mind_conversation.quiet_problems(db, datetime.now(timezone.utc), {})
        assert [(s.activity, p.trigger) for s, p in found] == [(latest.id, "quiet")]
        assert found[0][1].anchor["event"] == f"quiet:{latest.id}:2"
        _say(db, latest, "user", "ok back", ago=timedelta(minutes=1))  # they spoke again: not quiet now
        assert mind_conversation.quiet_problems(db, datetime.now(timezone.utc), {}) == []


def test_she_reads_the_conversation_what_is_open_and_her_memory(db_env, monkeypatch):
    monkeypatch.setattr(cognitive_live, "load_state", lambda uid, cid: {
        "notes": [{"id": "n1", "kind": "their_open_question", "text": "which bike should I buy?"}]})
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        _say(db, conversation, "user", "Which bike should I buy?", ago=timedelta(minutes=5))
        memory_manager.add_item(db, user.id, kind="goal", text_="pick a commuter bike", owner="user",
                                epistemic="reported")
        db.commit()
        from app.ai.mind.hard import HardInput

        base = HardInput(problem=Problem("open", "x", "t", "q"), user="Test user")
        scope = Scope(user.id, "conversation", conversation.id)
        inp, token = mind_conversation.SOURCE.gather(db, scope, base.problem, base)
    assert "Which bike should I buy?" in inp.conversation[-1]["content"]
    assert "Their last message: 5 min ago." in inp.now
    assert "Still open (their open question): which bike should I buy?" in inp.now
    assert inp.goal == "pick a commuter bike" and "pick a commuter bike" in inp.memories
    assert token == conversation.last_message_sequence


def test_a_thought_is_dropped_when_its_promise_closes_and_outdated_when_they_write_again(db_env):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message = _say(db, conversation, "user", "remind me tomorrow", ago=timedelta(minutes=5))
        promise = memory_manager.note_commitment(db, user.id, "remind them tomorrow",
                                                 source=memory_manager.Source("message", message.id))
        db.commit()
        scope = Scope(user.id, "conversation", conversation.id)
        problem = mind_conversation._item_problem(promise)
        assert mind_conversation.SOURCE.dropped(db, scope, problem, None) is None
        memory_manager.resolve(db, user.id, promise.id, status="cancelled", reason="not needed",
                               source=memory_manager.Source("message", message.id))
        db.commit()
        assert mind_conversation.SOURCE.dropped(db, scope, problem, None) == "it's no longer open"
        token = conversation.last_message_sequence
        assert mind_conversation.SOURCE.outdated(db, scope, problem, token) == []
        _say(db, conversation, "user", "actually never mind", ago=timedelta(seconds=1))
        assert mind_conversation.SOURCE.outdated(db, scope, problem, token) == ["they wrote 1 new message(s) since"]


def test_student_two_sees_her_thoughts_about_this_conversation(db_env):
    with db_env.db() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        _say(db, conversation, "user", "I might quit piano.", ago=timedelta(minutes=3))
        db.add(MindThought(user_id=user.id, level="hard", text="They sound tired of practising, not of music.",
                           source="conversation", activity_id=conversation.id, conclusion_kind="guess"))
        db.add(MindThought(user_id=user.id, level="hard", text="About another chat.", source="conversation",
                           activity_id=uuid.uuid4()))
        db.commit()
        live = student_two.gather(db, user_id=user.id, conversation_id=conversation.id, trigger_message_id=None,
                                  message="what do you think?", zone="UTC")
    assert live.thought is not None and live.thought.text == "They sound tired of practising, not of music."
    assert live.earlier_thoughts == ()
