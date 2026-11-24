"""One app's notifications looked up across the whole list (the WhatsApp incident: an alert ranked 28th of 31 was
on the phone, but the chat overview shows only the newest eight, so she said she couldn't see one).

Real Redis and PostgreSQL (TEST_REDIS_URL, TEST_DATABASE_URL), through the service and her tool."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.ai.tools import phone as tools
from app.schemas.phone import PhoneNotificationsReport
from app.services import phone_notifications, phone_state
from db_support import database  # noqa: F401
from test_phone_actions import Allow, channel  # noqa: F401


@pytest.fixture(autouse=True)
def _forget_this_phone(request):
    ns = request.getfixturevalue("channel") if "channel" in request.fixturenames else None
    yield
    if ns is not None:
        user = str(ns.user)
        ns.bus.hdel(phone_state.USERS_KEY, user)
        ns.bus.delete(phone_state._key(user), phone_notifications._key(user), phone_notifications._news_key(user),
                      phone_notifications._sense_key(user), phone_notifications._missed_key(user),
                      phone_notifications._marks_key(user))


def note(key: str, app: str, package: str, title: str, text: str) -> dict:
    return {"key": key, "revision": 1, "app": app, "package": package, "title": title, "text": text,
            "is_group": False, "can_reply": False, "messages": []}


def post(user, items: list[dict], at: float) -> None:
    phone_notifications.report(user, PhoneNotificationsReport.model_validate({"posted": items, "removed": []}),
                               now=at)


def thirty_one(channel) -> float:
    """31 alerts on the phone, one arriving each second; WhatsApp's is 28th newest (4th oldest)."""
    start = time.time() - 200
    for i in range(31):
        if i == 3:
            item = note("n_wa", "WhatsApp", "com.whatsapp", "Rina", "are you coming tonight?")
        else:
            item = note(f"n_{i}", "Gmail", "com.google.android.gm", f"Newsletter {i}", f"issue {i}")
        post(channel.user, [item], start + i)
    return start


def look(channel, app: str, authority=None) -> str:
    built = tools.build_phone_tools(channel.user, channel.conversation, message_id=None,
                                    authority=authority or Allow("Any WhatsApp notifications?"))
    check = next(t for t in built if t.name == "check_notifications")
    return asyncio.run(check.ainvoke({"app": app}))


def nothing_sent(channel) -> bool:
    """No phone action went out for this user: no pending action of theirs and no activity line."""
    import json

    from sqlalchemy import select

    from app.db.models import PhoneActivity

    pending = []
    for key in channel.bus.scan_iter("phone:action:pending:*"):
        raw = channel.bus.get(key)
        try:
            if raw and json.loads(raw).get("user") == str(channel.user):
                pending.append(key)
        except (ValueError, AttributeError):
            continue
    with channel.db() as db:
        lines = db.scalars(select(PhoneActivity).where(PhoneActivity.conversation_id == channel.conversation)).all()
    return not pending and not lines


def test_an_alert_below_the_overview_cutoff_is_found_by_its_app(channel):
    thirty_one(channel)
    every = phone_notifications.live(channel.user)
    assert len(every) == 31 and [n.key for n in every].index("n_wa") == 27  # 28th of 31, newest first
    overview = "\n".join(phone_notifications.section(channel.user, with_text=True))
    assert "WhatsApp" not in overview and "and 23 more" in overview  # the bounded overview is unchanged

    found = look(channel, "WhatsApp")
    assert found.startswith('1 notification from "WhatsApp" still on their phone, newest first (looked through all 31')
    assert "are you coming tonight?" in found and phone_notifications.THIRD_PARTY in found
    assert "Read only" in found and nothing_sent(channel)
    # By package too, and only that app's details reach the chat.
    by_package = look(channel, "com.whatsapp")
    assert "are you coming tonight?" in by_package and "Newsletter" not in by_package


def test_several_matches_come_newest_first_and_are_bounded(channel):
    start = thirty_one(channel)
    for i in range(7):
        post(channel.user, [note(f"n_wa{i}", "WhatsApp", "com.whatsapp", f"Friend {i}", f"message {i}")],
             start + 40 + i)
    found = look(channel, "whatsapp")
    assert found.startswith('8 notifications from "WhatsApp"')  # named as the app is, not as asked
    shown = [line for line in found.splitlines() if line.startswith("- [notif:")]
    assert len(shown) == phone_notifications.APP_SHOWN
    assert "message 6" in shown[0] and "message 5" in shown[1]  # newest first
    assert "- and 3 more from that app" in found


