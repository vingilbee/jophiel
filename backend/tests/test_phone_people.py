"""Affinity B1 (docs/PHONE_PHASE_8_AFFINITY_B1.md, delivery check 1): people and conversations from notifications,
never merged, idempotent, a failed write never overwriting; her replies echoed back; wake bursts and the hourly cap;
``read_person``; forgetting a person. Real Redis and PostgreSQL (TEST_REDIS_URL, TEST_DATABASE_URL)."""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import get_settings
from app.db.models import (
    PhoneAffinity, PhoneConversation, PhoneConversationMember, PhoneOutbound, PhonePerson, PhonePersonKey,
    PhoneRelationshipEvidence,
)
from app.schemas.phone import PhoneNotificationsReport
from app.services import mind_phone, phone_notifications, phone_people, phone_state
from db_support import database  # noqa: F401
from test_phone_actions import channel, headers  # noqa: F401
from test_phone_phase8 import _forget_this_phone  # noqa: F401


def message(event: str, sender: str, sender_id: str, text: str, *, at: datetime | None = None,
            identity: str = "contact", in_contacts: bool | None = True) -> dict:
    out = {"event_id": event, "sender": sender, "sender_id": sender_id, "identity": identity, "text": text}
    if in_contacts is not None:
        out["in_contacts"] = in_contacts
    if at is not None:
        out["at"] = at.isoformat()
    return out


def owner(event: str, text: str, at: datetime | None = None) -> dict:
    out = {"event_id": event, "sender": "You", "sender_id": "owner", "text": text}
    if at is not None:
        out["at"] = at.isoformat()
    return out


def chat(key: str, conversation_id: str, title: str, messages: list[dict], *, revision: int = 1,
         group: bool = False) -> dict:
    return {"key": key, "revision": revision, "app": "WhatsApp", "package": "com.whatsapp", "title": title,
            "text": messages[-1]["text"] if messages else None, "conversation": title,
            "conversation_id": conversation_id, "conversation_identity": "shortcut", "is_group": group,
            "can_reply": True, "messages": messages}


def report(channel, *posted: dict, now: float | None = None):
    return phone_notifications.report(channel.user, PhoneNotificationsReport.model_validate({"posted": list(posted)}),
                                      now=now)


