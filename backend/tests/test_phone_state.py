"""Her phone as a source (services/phone_state.py, services/mind_phone.py): the store, what counts as news, the
tick rules for a source that sits unchanged for hours, and where her phone thoughts go. Fake Redis, no database,
no network."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.mind import gates as g
from app.core.config import get_settings
from app.schemas.phone import PhoneStateReport
from app.services import mind, mind_hard, mind_phone, phone_state, presence


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    def hset(self, key, field=None, value=None, mapping=None):
        target = self.hashes.setdefault(key, {})
        if mapping:
            target.update(mapping)
        if field is not None:
            target[field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hdel(self, key, *fields):
        for f in fields:
            self.hashes.get(key, {}).pop(f, None)

    def expire(self, key, seconds):
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start:] if end == -1 else self.lists.get(key, [])[start:end + 1]


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(presence, "_client", lambda: fake)
    return fake


USER = uuid.uuid4()
NOW = 1_800_000_000.0


def open_app(user=USER, *, visible=True, voice=False, now=NOW):
    presence.report(user, "phone-socket", client="phone", visible=visible, last_active_at=now, voice_live=voice,
                    now=now)


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #

def test_a_report_keeps_latest_values_and_when_they_began(redis):
    assert phone_state.report(USER, {"battery": 80, "charging": False}, now=NOW) == ["battery", "charging"]
    assert redis.lists[phone_state.MIND_WAKEUPS] == ["phone"]  # a change ticks her phone source at once
    # The same value again: no change, its start stays, its confirmation moves.
    assert phone_state.report(USER, {"charging": False}, now=NOW + 60) == []
    assert len(redis.lists[phone_state.MIND_WAKEUPS]) == 1
    state = phone_state.read(USER, now=NOW + 60)
    assert state.get("charging") is False and state.since("charging") == NOW
    assert state.values["charging"].as_of == NOW + 60
    # A real change starts again; a field the phone didn't send is left alone.
    assert phone_state.report(USER, {"charging": True}, now=NOW + 120) == ["charging"]
    state = phone_state.read(USER, now=NOW + 120)
    assert state.since("charging") == NOW + 120 and state.get("battery") == 80


def test_unknown_fields_are_ignored_and_a_late_older_observation_never_wins(redis):
    assert phone_state.report(USER, {"bogus": 1, "place": "home"}, now=NOW) == ["place"]
    late = datetime.fromtimestamp(NOW - 300, timezone.utc)  # seen before the report that's already in
    assert phone_state.report(USER, {"place": "work"}, observed_at=late, now=NOW + 10) == []
    assert phone_state.read(USER).get("place") == "home"
    # An observed_at in the future or more than a day old is taken as now.
    assert phone_state._observed(datetime.fromtimestamp(NOW + 99, timezone.utc), NOW) == NOW
    assert phone_state._observed(datetime.fromtimestamp(NOW - 2 * 86400, timezone.utc), NOW) == NOW


def test_explicit_null_clears_and_the_app_comes_from_presence(redis):
    phone_state.report(USER, {"place": "home"}, now=NOW)
    phone_state.report(USER, {"place": None}, now=NOW + 5)
    assert "place" in phone_state.read(USER).values and phone_state.read(USER).get("place") is None
    assert phone_state.read(USER, now=NOW).app == "closed"
    open_app(voice=True)
    state = phone_state.read(USER, now=NOW)
    assert state.app == "open" and state.voice is True
    open_app(visible=False)
    assert phone_state.read(USER, now=NOW).app == "background"


def test_conversation_and_zone_are_kept_and_users_age_out(redis, monkeypatch):
    conversation = uuid.uuid4()
    phone_state.report(USER, {}, conversation_id=conversation, zone="Asia/Jakarta", now=NOW)
    state = phone_state.read(USER)
    assert state.conversation_id == conversation and state.zone == "Asia/Jakarta" and state.reported_at == NOW
    monkeypatch.setattr(get_settings(), "phone_state_keep_hours", 24.0)
    assert phone_state.users(now=NOW + 3600) == [USER]
    assert phone_state.users(now=NOW + 25 * 3600) == []


def test_no_redis_means_unknown_not_nothing(monkeypatch):
    monkeypatch.setattr(presence, "_client", lambda: None)
    assert phone_state.report(USER, {"battery": 50}) == []
    assert phone_state.read(USER) is None and phone_state.users() == []


def test_describe_says_how_old_it_is(redis):
    phone_state.report(USER, {"battery": 18, "charging": False, "network": "wifi", "wifi_name": "Home",
                              "motion": "walking", "place": "home"}, now=NOW)
    text = "\n".join(phone_state.describe(phone_state.read(USER, now=NOW + 5), NOW + 5))
    assert "reported just now" in text and "Battery: 18%, not charging (for 5 s)" in text
    assert 'Wi-Fi "Home"' in text and "walking" in text and "Place: home" in text
    stale = "\n".join(phone_state.describe(phone_state.read(USER), NOW + 3600))
    assert "last reported 1 h ago: what it said may no longer be true" in stale
    assert phone_state.describe(None, NOW) == []


def test_the_report_schema_is_partial_and_strict():
    assert PhoneStateReport(battery=40).fields() == {"battery": 40}
    assert PhoneStateReport(place=None).fields() == {"place": None}
    with pytest.raises(ValidationError):
        PhoneStateReport(latitude=1.0)
    with pytest.raises(ValidationError):
        PhoneStateReport(battery=140)


# --------------------------------------------------------------------------- #
# What counts as news
# --------------------------------------------------------------------------- #

def snap(**over):
    base = {"app": "closed", "voice": False, "screen": "off", "locked": True, "battery": "ok", "charging": False,
            "headphones": False, "network": ("wifi", "Home"), "dnd": False, "ringer": "normal", "motion": "still",
            "place": "home", "part_of_day": "evening", "quiet": None}
    return {**base, **over}


def state_with(**values):
    state = phone_state.PhoneState()
    for name, value in values.items():
        state.values[name] = phone_state.Value(value, NOW, NOW)
    return state


def test_a_first_tick_is_the_baseline_not_news():
    assert mind_phone.news(snap(), None, state_with()) == []


def test_what_a_person_would_notice_is_news():
    state = state_with(battery=23, charging=True, place="home")
    lines = mind_phone.news(snap(app="open", locked=False, charging=True), snap(), state)
    assert lines == ["They opened the Jophiel app", "They unlocked their phone", "Plugged in to charge (battery 23%)"]
    assert mind_phone.news(snap(place=None), snap(place="home"), state_with(place=None)) == ["Left home"]
    assert mind_phone.news(snap(place="work"), snap(place=None), state_with(place="work")) == ["Arrived at work"]
    assert mind_phone.news(snap(quiet="3 h"), snap(quiet="1 h"), state) == ["No message from them for 3 h now"]
    assert mind_phone.news(snap(voice=True), snap(), state) == ["A voice call with you started"]


def test_a_number_moving_isnt_news_but_a_band_is():
    assert phone_state.battery_band(63, False) == phone_state.battery_band(62, False) == "ok"
    assert mind_phone.news(snap(), snap(), state_with(battery=62)) == []
    assert mind_phone.news(snap(battery="low"), snap(), state_with(battery=19)) == ["Battery low: 19%"]
    assert phone_state.battery_band(96, True) == "full" and phone_state.battery_band(4, False) == "almost empty"


def test_bands_for_the_day_and_the_quiet():
    assert [mind_phone.part_of_day(h) for h in (6, 13, 19, 2)] == ["morning", "afternoon", "evening", "night"]
    assert mind_phone.quiet_band(None) is None and mind_phone.quiet_band(30 * 60) is None
    assert mind_phone.quiet_band(2 * 3600) == "1 h" and mind_phone.quiet_band(13 * 3600) == "12 h"


# --------------------------------------------------------------------------- #
# Polling and ticking
# --------------------------------------------------------------------------- #

def test_poll_ticks_on_a_change_or_the_heartbeat(redis, monkeypatch):
    monkeypatch.setattr(get_settings(), "phone_tick_seconds", 60.0)
    clock = [NOW]
    monkeypatch.setattr("time.time", lambda: clock[0])
    source = mind_phone.PhoneSource()
    phone_state.report(USER, {"battery": 80}, now=NOW)
    assert [s.user_id for s, _ in source.poll(None)] == [USER]  # first sight
    clock[0] += 5
    assert source.poll(None) == []  # nothing changed, heartbeat not due
    open_app(now=clock[0])
    assert len(source.poll(None)) == 1  # the app opened: at once
    clock[0] += 61
    open_app(now=clock[0])
    assert len(source.poll(None)) == 1  # heartbeat


class Gate:
    def __init__(self, level="light"):
        self.level, self.calls = level, 0

    def decide(self, text, situation=None):
        self.calls += 1
        return g.GateResult("fake", self.level, probabilities={self.level: 1.0})


@pytest.fixture
def ticking(redis, monkeypatch, tmp_path):
    settings = get_settings()
    for name, value in (("mind_think_threshold", 0.7), ("mind_quick_gap_seconds", 4.0), ("mind_quiet_seconds", 25.0),
                        ("mind_log_path", str(tmp_path / "mind.jsonl")), ("mind_hard_quiet_seconds", 0)):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(mind.desktop_preview, "redis_client", lambda: None)
    quick = []
    monkeypatch.setattr(mind._pool, "submit", lambda fn, source, scope, level, text, at, thinker: (
        quick.append((level, text)), setattr(mind._minds[scope.activity], "quick_busy", False)))
    mind._minds.clear()
    mind_hard.reset()
    scope = mind_phone.scope_of(USER)

    def run(gate, at, **values):
        if values:
            phone_state.report(USER, values, now=at)
        monkeypatch.setattr(mind, "_gates", lambda: (gate, gate, {"react": None, "light": None}))
        return mind.tick(None, mind_phone.SOURCE, scope, phone_state.read(USER, now=at), at)

    return SimpleNamespace(run=run, quick=quick, scope=scope)


def test_nothing_new_costs_no_model_call_and_starts_no_quiet_thought(ticking):
    gate = Gate("light")
    first = ticking.run(gate, NOW, battery=80, charging=False)
    assert first["gate"] == "skipped" and gate.calls == 0  # the baseline
    for i in range(1, 4):  # an unchanged phone, long past the quiet-thought interval
        record = ticking.run(gate, NOW + i * 60)
        assert record["gate"] == "skipped" and record["action"] == "none"
    assert gate.calls == 0 and ticking.quick == []


def test_a_change_is_read_by_the_gate_and_a_thought_follows(ticking):
    gate = Gate("light")
    ticking.run(gate, NOW, battery=40, charging=False)
    record = ticking.run(gate, NOW + 10, charging=True)
    assert gate.calls == 1 and record["action"] == "light" and record["news"] == 1
    level, text = ticking.quick[-1]
    assert level == "light" and "NEW SINCE THE LAST MOMENT\n- Plugged in to charge (battery 40%)" in text
    assert "THEIR PHONE" in text and "TIME" in text


def test_a_react_becomes_a_light_thought_on_the_phone(ticking):
    gate = Gate("react")
    ticking.run(gate, NOW, place="home")
    record = ticking.run(gate, NOW + 10, place=None)
    assert record["level"] == "light"  # no body to react with


def test_a_thought_about_an_older_moment_is_seen_as_such(redis):
    phone_state.report(USER, {"charging": True}, now=NOW)
    token = {**mind_phone.snapshot(phone_state.read(USER)), "_user": USER}
    assert mind_phone.SOURCE.changed_since(None, token) == []
    phone_state.report(USER, {"charging": False}, now=NOW + 5)
    assert mind_phone.SOURCE.changed_since(None, token) == ["charging: True -> False"]


def test_the_phone_source_is_one_of_her_sources():
    assert mind_phone.SOURCE in mind.sources()
    assert mind_phone.activity_of(USER) == mind_phone.activity_of(USER) != mind_phone.activity_of(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Where her phone thoughts go, and what her mind's "Now" shows
# --------------------------------------------------------------------------- #

def test_her_phone_thoughts_go_to_the_phones_conversation(redis, monkeypatch):
    from app.services import autonomous_speech, chat as chat_service

    phone_conv, latest = uuid.uuid4(), uuid.uuid4()
    conversations = {phone_conv: SimpleNamespace(id=phone_conv, user_id=USER),
                     latest: SimpleNamespace(id=latest, user_id=USER)}
    db = SimpleNamespace(get=lambda model, key: conversations.get(key))
    monkeypatch.setattr(chat_service, "list_conversations", lambda db, user_id: [conversations[latest]])
    scope = mind_phone.scope_of(USER)
    # Never reported a conversation: their latest one.
    phone_state.report(USER, {"battery": 50}, now=NOW)
    assert autonomous_speech._target(db, user_id=USER, activity_id=scope.activity, source="phone")[0].id == latest
    phone_state.report(USER, {}, conversation_id=phone_conv, now=NOW + 1)
    assert autonomous_speech._target(db, user_id=USER, activity_id=scope.activity, source="phone")[0].id == phone_conv
    assert "phone" in autonomous_speech.CONVERSATIONAL  # the conversation fences apply (newer message, said already)


def test_her_minds_now_shows_the_phone(redis):
    from app.services import mind_turn

    ev = SimpleNamespace(user_id=USER)
    assert mind_turn._phone(ev, datetime.fromtimestamp(NOW, timezone.utc)) == []
    phone_state.report(USER, {"battery": 12, "charging": False}, now=NOW)
    lines = mind_turn._phone(ev, datetime.fromtimestamp(NOW + 120, timezone.utc))
    assert lines[0] == "- The phone last reported 2 min ago." and any("Battery: 12%" in line for line in lines)


def test_the_rest_endpoint_records_a_background_report(redis):
    from app.api.routes import phone as phone_route

    session = SimpleNamespace(user=SimpleNamespace(id=USER), token="t")
    out = phone_route.report_state(PhoneStateReport(screen="on", locked=False), session=session)
    assert out.changed == ["screen", "locked"]
    assert phone_state.read(USER).get("locked") is False


def test_a_phone_between_ticks_keeps_what_the_next_tick_compares_against(redis, monkeypatch):
    """Regression: the loop forgets activities a poll didn't return; her phone source returns only the due ones."""
    monkeypatch.setattr(get_settings(), "phone_tick_seconds", 60.0)
    clock = [NOW]
    monkeypatch.setattr("time.time", lambda: clock[0])
    source = mind_phone.PhoneSource()
    ticks = []

    class Db:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def rollback(self):
            pass

    monkeypatch.setattr(mind, "SessionLocal", Db)
    monkeypatch.setattr(mind, "prune", lambda db: 0)
    monkeypatch.setattr(mind, "_last_prune", clock[0])
    monkeypatch.setattr(mind, "sources", lambda: [source])
    monkeypatch.setattr(mind, "tick", lambda db, src, scope, payload, at: (
        ticks.append(scope), mind._minds.setdefault(scope.activity, mind.ActivityMind())))
    mind._minds.clear()
    phone_state.report(USER, {"battery": 80}, now=NOW)
    mind.run_once()
    assert len(ticks) == 1 and mind_phone.activity_of(USER) in mind._minds
    clock[0] += 5
    mind.run_once()  # not due: no tick, but her mind of it stays
    assert len(ticks) == 1 and mind_phone.activity_of(USER) in mind._minds
    monkeypatch.setattr(get_settings(), "phone_state_keep_hours", 1.0)
    clock[0] += 2 * 3600
    mind.run_once()  # the phone went silent past the keep window: now it's gone
    assert mind_phone.activity_of(USER) not in mind._minds
