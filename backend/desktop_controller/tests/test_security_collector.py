"""The security collector: whole channels read only, continuity checked by record number, honest coverage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from desktop_controller.fake import FakeEventLog
from desktop_controller.link import SecurityConfig, relay_message, controller_message
from desktop_controller.security import (
    ChannelUnavailable, SecurityCollector, WindowsEventLog, parse_events, parse_time,
)

T0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def tick(self, seconds=10):
        self.now += timedelta(seconds=seconds)


def _setup(tmp_path, channels=("Security",), **kwargs):
    clock = Clock()
    log = FakeEventLog(list(channels), clock=clock)
    collector = SecurityCollector(log, tmp_path / "state.json", environment_id="env-1", clock=clock, **kwargs)
    collector.configure(SecurityConfig(enabled=True, epoch=1, channels=list(channels)))
    return clock, log, collector


def _round(collector, stored=True):
    batch = collector.collect()
    assert batch is not None
    collector.acknowledge(batch.batch_id, stored=stored)
    return {read.channel: read for read in batch.channels}


def test_monitoring_starts_at_the_log_end_and_covers_from_there(tmp_path):
    clock, log, collector = _setup(tmp_path)
    log.write("Security", 4624, message="before monitoring")
    first = _round(collector)["Security"]
    assert first.events == [] and first.intervals == []  # the bookmark is set; nothing before it is read
    clock.tick()
    log.write("Security", 4625, message="a failed logon", audit="failure")
    clock.tick()
    read = _round(collector)["Security"]
    assert [e.event_id for e in read.events] == [4625]
    (covered,) = read.intervals
    assert covered.status == "covered" and covered.start == "2026-11-10T12:00:00.000000Z"
    assert covered.end == "2026-11-10T12:00:20.000000Z" and covered.first_record == covered.last_record == 2


def test_a_quiet_read_still_verifies_coverage(tmp_path):
    clock, log, collector = _setup(tmp_path)
    log.write("Security", 4624)
    _round(collector)
    clock.tick(15)
    read = _round(collector)["Security"]
    assert read.events == []
    assert [(i.status, i.start, i.end) for i in read.intervals] == [
        ("covered", "2026-11-10T12:00:00.000000Z", "2026-11-10T12:00:15.000000Z")]


def test_bookmarks_move_only_when_the_relay_stored_the_batch(tmp_path):
    clock, log, collector = _setup(tmp_path)
    _round(collector)
    log.write("Security", 1)
    clock.tick()
    lost = _round(collector, stored=False)["Security"]
    clock.tick()
    again = _round(collector)["Security"]
    assert [e.record_id for e in lost.events] == [e.record_id for e in again.events] == [1]
    assert again.intervals[0].start == lost.intervals[0].start  # the same interval, claimed once it's stored


def test_a_restart_recovers_from_the_log_itself(tmp_path):
    clock, log, collector = _setup(tmp_path)
    _round(collector)
    for n in range(5):
        log.write("Security", 4688 + n)
    clock.tick(3600)  # the controller was gone an hour; the log kept everything
    revived = SecurityCollector(log, tmp_path / "state.json", environment_id="env-1", clock=clock)
    revived.configure(SecurityConfig(enabled=True, epoch=1, channels=["Security"]))
    read = _round(revived)["Security"]
    assert [e.event_id for e in read.events] == [4688, 4689, 4690, 4691, 4692]
    assert [i.status for i in read.intervals] == ["covered"]


def test_overwritten_records_are_a_gap_never_coverage(tmp_path):
    clock, log, collector = _setup(tmp_path)
    log.write("Security", 1)
    _round(collector)
    clock.tick()
    for _ in range(4):
        log.write("Security", 2)
    clock.tick()
    survivor = log.write("Security", 3)
    log.drop_oldest("Security", 5)  # the log wrapped: the bookmark and the next four are gone
    clock.tick()
    read = _round(collector)["Security"]
    gap, covered = read.intervals
    assert gap.status == "gap" and "overwritten" in gap.reason
    assert gap.end == survivor.time_created == covered.start
    assert [e.record_id for e in read.events] == [survivor.record_id]


def test_a_cleared_log_is_a_reset_with_a_new_stream(tmp_path):
    clock, log, collector = _setup(tmp_path)
    for _ in range(3):
        log.write("Security", 4624)
    _round(collector)
    clock.tick()
    log.clear("Security")
    clock.tick()
    cleared = log.write("Security", 1102, message="The audit log was cleared.")
    clock.tick()
    read = _round(collector)["Security"]
    assert read.stream == 1
    reset, covered = read.intervals
    assert reset.status == "reset" and reset.end == cleared.time_created == covered.start
    assert [e.event_id for e in read.events] == [1102]


def test_a_cleared_log_refilled_past_the_bookmark_is_still_a_reset(tmp_path):
    clock, log, collector = _setup(tmp_path)
    log.write("Security", 1)
    log.write("Security", 2)
    _round(collector)  # bookmark: record 2
    clock.tick()
    log.clear("Security")
    for _ in range(4):
        log.write("Security", 9)  # numbers 1-4: record 2 now is a different record
    clock.tick()
    read = _round(collector)["Security"]
    assert read.intervals[0].status == "reset"
    assert [e.record_id for e in read.events] == [1, 2, 3, 4]


def test_a_backlog_is_read_in_turns_and_coverage_stops_before_the_last_record(tmp_path):
    clock, log, collector = _setup(tmp_path, read_limit=3)
    _round(collector)
    for n in range(7):
        clock.tick(1)
        log.write("Security", 5000 + n)
    clock.tick(5)
    first = _round(collector)["Security"]
    assert first.more and [e.event_id for e in first.events] == [5000, 5001, 5002]
    assert parse_time(first.intervals[0].end) == parse_time(first.events[-1].time_created) - timedelta(seconds=1)
    second = _round(collector)["Security"]
    third = _round(collector)["Security"]
    assert [e.event_id for e in second.events + third.events] == [5003, 5004, 5005, 5006]
    assert not third.more and third.intervals[0].end == "2026-11-10T12:00:12.000000Z"
    assert second.intervals[0].start == first.intervals[0].end  # contiguous coverage


def test_an_unreadable_channel_says_why_and_the_others_carry_on(tmp_path):
    clock, log, collector = _setup(tmp_path, channels=("Security", "Microsoft-Windows-Sysmon/Operational"))
    log.logs.pop("Microsoft-Windows-Sysmon/Operational")
    log.deny("Security", "access denied: add the controller's Windows user to the \"Event Log Readers\" group")
    reads = _round(collector)
    assert reads["Security"].intervals[0].status == "unavailable"
    assert "Event Log Readers" in reads["Security"].intervals[0].reason
    assert "isn't on this machine" in reads["Microsoft-Windows-Sysmon/Operational"].intervals[0].reason
    log.allow("Security")
    clock.tick()
    _round(collector)  # bookmark set now
    clock.tick()
    log.write("Security", 4720)
    assert [e.event_id for e in _round(collector)["Security"].events] == [4720]


def test_switching_monitoring_on_again_skips_the_time_it_was_off(tmp_path):
    clock, log, collector = _setup(tmp_path)
    _round(collector)
    collector.configure(SecurityConfig(enabled=False, epoch=1, channels=["Security"]))
    assert collector.collect() is None
    log.write("Security", 4698, message="while monitoring was off")
    clock.tick(60)
    assert collector.configure(SecurityConfig(enabled=True, epoch=2, channels=["Security"]))
    assert _round(collector)["Security"].events == []  # a new bookmark at the end: the record is never read
    clock.tick()
    log.write("Security", 4699)
    assert [e.event_id for e in _round(collector)["Security"].events] == [4699]


def test_without_windows_every_channel_is_unavailable(tmp_path):
    collector = SecurityCollector(None, None, environment_id="env-1")
    collector.configure(SecurityConfig(enabled=True, epoch=1, channels=["Security", "System"]))
    batch = collector.collect()
    assert {r.channel: r.intervals[0].status for r in batch.channels} == {"Security": "unavailable",
                                                                         "System": "unavailable"}


def test_batches_stay_small_enough_for_the_relay(tmp_path):
    clock, log, collector = _setup(tmp_path, channels=("Security", "System"), read_limit=90)
    _round(collector)
    for _ in range(90):
        log.write("Security", 4663, message="x" * 590, data={f"F{i}": "y" * 230 for i in range(20)})
        log.write("System", 7036, message="z" * 590)
    clock.tick()
    batch = collector.collect()
    assert len(batch.model_dump_json()) < 64 * 1024
    assert any(read.more for read in batch.channels)
    controller_message.validate_json(batch.model_dump_json())  # what the relay parses


def test_wire_messages_round_trip():
    ack = relay_message.validate_json('{"type":"security_ack","batch_id":"' + "a" * 32 + '","status":"stored"}')
    assert ack.status == "stored"


# --------------------------------------------------------------------------- #
# Windows: wevtutil output
# --------------------------------------------------------------------------- #

RENDERED = """<Events><Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>
<Provider Name='Microsoft-Windows-Security-Auditing' Guid='{54849625-5478-4994-a5ba-3e3b0328c30d}'/>
<EventID>4625</EventID><Version>0</Version><Level>0</Level><Task>12544</Task><Opcode>0</Opcode>
<Keywords>0x8010000000000000</Keywords><TimeCreated SystemTime='2026-11-10T12:34:56.1234567Z'/>
<EventRecordID>81234</EventRecordID><Correlation/><Execution ProcessID='812' ThreadID='900'/>
<Channel>Security</Channel><Computer>LEGION-VM</Computer><Security/></System>
<EventData><Data Name='TargetUserName'>admin</Data><Data Name='IpAddress'>192.168.1.50</Data>
<Data Name='SubjectUserName'>-</Data></EventData>
<RenderingInfo Culture='en-US'><Message>An account failed to log on.