def counts(channel) -> dict[str, int]:
    with channel.db() as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                for model in (PhonePerson, PhonePersonKey, PhoneConversation, PhoneConversationMember, PhoneAffinity)}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Bursts off unless a test turns them on; one settings object every paced module reads."""
    state = {"settings": get_settings().model_copy(update={"phone_notification_burst_seconds": 0})}
    for module in (phone_notifications, mind_phone):
        monkeypatch.setattr(module, "get_settings", lambda: state["settings"])

    def configure(**updates):
        state["settings"] = state["settings"].model_copy(update=updates)

    return configure


BAND = [message("e_b1", "Rina", "p_rina", "rehearsal at 7"), message("e_b2", "Budi", "n_budi_band", "ok",
                                                                     identity="name", in_contacts=False)]
RINA_DM = chat("n_rina", "c_rina", "Rina", [message("e_r1", "Rina", "p_rina", "are you coming tonight?")])


# --------------------------------------------------------------------------- #
# Ingestion: who wrote, never merged, idempotent
# --------------------------------------------------------------------------- #

def test_the_same_group_snapshot_twice_changes_only_last_seen(channel):
    report(channel, chat("n_band", "c_band", "Band", BAND, group=True))
    with channel.db() as db:
        before = {p.display_name: p.last_seen_at for p in db.scalars(select(PhonePerson))}
    report(channel, chat("n_band", "c_band", "Band", BAND, group=True, revision=2))
    assert counts(channel) == {"phone_people": 2, "phone_person_keys": 2, "phone_conversations": 1,
                               "phone_conversation_members": 2, "phone_affinities": 2}
    with channel.db() as db:
        people = {p.display_name: p for p in db.scalars(select(PhonePerson))}
        assert set(people) == {"Rina", "Budi"} and all(p.names == [] for p in people.values())
        assert all(people[name].last_seen_at >= before[name] for name in before)
        assert people["Rina"].in_contacts is True and people["Budi"].in_contacts is False
        [conversation] = db.scalars(select(PhoneConversation))
        assert (conversation.title, conversation.is_group, conversation.app) == ("Band", True, "WhatsApp")
        affinities = db.scalars(select(PhoneAffinity)).all()
        assert {(a.familiarity, a.warmth, a.initiative, a.note) for a in affinities} == {("new", "neutral", "none", None)}


def test_a_contact_in_a_dm_and_a_group_is_one_person_two_strangers_called_budi_are_two(channel):
    report(channel, chat("n_band", "c_band", "Band", BAND, group=True), RINA_DM,
           chat("n_climb", "c_climb", "Climbing", [message("e_c1", "Budi", "n_budi_climb", "see you",
                                                           identity="name", in_contacts=False)], group=True))
    with channel.db() as db:
        people = db.scalars(select(PhonePerson)).all()
        assert sorted(p.display_name for p in people) == ["Budi", "Budi", "Rina"]
        rina = next(p for p in people if p.display_name == "Rina")
        rooms = db.scalars(select(PhoneConversation.title).join(
            PhoneConversationMember, PhoneConversationMember.conversation_id == PhoneConversation.id).where(
            PhoneConversationMember.person_id == rina.id)).all()
        assert sorted(rooms) == ["Band", "Rina"]
    # Who wrote, as the moment says it: Rina's handle, a contact, and the group she also shares with the user.
    lines = phone_notifications.news_lines(
        phone_notifications.pending_news(channel.user),
        people=phone_notifications.news_people(channel.user, phone_notifications.pending_news(channel.user)))
    [dm] = [line for line in lines if "from \"Rina\" (WhatsApp)" in line]
    assert f"[{phone_people.handle(rina.id)}, a contact, new, you two haven't spoken; also in \"Band\" group]" in dm
    [band] = [line for line in lines if 'the group "Band"' in line]
    assert '"Budi" [person:' in band and "not in their contacts" in band


def test_a_renamed_contact_keeps_the_old_name_and_a_reinstall_is_a_new_person(channel):
    report(channel, RINA_DM)
    report(channel, chat("n_rina", "c_rina", "Rina K", [message("e_r2", "Rina K", "p_rina", "hello?")], revision=2))
    # A reinstall: the phone's ids are new, so she can't tell it's the same Rina.
    report(channel, chat("n_rina2", "c_rina_new", "Rina K", [message("e_r3", "Rina K", "p_rina_new", "new phone")]))
    with channel.db() as db:
        people = db.scalars(select(PhonePerson).order_by(PhonePerson.first_seen_at)).all()
        assert [(p.display_name, p.names) for p in people] == [("Rina K", ["Rina"]), ("Rina K", [])]
    text = phone_people.read_person(channel.user, "rina k")
    assert text.startswith('2 people match "rina k": ask them which one they mean.')


def test_notifications_without_messages_and_owner_messages_make_no_person(channel):
    report(channel, {"key": "n_bank", "revision": 1, "app": "Bank", "title": "Payment", "text": "Rp 50.000 paid"},
           chat("n_mine", "c_mine", "Dewi", [owner("e_o1", "hi Dewi")]))
    assert counts(channel)["phone_people"] == 0


def test_a_failed_write_never_overwrites_and_the_moment_says_it_couldnt_look_them_up(channel, monkeypatch):
    report(channel, RINA_DM)
    with channel.db() as db:
        db.execute(update(PhoneAffinity).values(warmth="warm"))
        db.commit()
    phone_notifications.acknowledge_news(channel.user, phone_notifications.pending_news(channel.user))

    def down(*args, **kwargs):
        raise OperationalError("insert", {}, Exception("database down"))

    monkeypatch.setattr(phone_people, "_ingest_one", down)
    response = channel.client.post("/api/phone/notifications", headers=headers(channel.token), json={"posted": [
        chat("n_rina", "c_rina", "Rina", [message("e_r2", "Rina", "p_rina", "hello?")], revision=2),
        chat("n_ayu", "c_ayu", "Ayu", [message("e_a1", "Ayu", "p_ayu", "hi, I'm Ayu")])]})
    assert response.status_code == 200 and response.json() == {"posted": 2, "removed": 0}
    with channel.db() as db:
        assert db.scalar(select(PhoneAffinity.warmth)) == "warm"
        assert db.scalar(select(func.count()).select_from(PhonePerson)) == 1
    news = phone_notifications.pending_news(channel.user)
    lines = phone_notifications.news_lines(news, people=phone_notifications.news_people(channel.user, news))
    [ayu] = [line for line in lines if "Ayu" in line]
    assert '"Ayu" (WhatsApp) [couldn\'t look them up]' in ayu and "new" not in ayu.split("]")[1]
    # The records unreadable at all: everyone is "couldn't look them up", never "new".
    monkeypatch.setattr(phone_people, "SessionLocal", lambda: (_ for _ in ()).throw(down()))
    assert set(phone_notifications.news_people(channel.user, news).values()) == {"couldn't look them up"}


def test_labels_are_fixed_sets():
    assert phone_people.valid_labels(familiarity="close", warmth="fond", initiative="holding_back")
    assert not phone_people.valid_labels(warmth="loves")
    assert not phone_people.valid_labels(trust="high")


def test_the_database_refuses_a_label_outside_its_set(channel):
    report(channel, RINA_DM)
    with channel.db() as db, pytest.raises(IntegrityError):
        db.execute(update(PhoneAffinity).values(familiarity="best_friend"))
        db.flush()


# --------------------------------------------------------------------------- #
# Her replies echoed back
# --------------------------------------------------------------------------- #

def send(channel, text: str, *, status: str = "done", conversation: str = "c_rina") -> uuid.UUID:
    action_id = uuid.uuid4()
    assert phone_people.record_outbound(channel.user, action_id, key="n_rina", conversation_id=conversation,
                                        revision=1, text=text)
    phone_people.settle_outbound(action_id, status)
    return action_id


def outbound(channel, action_id: uuid.UUID) -> PhoneOutbound:
    with channel.db() as db:
        return db.get(PhoneOutbound, action_id)


def now_line(channel) -> str:
    """Rina's line in her "Now", without the note of what she just sent there (its own test)."""
    [line] = [line for line in phone_notifications.section(channel.user, with_text=True) if "Rina" in line]
    return re.sub(r' \(you sent ".*" here [^)]*\)$', "", line)


