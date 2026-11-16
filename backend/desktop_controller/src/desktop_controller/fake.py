"""Fake controller pieces for tests and stage-A demos (plan §13 A).

``FakeController`` is the real ``Scheduler`` wired to a manual clock and a
backend that records every physical input instead of sending it to an OS.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import Profile
from .scheduler import Scheduler


class ManualClock:
    def __init__(self, start_ms: int = 0):
        self._now = start_ms

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("a monotonic clock never goes backwards")
        self._now += ms


@dataclass
class RecordingBackend:
    clock: ManualClock
    log: list[tuple] = field(default_factory=list)

    def key_down(self, key: str) -> None:
        self.log.append((self.clock.now_ms(), "down", key))

    def key_up(self, key: str) -> None:
        self.log.append((self.clock.now_ms(), "up", key))

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        self.log.append((self.clock.now_ms(), "rel", dx, dy))

    def mouse_move_to(self, x: int, y: int) -> None:
        self.log.append((self.clock.now_ms(), "move_to", x, y))

    def scroll(self, amount: int) -> None:
        self.log.append((self.clock.now_ms(), "scroll", amount))

    # -- helpers for assertions ---------------------------------------------

    def keys(self) -> list[tuple]:
        return [e for e in self.log if e[1] in ("down", "up")]

    def relative_total(self) -> tuple[int, int]:
        rel = [e for e in self.log if e[1] == "rel"]
        return (sum(e[2] for e in rel), sum(e[3] for e in rel))

    def clear(self) -> None:
        self.log.clear()


class FakeController:
    def __init__(self, profile: Profile, viewport_size: tuple[int, int] = (1280, 720)):
        self.clock = ManualClock()
        self.backend = RecordingBackend(self.clock)
        self.scheduler = Scheduler(profile, self.clock, self.backend, viewport_size)
        self.backend.clear()  # drop the startup release from the log

    def step(self, ms: int, samples=None, every_ms: int = 10) -> None:
        """Advance ``ms`` in small ticks, like a 100 Hz controller loop."""
        remaining = ms
        while remaining > 0:
            dt = min(every_ms, remaining)
            self.clock.advance(dt)
            self.scheduler.tick(samples(self.clock.now_ms()) if callable(samples) else samples)
            remaining -= dt


class FakeInput:
    """A guarded input backend with no OS: records input, and can pretend the
    game window was lost (``lost = "not_foreground"``) to exercise the guard.
    Used by tests and ``desktop-controller run --fake-input`` dry runs."""

    def __init__(self, clock=None, viewport: tuple[int, int] = (1280, 720), echo: bool = False):
        from .runtime import MonotonicClock

        self.clock = clock or MonotonicClock()
        self.viewport = viewport
        self.cursor_shown = False
        self.lost: str | None = None
        self.echo = echo
        self.log: list[tuple] = []
        self.down: set[str] = set()

    def _guard(self) -> None:
        if self.lost:
            from .runtime import TargetLost

            raise TargetLost(self.lost)

    def _record(self, *event) -> None:
        self.log.append((self.clock.now_ms(), *event))
        if self.echo:
            print("input:", *event, flush=True)

    def viewport_size(self) -> tuple[int, int]:
        self._guard()
        return self.viewport

    def cursor_visible(self) -> bool:
        return self.cursor_shown

    def key_down(self, key: str) -> None:
        self._guard()
        self.down.add(key)
        self._record("down", key)

    def key_up(self, key: str) -> None:
        if key in self.down:
            self.down.discard(key)
            self._record("up", key)

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        self._guard()
        self._record("rel", dx, dy)

    def mouse_move_to(self, x: int, y: int) -> None:
        self._guard()
        self._record("move_to", x, y)

    def scroll(self, amount: int) -> None:
        self._guard()
        self._record("scroll", amount)

    def relative_total(self) -> tuple[int, int]:
        rel = [e for e in self.log if e[1] == "rel"]
        return (sum(e[2] for e in rel), sum(e[3] for e in rel))


class FakeDesktopInput:
    """``desktop.DesktopInput`` that records what it would send (tests, ``--fake-input`` dry runs)."""

    def __init__(self, *, echo: bool = False, bounds=(0, 0, 1920, 1080), fail_on: str | None = None):
        self.events: list[tuple] = []
        self.echo = echo
        self.bounds = bounds
        self.sensitive = False
        self.fail_on = fail_on  # an event name that raises desktop.Failed (tests)
        self.held: set[str] = set()

    def _record(self, *event) -> None:
        if self.fail_on == event[0]:
            from .desktop import Failed

            raise Failed(f"fake failure on {event[0]}")
        self.events.append(event)
        if self.echo:
            print("desktop:", *event, flush=True)

    def key_down(self, key: str) -> None:
        self._record("key_down", key)
        self.held.add(f"key:{key}")

    def key_up(self, key: str) -> None:
        self.events.append(("key_up", key))
        self.held.discard(f"key:{key}")

    def type_text(self, text: str) -> None:
        self._record("type", text)

    def move_to(self, x: int, y: int) -> None:
        self._record("move", x, y)

    def button_down(self, button: str) -> None:
        self._record("button_down", button)
        self.held.add(f"button:{button}")

    def button_up(self, button: str) -> None:
        self.events.append(("button_up", button))
        self.held.discard(f"button:{button}")

    def wheel(self, notches: int, horizontal: bool = False) -> None:
        self._record("hwheel" if horizontal else "wheel", notches)

    def screen_bounds(self):
        return self.bounds

    def sensitive_in_front(self) -> bool:
        return self.sensitive

    def foreground(self):
        return "explorer.exe", "Fake desktop"


class FakeEventLog:
    """Windows event logs in memory, for the security collector's tests and dry runs (``--fake-security``).

    ``write`` appends a record (numbers go up by one); ``clear`` empties a log and starts its numbers again, as
    Windows does; ``drop_oldest`` overwrites the oldest records, as a full log that wraps does. ``deny`` makes a
    channel unreadable with the given reason."""

    def __init__(self, channels: list[str] | None = None, clock=None):
        from datetime import datetime, timezone

        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.logs: dict[str, list] = {name: [] for name in channels or []}
        self.next_number: dict[str, int] = {name: 1 for name in channels or []}
        self.denied: dict[str, str] = {}
        self.reads = 0

    @classmethod
    def demo(cls) -> "FakeEventLog":
        log = cls(["Security", "System"])
        log.write("Security", 4624, message="An account was successfully logged on.", audit="success")
        return log

    def write(self, channel: str, event_id: int, *, message: str = "", level: int = 0, provider: str = "Test",
              audit: str | None = None, data: dict | None = None, at=None):
        from .link import SecurityEvent
        from .security import iso

        self.logs.setdefault(channel, [])
        number = self.next_number.setdefault(channel, 1)
        self.next_number[channel] = number + 1
        record = SecurityEvent(record_id=number, time_created=iso(at or self._clock()), provider=provider,
                               event_id=event_id, level=level, audit=audit, message=message, data=data or {})
        self.logs[channel].append(record)
        return record

    def clear(self, channel: str) -> None:
        self.logs[channel] = []
        self.next_number[channel] = 1

    def drop_oldest(self, channel: str, count: int) -> None:
        self.logs[channel] = self.logs[channel][count:]

    def deny(self, channel: str, reason: str) -> None:
        self.denied[channel] = reason

    def allow(self, channel: str) -> None:
        self.denied.pop(channel, None)

    def _log(self, channel: str) -> list:
        from .security import ChannelUnavailable

        if channel in self.denied:
            raise ChannelUnavailable(self.denied[channel])
        if channel not in self.logs:
            raise ChannelUnavailable("this channel isn't on this machine (not installed or not enabled)")
        return self.logs[channel]

    def info(self, channel: str):
        from .security import LogInfo

        records = self._log(channel)
        return LogInfo(oldest=records[0].record_id if records else 0, count=len(records))

    def read(self, channel: str, *, from_record: int, limit: int) -> list:
        self.reads += 1
        return [r for r in self._log(channel) if r.record_id >= from_record][:limit]

    def newest(self, channel: str):
        records = self._log(channel)
        return records[-1] if records else None