Subject:
	Security ID:		NULL SID</Message><Level>Information</Level><Task>Logon</Task><Opcode>Info</Opcode>
<Keywords><Keyword>Audit Failure</Keyword></Keywords></RenderingInfo></Event>
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Service Control Manager'/>
<EventID Qualifiers='16384'>7045</EventID><Level>4</Level><TimeCreated SystemTime='2026-11-10T12:35:00.5Z'/>
<EventRecordID>9001</EventRecordID><Computer>LEGION-VM</Computer><Security UserID='S-1-5-21-1'/></System>
<EventData><Data Name='ServiceName'>Updater</Data><Data Name='ImagePath'>C:\\Temp\\u.exe</Data></EventData></Event></Events>"""


def test_rendered_records_parse_into_events():
    failed, service = parse_events(RENDERED)
    assert (failed.record_id, failed.event_id, failed.audit) == (81234, 4625, "failure")
    assert failed.time_created == "2026-11-10T12:34:56.123456Z"
    assert failed.provider == "Microsoft-Windows-Security-Auditing" and failed.task == "Logon"
    assert failed.data == {"TargetUserName": "admin", "IpAddress": "192.168.1.50"}  # "-" is no value
    assert failed.message.startswith("An account failed to log on. Subject: Security ID: NULL SID")
    assert failed.process_id == 812 and failed.computer == "LEGION-VM"
    assert (service.event_id, service.user_sid, service.data["ImagePath"]) == (7045, "S-1-5-21-1", "C:\\Temp\\u.exe")


def test_wevtutil_is_only_asked_to_read():
    calls = []

    def runner(args):
        calls.append(args)
        if args[0] == "gli":
            return 0, b"creationTime: x\r\nnumberOfLogRecords: 10\r\noldestRecordNumber: 81230\r\n", b""
        return 0, ("\ufeff" + RENDERED).encode("utf-16-le"), b""

    log = WindowsEventLog(runner)
    assert log.info("Security").newest == 81239
    assert [e.record_id for e in log.read("Security", from_record=81234, limit=5)] == [81234, 9001]
    assert {args[0] for args in calls} == {"gli", "qe"}  # never cl (clear), sl (set) or anything that writes
    assert "/q:*[System[(EventRecordID>=81234)]]" in calls[1] and "/uni:true" in calls[1]


def test_wevtutil_refusals_become_reasons():
    log = WindowsEventLog(lambda args: (5, b"", b"Failed to read log information. Access is denied."))
    try:
        log.info("Security")
    except ChannelUnavailable as exc:
        assert "Event Log Readers" in str(exc)
    else:
        raise AssertionError("expected ChannelUnavailable")

    retried = []

    def old_wevtutil(args):
        retried.append(args)
        if "/uni:true" in args:
            return 87, b"", b"The parameter is incorrect."
        return 0, RENDERED.encode(), b""

    assert len(WindowsEventLog(old_wevtutil).read("Security", from_record=1, limit=5)) == 2
    assert len(retried) == 2
