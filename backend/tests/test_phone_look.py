"""She looks at the phone again after a turn whose hands acted there (services/phone_look.py): when a look is
scheduled, what drops it, and that only such a wake (never one someone else's words caused) has her hands."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.ai import chat as ai_chat
from app.ai.context import AgentRuntimeContext
from app.ai.tools import phone_control as control_tools
from app.core.config import get_settings
from app.db.models import ChatMessage, CoordinatorWork
from app.services import coordinator, phone_actions, phone_control, phone_look
from db_support import create_conversation, create_user, database  # noqa: F401


# --------------------------------------------------------------------------- #
# The end of a turn
# --------------------------------------------------------------------------- #


@pytest.fixture
def ended(monkeypatch):
    """Finish a turn's control synchronously, with the phone and the store faked; returns the looks scheduled."""
    scheduled, state = [], {"superseded": False}
    monkeypatch.setattr(phone_actions, "record_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_actions, "act", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "superseded", lambda *args, **kwargs: state["superseded"])
    monkeypatch.setattr(phone_look, "schedule", lambda **kwargs: scheduled.append(kwargs))

    def finish(**fields):
        control = control_tools.PhoneControl(uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4()),
                                             asked=fields.pop("asked", uuid.uuid4()), zone="America/Vancouver",
                                             steps=3, **fields)
        control._finish()
        return control

    return scheduled, state, finish


def test_a_turn_whose_hands_acted_schedules_a_look(ended):
    scheduled, _, finish = ended
    control = finish(acted=True, ended=False, answered=True)
    assert scheduled == [{"user_id": control.user_id, "conversation_id": control.conversation_id,
                          "turn": control.turn, "asked": control.asked, "zone": "America/Vancouver"}]


@pytest.mark.parametrize("why", ["only_read", "taken_over", "superseded", "no_message", "stopped"])
def test_no_look_after_reading_only_a_takeover_their_next_words_or_no_message(ended, why):
    """"stopped": their voice stopped the turn before their words were a message (seen live 2026-11-25: the look
    was booked in the two seconds before the transcript arrived)."""
    scheduled, state, finish = ended
    state["superseded"] = why == "superseded"
    finish(acted=why != "only_read", taken_over=why == "taken_over", ended=False, answered=why != "stopped",
           **({"asked": None} if why == "no_message" else {}))
    assert scheduled == []


# --------------------------------------------------------------------------- #
# Her hands on the wake
# --------------------------------------------------------------------------- #


def _registered(monkeypatch):
    settings = get_settings().model_copy(update={"redis_url": "redis://unused"})
    monkeypatch.setattr(ai_chat, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_chat, "build_server_config", lambda connections: {})
    monkeypatch.setattr(ai_chat, "build_agent", lambda tools, **kwargs: {tool.name for tool in tools})
    monkeypatch.setattr(ai_chat, "get_agent", lambda *args: set())
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_tools.PhoneControl, "finish", lambda self: None)

    async def registered(context):
        async with ai_chat._orchestrator_for(context) as (names, _):
            return names

    return lambda context: asyncio.run(registered(context))


def test_a_look_wake_has_her_hands_for_the_message_it_follows(monkeypatch):
    registered = _registered(monkeypatch)
    asked = uuid.uuid4()
    look = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), turn_id=uuid.uuid4(),
                               phone="Notifications: shared", wake="look again", phone_look=asked)
    assert set(control_tools.TOOL_NAMES) <= registered(look)
    assert look.phone_control.asked == asked and look.phone_control.turn == str(look.turn_id)
    # Not on any other wake, one someone else's words caused, or without a phone.
    for other in (replace(look, phone_look=None, phone_control=None),
                  replace(look, third_party=True, phone_control=None),
                  replace(look, phone=None, phone_control=None)):
        assert not set(control_tools.TOOL_NAMES) & registered(other)


def test_a_reply_turns_control_carries_the_users_message(monkeypatch):
    registered = _registered(monkeypatch)
    reply = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), source_message_id=uuid.uuid4(),
                                turn_id=uuid.uuid4(), phone="Notifications: shared", zone="Europe/Paris")
    registered(reply)
    assert reply.phone_control.asked == reply.source_message_id and reply.phone_control.zone == "Europe/Paris"


