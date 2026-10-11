"""What each kind of game event does, in one table.

A game event is why Columbina wakes during play (``agent_event_jobs`` reasons,
"kind: detail"). Her hands raise them from the pack's notices and from plan
progress; ``desktop_sessions`` decides whether one wakes her, ``agent_say``
whether she may say something about it. Both read this table, so a new kind
of event is one row here.

The session's commentary mode is quiet, occasional (the default) or commentary.
"""
from __future__ import annotations

from dataclasses import dataclass

QUIET, OCCASIONAL, COMMENTARY = "quiet", "occasional", "commentary"
ALL_MODES = frozenset({QUIET, OCCASIONAL, COMMENTARY})
NOT_QUIET = frozenset({OCCASIONAL, COMMENTARY})


@dataclass(frozen=True)
class Kind:
    # Commentary modes in which a notice of this kind wakes her (for kinds her
    # hands report as notices; plan progress and pauses always wake her).
    wakes: frozenset[str] = ALL_MODES
    # Modes in which she may say something about it: subject to the gap between
    # unprompted messages, unless `always` (then also in quiet, with no gap).
    said: frozenset[str] = frozenset({COMMENTARY})
    always: bool = False
    # Cuts off a reply she's speaking (what she was saying is out of date).
    interrupts: bool = False


KINDS: dict[str, Kind] = {
    # Plan progress.
    "plan_low": Kind(),
    "goal_done": Kind(said=NOT_QUIET),
    # Her hands paused or are failing.
    "paused": Kind(said=NOT_QUIET),
    "stuck": Kind(always=True),  # she may need to ask the user for what's missing
    "struggling": Kind(always=True),
    "needs_human": Kind(always=True, interrupts=True),
    # From the pack's notices.
    "danger": Kind(said=NOT_QUIET, interrupts=True),  # urgent: a mob on her, low health, a death
    "warning": Kind(wakes=NOT_QUIET, said=NOT_QUIET),  # hurt, sunset, a mob near the user, a mode result
    "discovery": Kind(wakes=NOT_QUIET),  # a village, a spawner, ore
    "notable": Kind(wakes=frozenset({COMMENTARY})),
}
UNKNOWN = Kind(wakes=frozenset())  # never wakes her by itself


def kind_of(reason: str) -> str:
    return reason.split(":", 1)[0]


def rules(reason: str) -> Kind:
    return KINDS.get(kind_of(reason), UNKNOWN)
