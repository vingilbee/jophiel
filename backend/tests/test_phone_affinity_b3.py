"""Affinity B3 (docs/PHONE_PHASE_8_AFFINITY_B3.md): her own replies to people on the phone. Step 1, the message that
woke her: only a strongly identified triggering exchange is answerable, every send goes through the B2 check bound
to that exchange, is checked again at the last send point and claimed once. Step 2, a follow-up she scheduled: kept
by stable ids, answerable when due only in the same conversation, to the same person, once per due occurrence.
Fictional exchanges, scripted check model, the phone's dispatch recorded. Real Redis and PostgreSQL
(TEST_REDIS_URL, TEST_DATABASE_URL)."""
from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import CoordinatorWork, PhoneOutbound
from app.schemas.phone import PhoneNotificationsReport
from app.services import coordinator, mind_turn, phone_actions, phone_notifications, phone_people, phone_replies
from app.services import phone_reply_check as RC
from db_support import db_env, database  # noqa: F401
from test_phone_actions import channel, headers  # noqa: F401
from test_phone_people import _settings, chat, message, owner, report  # noqa: F401
from test_phone_phase8 import _forget_this_phone  # noqa: F401
from test_phone_relationships import BAND_KEY, DM_KEY, band, env, ref, rina_dm  # noqa: F401

SARI_KEY = "n_sari"
PASS = {"send": True}


class Phone:
    """The dispatcher as the backend calls it: what reached the phone, settled the way ``act`` settles."""

    def __init__(self, result: str = "done"):
        self.frames: list[dict] = []
        self.result = result
        self.lock = threading.Lock()

    def __call__(self, user_id, conversation_id, action, args, *, target=None, view=None, asked_by="user",
                 action_id=None, claimed=False):
        with self.lock:
            self.frames.append({**args, "action": action, "asked_by": asked_by, "action_id": action_id,
                                "claimed": claimed, "session": target[0] if target else None})
        phone_people.settle_outbound(action_id, self.result)
        text = "Reply sent" if self.result == "done" else "Reply sent, no answer yet. It may or may not have happened."
        return phone_actions.Outcome(self.result, action_id, text)


class Judge:
    def __init__(self, verdict=PASS, during=None):
        self.verdict, self.during, self.calls = verdict, during, 0

    def __call__(self, system, user):
        self.calls += 1
        if self.during:
            self.during()
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


@pytest.fixture
def b3(env, monkeypatch):
    """The Messages switch on, one signed-in phone session, the dispatch recorded."""
    state = {"phone": {"messages_on": True}, "session": ("session-a", None)}
    monkeypatch.setattr(phone_replies.phone_state, "read", lambda user_id, **kw: state["phone"])
    monkeypatch.setattr(phone_replies.phone_actions, "target_for", lambda user_id: state["session"])
    phone = Phone()
    monkeypatch.setattr(phone_actions, "act", phone)
    return SimpleNamespace(env=env, state=state, phone=phone, user=env.user, chat=env.conversation)


def sari(*messages: dict, revision: int = 1) -> dict:
    return chat(SARI_KEY, "c_sari", "Sari", list(messages), revision=revision)


def targets(b3) -> list[dict]:
    news = phone_notifications.pending_news(b3.user)
    found = phone_replies.targets_from_news(news)
    phone_notifications.acknowledge_news(b3.user, news)
    return found


def answer(b3, handle: str, text: str, found: list[dict], judge=None) -> str:
    return phone_replies.reply(b3.user, b3.chat, handle, text, targets=found, model=judge or Judge())


def outbound(b3) -> list[PhoneOutbound]:
    with b3.env.db() as db:
        return list(db.scalars(select(PhoneOutbound).where(PhoneOutbound.asked_by == "her")
                               .order_by(PhoneOutbound.created_at)))


def remove(b3, key: str):
    phone_notifications.report(b3.user, PhoneNotificationsReport.model_validate({"removed": [{"key": key}]}))


# --------------------------------------------------------------------------- #
# Which exchanges a wake may answer
# --------------------------------------------------------------------------- #

