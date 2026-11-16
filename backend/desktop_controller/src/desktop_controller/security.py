"""Security activity on the VM: its Windows event logs, read only (docs/DESKTOP_NETWORK_AWARENESS_PLAN.md).

The collector reads whole channels (the active collection filter is the list of channels, never a list of
attacks or event ids) and reports, per channel, what it read and what it can vouch for:

- **covered**: from the last read to this one every record was read. Continuity is checked by record number:
  each read starts *at* the bookmark (the last record read), and that record must still be there with the
  same time. So a relay outage or a controller restart is recovered from the log itself, as long as the log
  still holds the records.
- **gap**: the records after the bookmark were overwritten before they could be read (the log wrapped).
- **reset**: the bookmark's number now holds a different record, or the log is shorter than the bookmark:
  the log was cleared or recreated. Its stream number goes up.
- **unavailable**: the channel can't be read (not installed, access denied, not Windows).

Bookmarks move only when the relay answers ``stored`` (``acknowledge``), so a lost batch is read again and
the backend's observation keys deduplicate it. Each time the owner switches monitoring on, the epoch changes
and every bookmark starts again at the log's current end: nothing from the time monitoring was off is read.

Nothing here changes the machine: ``wevtutil gli`` and ``wevtutil qe`` only read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .link import (
    SECURITY_DATA_FIELDS,
    SECURITY_FIELD_CHARS,
    SECURITY_MAX_EVENTS,
    SECURITY_MESSAGE_CHARS,
    SecurityBatch,
    SecurityChannelRead,
    SecurityConfig,
    SecurityEvent,
    SecurityInterval,
)

# Records per channel per read. More waiting: the read says ``more`` and the next one follows at once.
READ_LIMIT = 50
# A batch stays well under the relay's 64 KiB text-message limit.
BATCH_BYTES = 48_000
# How long the controller waits for the relay's answer before reading the same records again.
ACK_SECONDS = 30.0
# A record's own time is not strictly in record order: when a read stops early, coverage ends this much before
# the last record read, so nothing written just before it can be claimed as seen.
ORDER_MARGIN = timedelta(seconds=1)
STATE_VERSION = 1
AUDIT_FAILURE = 0x10000000000000
AUDIT_SUCCESS = 0x20000000000000
NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


class ChannelUnavailable(Exception):
    """The channel can't be read right now; ``str(exc)`` says why, for a person."""


@dataclass(frozen=True)
class LogInfo:
    oldest: int
    count: int

    @property
    def newest(self) -> int | None:
        return self.oldest + self.count - 1 if self.count > 0 else None


class EventLogBackend(Protocol):
    def info(self, channel: str) -> LogInfo: ...

    def read(self, channel: str, *, from_record: int, limit: int) -> list[SecurityEvent]:
        """Records numbered ``from_record`` and up, oldest first, at most ``limit``."""
        ...

    def newest(self, channel: str) -> SecurityEvent | None: ...


# --------------------------------------------------------------------------- #
# Times
# --------------------------------------------------------------------------- #

_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def parse_time(text: str) -> datetime:
    """Windows writes seven fraction digits ("...56.1234567Z"); Python wants at most six."""
    match = _TIME.match(text.strip())
    if not match:
        raise ValueError(f"not a time: {text!r}")
    day, clock, fraction, zone = match.groups()
    micro = (fraction or "0")[:6].ljust(6, "0")
    zone = "+00:00" if zone in (None, "Z") else zone if ":" in zone else zone[:3] + ":" + zone[3:]
    return datetime.fromisoformat(f"{day}T{clock}.{micro}{zone}").astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Reading Windows event logs: wevtutil, read only
