"""Events from the other parts of her (a thought of her own, a question from her slow thinking, a learning proposal)
wake her mind through the real durable coordinator and chat worker; she decides what, if anything, to say. Only the
model's run is scripted (``test_coordinator.env``). Execution fences are checked before the wake and again before
anything is stored: a newer user message, a settled premise, something forgotten, the same thing already said in
this quiet stretch, an expired event."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import ChatMessage, CoordinatorOutput, CoordinatorWork, DesktopEnvironment, DesktopSession
from app.services import autonomous_speech, coordinator
from app.services import chat as chat_service
from test_coordinator import drain, env, messages, outputs, run_job, says  # noqa: F401
from db_support import database, db_env  # noqa: F401


def propose(env, text="A completed autonomous thought", *, event=None, question=False, premise=None):
    with env.db() as db:
        result = autonomous_speech.submit(
            db, user_id=env.user.id, activity_id=env.conversation.id, source="conversation",
            event_id=event or uuid.uuid4(), text=text, question=question, premise=premise,
            reasons=["question: test" if question else "thought: test"], commentary_mode="occasional")
        assert result.startswith("Proposed")
        return db.scalar(select(CoordinatorWork).order_by(CoordinatorWork.created_at.desc()).limit(1))


def _said(env):
    return [content for role, content in messages(env) if role == "assistant"]


def write(env, text):
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        chat_service.add_message(db, conversation=conversation, role="user", content=text)


@pytest.mark.parametrize("choice", ["say", "silent"])
def test_a_thought_of_hers_wakes_her_mind_and_she_decides(env, choice):
    work = propose(env)
    drain(env)
    assert env.thoughts == []  # a thought already made: nothing is thought again

    async def decide(use, context):
        assert "A thought of your own" in context.wake and "A completed autonomous thought" in context.wake
        if choice == "silent":
            await use("stay_silent", reason="not now")
            return ""
        return "Something I was thinking about..."

    env.scripts.append(decide)
    assert run_job(env)
    [output] = outputs(env)
    if choice == "say":
        assert _said(env) == ["Something I was thinking about..."] and env.announced
        assert (output.decision, output.status) == ("said", "delivered")
    else:
        assert _said(env) == [] and not env.announced
        assert (output.decision, output.status) == ("silent", "not_sent")
    assert output.work_id == work.id
    drain(env)
    assert not run_job(env)  # once


def test_a_game_event_reaches_her_in_the_active_voice_conversation(env, monkeypatch):
    socket_id = uuid.uuid4()
    monkeypatch.setattr(autonomous_speech.voice_target, "current", lambda _: (socket_id, env.conversation.id))
    with env.db() as db:
        desktop = DesktopEnvironment(owner_user_id=env.user.id, display_name="Minecraft", hosting_kind="game_pack")
        db.add(desktop)
        db.flush()
        game = DesktopSession(owner_user_id=env.user.id, environment_id=desktop.id, profile_id="minecraft_pack",
                              profile_revision=1, state="running", max_duration_seconds=600)
        db.add(game)
        db.commit()
        assert game.conversation_id is None
        assert autonomous_speech.submit(
            db, user_id=env.user.id, activity_id=game.id, source="minecraft",
            event_id=uuid.uuid4(), text="I need wood first.", reasons=["light: need wood"],
            commentary_mode="quiet").startswith("Proposed")
        work = db.scalar(select(CoordinatorWork))
        assert work.conversation_id == env.conversation.id and work.input["voice_socket_id"] == str(socket_id)
    drain(env)

    async def decide(use, context):
        # The user's commentary setting reaches her as a fact, not as a rule that decided for her.
        assert "commentary setting for this game: quiet" in context.wake
        await use("stay_silent", reason="they asked for quiet")
        return ""

    env.scripts.append(decide)
    assert run_job(env)
    assert outputs(env)[0].decision == "silent"


def test_duplicate_events_are_one_wake_and_a_new_message_fences_it(env):
    event = uuid.uuid4()
    work = propose(env, event=event)
    assert propose(env, event=event).id == work.id
    drain(env)
    write(env, "Different topic now")
    assert coordinator.follow_up_still_valid(work.id) == (False, None)
    assert run_job(env)  # the wake job runs, finds it stale, and wakes nobody
    assert env.seen == [] and _said(env) == [] and not env.announced
    assert outputs(env)[0].status == "cancelled"


def test_a_new_message_while_she_words_it_keeps_it_unsaid(env):
    propose(env)
    drain(env)

    async def decide(use, context):
        write(env, "Stop, new topic")
        return "An obsolete thought"

    env.scripts.append(decide)
    assert run_job(env)
    assert _said(env) == [] and not env.announced and outputs(env)[0].status == "cancelled"


def test_an_expired_event_never_wakes_her(env):
    work = propose(env)
    with env.db() as db:
        db.get(CoordinatorWork, work.id).deadline_at = coordinator._now() - timedelta(seconds=1)
        db.commit()
    drain(env)
    assert not run_job(env)
    assert outputs(env)[0].decision == "rejected"


def test_in_chat_no_game_pacing_or_recent_reply_rule_holds_a_thought_back(env, monkeypatch):
    """A conversation's event: the user just spoke and the game rules would say quiet; neither decides for her."""
    monkeypatch.setattr(autonomous_speech.agent_say, "allowed", lambda *a, **k: (False, "quiet"))
    write(env, "hi")
    propose(env)
    drain(env)
    env.scripts.append(says("Oh, one more thing."))
    assert run_job(env) and env.announced and outputs(env)[0].status == "delivered"