def test_only_strongly_identified_triggering_messages_are_answerable(b3):
    titled = {**chat("n_title", "c_title", "Mo", [message("e_t1", "Mo", "p_mo", "hey")]),
              "conversation_identity": "title"}
    no_button = {**chat("n_quiet", "c_quiet", "Lu", [message("e_q1", "Lu", "p_lu", "hey")]), "can_reply": False}
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "hi Jophiel!")),
           band(message("e_b1", "Rina", "p_rina", "who's in?"),
                message("e_b2", "Rina", "n_rina_name", "me", identity="name", in_contacts=False)),
           titled, no_button)
    found = targets(b3)
    # The DM, bound to its own message; the group's newest speaker has a name-derived id (two Rinas could share it);
    # a title can't identify an audience; no reply button, no opportunity.
    assert [(t["handle"], t["event_id"], t["sender_id"]) for t in found] == [(ref(DM_KEY), "e_r1", "p_rina")]


def test_batched_dm_and_group_each_bound_to_its_own_message_and_one_cant_answer_the_other(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "hi Jophiel!")),
           band(message("e_b1", "Rina", "p_rina", "who's coming saturday?")))
    found = targets(b3)
    assert {(t["handle"], t["conversation_id"], t["event_id"]) for t in found} == {
        (ref(DM_KEY), "c_rina", "e_r1"), (ref(BAND_KEY), "c_band", "e_b1")}
    only_dm = [t for t in found if t["conversation_id"] == "c_rina"]
    said = answer(b3, ref(BAND_KEY), "I'm in!", only_dm)
    assert said.startswith("Not sent") and "isn't a message that woke you" in said
    assert b3.phone.frames == [] and outbound(b3) == []


def test_a_stranger_and_a_close_friend_get_the_same_permissions_and_one_exact_send_each(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "you're the best, Jophiel")),
           sari(message("e_s1", "Sari", "p_sari", "hello? who is this", identity="name", in_contacts=False)))
    found = targets(b3)
    assert {t["handle"] for t in found} == {ref(DM_KEY), ref(SARI_KEY)}
    draft = "Aw, thank you  Rina!\n\nThat made my evening."
    assert answer(b3, ref(DM_KEY), draft, found) == "Reply sent"
    assert answer(b3, ref(SARI_KEY), "Hi! I'm Jophiel, Vincent's AI companion.", found) == "Reply sent"
    sent = "Aw, thank you Rina! That made my evening."  # exactly the text the check read
    assert [f["text"] for f in b3.phone.frames] == [sent, "Hi! I'm Jophiel, Vincent's AI companion."]
    assert all(f["asked_by"] == "her" and f["claimed"] and f["action"] == "reply_notification"
               for f in b3.phone.frames)
    assert b3.phone.frames[0]["key"] == DM_KEY and b3.phone.frames[0]["revision"] == 1
    rows = outbound(b3)
    assert [(r.text, r.status, r.asked_by) for r in rows] == [
        (sent, "sent", "her"), ("Hi! I'm Jophiel, Vincent's AI companion.", "sent", "her")]
    assert rows[0].person_id is not None and rows[0].attempt_key == "reply:c_rina:e_r1"


# --------------------------------------------------------------------------- #
# The check, and what never reaches the phone
# --------------------------------------------------------------------------- #

def test_a_hold_an_error_or_an_oversized_draft_sends_nothing_and_nothing_is_cut(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "where's Vincent right now?")))
    found = targets(b3)
    held = answer(b3, ref(DM_KEY), "He's at the dentist!", found,
                  Judge(RC.Verdict(send=False, reveals=["where the user is"], why="it says where he is")))
    assert held.startswith("Held by the check") and "where the user is" in held
    broken = answer(b3, ref(DM_KEY), "hey!", found, Judge(RuntimeError("model down")))
    assert broken.startswith("Held by the check") and "couldn't run" in broken
    unused = Judge()
    long = answer(b3, ref(DM_KEY), "ha " * 1000, found, unused)
    assert long.startswith("Not sent") and "never cut" in long and unused.calls == 0
    assert b3.phone.frames == [] and outbound(b3) == []
    # A held draft keeps nothing: a fresh one can still pass, once.
    assert answer(b3, ref(DM_KEY), "I'll let him know you asked!", found) == "Reply sent"
    assert len(b3.phone.frames) == 1


