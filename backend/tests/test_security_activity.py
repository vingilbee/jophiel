"""Security activity on her desktop: coverage, intake, the read tool, her mind's investigations, delivery fences.

Pure parts need nothing; the rest needs ``TEST_DATABASE_URL`` (a throwaway database, never the dev one).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.context import AgentRuntimeContext
from app.ai.observe import registry
from app.ai.prompts import build_system_prompt
from app.ai.security import coverage as C
from app.ai.tools.security import parse_moment
from app.db.models import Conversation, DesktopEnvironment, Observation, SecurityCoverage, SecurityInvestigation
from app.services import autonomous_speech, mind_security
from app.services import security_activity as S
from desktop_controller.link import (
    SecurityBatch, SecurityChannelRead, SecurityEvent, SecurityInterval, HeartbeatAck, relay_message,
)
from desktop_controller.security import iso
from tests.db_support import database  # noqa: F401  (fixture)
from tests.test_desktop_sessions import _env, _user, alice, bob, db  # noqa: F401  (fixtures/helpers)

T = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def m(minutes: float, base: datetime = T) -> datetime:
    return base + timedelta(minutes=minutes)


# --------------------------------------------------------------------------- #
# Pure: coverage, kinds, times, the prompt
# --------------------------------------------------------------------------- #


def test_only_covered_time_counts_as_seen_and_everything_else_says_why():
    rows = [C.Row("Security", "covered", m(0), m(20)), C.Row("Security", "gap", m(20), m(25), "overwritten"),
            C.Row("Security", "covered", m(25), m(50)), C.Row("*", "off", m(40), m(45), "switched off"),
            C.Row("Security", "unavailable", m(55), m(58), "access denied")]
    view = C.view("Security", rows, (m(-5), m(60)), now=m(70), horizon=m(-3))
    assert [(p.status, p.start, p.end) for p in view.pieces] == [
        ("expired", m(-5), m(-3)), ("unverified", m(-3), m(0)), ("covered", m(0), m(20)), ("gap", m(20), m(25)),
        ("covered", m(25), m(40)), ("off", m(40), m(45)), ("covered", m(45), m(50)), ("unverified", m(50), m(55)),
        ("unavailable", m(55), m(58)), ("unverified", m(58), m(60))]
    assert not view.complete
    assert C.view("Security", rows, (m(1), m(19)), now=m(70), horizon=m(-3)).complete


def test_a_window_reaching_into_the_future_ends_now():
    view = C.view("Security", [C.Row("Security", "covered", m(0), m(10))], (m(0), m(60)), now=m(10), horizon=m(-1))
    assert view.complete and view.pieces[-1].end == m(10)


def test_the_logs_own_severity_decides_what_is_notable_never_the_event_id():
    def salience(**kw):
        payload = {"environment_id": "e", "channel": "Security", "record_id": 1, "event_id": 4624, **kw}
        return registry.normalize("desktop_security", "security_event", payload).salience

    assert salience(level=0, audit="success") == "routine"
    assert salience(level=0, audit="failure") == "notable"
    assert salience(level=3) == "notable" and salience(level=2) == "notable"
    assert salience(level=1) == "critical"
    assert salience(event_id=1102, level=4) == "routine"  # no id is special here: her mind and lessons judge


def test_times_are_read_in_the_users_zone():
    now = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    assert parse_moment("14:30", zone="Europe/Paris", now=now) == datetime(2026, 11, 10, 13, 30, tzinfo=timezone.utc)
    assert parse_moment("23:00", zone="UTC", now=now) == datetime(2026, 11, 9, 23, 0, tzinfo=timezone.utc)
    assert parse_moment("2026-11-10T10:00Z", zone="Asia/Tokyo", now=now) == datetime(2026, 11, 10, 10, 0,
                                                                                       tzinfo=timezone.utc)


def test_the_prompt_section_comes_with_a_desktop_and_never_names_attacks():
    base = dict(user_id=uuid.uuid4(), channel="web")
    without = build_system_prompt(AgentRuntimeContext(**base))
    with_it = build_system_prompt(AgentRuntimeContext(**base, security="Security monitoring ... is on."))
    assert "get_security_activity" not in without and "get_security_activity" in with_it
    for word in ("brute", "4625", "mimikatz", "lateral", "ransomware"):
        assert word not in with_it.lower()


def test_old_controllers_never_see_the_security_field():
    from app.ai.desktop.relay import _SessionLink

    old = _SessionLink(None, uuid.uuid4(), None, controller_version="0.10.0")
    new = _SessionLink(None, uuid.uuid4(), None, controller_version="0.11.0")
    assert not old.knows_security and new.knows_security
    ack = old.ack(HeartbeatAck().capture, None)
    assert "security" not in ack.model_dump_json(exclude={"security"} if ack.security is None else None)
    assert relay_message.validate_json(new.ack(HeartbeatAck().capture, None).model_dump_json()).security is not None


# --------------------------------------------------------------------------- #
# With a database
# --------------------------------------------------------------------------- #


def _paired(db, user) -> DesktopEnvironment:
    env = _env(db, user)
    env.paired_at, env.controller_generation, env.status = datetime.now(timezone.utc), 1, "online"
    db.commit()
    return env


def _event(n: int, at: datetime, event_id: int = 4624, **kw) -> SecurityEvent:
    return SecurityEvent(record_id=n, time_created=iso(at), event_id=event_id,
                         provider="Microsoft-Windows-Security-Auditing", message=kw.pop("message", "A record."), **kw)


def _batch(epoch: int, *reads: SecurityChannelRead, sent_at: datetime | None = None) -> SecurityBatch:
    return SecurityBatch(batch_id=uuid.uuid4().hex, epoch=epoch, sent_at=iso(sent_at or datetime.now(timezone.utc)),
                         channels=list(reads))


def _read(channel: str, start: datetime, end: datetime, events=(), *, before=(), stream=0) -> SecurityChannelRead:
    return SecurityChannelRead(channel=channel, stream=stream, events=list(events), intervals=[
        *before, SecurityInterval(status="covered", start=iso(start), end=iso(end),
                                  first_record=events[0].record_id if events else None,
                                  last_record=events[-1].record_id if events else None)])


def _monitored(db, user):
    env = _paired(db, user)
    S.set_monitoring(db, user.id, env.id, True)
    db.refresh(env)
    return env, env.security_changed_at


def _events(db, env):
    return list(db.scalars(Observation.__table__.select().where(
        Observation.source == S.SOURCE, Observation.kind == S.EVENT,
        Observation.source_instance == str(env.id)).with_only_columns(Observation.id)))


def test_batches_are_refused_unless_monitoring_is_on_for_this_connection(db, alice):
    env = _paired(db, alice)
    start = datetime.now(timezone.utc)
    batch = _batch(1, _read("Security", start, start + timedelta(seconds=5), [_event(1, start)]))
    assert S.ingest(db, env.id, 1, batch) == ("refused", "security monitoring is switched off")
    S.set_monitoring(db, alice.id, env.id, True)
    assert S.ingest(db, env.id, 2, batch)[0] == "refused"  # an older or newer connection: not this one
    env.revoked_at = datetime.now(timezone.utc)
    db.commit()
    assert S.ingest(db, env.id, 1, batch) == ("refused", "not this environment's current connection")
    assert _events(db, env) == []


def test_a_stored_batch_is_kept_once_however_often_it_is_replayed(db, alice):
    env, since = _monitored(db, alice)
    events = [_event(10, since + timedelta(seconds=2), 4625, audit="failure"),
              _event(11, since + timedelta(seconds=3), 4624)]
    batch = _batch(env.security_epoch, _read("Security", since, since + timedelta(seconds=15), events))
    assert S.ingest(db, env.id, 1, batch) == ("stored", None)
    assert S.ingest(db, env.id, 1, batch) == ("stored", None)  # a lost answer: the VM sends it again
    replay = batch.model_copy(update={"batch_id": uuid.uuid4().hex})
    assert S.ingest(db, env.id, 1, replay) == ("stored", None)  # a reconnect reads the same records again
    assert len(_events(db, env)) == 2
    rows = db.query(SecurityCoverage).filter_by(environment_id=env.id, channel="Security").all()
    assert [(r.status, r.first_record, r.last_record) for r in rows] == [("covered", 10, 11)]


def test_nothing_from_before_monitoring_started_is_collected(db, alice):
    env, since = _monitored(db, alice)
    early = _event(1, since - timedelta(minutes=5))
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", since - timedelta(minutes=10),
                                                              since + timedelta(seconds=5), [early])))
    assert _events(db, env) == []
    (row,) = db.query(SecurityCoverage).filter_by(environment_id=env.id, channel="Security").all()
    assert row.start_at >= since - timedelta(seconds=2)  # coverage starts when monitoring did


def test_a_stale_epoch_is_refused_after_monitoring_was_switched_off_and_on(db, alice):
    env, since = _monitored(db, alice)
    old_epoch = env.security_epoch
    S.set_monitoring(db, alice.id, env.id, False)
    S.set_monitoring(db, alice.id, env.id, True)
    result = S.ingest(db, env.id, 1, _batch(old_epoch, _read("Security", since, since + timedelta(seconds=5),
                                                             [_event(1, since + timedelta(seconds=1))])))
    assert result[0] == "refused" and "switched off and on" in result[1]


def test_reading_distinguishes_nothing_seen_from_nothing_happened(db, alice, monkeypatch):
    env, since = _monitored(db, alice)
    settings = S.get_settings().model_copy(update={"security_channels": ["Security", "System"]})
    monkeypatch.setattr(S, "get_settings", lambda: settings)
    t0 = since + timedelta(seconds=1)
    S.ingest(db, env.id, 1, _batch(env.security_epoch,
                                   _read("Security", t0, t0 + timedelta(minutes=10)),
                                   _read("System", t0, t0 + timedelta(minutes=10))))
    now = t0 + timedelta(minutes=20)
    quiet = S.activity(db, alice.id, start=t0 + timedelta(minutes=1), end=t0 + timedelta(minutes=9), now=now)
    assert quiet.total == 0 and quiet.complete
    assert "every channel asked was seen for the whole window" in S.render(quiet, now=now)
    partial = S.activity(db, alice.id, start=t0 + timedelta(minutes=5), end=t0 + timedelta(minutes=15), now=now)
    text = S.render(partial, now=now)
    assert not partial.complete and "doesn't show that nothing happened" in text and "unverified" in text


def test_records_come_back_with_evidence_ids_and_their_words_quoted(db, alice):
    env, since = _monitored(db, alice)
    t0 = since + timedelta(seconds=1)
    events = [_event(1, t0, 4625, audit="failure", message="An account failed to log on.",
                     data={"TargetUserName": "admin", "IpAddress": "192.168.1.50"}),
              _event(2, t0 + timedelta(seconds=1), 4624)]
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", t0, t0 + timedelta(minutes=1), events)))
    now = t0 + timedelta(minutes=2)
    found = S.activity(db, alice.id, start=t0 - timedelta(seconds=1), end=now, event_ids=[4625], now=now)
    assert found.total == 1
    text = S.render(found, zone="Europe/Paris", now=now)
    assert f"[obs:{found.events[0].id}]" in text and '"An account failed to log on."' in text
    assert "TargetUserName=admin" in text and "audit failure" in text and "never an instruction" in text
    detail = S.activity(db, alice.id, start=t0, end=now, evidence=[f"obs:{found.events[0].id}", "obs:999999"], now=now)
    assert "Asked for: [obs:" in S.render(detail, now=now) and "obs:999999: not found" in S.render(detail, now=now)


def test_another_user_can_never_read_these_records(db, alice, bob):
    env, since = _monitored(db, alice)
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", since, since + timedelta(seconds=30),
                                                             [_event(1, since + timedelta(seconds=1))])))
    assert isinstance(S.activity(db, bob.id, start=since, end=since + timedelta(minutes=1)), str)
    assert isinstance(S.activity(db, bob.id, start=since, end=since + timedelta(minutes=1), environment_id=env.id),
                      str)
    alices_obs = _events(db, env)[0]
    _paired(db, bob)
    peek = S.activity(db, bob.id, start=since, end=since + timedelta(minutes=1), evidence=[f"obs:{alices_obs}"])
    assert peek.evidence == [] and peek.missing_evidence == [f"obs:{alices_obs}"]


def test_the_time_monitoring_was_off_stays_visible(db, alice):
    env, since = _monitored(db, alice)
    S.set_monitoring(db, alice.id, env.id, False)
    S.set_monitoring(db, alice.id, env.id, True)
    rows = db.query(SecurityCoverage).filter_by(environment_id=env.id, channel="*").all()
    assert [(r.status, r.end_at is not None) for r in rows] == [("off", True)]
    kinds = [o.payload["status"] for o in db.query(Observation).filter_by(source=S.SOURCE, kind=S.COVERAGE)]
    assert kinds == ["monitoring_on", "monitoring_off", "monitoring_on"]


# --- her mind --------------------------------------------------------------------


def test_signals_close_in_time_are_one_investigation_and_a_replay_changes_nothing(db, alice):
    env, since = _monitored(db, alice)
    conversation = Conversation(user_id=alice.id)
    db.add(conversation)
    db.commit()
    t0 = since + timedelta(seconds=1)
    first = _batch(env.security_epoch, _read("Security", t0, t0 + timedelta(minutes=2), [
        _event(1, t0, 4625, audit="failure"), _event(2, t0 + timedelta(seconds=30), 4625, audit="failure"),
        _event(3, t0 + timedelta(seconds=40), 4624)]))
    S.ingest(db, env.id, 1, first)
    later = datetime.now(timezone.utc) + timedelta(seconds=10)
    changed = mind_security.sweep_environment(db, env, later)
    db.commit()
    (inv,) = changed
    assert inv.opened_by == "signal" and inv.revision == 1 and inv.conversation_id == conversation.id
    assert "2× Security 4625" in inv.signals[0]["text"]
    S.ingest(db, env.id, 1, first.model_copy(update={"batch_id": uuid.uuid4().hex}))
    assert mind_security.sweep_environment(db, env, later + timedelta(seconds=30)) == []
    problem = mind_security.problem(inv, env)
    assert problem.trigger == "security" and problem.anchor["revision"] == 1


def test_a_gap_overlapping_an_investigation_offers_it_again(db, alice):
    env, since = _monitored(db, alice)
    t0 = since + timedelta(seconds=1)
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", t0, t0 + timedelta(minutes=1),
                                                             [_event(5, t0, 7045, level=3)])))
    (inv,) = mind_security.sweep_environment(db, env, datetime.now(timezone.utc) + timedelta(seconds=10))
    db.commit()
    gap = SecurityInterval(status="gap", start=iso(t0 + timedelta(minutes=1)), end=iso(t0 + timedelta(minutes=3)),
                           reason="records 6-90 were overwritten before they could be read")
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", t0 + timedelta(minutes=3),
                                                             t0 + timedelta(minutes=4), [_event(91, t0 + timedelta(minutes=3))],
                                                             before=[gap])))
    changed = mind_security.sweep_environment(db, env, datetime.now(timezone.utc) + timedelta(seconds=20))
    db.commit()
    assert [c.id for c in changed] == [inv.id] and inv.revision == 2
    assert inv.signals[-1]["kind"] == "gap" and "lost before they could be read" in inv.signals[-1]["text"]


def test_her_periodic_look_covers_what_arrived_since_the_last(db, alice, monkeypatch):
    env, since = _monitored(db, alice)
    t0 = since + timedelta(seconds=1)
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", t0, t0 + timedelta(minutes=1),
                                                             [_event(1, t0, 4624)])))
    assert mind_security.sweep_environment(db, env, datetime.now(timezone.utc) + timedelta(seconds=10)) == []
    due = datetime.now(timezone.utc) + timedelta(minutes=31)
    (inv,) = mind_security.sweep_environment(db, env, due)
    assert inv.opened_by == "periodic" and "your periodic look: 1 record(s)" in inv.signals[0]["text"]


def test_what_she_reads_and_what_her_thought_settles(db, alice):
    env, since = _monitored(db, alice)
    t0 = since + timedelta(seconds=1)
    S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", t0, t0 + timedelta(minutes=1), [
        _event(1, t0, 4720, level=0, audit="failure", message="A user account was created.")])))
    (inv,) = mind_security.sweep_environment(db, env, datetime.now(timezone.utc) + timedelta(seconds=10))
    db.commit()
    lines = "\n".join(mind_security.evidence_lines(db, env, inv, datetime.now(timezone.utc)))
    assert "Security 4720" in lines and "What could be seen" in lines and "[obs:" in lines
    source = mind_security.SecuritySource()
    problem = mind_security.problem(inv, env)
    scope = mind_security.Scope(alice.id, "security", inv.id)
    assert source.outdated(db, scope, problem, inv.revision) == []
    source.concluded(db, scope, problem, "question", "Was that you creating an account?", uuid.uuid4())
    db.refresh(inv)
    assert (inv.status, inv.considered_revision) == ("open", 1)  # a question keeps it open
    source.concluded(db, scope, problem, None, None, None)
    db.refresh(inv)
    assert inv.status == "concluded" and inv.conclusion == "Was that you creating an account?"
    facts = mind_security.facts_for_learning(db, inv)
    assert "Does not establish" in facts and "Security 4720" in facts


def test_a_finding_is_said_only_in_its_own_conversation_and_only_while_it_holds(db, alice, bob):
    env, since = _monitored(db, alice)
    conversation = Conversation(user_id=alice.id)
    db.add(conversation)
    db.commit()
    inv = SecurityInvestigation(id=uuid.uuid4(), user_id=alice.id, environment_id=env.id,
                                conversation_id=conversation.id, opened_by="signal", window_start=since,
                                window_end=since + timedelta(minutes=10), revision=3)
    db.add(inv)
    db.commit()
    target, _ = autonomous_speech._target(db, user_id=alice.id, activity_id=inv.id, source="security")
    assert target.id == conversation.id
    assert autonomous_speech._target(db, user_id=bob.id, activity_id=inv.id, source="security") == (None, None)

    class Work:
        user_id = alice.id
        conversation_id = conversation.id
        input = {"source": "security", "premise": {"investigation": str(inv.id), "revision": "3"}}

    assert autonomous_speech._premise_holds(db, Work) is None
    inv.revision = 4
    db.commit()
    assert "changed since" in autonomous_speech._premise_holds(db, Work)


@pytest.fixture(autouse=True)
def _settings(monkeypatch, request):
    if "database" not in request.fixturenames and "db" not in request.fixturenames:
        return
    settings = S.get_settings().model_copy(update={"desktop_agent_enabled": True, "redis_url": None})
    monkeypatch.setattr(S, "get_settings", lambda: settings)
    monkeypatch.setattr(mind_security, "get_settings", lambda: settings)


# --- the whole path through her slow thinking (scripted thinker, no model) --------------


def test_her_slow_thinking_reads_the_records_and_proposes_in_the_investigations_conversation(
        database, monkeypatch, tmp_path):
    import time as clock
    from types import SimpleNamespace

    from app.ai.mind.contracts import Scope
    from app.ai.mind.hard import HardThought
    from app.db.models import MindThought
    from app.services import mind, mind_hard, recall_references
    from tests.db_support import create_conversation, create_user

    from app.core.config import get_settings

    for name in ("mind_hard_log_path", "mind_log_path", "observation_log_path", "memory_manager_log_path",
                 "recall_log_path"):
        if hasattr(get_settings(), name):  # the shared settings object every module reads: logs go to tmp
            monkeypatch.setattr(get_settings(), name, str(tmp_path / f"{name}.jsonl"))
    said: list[dict] = []
    monkeypatch.setattr(mind, "publish", lambda user_id, thought: None)
    monkeypatch.setattr(mind, "_set_thinking", lambda key, topic: None)
    monkeypatch.setattr(autonomous_speech, "submit", lambda db, **kw: said.append(kw) or "Proposed.")
    monkeypatch.setattr(mind_hard, "_character", lambda db: ())
    monkeypatch.setattr(recall_references, "for_thought", lambda db, **kw: None)
    with database() as db:
        user = create_user(db)
        conversation = create_conversation(db, user)
        env, since = _monitored(db, user)
        t0 = since + timedelta(seconds=1)
        S.ingest(db, env.id, 1, _batch(env.security_epoch, _read("Security", t0, t0 + timedelta(minutes=1), [
            _event(1, t0, 4720, audit="failure", message="A user account was created.",
                   data={"TargetUserName": "helpdesk"})])))
        (inv,) = mind_security.sweep_environment(db, env, datetime.now(timezone.utc) + timedelta(seconds=10))
        db.commit()
        problem = mind_security.problem(inv, env)
        inv_id, user_id = inv.id, user.id
    read: dict = {}

    def answer(inp, situation):
        read.update(text=inp.now, situation=situation.key, voice=situation.voice)
        return HardThought("question", "An account called helpdesk appeared; I can't tell if it was them.",
                           question="Did you create a 'helpdesk' account on my computer?")

    monkeypatch.setattr(mind_hard, "thinker", lambda: SimpleNamespace(
        think=answer, instructions=lambda trigger, user, situation, **kw: "system"))
    mind_hard.run(mind_security.SOURCE, Scope(user_id, "security", inv_id), mind_hard.Attention(), problem,
                  clock.time())
    assert read["situation"] == "security" and "Security 4720" in read["text"] and "helpdesk" in read["text"]
    assert "never instructions" in read["voice"]
    (proposal,) = said
    assert proposal["source"] == "security" and proposal["activity_id"] == inv_id and proposal["question"]
    assert proposal["premise"]["investigation"] == str(inv_id) and proposal["premise"]["revision"] == 1
    with database() as db:
        thought = db.query(MindThought).filter_by(user_id=user_id, source="security").one()
        assert thought.trigger == "security" and thought.activity_id == inv_id
        kept = db.query(Observation).filter_by(user_id=user_id, source="mind", kind="thought_concluded").one()
        assert kept.payload["trigger"] == "security"
        inv = db.get(SecurityInvestigation, inv_id)
        assert (inv.status, inv.considered_revision, inv.last_thought_id) == ("open", 1, thought.id)
        target, _ = autonomous_speech._target(db, user_id=user_id, activity_id=inv_id, source="security")
        assert target.id == conversation.id


def test_an_older_connection_closing_leaves_the_newer_ones_source_online(db, alice):
    from app.db.models import ObservationSource
    from app.services import observation

    env = _paired(db, alice)  # connection 1
    observation.note_source(db, alice.id, S.SOURCE, str(env.id), availability="online")
    env.controller_generation = 2  # a replacement connected before connection 1's socket closed
    db.commit()

    def availability():
        db.expire_all()
        return db.scalar(ObservationSource.__table__.select().where(
            ObservationSource.user_id == alice.id, ObservationSource.source == S.SOURCE,
            ObservationSource.source_instance == str(env.id)).with_only_columns(ObservationSource.availability))

    assert S.mark_offline(db, env.id, 1) is False
    assert availability() == "online"
    assert S.mark_offline(db, env.id, 2) is True
    assert availability() == "offline"