def test_her_reply_echoed_back_is_hers_and_the_users_own_words_are_not(channel):
    report(channel, RINA_DM)
    mine = send(channel, "It's Jophiel: he's on his way!")
    row = outbound(channel, mine)
    assert (row.status, row.asked_by, row.text) == ("sent", "user", "It's Jophiel: he's on his way!")
    with channel.db() as db:
        rina = db.scalar(select(PhonePerson.id))
    assert row.person_id == rina  # a direct chat with one person
    # The phone shows her reply as an owner message (spacing and case trimmed by the phone): hers.
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "it's jophiel:  he's on "
                                                                                          "his way!")], revision=2))
    assert outbound(channel, mine).echo_event_id == "e_o1"
    assert now_line(channel).endswith(", you answered since")
    # The user then writes something of their own: not hers.
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "It's Jophiel: he's on "
                                                                                          "his way!"),
                                                      owner("e_o2", "actually 10 min late")], revision=3))
    assert now_line(channel).endswith(", answered from the phone since")
    # And she has spoken with Rina now.
    news = phone_notifications.pending_news(channel.user)
    [rina_now] = phone_notifications.news_people(channel.user, news).values()
    assert "you've written to them 1 time, last" in rina_now and "haven't spoken" not in rina_now
    assert "'It's Jophiel: he's on his way!'" in phone_people.read_person(channel.user, "Rina")


def test_two_identical_sends_stay_ambiguous_and_an_unknown_send_that_echoes_becomes_sent(channel):
    report(channel, RINA_DM)
    first, second = send(channel, "ok!"), send(channel, "ok!")
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "ok!")], revision=2))
    assert outbound(channel, first).echo_event_id is None and outbound(channel, second).echo_event_id is None
    assert now_line(channel).endswith(", answered from the phone since")

    unsure = send(channel, "see you there", status="unconfirmed")
    assert outbound(channel, unsure).status == "unknown"
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o3", "see you there")],
                         revision=3))
    row = outbound(channel, unsure)
    assert (row.status, row.echo_event_id) == ("sent", "e_o3")
    # A late "failed" can't undo an echo that showed it arrived.
    phone_people.settle_outbound(unsure, "failed")
    assert outbound(channel, unsure).status == "sent"


