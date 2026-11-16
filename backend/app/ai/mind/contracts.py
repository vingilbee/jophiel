"""What the general mind and its sources and bodies agree on (docs/COLUMBINA_MIND_DESIGN.md, docs/MIND_ARCHITECTURE.md).

The mind (services/mind.py: attention, quick thoughts; services/mind_hard.py: problems, hard
thoughts, where conclusions go) knows nothing about a game. A **source** tells it what is going
on (a ``Moment``), what is worth thinking hard about (``Problem``s) and whether a problem still
holds. A **body** keeps acting on its own and is steered with commands it can accept or refuse.
Minecraft is one source with one body (services/mind_minecraft.py); her desktop's security records
are another, with no body (services/mind_security.py). A conversation is not a source of background
thinking: every message wakes her mind directly (services/mind_turn.py), and what she chose to come
back to wakes it later (services/coordinator.py).

Shaped by those sources only: add a field when one of them needs it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from app.ai.mind.hard import HardInput, Problem
from app.ai.mind.situation import Situation

# Which waiting problem starts first when several activities have one (lower first).
PRIORITY = {"handoff": 0, "failing": 1, "surprise": 2, "promise": 2, "intention": 2, "conflict": 2, "big": 3,
            "open": 3, "quiet": 9, "review": 1, "security": 2}


@dataclass(frozen=True)
class Scope:
    """What a piece of thinking is about. Attention is shared per user;
    thoughts, intentions, the open question, hand-offs and tips stay per activity, so a
    thought about an old game never steers a new one."""

    user_id: uuid.UUID
    source: str  # "minecraft", "conversation"
    activity: uuid.UUID  # the source's own id for it: a game session id, a conversation id

    @property
    def key(self) -> str:
        """Per-activity state (intentions, the question, hand-offs) is stored under this."""
        return str(self.activity)


@dataclass(frozen=True)
class Rhythm:
    """How a source produces moments: polled (``every`` seconds) or when something happens."""

    kind: Literal["poll", "event"]
    every: float = 1.0


@dataclass
class Moment:
    """One tick of a source, as the general mind reads it."""

    text: str  # what the gate and quick thoughts read
    pressure: str  # urgent / busy / calm, decided by the source's code
    fresh: list[str]  # what's news since the last tick
    changed: list[str] = field(default_factory=list)
    why: str = ""
    can_act: bool = True  # its body is acting now (a reaction makes sense)
    danger: Any = None  # the source's own danger; compared with Source.worse
    talk: list[dict] = field(default_factory=list)  # recent talk {"role", "content", "at"}
    plan: dict | None = None
    capture: Callable[[], Any] | None = None  # the source as it is now, for Source.changed_since
    data: Any = None  # the source's own facts behind the moment (for its problems and recording)


@dataclass(frozen=True)
class Command:
    control: str  # one of the body's controls(), e.g. "tip"
    text: str


@dataclass(frozen=True)
class CommandResult:
    status: Literal["accepted", "refused", "unsupported"]
    detail: str = ""


class Body(Protocol):
    """Something that keeps acting on its own: steered, never called."""

    def controls(self) -> frozenset[str]: ...

    def command(self, scope: Scope, command: Command) -> CommandResult: ...


class Source(Protocol):
    """What tells the mind what is going on. ``db`` is a database session; ``state`` is the
    source's own per-activity memory for finding problems (it lives in the mind's process)."""

    name: str
    rhythm: Rhythm
    shadow: bool  # shadow sources think only into a log: never saved, said or sent to a body

    def enabled(self) -> bool: ...

    # Polled sources (rhythm "poll"): their live activities, and one moment per activity per tick.
    def poll(self, db: Any) -> list[tuple[Scope, Any]] | None: ...  # None: unreachable, no tick

    def moment(self, db: Any, scope: Scope, payload: Any, previous: Any, at: float, *,
               thoughts: list[dict], thinking: str | None) -> Moment: ...

    def previous(self, moment: Moment) -> Any: ...  # what the next moment compares against

    def after_tick(self, scope: Scope, moment: Moment, number: int, decision: dict) -> None: ...

    # Quick thoughts.
    def gate_situation(self) -> Situation: ...

    def situation(self) -> Situation: ...  # with her voice there, for spoken lines

    def worse(self, danger: Any, before: Any) -> bool: ...

    def live(self, db: Any, scope: Scope) -> bool: ...

    def changed_since(self, db: Any, token: Any) -> list[str]: ...  # token: Moment.capture()

    # Hard thoughts: problems, what she reads, whether a problem still holds.
    def new_state(self) -> Any: ...

    def problems(self, scope: Scope, state: Any, moment: Moment, *, gate_hard: bool) -> list[Problem]: ...

    def drop_waiting(self, scope: Scope, moment: Moment, problem: Problem) -> str | None: ...

    def thinks_when_quiet(self) -> bool: ...  # whether quiet time in it starts a thought (never on what)

    def gather(self, db: Any, scope: Scope, problem: Problem,
               base: HardInput) -> tuple[HardInput | None, Any]: ...  # None: the activity is gone

    def dropped(self, db: Any, scope: Scope, problem: Problem, cancelled: str | None) -> str | None: ...

    def outdated(self, db: Any, scope: Scope, problem: Problem, token: Any) -> list[str]: ...

    def current_goal(self, db: Any, scope: Scope) -> str | None: ...

    # Where conclusions and speech go.
    def speaking(self, db: Any, scope: Scope) -> dict: ...  # autonomous_speech.submit settings (commentary mode)

    def body(self, scope: Scope) -> Body | None: ...

    def thought_session(self, scope: Scope) -> uuid.UUID | None: ...  # mind_thoughts.session_id, if any

    def handoff_scope(self, db: Any, user_id: uuid.UUID) -> Scope | None: ...  # where think_it_over goes
