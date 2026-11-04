"""What each kind of game event does, in one table.

A game event is why Jophiel wakes during play (``agent_event_jobs`` reasons,
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
    # Too pressing to wait out the gap (only the short URGENT_GAP_SECONDS): a mob
    # on her, a death. She's reacting, like anyone would.
    urgent: bool = False
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
    "danger": Kind(said=NOT_QUIET, urgent=True, interrupts=True),  # urgent: a mob on her, low health, a death
    "warning": Kind(wakes=NOT_QUIET, said=NOT_QUIET),  # hurt, sunset, a mob near the user, a mode result
    "discovery": Kind(wakes=NOT_QUIET),  # a village, a spawner, ore
    "notable": Kind(wakes=frozenset({COMMENTARY})),
    # Her mind (services/mind.py), which decides itself when a moment is worth it: what
    # she says from a thought still goes through the gap and the commentary mode.
    "react": Kind(said=NOT_QUIET, urgent=True),
    "light": Kind(said=NOT_QUIET),
    "thought": Kind(said=NOT_QUIET),
    # Her planner (services/game_planning): a question for the user gets through like being
    # stuck does; a line about the plan (a goal she picked, being done) like a thought.
    "ask": Kind(always=True),
    "plan": Kind(said=NOT_QUIET),
    # How teaching herself a move ended (services/game_practice.py): learned it, or couldn't.
    "learning": Kind(said=NOT_QUIET),
    # She corrects something she just said that the game has since made wrong
    # (services/game_correction.py): in any mode, with no gap. Never wakes her.
    "correction": Kind(wakes=frozenset(), always=True),
    # A question from her own slow thinking (services/mind_hard.py), asked at a natural pause:
    # she needs the user's answer, so in any mode. Never wakes her.
    "question": Kind(wakes=frozenset(), always=True),
}
# What her mind replaces while it's on: notices she'd be woken to comment on (it sees
# the same notices every tick). Plan progress, stuck and pauses still wake her.
COMMENTARY_KINDS = frozenset({"danger", "warning", "discovery", "notable", "learning"})
# Between two urgent lines (a fight is many danger notices).
URGENT_GAP_SECONDS = 8.0
UNKNOWN = Kind(wakes=frozenset())  # never wakes her by itself


def kind_of(reason: str) -> str:
    return reason.split(":", 1)[0]


def rules(reason: str) -> Kind:
    return KINDS.get(kind_of(reason), UNKNOWN)