def test_once_it_is_gone_there_is_no_active_match(channel):
    thirty_one(channel)
    phone_notifications.report(channel.user, PhoneNotificationsReport.model_validate(
        {"posted": [], "removed": [{"key": "n_wa", "reason": "click"}]}))
    found = look(channel, "WhatsApp")
    assert found.startswith('Looked through all 30 notifications still on their phone: none from an app called '
                            '"WhatsApp"')
    assert "Can't check" not in found


def test_sharing_off_or_an_unreadable_store_means_she_cannot_check(channel, monkeypatch):
    thirty_one(channel)
    phone_notifications.report(channel.user, PhoneNotificationsReport.model_validate(
        {"posted": [], "removed": [], "all_removed": True}))
    off = look(channel, "WhatsApp")
    assert off.startswith("Can't check: their notifications aren't shared") and "none from" not in off
    assert phone_notifications.lookup_app(channel.user, "WhatsApp").status == "not_shared"

    monkeypatch.setattr(phone_notifications, "_client", lambda: None)
    down = look(channel, "WhatsApp")
    assert down.startswith("Can't check: their phone's notifications couldn't be read") and "none from" not in down


def test_the_lookup_is_bound_to_their_message_and_only_reads(channel):
    thirty_one(channel)
    asked = Allow("Any WhatsApp notifications?")
    look(channel, "WhatsApp", authority=asked)
    assert asked.asked == ['read the notifications from "WhatsApp" still on their phone (read only: nothing is '
                           "replied to, dismissed or sent)"]
    refused = look(channel, "WhatsApp", authority=Allow(allowed=False))
    assert refused.startswith("Not done") and "are you coming tonight?" not in refused
    assert nothing_sent(channel)


def test_her_own_turns_have_no_lookup(channel):
    built = tools.build_phone_tools(channel.user, channel.conversation, own_turn=True)
    assert "check_notifications" not in {t.name for t in built}


def test_a_name_that_only_contains_the_app_asked_about_is_never_read_out_as_it(channel):
    """Mail and Gmail both on the phone: asking about Mail reads Mail's alert only, labelled Mail."""
    start = time.time() - 50
    post(channel.user, [note("n_gm", "Gmail", "com.google.android.gm", "Bank", "your statement is ready")], start)
    post(channel.user, [note("n_mail", "Mail", "com.apple.mail", "Sam", "lunch tomorrow?")], start + 1)
    found = look(channel, "Mail")
    assert found.startswith('1 notification from "Mail" still on their phone')
    assert "lunch tomorrow?" in found and "statement" not in found and "Gmail" not in found
    gmail = look(channel, "gmail")
    assert "statement" in gmail and "lunch" not in gmail


def test_only_a_close_name_on_the_phone_gives_names_not_words(channel):
    post(channel.user, [note("n_gm", "Gmail", "com.google.android.gm", "Bank", "your statement is ready")],
         time.time() - 10)
    found = look(channel, "Mail")
    assert found.startswith('Looked through all 1 notification still on their phone: none from an app called "Mail"')
    assert '"Gmail" (1)' in found and "ask which they meant" in found
    assert "statement" not in found and "Bank" not in found  # nothing of the other app's is read out


@pytest.mark.parametrize(("app", "package", "asked", "matches"), [
    ("WhatsApp", "com.whatsapp", "WhatsApp", True),
    ("WhatsApp", "com.whatsapp", "whats app", True),
    ("WhatsApp", "com.whatsapp", "com.whatsapp", True),
    (None, "com.whatsapp", "WhatsApp", True),
    ("WhatsApp Business", "com.whatsapp.w4b", "WhatsApp", False),
    ("Gmail", "com.google.android.gm", "Mail", False),
    ("Gmail", "com.google.android.gm", "WhatsApp", False),
    ("Gmail", "com.google.android.gm", "gmail", True),
    ("Signal", "org.thoughtcrime.securesms", "a", False),
])
def test_matching_by_exact_app_name_or_package(app, package, asked, matches):
    entry = phone_notifications.Notification(
        key="k", revision=1, app=app, package=package, title=None, text=None, conversation=None,
        conversation_id=None, conversation_identity=None, is_group=False)
    assert phone_notifications.from_app(entry, asked) is matches


@pytest.mark.parametrize(("app", "package", "asked", "close"), [
    ("Gmail", "com.google.android.gm", "Mail", True),
    ("WhatsApp Business", "com.whatsapp.w4b", "WhatsApp", True),
    ("WhatsApp", "com.whatsapp", "WhatsApp", False),  # exact is a match, not a close name
    ("Gmail", "com.google.android.gm", "com", False),  # a package's prefix names nothing
])
def test_close_names(app, package, asked, close):
    entry = phone_notifications.Notification(
        key="k", revision=1, app=app, package=package, title=None, text=None, conversation=None,
        conversation_id=None, conversation_identity=None, is_group=False)
    assert phone_notifications.close_to(entry, asked) is close
