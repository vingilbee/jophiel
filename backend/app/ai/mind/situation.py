"""What she is doing right now, as the source of the ticks describes it.

The mind's prompts (the gate in ``gates.py``, quick thoughts in ``think.py``)
are general: they never name a game, a mob or a skill. A source (the Minecraft
pack today; later conversation, idle time, the screen) supplies one
``Situation``: a line saying what she is doing, its own examples for the gate's
levels, and the voice her spoken lines take there. Everything else a source
knows goes into its tick text.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Situation:
    key: str
    doing: str  # "playing Minecraft as her own player next to {user}"
    examples: dict[str, str] = field(default_factory=dict)  # per gate level: what that level looks like here
    voice: str = ""  # how her spoken lines sound here (a prompt section), if not her usual voice
    hands: bool = False  # a body acts here on its own (her hands in a game) and takes tips from her thinking

    def describe(self, user: str = "the user") -> str:
        return self.doing.format(user=user)


GENERAL = Situation("general", "with {user}")