def test_an_owner_message_seen_before_her_send_is_never_matched_later(channel):
    """The user's own "ok" (no ``at`` from the phone) is in the snapshot before she sends "ok": every later
    snapshot still shows it, and it is never taken for her reply. Hers, newly shown, is."""
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "ok")]))
    mine = send(channel, "ok")
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "ok")], revision=2))
    assert outbound(channel, mine).echo_event_id is None
    assert now_line(channel).endswith(", answered from the phone since")
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "ok"), owner("e_o2", "ok")],
                         revision=3))
    assert outbound(channel, mine).echo_event_id == "e_o2"
    assert now_line(channel).endswith(", you answered since")


def test_two_snapshots_of_one_notification_in_one_report_each_keep_their_new_owner_messages(channel):
    """The phone batches revisions 2 and 3 of one key into one report: her echo first shown in revision 2 is
    still matched, though revision 3 (compared against 2) doesn't show it as new."""
    report(channel, RINA_DM)
    mine = send(channel, "hi Rina")
    report(channel,
           chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "hi Rina")], revision=2),
           chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "hi Rina"),
                                             owner("e_o2", "see you at 7")], revision=3))
    assert outbound(channel, mine).echo_event_id == "e_o1"


def test_an_echo_too_late_or_a_failed_send_is_never_hers(channel):
    report(channel, RINA_DM)
    failed = send(channel, "on my way", status="failed")
    late = send(channel, "running late")
    with channel.db() as db:
        db.execute(update(PhoneOutbound).where(PhoneOutbound.id == late).values(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
        db.commit()
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"], owner("e_o1", "on my way"),
                                                      owner("e_o2", "running late")], revision=2))
    assert outbound(channel, failed).echo_event_id is None and outbound(channel, late).echo_event_id is None


def test_what_she_just_sent_is_beside_the_news_and_the_notification_it_shows_in(channel):
    """Her own words beside the line, so a message of hers coming back (however the phone labelled it) reads as
    hers: the latest send into that conversation only, failed ones and old ones left out."""
    report(channel, RINA_DM)
    send(channel, "on my way", status="failed")
    send(channel, "older one")
    send(channel, "Okay.")
    # Her "Okay." shown back under a sender the phone didn't mark as the user (2026-11-24: WhatsApp's "You").
    report(channel, chat("n_rina", "c_rina", "Rina", [*RINA_DM["messages"],
                                                      message("e_y1", "You", "p_you", "Okay.", identity="name")],
                         revision=2))
    news = phone_notifications.pending_news(channel.user)
    lines = phone_notifications.news_lines(news, sent=phone_notifications.news_sent(channel.user, news))
    assert re.search(r'"Okay\." \(you sent "Okay\." here \d+ s ago\)$', lines[-1]), lines[-1]
    assert "on my way" not in lines[-1] and "older one" not in lines[-1]
    assert any('(you sent "Okay." here' in line for line in phone_notifications.section(channel.user, with_text=True))
    # Another conversation, and a send long ago: nothing beside them.
    assert phone_people.recent_sends(channel.user, ["c_other"], at=time.time()) == {}
    assert phone_people.recent_sends(channel.user, ["c_rina"], at=time.time() + 3600) == {}


def test_a_reply_sent_through_the_phone_is_recorded_and_settled(channel, monkeypatch):
    from app.ai.tools import phone as tools
    from app.services import fcm
    from test_phone_actions import receive, wait_for_phone
    from test_phone_phase8 import run_action

    monkeypatch.setattr(fcm, "send", lambda *args, **kwargs: pytest.fail("The action reached a live socket"))
    from app.services import phone_reply_check

    monkeypatch.setattr(phone_reply_check, "_judge", lambda system, user: {"send": True})
    report(channel, RINA_DM)
    action, *_ = tools.build_phone_tools(channel.user, channel.conversation, request="tell Rina hi")
    with channel.client.websocket_connect("/api/chat/ws", headers=headers(channel.token)) as ws:
        receive(ws, "history")
        wait_for_phone(channel.user)
        frame, _ = run_action(channel, ws, action.ainvoke({
            "action": "reply_notification", "notification": phone_notifications.ref("n_rina"), "text": "hi Rina"}),
            {"status": "done"})
    row = outbound(channel, uuid.UUID(frame["action_id"]))
    assert (row.status, row.text, row.notification_key, row.conversation_id, row.asked_by) == (
        "sent", "hi Rina", "n_rina", "c_rina", "user")