def test_the_messages_switch_off_or_unknown_sends_nothing(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "hi!")))
    found = targets(b3)
    for phone in ({"messages_on": False}, {}, None):
        b3.state["phone"] = phone
        assert "Messages switch isn't on" in answer(b3, ref(DM_KEY), "hi Rina!", found)
    assert b3.phone.frames == [] and outbound(b3) == []


@pytest.mark.parametrize("change", ["pause", "remove", "revision", "speaker", "session", "authorization"])
def test_a_change_during_the_check_leaves_the_attempt_unsent_and_resuming_alone_sends_nothing(b3, change):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "where are you guys?")),
           band(message("e_b1", "Rina", "p_rina", "who's coming saturday?")))
    found = targets(b3)
    handle = ref(BAND_KEY) if change == "speaker" else ref(DM_KEY)

    def during():
        if change == "pause":
            b3.state["phone"] = {"messages_on": False}
        elif change == "remove":
            remove(b3, DM_KEY)
        elif change == "revision":
            report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "where are you guys?"),
                                   message("e_r2", "Rina", "p_rina", "nvm found you"), revision=2))
        elif change == "speaker":
            report(b3.env, band(message("e_b1", "Rina", "p_rina", "who's coming saturday?"),
                                message("e_b2", "Budi", "p_budi", "me!"), revision=2))
        elif change == "session":
            b3.state["session"] = ("session-b", None)
        elif change == "authorization":
            from app.services import phone_relationships
            phone_relationships.authorize_detail(b3.user, "Rina", "Vincent is at the library")

    said = answer(b3, handle, "Hey Rina!", found, Judge(during=during))
    why = {"pause": "Messages switch isn't on", "remove": "gone from their phone", "revision": "changed since",
           "speaker": "changed since", "session": "phone it would go through changed",
           "authorization": "what the user authorized"}[change]
    assert said.startswith("Not sent") and why in said, said
    assert b3.phone.frames == [] and outbound(b3) == []
    b3.state["phone"] = {"messages_on": True}
    b3.state["session"] = ("session-a", None)
    assert b3.phone.frames == []  # resuming flushes nothing: only a fresh decision could send


# --------------------------------------------------------------------------- #
# Exactly once
# --------------------------------------------------------------------------- #

def test_a_replayed_wake_and_two_concurrent_wakes_publish_once(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "hi!")))
    found = targets(b3)
    assert answer(b3, ref(DM_KEY), "Hi Rina!", found) == "Reply sent"
    again = answer(b3, ref(DM_KEY), "Hi Rina!", found)
    assert "already sent or attempted" in again
    report(b3.env, sari(message("e_s1", "Sari", "p_sari", "hello")))
    more = targets(b3)
    gate = threading.Barrier(2)
    results: list[str] = []

    def wake():
        gate.wait()
        results.append(answer(b3, ref(SARI_KEY), "Hello Sari!", more))

    threads = [threading.Thread(target=wake) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r == "Reply sent" for r in results) == [False, True]
    assert [f["text"] for f in b3.phone.frames] == ["Hi Rina!", "Hello Sari!"]
    assert len(outbound(b3)) == 2


def test_a_timeout_stays_unknown_and_a_late_success_settles_the_same_attempt(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "hi!")))
    found = targets(b3)
    b3.phone.result = "unconfirmed"
    assert "may or may not" in answer(b3, ref(DM_KEY), "Hi Rina!", found)
    [row] = outbound(b3)
    assert row.status == "unknown"
    assert "already sent or attempted" in answer(b3, ref(DM_KEY), "Hi Rina!", found)  # never resent blindly
    phone_people.settle_outbound(row.id, "done")  # the phone's late answer, for the same action id
    [row] = outbound(b3)
    assert row.status == "sent" and len(b3.phone.frames) == 1


