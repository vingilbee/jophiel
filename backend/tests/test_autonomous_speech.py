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
    with env.db() as db:  # production cognition: Student 2 (scripted to keep it) read her own thought as [T1]
        output = db.scalar(select(CoordinatorOutput))
        assert (output.decision, output.decision_origin) == ("silent", "student_2")
    situation = env.calls["situations"][-1]
    assert "[T1] HER PRIVATE THOUGHT (conclusion, just finished; not said to anyone): A completed autonomous" in situation
    assert "WORK: none." in situation  # her own thought has no thinking work behind it


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


def test_an_expired_proposal_stays_private(env, monkeypatch):
    from datetime import timedelta
    work = propose(env, monkeypatch)
    with env.db() as db:
        db.get(CoordinatorWork, work.id).deadline_at = coordinator._now() - timedelta(seconds=1)
        db.commit()
    coordinator.execute_once()
    assert not _run_chat(env)
    with env.db() as db:
        assert db.scalar(select(CoordinatorOutput)).decision == "rejected"


@pytest.mark.parametrize("rule", ["quiet", "recent_user"])
def test_in_chat_only_student_two_decides_game_pacing_rules_do_not_hold_it_back(env, monkeypatch, rule):
    """A conversation's proposal (the user's decision, 2026-10-28): Student 2 alone decides whether it is said."""
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda _: ResultDecision("respond", "stub", True))
    propose(env, monkeypatch)
    if rule == "quiet":
        monkeypatch.setattr(autonomous_speech.agent_say, "allowed", lambda *a, **k: (False, "quiet"))
    else:
        monkeypatch.setattr(autonomous_speech.agent_say, "user_spoke_since", lambda *a: True)
    coordinator.execute_once()
    assert _run_chat(env) and env.announced
    with env.db() as db:
        assert db.scalar(select(CoordinatorOutput)).status == "delivered"


# --- checked again before it's said (plan stage 2) ----------------------------------------------------------


def _ask(env, monkeypatch, text, *, premise=None, event=None):
    monkeypatch.setattr(autonomous_speech.agent_say, "user_spoke_since", lambda *a: False)
    monkeypatch.setattr(autonomous_speech.agent_say, "allowed", lambda *a, **k: (True, ""))
    monkeypatch.setattr(autonomous_speech.desktop_preview, "redis_client", lambda: None)
    with env.db() as db:
        assert autonomous_speech.submit(
            db, user_id=env.user.id, activity_id=env.conversation.id, source="conversation",
            event_id=event or uuid.uuid4(), text=text, question=True, premise=premise,
            reasons=["question: test"], commentary_mode="occasional").startswith("Proposed")


def _said(env):
    with env.db() as db:
        return [m.content for m in db.scalars(select(ChatMessage).where(ChatMessage.role == "assistant")
                                              .order_by(ChatMessage.conversation_sequence))]


def _conflict(env):
    from app.db.models import MemoryConflict, SemanticMemory
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        said = chat_service.add_message(db, conversation=conversation, role="user", content="my dog Biscuit was muddy")
        fact = SemanticMemory(user_id=env.user.id, kind="fact", normalized_key="fact:dog",
                              content="The user's dog is named Pretzel.", importance=0.7, confidence=0.9)
        db.add(fact)
        db.flush()
        row = MemoryConflict(user_id=env.user.id, fact_id=fact.id, fact_revision=1, known_content=fact.content,
                             stated="dog is Biscuit", user_words="my dog Biscuit", source_message_id=said.id,
                             conversation_id=env.conversation.id)
        db.add(row)
        db.commit()
        return row.id, fact.id


def _mouth(monkeypatch):
    from app.workers import chat as worker

    async def mouth(**kwargs):
        decision = kwargs["context"].thought_result or ""
        yield {"type": "token", "content": decision.split("\n")[0][:120] or "said"}
    monkeypatch.setattr(worker, "stream_chat", mouth)


def test_a_question_about_a_settled_disagreement_is_never_asked(env, monkeypatch):
    from app.services import memory_conflicts
    from app.db.models import SemanticMemory
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda _: ResultDecision("ask", "stub", True))
    conflict, fact = _conflict(env)
    _ask(env, monkeypatch, "Is Biscuit your dog, or Pretzel?", premise={"conflict": conflict, "event": "x"})
    with env.db() as db:
        memory_conflicts.settle(db, db.get(SemanticMemory, fact), "known_confirmed", message_id=None)
        db.commit()
    coordinator.execute_once()
    assert not _run_chat(env) and _said(env) == []
    with env.db() as db:
        assert db.scalar(select(CoordinatorOutput)).decision == "rejected"


def test_the_same_question_in_other_words_is_asked_once_per_quiet_stretch(env, monkeypatch):
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda _: ResultDecision("ask", "stub", True))
    _mouth(monkeypatch)
    _ask(env, monkeypatch, "Is Biscuit your dog now, or did you mean Pretzel?")
    assert coordinator.execute_once() and _run_chat(env)
    assert len(_said(env)) == 1
    _ask(env, monkeypatch, "Wait, I may have this mixed up: is Biscuit your dog, or did you mean Pretzel?")
    coordinator.execute_once()
    assert not _run_chat(env) and len(_said(env)) == 1  # not again, though time passed and the words differ
    _ask(env, monkeypatch, "Want to plan something for the weekend?")  # a different thing is fine
    assert coordinator.execute_once() and _run_chat(env) and len(_said(env)) == 2
    with env.db() as db:  # the user writes: a new stretch
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        chat_service.add_message(db, conversation=conversation, role="user", content="sorry, was away")
    _ask(env, monkeypatch, "Is Biscuit your dog now, or did you mean Pretzel?")
    assert coordinator.execute_once() and _run_chat(env) and len(_said(env)) == 3


def test_something_forgotten_since_it_was_thought_keeps_it_private(env, monkeypatch):
    from app.db.models import MemoryForget
    monkeypatch.setitem(cognition_decisions.PROVIDERS, "result", lambda _: ResultDecision("respond", "stub", True))
    _ask(env, monkeypatch, "How's your knee after the race?")
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        asked = chat_service.add_message(db, conversation=conversation, role="assistant", content="Sure.")
        db.add(MemoryForget(user_id=env.user.id, source_message_id=asked.id, conversation_id=env.conversation.id,
                            status="done", request=None, removed={"episode": 1}, finished_at=coordinator._now()))
        db.commit()
    coordinator.execute_once()
    assert not _run_chat(env)
    with env.db() as db:
        work = db.scalar(select(CoordinatorWork).where(CoordinatorWork.kind == "mind"))
        assert "forgotten" in autonomous_speech.why_not(db, work)