def test_the_wake_reads_what_they_asked_and_leaves_the_choice_to_her():
    ended_at = (datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat()
    info = {"work_id": str(uuid.uuid4()), "reason": "due",
            "input": {"origin": "phone_look:t", "look": {"text": "Okay, so, try to attack.", "ended_at": ended_at}}}
    text = coordinator.render_wake(info)
    assert "Okay, so, try to attack." in text and "read_phone_screen" in text and "stay_silent" in text
    assert "ended 12 s ago" in text or "ended 13 s ago" in text
    assert phone_look.asked_of({"origin": "phone_look:t", "look": {"asked": "nope"}}) is None
    assert phone_look.asked_of({"origin": "elsewhere", "look": {"asked": str(uuid.uuid4())}}) is None


# --------------------------------------------------------------------------- #
# Scheduling and validity (real PostgreSQL)
# --------------------------------------------------------------------------- #


@pytest.fixture
def talk(database):  # noqa: F811
    with database() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        message = ChatMessage(conversation_id=conversation.id, role="user", content="Okay, so, try to attack.")
        db.add(message)
        db.commit()
        return type("Talk", (), {"db": database, "user": user.id, "conversation": conversation.id,
                                 "message": message.id, "at": message.created_at})


def _looks(talk):
    with talk.db() as db:
        return list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.kind == "wake")
                               .order_by(CoordinatorWork.created_at)))


def test_a_look_is_a_wake_shortly_after_and_a_newer_one_replaces_it(talk):
    now = datetime.now(timezone.utc)
    first = phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t1",
                                asked=talk.message, now=now)
    second = phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t2",
                                 asked=talk.message, now=now)
    rows = {row.id: row for row in _looks(talk)}
    assert rows[first].status == "cancelled" and rows[second].status == "queued"
    work = rows[second]
    assert abs((work.next_attempt_at - now).total_seconds() - phone_look.LOOK_SECONDS) < 1
    assert work.input["origin"] == "phone_look:t2" and work.input["look"]["asked"] == str(talk.message)
    assert work.input["look"]["text"] == "Okay, so, try to attack." and not work.input.get("third_party")
    with talk.db() as db:
        assert phone_look.valid(db, db.get(CoordinatorWork, second))


def test_their_next_words_drop_the_look(talk):
    work_id = phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t", asked=talk.message)
    with talk.db() as db:
        db.add(ChatMessage(conversation_id=talk.conversation, role="user", content="Uh, please say it again."))
        db.commit()
        assert not phone_look.valid(db, db.get(CoordinatorWork, work_id))
    # And no new look follows that old message.
    assert phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t2",
                               asked=talk.message) is None


def test_the_window_from_their_message_bounds_the_chain(talk):
    at = talk.at if talk.at.tzinfo else talk.at.replace(tzinfo=timezone.utc)
    inside = at + timedelta(seconds=phone_look.WINDOW_SECONDS - phone_look.LOOK_SECONDS - 5)
    assert phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t", asked=talk.message,
                               now=inside) is not None
    late = at + timedelta(seconds=phone_look.WINDOW_SECONDS - phone_look.LOOK_SECONDS + 5)
    assert phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t2", asked=talk.message,
                               now=late) is None
    with talk.db() as db:
        work = db.scalar(select(CoordinatorWork).where(CoordinatorWork.kind == "wake"))
        assert not phone_look.valid(db, work, now=at + timedelta(seconds=phone_look.WINDOW_SECONDS + 1))


def test_a_look_never_follows_someone_elses_words(talk):
    work_id = phone_look.schedule(user_id=talk.user, conversation_id=talk.conversation, turn="t", asked=talk.message)
    with talk.db() as db:
        work = db.get(CoordinatorWork, work_id)
        work.input = {**work.input, "third_party": True}
        db.commit()
        assert not phone_look.valid(db, db.get(CoordinatorWork, work_id))