def test_a_claimed_reply_is_settled_by_the_dispatcher_never_recorded_twice(env, monkeypatch):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hi!")))
    claim = phone_people.claim_outbound(env.user, "reply:c_rina:e_r1", key=DM_KEY, conversation_id="c_rina",
                                        revision=1, text="Hi Rina!", person_id=None)
    assert claim is not None
    assert phone_people.claim_outbound(env.user, "reply:c_rina:e_r1", key=DM_KEY, conversation_id="c_rina",
                                       revision=1, text="Hi Rina!", person_id=None) is None
    monkeypatch.setattr(phone_actions, "_client", lambda: None)
    outcome = phone_actions.act(env.user, env.conversation, "reply_notification",
                                {"key": DM_KEY, "conversation_id": "c_rina", "revision": 1, "text": "Hi Rina!"},
                                asked_by="her", action_id=claim, claimed=True)
    assert outcome.status == "failed" and outcome.action_id == claim
    with env.db() as db:
        rows = db.scalars(select(PhoneOutbound)).all()
    assert [(r.id, r.status) for r in rows] == [(claim, "failed")]


# --------------------------------------------------------------------------- #
# Step 2: a follow-up when it comes due
# --------------------------------------------------------------------------- #

def _schedule(b3, follow_up: dict, *, moves=None) -> CoordinatorWork:
    with b3.env.db() as db:
        row = coordinator.schedule_wake(
            db, user_id=b3.user, conversation_id=b3.chat, about="see how Rina's audition went",
            due_at=datetime.now(timezone.utc) + timedelta(days=2), phone_follow_up=None if moves else follow_up,
            moves=db.get(CoordinatorWork, moves) if moves else None, reason="after the weekend" if moves else None)
        db.commit()
        return row


def test_a_follow_up_keeps_who_and_where_by_stable_ids_through_a_reschedule(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "my audition is friday, wish me luck")))
    follow_up = phone_replies.follow_up_for(b3.user, ref(DM_KEY))
    assert set(follow_up) == {"person_id", "conversation_id", "is_group", "sender_id", "event_id"}
    assert follow_up["conversation_id"] == "c_rina" and DM_KEY not in str(follow_up)  # no handle as identity
    first = _schedule(b3, follow_up)
    assert first.input["phone_follow_up"] == follow_up and first.input["third_party"] is True
    moved = _schedule(b3, follow_up, moves=first.id)
    assert moved.input["phone_follow_up"] == follow_up and moved.input["third_party"] is True
    with pytest.raises(ValueError):
        phone_replies.follow_up_for(b3.user, "notif:00000000")


def test_a_due_follow_up_with_a_live_target_sends_a_fresh_reply_once_per_occurrence(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "my audition is friday, wish me luck")))
    follow_up = phone_replies.follow_up_for(b3.user, ref(DM_KEY))
    lines = phone_replies.follow_up_lines(b3.user, follow_up)
    assert any(f"[{ref(DM_KEY)}]" in line and "reply button" in line for line in lines)
    said = phone_replies.reply(b3.user, b3.chat, ref(DM_KEY), "How did the audition go?", follow_up=follow_up,
                               wake_id="w1", model=Judge())
    assert said == "Reply sent"
    again = phone_replies.reply(b3.user, b3.chat, ref(DM_KEY), "How did the audition go?", follow_up=follow_up,
                                wake_id="w1", model=Judge())
    assert "already sent or attempted" in again
    assert [r.attempt_key for r in outbound(b3)] == ["followup:w1"]