# --------------------------------------------------------------------------- #
# Wake bursts and the hourly cap
# --------------------------------------------------------------------------- #

def poll(channel, source):
    with channel.db() as db:
        rows = [row for row in source.poll(db) if row[0].user_id == channel.user]
        if not rows:
            return None
        [(scope, payload)] = rows
        moment = source.moment(db, scope, payload, None, time.time(), thoughts=[], thinking=None)
    source.after_tick(scope, moment, 0, {})
    return moment


def burst(start: int, count: int, sender="Budi", sender_id="n_budi") -> list[dict]:
    return [message(f"e_{n}", sender, sender_id, f"line {n}", identity="name", in_contacts=False)
            for n in range(start, start + count)]


def test_ten_messages_in_a_burst_give_one_tick_with_all_ten(channel, _settings):
    _settings(phone_notification_burst_seconds=20)
    source = mind_phone.PhoneSource()
    t = time.time()
    # Ten lines from one chat (five per notification snapshot, as the phone sends them) within 20 s.
    report(channel, chat("n_band", "c_band", "Band", burst(0, 5), group=True), now=t)
    report(channel, chat("n_band", "c_band", "Band", burst(5, 5), group=True, revision=2), now=t + 8)
    assert poll(channel, source) is None  # still bursting: no tick yet, the news waits
    assert len(phone_notifications.pending_news(channel.user)) == 2
    assert phone_notifications.ready(phone_notifications.pending_news(channel.user), now=t + 8 + 21) == 2

    # The same burst, quiet for 20 s now: one tick reads all ten.
    for key in (phone_notifications._news_key(channel.user),):
        channel.bus.delete(key)
    report(channel, chat("n_band2", "c_band2", "Band 2", burst(10, 5), group=True), now=t - 40)
    report(channel, chat("n_band2", "c_band2", "Band 2", burst(15, 5), group=True, revision=2), now=t - 30)
    moment = poll(channel, source)
    said = " ".join(moment.fresh)
    assert all(f'"line {n}"' in said for n in range(10, 20))
    assert phone_notifications.pending_news(channel.user) == []
    assert poll(channel, source) is None


def test_a_chat_that_never_goes_quiet_is_still_read_and_removals_dont_wait_for_nothing():
    now = 1000.0
    busy = [{"kind": "posted", "conversation_id": "c", "at": now - 70 + 5 * n} for n in range(14)]
    assert phone_notifications.ready(busy, now=now, burst=20) == len(busy)  # oldest waited 3 bursts
    fresh = [{"kind": "removed", "key": "x", "at": now}, {"kind": "posted", "conversation_id": "c", "at": now - 1},
             {"kind": "posted", "conversation_id": "d", "at": now - 50}]
    assert phone_notifications.ready(fresh, now=now, burst=20) == 1  # a prefix: "d" waits behind "c"


def test_a_full_queue_says_how_much_went_unread_and_acknowledges_only_what_she_was_told(channel, monkeypatch):
    monkeypatch.setattr(phone_notifications, "NEWS_KEEP", 3)
    for n in range(5):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    news = phone_notifications.pending_news(channel.user)
    assert [entry["kind"] for entry in news] == ["missed", "posted", "posted", "posted"]
    assert news[0]["count"] == 2
    assert phone_notifications.news_lines(news[:1])[0].startswith("2 older notification updates on their phone "
                                                                  "went unread")
    # One more report trims n_2, which she had already read: not missed. What she was told is subtracted.
    report(channel, chat("n_5", "c_5", "Chat 5", burst(50, 1, sender_id="p_5")))
    phone_notifications.acknowledge_news(channel.user, news)
    later = phone_notifications.pending_news(channel.user)
    assert [(entry["kind"], entry.get("key")) for entry in later] == [("posted", "n_5")]
    # Missed after she read the count stays for her next moment.
    for n in (6, 7, 8):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    later = phone_notifications.pending_news(channel.user)
    assert [(entry["kind"], entry.get("count")) for entry in later] == [("missed", 1), *[("posted", None)] * 3]
    phone_notifications.acknowledge_news(channel.user, later)
    assert phone_notifications.pending_news(channel.user) == []


