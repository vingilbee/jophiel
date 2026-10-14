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