def test_a_due_follow_up_answered_expired_or_someone_else_talking_sends_nothing(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "audition friday!")),
           band(message("e_b1", "Rina", "p_rina", "who's coming saturday?")))
    dm = phone_replies.follow_up_for(b3.user, ref(DM_KEY))
    group = phone_replies.follow_up_for(b3.user, ref(BAND_KEY))
    # Answered from the phone since: nobody there she'd be answering.
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "audition friday!"), owner("e_o1", "good luck!"),
                           revision=2))
    said = phone_replies.reply(b3.user, b3.chat, ref(DM_KEY), "How did it go?", follow_up=dm, wake_id="w1",
                               model=Judge())
    assert said.startswith("Not sent") and "fresh chance" in said
    # Someone else wrote last in the group: a changed recipient.
    report(b3.env, band(message("e_b1", "Rina", "p_rina", "who's coming saturday?"),
                        message("e_b2", "Budi", "p_budi", "me!"), revision=2))
    assert phone_replies.reply(b3.user, b3.chat, ref(BAND_KEY), "Rina, how was it?", follow_up=group,
                               wake_id="w2", model=Judge()).startswith("Not sent")
    # Another conversation's notification can't stand in for this one.
    assert "another conversation" in phone_replies.reply(b3.user, b3.chat, ref(BAND_KEY), "hi", follow_up=dm,
                                                         wake_id="w3", model=Judge())
    # Expired: the notification is gone, so there's no reply button.
    remove(b3, DM_KEY)
    assert any("Nothing can be sent there now" in line for line in phone_replies.follow_up_lines(b3.user, dm))
    assert phone_replies.reply(b3.user, b3.chat, "", "How did it go?", follow_up=dm, wake_id="w4",
                               model=Judge()).startswith("Not sent")
    assert b3.phone.frames == [] and outbound(b3) == []


def test_a_request_for_space_reaches_the_check_on_a_follow_up(b3):
    from app.services import phone_relationships
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "please don't ask me about the audition")))
    phone_relationships.note_request_for_space(
        b3.user, ref(DM_KEY), about=[phone_relationships.event_handle("c_rina", "e_r1")],
        their_words="please don't ask me about the audition", everywhere=False, topic="the audition")
    follow_up = phone_replies.follow_up_for(b3.user, ref(DM_KEY))
    seen: list[str] = []

    def judge(system, user):
        seen.append(user)
        return RC.Verdict(send=False, reveals=["the audition"], why="she asked to leave it alone")

    said = phone_replies.reply(b3.user, b3.chat, ref(DM_KEY), "So, how did the audition go?", follow_up=follow_up,
                               wake_id="w1", model=judge)
    assert said.startswith("Held by the check") and "asked her to leave the audition alone" in seen[0]
    assert b3.phone.frames == []


# --------------------------------------------------------------------------- #
# Which turns get the tool
# --------------------------------------------------------------------------- #

def test_the_reply_tool_only_on_bound_wakes_and_never_with_device_actions(monkeypatch):
    import asyncio

    from app.ai import chat as ai_chat
    from app.ai.context import AgentRuntimeContext
    from app.ai.tools import phone as tools
    from app.core.config import get_settings

    def names(context, **update) -> set[str]:
        settings = get_settings().model_copy(update={"redis_url": "redis://unused", **update})
        monkeypatch.setattr(ai_chat, "get_settings", lambda: settings)
        monkeypatch.setattr(tools, "get_settings", lambda: settings)
        monkeypatch.setattr(ai_chat, "build_server_config", lambda connections: {})
        monkeypatch.setattr(ai_chat, "build_agent", lambda built, **kwargs: built)
        monkeypatch.setattr(ai_chat, "get_agent", lambda *args: [])

        async def run():
            async with ai_chat._orchestrator_for(context) as (built, _):
                return {t.name for t in built}
        return asyncio.run(run())

    target = {"handle": "notif:1a2b3c4d", "key": "k", "revision": 1, "conversation_id": "c", "is_group": False,
              "event_id": "e", "sender_id": "s"}
    bound = SimpleNamespace(conversation_id=None, model=None, reply_targets=[target], phone_follow_up=None,
                            wake_work_id="w")
    unbound = SimpleNamespace(**{**bound.__dict__, "reply_targets": []})
    theirs = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), wake="a notification",
                                 third_party=True, mind=bound)
    assert "reply_notification" in names(theirs)
    assert "phone_action" not in names(theirs)
    assert "reply_notification" not in names(replace(theirs, mind=unbound))
    own = replace(theirs, third_party=False)
    assert "reply_notification" not in names(own)
    reply = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), source_message_id=uuid.uuid4(),
                                mind=unbound)
    assert "reply_notification" not in names(reply)