def test_the_queue_and_the_missed_count_are_read_together(channel, monkeypatch):
    """A report trimming the queue between the two reads would show an item and count it missed too: both are
    read in one MULTI/EXEC, so a trim lands wholly before or after."""
    monkeypatch.setattr(phone_notifications, "NEWS_KEEP", 2)
    for n in range(2):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    calls = []
    real = channel.bus.pipeline

    def pipeline(*args, **kwargs):
        pipe = real(*args, **kwargs)
        calls.append(pipe)
        return pipe

    monkeypatch.setattr(channel.bus, "pipeline", pipeline)
    news = phone_notifications.pending_news(channel.user)
    assert len(calls) == 1 and calls[0].transaction  # one MULTI/EXEC, not two separate reads
    monkeypatch.setattr(channel.bus, "pipeline", real)
    report(channel, chat("n_2", "c_2", "Chat 2", burst(20, 1, sender_id="p_2")))  # trims n_0, counts it
    shown = [entry.get("key") for entry in news]
    assert shown == ["n_0", "n_1"]
    after = phone_notifications.pending_news(channel.user)
    assert [(e["kind"], e.get("count"), e.get("key")) for e in after] == [
        ("missed", 1, None), ("posted", None, "n_1"), ("posted", None, "n_2")]


def test_an_item_she_read_then_a_full_queue_trimmed_is_not_missed(channel, monkeypatch):
    monkeypatch.setattr(phone_notifications, "NEWS_KEEP", 2)
    for n in range(2):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    news = phone_notifications.pending_news(channel.user)  # her tick reads n_0 and n_1
    report(channel, chat("n_2", "c_2", "Chat 2", burst(20, 1, sender_id="p_2")))  # trims n_0 before her ack
    phone_notifications.acknowledge_news(channel.user, news)
    assert [e.get("key") for e in phone_notifications.pending_news(channel.user)] == ["n_2"]  # no "missed"
    # One she never read is still counted.
    for n in (3, 4):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    after = phone_notifications.pending_news(channel.user)
    assert [(e["kind"], e.get("count"), e.get("key")) for e in after] == [
        ("missed", 1, None), ("posted", None, "n_3"), ("posted", None, "n_4")]
    phone_notifications.acknowledge_news(channel.user, after)
    assert phone_notifications.pending_news(channel.user) == []


def test_a_tick_still_in_flight_after_many_trims_keeps_what_it_read(channel, monkeypatch):
    """However much a full queue trims before her tick acknowledges, what that tick read is taken back out of the
    missed count, and the queue's marks stay a constant size."""
    monkeypatch.setattr(phone_notifications, "NEWS_KEEP", 2)
    for n in range(2):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    news = phone_notifications.pending_news(channel.user)  # her tick reads n_0, n_1
    for n in range(2, 12):  # ten more: n_0 and n_1 trimmed, then eight she never read
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    marks = phone_notifications._marks_key(channel.user)
    assert channel.bus.hlen(marks) <= 3
    assert int(channel.bus.hget(marks, "missed")) == 10
    phone_notifications.acknowledge_news(channel.user, news)
    after = phone_notifications.pending_news(channel.user)
    assert [(e["kind"], e.get("count")) for e in after] == [("missed", 8), ("posted", None), ("posted", None)]
    # Acknowledged again (a retry): nothing more taken back.
    phone_notifications.acknowledge_news(channel.user, news)
    assert phone_notifications.pending_news(channel.user)[0]["count"] == 8


def test_a_set_left_by_an_earlier_build_breaks_nothing(channel):
    legacy = phone_notifications.LEGACY_TRIMMED + str(channel.user)
    channel.bus.sadd(legacy, "old-id")
    report(channel, RINA_DM)
    assert len(phone_notifications.pending_news(channel.user)) == 1
    phone_notifications.acknowledge_news(channel.user, phone_notifications.pending_news(channel.user))
    assert phone_notifications.pending_news(channel.user) == []
    phone_notifications.report(channel.user, PhoneNotificationsReport.model_validate({"all_removed": True}))
    assert not channel.bus.exists(legacy)