# --------------------------------------------------------------------------- #


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or (len(raw) > 1 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16-le", errors="replace").lstrip("﻿")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if sys.platform == "win32" else "latin-1", errors="replace")


def _explain(text: str) -> str:
    lowered = text.lower()
    if "access is denied" in lowered:
        return ("access denied: add the controller's Windows user to the \"Event Log Readers\" group "
                "(the Security log needs it), then sign out and in again")
    if "could not be found" in lowered or "does not exist" in lowered:
        return "this channel isn't on this machine (not installed or not enabled)"
    line = " ".join(text.split())
    return (line[:180] or "wevtutil failed without saying why")


Runner = Callable[[list[str]], tuple[int, bytes, bytes]]


def _run_wevtutil(args: list[str]) -> tuple[int, bytes, bytes]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run(["wevtutil", *args], capture_output=True, timeout=30, creationflags=flags)
    return done.returncode, done.stdout, done.stderr


class WindowsEventLog:
    """``EventLogBackend`` over ``wevtutil`` (built into Windows): ``gli`` for a log's size, ``qe`` for records,
    rendered with their message text. Never writes, clears or reconfigures a log."""

    def __init__(self, runner: Runner | None = None):
        self._run = runner or _run_wevtutil
        self._unicode = True  # ask for UTF-16 output; dropped once if this wevtutil doesn't take /uni

    def _call(self, args: list[str], *, unicode_flag: bool = False) -> str:
        full = [*args, "/uni:true"] if unicode_flag and self._unicode else args
        try:
            code, out, err = self._run(full)
        except FileNotFoundError:
            raise ChannelUnavailable("wevtutil isn't available on this machine") from None
        except subprocess.TimeoutExpired:
            raise ChannelUnavailable("reading the log timed out") from None
        if code != 0 and unicode_flag and self._unicode and "parameter is incorrect" in _decode(err or out).lower():
            self._unicode = False
            return self._call(args)
        if code != 0:
            raise ChannelUnavailable(_explain(_decode(err or out)))
        return _decode(out)

    def info(self, channel: str) -> LogInfo:
        fields: dict[str, str] = {}
        for line in self._call(["gli", channel]).splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        try:
            return LogInfo(oldest=int(fields.get("oldestRecordNumber") or 0),
                           count=int(fields.get("numberOfLogRecords") or 0))
        except ValueError:
            raise ChannelUnavailable("couldn't read the log's size") from None

    def _query(self, channel: str, query: str, count: int, newest_first: bool) -> list[SecurityEvent]:
        text = self._call(["qe", channel, f"/q:{query}", f"/c:{count}", f"/rd:{'true' if newest_first else 'false'}",
                           "/f:RenderedXml", "/e:Events"], unicode_flag=True)
        return parse_events(text)

    def read(self, channel: str, *, from_record: int, limit: int) -> list[SecurityEvent]:
        return self._query(channel, f"*[System[(EventRecordID>={max(0, from_record)})]]", limit, False)

    def newest(self, channel: str) -> SecurityEvent | None:
        found = self._query(channel, "*", 1, True)
        return found[0] if found else None


def _clip(text: str | None, limit: int) -> str:
    words = " ".join((text or "").split())
    return words if len(words) <= limit else words[: limit - 1] + "…"


def _int(text: str | None) -> int | None:
    try:
        return int(text, 0) if text not in (None, "") else None
    except ValueError:
        return None


def _event(node: ET.Element) -> SecurityEvent | None:
    system = node.find(f"{NS}System")
    if system is None:
        return None
    record = _int(system.findtext(f"{NS}EventRecordID"))
    created = system.find(f"{NS}TimeCreated")
    event_id = _int(system.findtext(f"{NS}EventID"))
    if record is None or created is None or event_id is None or not created.get("SystemTime"):
        return None
    provider = system.find(f"{NS}Provider")
    execution = system.find(f"{NS}Execution")
    security = system.find(f"{NS}Security")
    rendering = node.find(f"{NS}RenderingInfo")
    keywords_mask = _int(system.findtext(f"{NS}Keywords")) or 0
    audit = "failure" if keywords_mask & AUDIT_FAILURE else "success" if keywords_mask & AUDIT_SUCCESS else None
    data: dict[str, str] = {}

    def keep(name: str, value: str | None) -> None:
        if len(data) < SECURITY_DATA_FIELDS and value not in (None, "", "-"):
            data[_clip(name, 64) or f"Data{len(data)}"] = _clip(value, SECURITY_FIELD_CHARS)

    event_data = node.find(f"{NS}EventData")
    if event_data is not None:
        for i, item in enumerate(event_data.findall(f"{NS}Data")):
            keep(item.get("Name") or f"Data{i}", item.text)
    user_data = node.find(f"{NS}UserData")
    if user_data is not None:
        for holder in list(user_data)[:1]:
            for item in holder:
                keep(item.tag.split("}")[-1], " ".join(t.strip() for t in item.itertext() if t.strip()))
    level_name = task = opcode = message = None
    keywords: list[str] = []
    if rendering is not None:
        message = rendering.findtext(f"{NS}Message")
        level_name = rendering.findtext(f"{NS}Level")
        task = rendering.findtext(f"{NS}Task")
        opcode = rendering.findtext(f"{NS}Opcode")
        keywords = [_clip(k.text, 48) for k in rendering.findall(f"{NS}Keywords/{NS}Keyword") if k.text][:8]
    try:
        created_at = iso(parse_time(created.get("SystemTime", "")))
    except ValueError:
        return None
    return SecurityEvent(
        record_id=record, time_created=created_at,
        provider=_clip(provider.get("Name") if provider is not None else "", 128),
        event_id=event_id & 0xFFFF, version=_int(system.findtext(f"{NS}Version")),
        level=_int(system.findtext(f"{NS}Level")), level_name=_clip(level_name, 32) or None,
        task=_clip(task or system.findtext(f"{NS}Task"), 64) or None,
        opcode=_clip(opcode or system.findtext(f"{NS}Opcode"), 64) or None,
        keywords=keywords, audit=audit, computer=_clip(system.findtext(f"{NS}Computer"), 128) or None,
        user_sid=_clip(security.get("UserID") if security is not None else None, 96) or None,
        process_id=_int(execution.get("ProcessID")) if execution is not None else None,
        message=_clip(message, SECURITY_MESSAGE_CHARS), data=data,
    )


def parse_events(text: str) -> list[SecurityEvent]:
    """``wevtutil qe /f:RenderedXml /e:Events`` output -> records, in the order given. A record that doesn't
    parse is left out (and shows up as a break in record numbers, so never as silent coverage)."""
    text = text.strip()
    if not text:
        return []
    root = ET.fromstring(text if text.startswith("<Events") else f"<Events>{text}</Events>")
    out = []
    for node in root.iter(f"{NS}Event"):
        event = _event(node)
        if event is not None:
            out.append(event)
    return out


# --------------------------------------------------------------------------- #
# The collector
# --------------------------------------------------------------------------- #


@dataclass
class ChannelState:
    record: int | None = None  # the bookmark: last record read (0: the log was empty), None: not set yet
    record_time: str | None = None
    covered_until: str | None = None  # coverage so far ends here (the VM's clock)
    stream: int = 0


@dataclass
class _Pending:
    batch_id: str
    epoch: int
    proposals: dict[str, ChannelState] = field(default_factory=dict)
    sent_at: float = field(default_factory=time.monotonic)


class SecurityCollector:
    """Reads the monitored channels and builds batches; moves bookmarks only on the relay's ``stored``."""

    def __init__(self, backend: EventLogBackend | None, state_path: Path | None = None, *,
                 environment_id: str = "", clock: Callable[[], datetime] = _utcnow, read_limit: int = READ_LIMIT,
                 batch_bytes: int = BATCH_BYTES, unavailable_reason: str | None = None):
        self.backend = backend
        self.state_path = state_path
        self.environment_id = environment_id
        self.clock = clock
        self.read_limit = max(1, min(read_limit, SECURITY_MAX_EVENTS - 1))
        self.batch_bytes = batch_bytes
        self.unavailable_reason = unavailable_reason or "this controller can't read Windows event logs (not Windows)"
        self.config = SecurityConfig()
        self.epoch = 0
        self.channels: dict[str, ChannelState] = {}
        self._pending: _Pending | None = None
        self._attempted: dict[str, datetime] = {}  # last read attempt per channel, for unavailable intervals
        self._turn = 0
        self._load()

    # --- state on disk ---------------------------------------------------------

    def _load(self) -> None:
        if self.state_path is None:
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("version") != STATE_VERSION or data.get("environment_id") != self.environment_id:
            return  # another pairing's bookmarks: start afresh
        self.epoch = int(data.get("epoch") or 0)
        self.channels = {name: ChannelState(**state) for name, state in (data.get("channels") or {}).items()}

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "version": STATE_VERSION, "environment_id": self.environment_id, "epoch": self.epoch,
            "channels": {name: asdict(state) for name, state in self.channels.items()},
        }, indent=1), encoding="utf-8")
        os.replace(temporary, self.state_path)

    # --- the owner's switch ----------------------------------------------------

    def configure(self, config: SecurityConfig) -> bool:
        """Apply what the relay last said. A new epoch (monitoring switched on again) starts every bookmark
        afresh at the log's end. Returns whether the epoch changed."""
        self.config = config
        if not config.enabled or config.epoch == self.epoch:
            return False
        self.epoch = config.epoch
        self.channels = {}
        self._pending = None
        self._save()
        return True

    # --- one read --------------------------------------------------------------

    def collect(self) -> SecurityBatch | None:
        """One read of every monitored channel that fits in a batch (busy channels take turns), or None when
        monitoring is off. Nothing moves until ``acknowledge``."""
        if not self.config.enabled or self.epoch < 1 or not self.config.channels:
            return None
        channels = list(dict.fromkeys(self.config.channels))
        start = self._turn % len(channels)
        self._turn += 1
        order = channels[start:] + channels[:start]
        reads: list[SecurityChannelRead] = []
        proposals: dict[str, ChannelState] = {}
        used = 400  # the batch's own fields
        for channel in order:
            budget = self.batch_bytes - used
            if budget < 1500:
                break
            read, proposal = self._read_channel(channel, budget)
            if read is None:
                continue
            used += len(read.model_dump_json()) + 2
            reads.append(read)
            if proposal is not None:
                proposals[channel] = proposal
        if not reads:
            return None
        batch = SecurityBatch(batch_id=uuid.uuid4().hex, epoch=self.epoch, sent_at=iso(self.clock()), channels=reads)
        self._pending = _Pending(batch.batch_id, self.epoch, proposals)
        return batch

    def acknowledge(self, batch_id: str, *, stored: bool) -> None:
        """The relay's answer (or none: ``stored`` False). Only ``stored`` moves the bookmarks."""
        pending, self._pending = self._pending, None
        if pending is None or pending.batch_id != batch_id or not stored or pending.epoch != self.epoch:
            return
        self.channels.update(pending.proposals)
        self._save()

    def _unavailable(self, channel: str, reason: str, now: datetime) -> SecurityChannelRead:
        state = self.channels.get(channel) or ChannelState()
        since = self._attempted.get(channel, now)
        self._attempted[channel] = now
        return SecurityChannelRead(channel=channel, stream=state.stream, intervals=[SecurityInterval(
            status="unavailable", start=iso(min(since, now)), end=iso(now), reason=_clip(reason, 200))])

    def _read_channel(self, channel: str, budget: int) -> tuple[SecurityChannelRead | None, ChannelState | None]:
        now = self.clock()
        if self.backend is None:
            return self._unavailable(channel, self.unavailable_reason, now), None
        state = self.channels.get(channel) or ChannelState()
        try:
            if state.record is None:
                # Monitoring (re)started: the bookmark is the log's end now; coverage starts here.
                newest = self.backend.newest(channel)
                self._attempted[channel] = now
                return (SecurityChannelRead(channel=channel, stream=state.stream),
                        ChannelState(record=newest.record_id if newest else 0,
                                     record_time=newest.time_created if newest else None,
                                     covered_until=iso(now), stream=state.stream))
            return self._continue(channel, state, budget)
        except ChannelUnavailable as exc:
            return self._unavailable(channel, str(exc), now), None

    def _continue(self, channel: str, state: ChannelState, budget: int) -> tuple[SecurityChannelRead, ChannelState]:
        asked = self.clock()  # everything written before this moment is in what the query returns
        self._attempted[channel] = asked
        start = parse_time(state.covered_until) if state.covered_until else asked
        bookmark = state.record or 0
        records = self.backend.read(channel, from_record=max(bookmark, 1), limit=self.read_limit + 2)
        stream, lost_until, lost_status, lost_reason = state.stream, None, None, None
        if bookmark > 0:
            head = records[0] if records else None
            if head is not None and head.record_id == bookmark and _same_time(head.time_created, state.record_time):
                records = records[1:]  # the bookmark is still there: the log is continuous
            elif head is not None and head.record_id == bookmark:
                lost_status, lost_reason = "reset", "the log was cleared or recreated since the last read"
            elif head is not None and head.record_id == bookmark + 1:
                pass  # only the bookmark itself was overwritten; everything after it is here
            elif head is not None and head.record_id > bookmark + 1:
                lost_status = "gap"
                lost_reason = (f"records {bookmark + 1}-{head.record_id - 1} were overwritten before they could "
                               "be read (the log is full and wraps)")
                lost_until = parse_time(head.time_created)
            else:
                info = self.backend.info(channel)
                if info.newest is None or info.newest < bookmark:
                    lost_status, lost_reason = "reset", "the log was cleared or recreated since the last read"
            if lost_status == "reset":
                stream += 1
                records = self.backend.read(channel, from_record=0, limit=self.read_limit + 1)
                lost_until = parse_time(records[0].time_created) if records else asked
        more = len(records) > self.read_limit
        records = records[: self.read_limit]
        for i in range(1, len(records)):
            if records[i].record_id != records[i - 1].record_id + 1:
                records, more = records[:i], True  # a break in numbering: the next read reports it as a gap
                break
        while True:
            read, proposal = self._build(channel, state, stream, start, asked, records, more,
                                         lost_status, lost_reason, lost_until)
            if len(read.model_dump_json()) <= budget or not records:
                return read, proposal
            records, more = records[: max(0, len(records) * 2 // 3)], True

    def _build(self, channel: str, state: ChannelState, stream: int, start: datetime, asked: datetime,
               records: list[SecurityEvent], more: bool, lost_status: str | None, lost_reason: str | None,
               lost_until: datetime | None) -> tuple[SecurityChannelRead, ChannelState]:
        end = asked
        if more and records:
            end = min(asked, parse_time(records[-1].time_created) - ORDER_MARGIN)
        elif more:
            end = start  # nothing could be sent: no new coverage
        end = max(end, start)
        intervals: list[SecurityInterval] = []
        covered_from = start
        if lost_status is not None:
            # What was lost runs up to the first record still there (or now, when none is): never claimed as seen.
            lost_end = min(max(lost_until or start, start), max(asked, start))
            intervals.append(SecurityInterval(status=lost_status, start=iso(start), end=iso(lost_end),
                                              reason=lost_reason))
            covered_from = lost_end
            end = max(end, lost_end)
        if end > covered_from:
            intervals.append(SecurityInterval(
                status="covered", start=iso(covered_from), end=iso(end),
                first_record=records[0].record_id if records else None,
                last_record=records[-1].record_id if records else None))
        if records:
            bookmark, bookmark_time = records[-1].record_id, records[-1].time_created
        elif lost_status == "reset":
            bookmark, bookmark_time = 0, None
        else:
            bookmark, bookmark_time = state.record, state.record_time
        proposal = ChannelState(record=bookmark, record_time=bookmark_time, covered_until=iso(end), stream=stream)
        return SecurityChannelRead(channel=channel, stream=stream, intervals=intervals, events=records,
                                   more=more), proposal

    # --- for the connection loop ------------------------------------------------

    @property
    def pending(self) -> str | None:
        return self._pending.batch_id if self._pending else None


def _same_time(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    try:
        return parse_time(a) == parse_time(b)
    except ValueError:
        return a == b


def default_state_path(config_path: Path) -> Path:
    return config_path.with_name("security_state.json")


def collector_for(environment_id: str, state_path: Path, *, fake: bool = False) -> SecurityCollector:
    """Real event logs on Windows; elsewhere every channel reports itself unavailable (the owner sees why)."""
    if fake:
        from .fake import FakeEventLog

        return SecurityCollector(FakeEventLog.demo(), state_path, environment_id=environment_id)
    if sys.platform != "win32":
        return SecurityCollector(None, state_path, environment_id=environment_id)
    return SecurityCollector(WindowsEventLog(), state_path, environment_id=environment_id)


__all__ = ["ChannelUnavailable", "LogInfo", "SecurityCollector", "WindowsEventLog", "collector_for",
           "default_state_path", "parse_events", "parse_time", "iso"]
