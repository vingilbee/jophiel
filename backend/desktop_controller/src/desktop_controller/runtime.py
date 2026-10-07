"""The guest's session runtime: scheduler + lease + watchdog (plan §19).

The network side (``connection``) only *posts* things here: a control grant
(the lease), a decision, a cancel. Everything that touches input happens on
this module's own threads, so a slow network, capture, or event loop can never
delay a release:

- the **tick thread** advances the scheduler every ``TICK_MS`` (timed input,
  expiry, and the lease: no renewal within ``lease_ms`` means release all);
- the **watchdog thread** checks the tick thread is alive. If it stalls
  while keys may be down, the watchdog releases every key the profile can
  press, straight through the backend.

Anything the game-window guard refuses (``TargetLost``) cancels the decision,
releases everything, and is reported so the session pauses; so does a
watchdog release.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from . import safety
from .profile import UNKNOWN_CONTEXT, Profile
from .scheduler import DecisionRecord, DecisionState, InputBackend, Scheduler

TICK_MS = 10
# The watchdog releases everything if the tick thread hasn't run for this long.
WATCHDOG_STALL_MS = 250
WATCHDOG_EVERY_MS = 50


class GuardedBackend(InputBackend, Protocol):
    def viewport_size(self) -> tuple[int, int]: ...
    def cursor_visible(self) -> bool: ...


class MonotonicClock:
    def now_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


@dataclass(frozen=True)
class Report:
    """Something the relay should hear about."""

    kind: str  # "decision" | "pause" (the session must pause: target lost, watchdog)
    decision_id: str | None = None
    state: str | None = None
    ended_by: str | None = None
    ran_ms: int | None = None
    rejection_code: str | None = None
    rejection_reason: str | None = None
    reason: str | None = None


class TargetLost(Exception):
    """The game window is gone, minimized, or not the active window. Raised by
    an input backend instead of sending input anywhere else."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SessionRuntime:
    def __init__(self, profile: Profile, backend: GuardedBackend, *, clock=None, threads: bool = True):
        self.profile = profile
        self.backend = backend
        self.clock = clock or MonotonicClock()
        self._lock = threading.RLock()
        self.scheduler = Scheduler(profile, self.clock, backend, backend.viewport_size())
        self._reports: deque[Report] = deque(maxlen=256)
        self._reported: set[str] = set()
        self._last_tick_ms = self.clock.now_ms()
        self._stop = threading.Event()
        self._stalled = False
        self._threads: list[threading.Thread] = []
        if threads:
            for target in (self._tick_loop, self._watchdog_loop):
                thread = threading.Thread(target=target, daemon=True)
                thread.start()
                self._threads.append(thread)

    # -- posted by the connection -------------------------------------------

    def grant(self, generation: int, lease_ms: int) -> None:
        """Renew the lease for ``generation``. A new generation fences everything
        before it: the active decision is cancelled and its keys released."""
        with self._lock:
            if generation != self.scheduler.generation:
                self.scheduler.cancel("superseded_generation")
                self.scheduler.generation = generation
            self.scheduler.grant_lease(lease_ms, generation)

    def revoke(self, reason: str = "cancelled") -> None:
        """No control any more (pause, takeover, end, lost lease): release now."""
        with self._lock:
            generation = self.scheduler.generation
            self.scheduler.cancel(reason)
            self.scheduler.generation = generation  # the server owns generation numbers

    def apply(self, decision_id: str, raw: object, generation: int) -> DecisionRecord:
        with self._lock:
            try:
                record = self.scheduler.apply(decision_id, raw, self.context(), generation)
            except TargetLost as lost:
                self._pause(lost.reason)
                record = self.scheduler.records.get(decision_id) or DecisionRecord(
                    decision_id, "", DecisionState.CANCELLED, ended_by="target_lost"
                )
            self._collect()
            return record

    def context(self) -> str:
        detect = self.profile.contexts.detect
        if detect.method != "cursor_visibility":
            return UNKNOWN_CONTEXT
        name = detect.cursor_visible if self.backend.cursor_visible() else detect.cursor_hidden
        return name or UNKNOWN_CONTEXT

    def tick(self) -> None:
        with self._lock:
            if self._stalled:
                # The watchdog released keys behind the scheduler's back: make
                # its bookkeeping match, and stop until the session resumes.
                self._stalled = False
                self._pause("watchdog")
            try:
                self.scheduler.tick()
            except TargetLost as lost:
                self._pause(lost.reason)
            self._last_tick_ms = self.clock.now_ms()
            self._collect()

    def drain(self) -> list[Report]:
        out = []
        while self._reports:
            out.append(self._reports.popleft())
        return out

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self.scheduler.cancel("shutdown")
        for thread in self._threads:
            thread.join(timeout=1)

    # -- internals -----------------------------------------------------------

    def _pause(self, reason: str) -> None:
        generation = self.scheduler.generation
        self.scheduler.cancel(reason)
        self.scheduler.generation = generation
        self._reports.append(Report("pause", reason=reason))

    def _collect(self) -> None:
        """Queue a report for each decision that reached a final state."""
        for record in list(self.scheduler.records.values())[-32:]:
            if record.state == DecisionState.RUNNING or record.decision_id in self._reported:
                continue
            self._reported.add(record.decision_id)
            if len(self._reported) > 4096:
                self._reported.clear()
            self._reports.append(Report(
                "decision", decision_id=record.decision_id, state=record.state.value, ended_by=record.ended_by,
                ran_ms=record.ran_ms,
                rejection_code=record.rejection.code.value if record.rejection else None,
                rejection_reason=record.rejection.reason if record.rejection else None,
            ))

    def _tick_loop(self) -> None:
        while not self._stop.wait(TICK_MS / 1000):
            self.tick()

    def _watchdog_loop(self) -> None:
        """Independent of the scheduler's lock: if ticking stalls, let go of
        every key this profile can press, directly through the backend."""
        while not self._stop.wait(WATCHDOG_EVERY_MS / 1000):
            if self.clock.now_ms() - self._last_tick_ms > WATCHDOG_STALL_MS:
                self.release_everything()
                self._stalled = True

    def release_everything(self) -> None:
        for key in sorted({spec.key.upper() for spec in self.profile.buttons.values()}):
            if key in safety.ALLOWED_KEYS:
                try:
                    self.backend.key_up(key)
                except Exception:
                    pass