def test_nothing_queued_before_a_switch_off_is_taken_back_out_of_the_missed_count(channel, monkeypatch):
    monkeypatch.setattr(phone_notifications, "NEWS_KEEP", 2)
    for n in range(3):  # n_0 trimmed unread
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    news = phone_notifications.pending_news(channel.user)
    phone_notifications.report(channel.user, PhoneNotificationsReport.model_validate({"all_removed": True}))
    for n in range(3, 6):  # back on: n_3 trimmed unread
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    # Back on, the queue holds the "cleared" line and n_3..n_5; with room for 2, "cleared" and n_3 go unread.
    phone_notifications.acknowledge_news(channel.user, news)  # what it read (and was told) went with the switch-off
    missed = phone_notifications.pending_news(channel.user)[0]
    assert (missed["kind"], missed["count"]) == ("missed", 2)


def test_acknowledging_the_missed_count_keeps_what_was_added_meanwhile(channel, monkeypatch):
    """An earlier build's count (3) is folded in; 2 more trimmed while she acknowledges make the transaction retry
    and stay for her next moment."""
    channel.bus.set(phone_notifications._missed_key(channel.user), 3, ex=600)
    [missed] = phone_notifications.pending_news(channel.user)
    assert missed["count"] == 3
    marks = phone_notifications._marks_key(channel.user)
    real = channel.bus.pipeline
    raced = []

    def pipeline():
        pipe = real()
        if not raced:
            raced.append(True)
            watch = pipe.watch

            def watch_then_race(*keys):
                watch(*keys)
                channel.bus.hincrby(marks, "missed", 2)
            pipe.watch = watch_then_race
        return pipe

    monkeypatch.setattr(channel.bus, "pipeline", pipeline)
    phone_notifications.acknowledge_news(channel.user, [missed])
    monkeypatch.setattr(channel.bus, "pipeline", real)
    assert not channel.bus.exists(phone_notifications._missed_key(channel.user))
    [later] = phone_notifications.pending_news(channel.user)
    assert later["count"] == 2
    phone_notifications.acknowledge_news(channel.user, [later])
    assert phone_notifications.pending_news(channel.user) == []


def test_acknowledging_the_same_tick_again_changes_nothing(channel, monkeypatch):
    """after_tick retried (a worker restart, a lost reply): the missed count she was told and the items she read
    that were trimmed since are applied once."""
    monkeypatch.setattr(phone_notifications, "NEWS_KEEP", 2)
    for n in range(4):  # n_0, n_1 trimmed unread
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    news = phone_notifications.pending_news(channel.user)  # told 2 missed; reads n_2, n_3
    assert news[0]["count"] == 2
    report(channel, chat("n_4", "c_4", "Chat 4", burst(40, 1, sender_id="p_4")))  # trims n_2: read, not missed
    for _ in range(3):
        phone_notifications.acknowledge_news(channel.user, news)
    assert [e.get("key") for e in phone_notifications.pending_news(channel.user)] == ["n_4"]
    # Two more trimmed unread (n_4 and n_5) after that: counted, and a late retry of the old tick doesn't eat them.
    for n in (5, 6, 7):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
    phone_notifications.acknowledge_news(channel.user, news)
    assert phone_notifications.pending_news(channel.user)[0]["count"] == 2


def test_the_hourly_cap_holds_news_and_drops_none(channel, _settings):
    _settings(phone_notification_ticks_per_hour=2)
    source = mind_phone.PhoneSource()
    phone_state.report(channel.user, {"battery": 80})
    assert poll(channel, source) is not None  # the baseline: not caused by a notification
    for n in range(2):
        report(channel, chat(f"n_{n}", f"c_{n}", f"Chat {n}", burst(n * 10, 1, sender_id=f"p_{n}")))
        assert poll(channel, source) is not None
    report(channel, chat("n_held", "c_held", "Held", burst(50, 1, sender_id="p_held")))
    assert poll(channel, source) is None  # past the cap: it waits
    assert len(phone_notifications.pending_news(channel.user)) == 1
    # The heartbeat reads it.
    snapshot, _ = source._seen[channel.user]
    source._seen[channel.user] = (snapshot, time.time() - get_settings().phone_tick_seconds - 1)
    moment = poll(channel, source)
    assert moment is not None and any('"line 50"' in line for line in moment.fresh)