def test_targets_are_read_only_on_the_notification_wake_itself():
    target = {"handle": "notif:1a2b3c4d"}
    follow_up = {"conversation_id": "c_rina", "person_id": str(uuid.uuid4())}

    def ev(kind, inp):
        return mind_turn.Evaluation(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "thought",
                                    wake={"kind": kind, "work_id": "w1", "input": inp})

    woke = ev("mind", {"autonomous": True, "third_party": True, "reply_targets": [target]})
    assert woke.reply_targets == [target] and woke.phone_follow_up is None
    # Thinking it started carries third_party but no targets; a due wake carries only its follow-up.
    assert ev("mind", {"third_party": True, "reply_targets": [target]}).reply_targets == []
    due = ev("wake", {"third_party": True, "phone_follow_up": follow_up, "reply_targets": [target]})
    assert due.reply_targets == [] and due.phone_follow_up == follow_up and due.wake_work_id == "w1"
    assert ev("mind", {"autonomous": True, "reply_targets": [target]}).reply_targets == []  # not someone's words


def test_reply_targets_reach_the_wake_input_only_with_someone_elses_words(env):
    from app.services import autonomous_speech
    target = {"handle": "notif:1a2b3c4d"}
    with env.db() as db:
        for event, third in (("e1", True), ("e2", False)):
            autonomous_speech.submit(db, user_id=env.user, activity_id=uuid.uuid4(), source="phone", event_id=event,
                                     text="Rina wrote", reasons=["light"], commentary_mode="occasional",
                                     third_party=third, reply_targets=[target])
        rows = db.scalars(select(CoordinatorWork).where(CoordinatorWork.kind == "mind")).all()
        by_event = {r.input["event_id"]: r.input for r in rows}
    assert by_event["e1"]["reply_targets"] == [target] and "reply_targets" not in by_event["e2"]
    with env.db() as db:
        assert db.scalar(select(func.count()).select_from(PhoneOutbound)) == 0


# --------------------------------------------------------------------------- #
# Review fixes (PR 50)
# --------------------------------------------------------------------------- #

def test_rescheduling_a_follow_up_keeps_its_recipient_and_refuses_another(b3):
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "audition friday!")),
           sari(message("e_s1", "Sari", "p_sari", "hello")))
    rina = phone_replies.follow_up_for(b3.user, ref(DM_KEY))
    first = _schedule(b3, rina)
    with b3.env.db() as db:
        with pytest.raises(ValueError, match="can't change who"):
            coordinator.schedule_wake(
                db, user_id=b3.user, conversation_id=b3.chat, about="say hi to Sari",
                due_at=datetime.now(timezone.utc) + timedelta(days=3), reason="later",
                moves=db.get(CoordinatorWork, first.id),
                phone_follow_up=phone_replies.follow_up_for(b3.user, ref(SARI_KEY)))
        db.rollback()
        assert db.get(CoordinatorWork, first.id).status != "cancelled"  # nothing moved
    # The same person named again (a newer message of hers) keeps the original record.
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "audition friday!"),
                           message("e_r2", "Rina", "p_rina", "so nervous"), revision=2))
    with b3.env.db() as db:
        moved = coordinator.schedule_wake(
            db, user_id=b3.user, conversation_id=b3.chat, about="see how it went",
            due_at=datetime.now(timezone.utc) + timedelta(days=3), reason="after the weekend",
            moves=db.get(CoordinatorWork, first.id), phone_follow_up=phone_replies.follow_up_for(b3.user, ref(DM_KEY)))
        db.commit()
    assert moved.input["phone_follow_up"] == rina


def test_a_newer_message_in_another_notification_of_the_conversation_unbinds_the_older_one(b3):
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    report(b3.env, rina_dm(message("e_r1", "Rina", "p_rina", "are you there?", at=t0)))
    older = targets(b3)
    report(b3.env, chat("n_rina_2", "c_rina", "Rina",
                        [message("e_r2", "Rina", "p_rina", "never mind, sorted", at=t0 + timedelta(minutes=2))]))
    newer = targets(b3)
    said = answer(b3, ref(DM_KEY), "I'm here!", older)
    assert said.startswith("Not sent") and "different exchange" in said
    assert b3.phone.frames == [] and outbound(b3) == []
    assert answer(b3, ref("n_rina_2"), "Glad it's sorted!", newer) == "Reply sent"