# --- checked again before it's said ----------------------------------------------------------------------------


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


def test_a_question_about_a_settled_disagreement_never_wakes_her(env):
    from app.db.models import SemanticMemory
    from app.services import memory_conflicts

    conflict, fact = _conflict(env)
    propose(env, "Is Biscuit your dog, or Pretzel?", question=True, premise={"conflict": conflict})
    with env.db() as db:
        memory_conflicts.settle(db, db.get(SemanticMemory, fact), "known_confirmed", message_id=None)
        db.commit()
    drain(env)
    assert not run_job(env) and _said(env) == [] and outputs(env)[0].decision == "rejected"


def test_the_same_question_in_other_words_is_said_once_per_quiet_stretch(env):
    def ask_it(text):
        propose(env, text, question=True)
        drain(env)
        env.scripts.append(says(text))
        return run_job(env)

    assert ask_it("Is Biscuit your dog now, or did you mean Pretzel?") and len(_said(env)) == 1
    # Not again, though the words differ: dropped before her mind is even woken.
    assert not ask_it("Wait, I may have this mixed up: is Biscuit your dog, or did you mean Pretzel?")
    env.scripts.clear()
    assert len(_said(env)) == 1
    assert ask_it("Want to plan something for the weekend?") and len(_said(env)) == 2  # a different thing is fine
    write(env, "sorry, was away")  # the user writes: a new stretch
    assert ask_it("Is Biscuit your dog now, or did you mean Pretzel?") and len(_said(env)) == 3


def test_something_forgotten_since_it_was_thought_keeps_it_private(env):
    from app.db.models import MemoryForget

    work = propose(env, "How's your knee after the race?", question=True)
    with env.db() as db:
        conversation = chat_service.get_conversation(db, user_id=env.user.id, conversation_id=env.conversation.id)
        asked = chat_service.add_message(db, conversation=conversation, role="assistant", content="Sure.")
        db.add(MemoryForget(user_id=env.user.id, source_message_id=asked.id, conversation_id=env.conversation.id,
                            status="done", request=None, removed={"episode": 1}, finished_at=coordinator._now()))
        db.commit()
    drain(env)
    assert not run_job(env)
    with env.db() as db:
        assert "forgotten" in autonomous_speech.why_not(db, db.get(CoordinatorWork, work.id))