# --------------------------------------------------------------------------- #
# "How do you feel about Rina?" and forgetting a person
# --------------------------------------------------------------------------- #

def test_read_person_one_several_none(channel):
    report(channel, RINA_DM, chat("n_band", "c_band", "Band", BAND, group=True),
           chat("n_climb", "c_climb", "Climbing", [message("e_c1", "Budi", "n_budi_climb", "see you",
                                                           identity="name", in_contacts=False)], group=True))
    rina = phone_people.read_person(channel.user, "Rina")
    assert '"Rina": a contact of theirs' in rina
    assert "familiarity new, warmth neutral, initiative none" in rina
    assert "Last direct exchange: none yet." in rina and "You haven't sent them anything." in rina
    assert "you two haven't really spoken" in rina
    assert '"Band" group' in rina and "a direct chat (WhatsApp)" in rina

    budi = phone_people.read_person(channel.user, "budi")
    assert budi.startswith('2 people match "budi": ask them which one they mean.')
    assert '"Band" group' in budi and '"Climbing" group' in budi

    assert phone_people.read_person(channel.user, "Sari").startswith('No one called "Sari"')
    # By the handle she was shown.
    with channel.db() as db:
        rina_id = db.scalar(select(PhonePerson.id).where(PhonePerson.display_name == "Rina"))
    assert phone_people.read_person(channel.user, phone_people.handle(rina_id)).startswith(
        f'[{phone_people.handle(rina_id)}] "Rina"')


def test_every_person_is_reachable_a_page_at_a_time_and_by_name(channel):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.routes import memory

    report(channel, *(chat(f"n_{n}", f"c_{n}", f"Friend {n}", [message(f"e_{n}", f"Friend {n}", f"p_{n}", "hi")])
                      for n in range(5)))
    app = FastAPI()
    app.include_router(memory.router, prefix="/api")
    with channel.db() as db:
        user = db.get(__import__("app.db.models", fromlist=["User"]).User, channel.user)
    app.dependency_overrides[deps.require_owner] = lambda: user
    app.dependency_overrides[deps.get_current_user] = lambda: user
    with TestClient(app) as client:
        seen, offset = [], 0
        while offset is not None:
            page = client.get("/api/memory/people", params={"limit": 2, "offset": offset}).json()
            assert page["total"] == 5 and len(page["people"]) <= 2
            seen += [p["display_name"] for p in page["people"]]
            offset = page["next_offset"]
        assert sorted(seen) == [f"Friend {n}" for n in range(5)]
        [found] = client.get("/api/memory/people", params={"q": "friend 3"}).json()["people"]
        assert found["display_name"] == "Friend 3"
        assert client.delete(f"/api/memory/people/{found['id']}").status_code == 204
        assert client.get("/api/memory/people").json()["total"] == 4


def test_forgetting_a_person_cascades_and_keeps_her_sent_text(channel):
    report(channel, RINA_DM, chat("n_band", "c_band", "Band", BAND, group=True))
    sent = send(channel, "hi Rina")
    with channel.db() as db:
        rina = db.scalar(select(PhonePerson.id).where(PhonePerson.display_name == "Rina"))
        db.add(PhoneRelationshipEvidence(user_id=channel.user, person_id=rina, event_id="e_r1", dimension="warmth",
                                         observation="she asked how I was"))
        db.commit()
        [entry] = [p for p in phone_people.inspect(db, channel.user)[0] if p["id"] == rina]
        assert entry["sent"] == 1 and entry["affinity"]["familiarity"] == "new"
        assert phone_people.delete_person(db, channel.user, rina)
        db.commit()
        assert not phone_people.delete_person(db, channel.user, rina)
        for model in (PhonePersonKey, PhoneAffinity, PhoneRelationshipEvidence):
            assert db.scalar(select(func.count()).select_from(model).where(model.person_id == rina)) == 0
        assert db.scalar(select(func.count()).select_from(PhoneConversationMember).where(
            PhoneConversationMember.person_id == rina)) == 0
        row = db.get(PhoneOutbound, sent)
        assert (row.text, row.person_id, row.conversation_id) == ("hi Rina", None, "c_rina")
        assert db.scalar(select(func.count()).select_from(PhonePerson)) == 1  # Budi stays
