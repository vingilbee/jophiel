"""Her next challenge while exploring (stage H3, Voyager's automatic curriculum).

When a goal is done and exploring is on (``services/game_challenges``), one call
picks one small challenge just beyond what she can do now, from her game summary
(what she carries, her skills and learned moves, the place, the user) and her
record: challenges done (not again), challenges she failed (only again once she
has a new move, or in a later session) and the ones she was just offered. The
aim is flexible, by the user's choice (2026-10-18): something new for her
(``discover``: an item, a tool, the next step up the tool ladder, a place), or
something for the two of them (``for_us``: a shelter at sunset, gear for the
user), whichever fits the moment.

It only proposes. Jophiel's background turn reads it in her summary and makes
it her goal (``set_game_plan(challenge=true)``), in her own words; her hands try
it with their skills, and a lesson starts only where none does it. Pure: the
model is passed in.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.desktop.driver import DriverModel

CHECK_KINDS = ("has", "gained", "killed", "top_here", "peak", "near_player")

SCHEMA = {
    "type": "object",
    "properties": {
        "challenge": {"type": "string", "description": "one small task, a short sentence"},
        "why": {"type": "string", "description": "one sentence, in your own voice: why this one, now"},
        "aim": {"type": "string", "enum": ["discover", "for_us"]},
        "check_kind": {"type": "string", "enum": ["none", *CHECK_KINDS],
                       "description": "how code can count it done, or none"},
        "check_what": {"type": "string", "description": "the item or mob for has/gained/killed, else \"\""},
        "check_count": {"type": "integer", "description": "how many for has/gained/killed, else 0"},
    },
    "required": ["challenge", "why", "aim", "check_kind", "check_what", "check_count"],
    "additionalProperties": False,
}

PROMPT = """You are Jophiel, playing {game} as your own player, often next to the user. You just \
finished a goal and you are exploring: you pick your own next challenge, the way a curious player \
does. Pick ONE.

- Just beyond what you can do now: not something you've done (see "Done"), not something far out of \
reach (no diamond tools with no iron). The next step from what you carry and know is best: logs -> \
planks -> a table -> wooden tools -> stone tools -> a furnace -> iron; a place you haven't been; a \
mob you haven't dealt with; a move you don't have yet (you'll teach yourself what your skills can't do).
- Fit the moment. At sunset or with hostiles about, something for the two of you comes first (a \
shelter, light, a bed). If the user is doing something (building, mining, hurt), something that helps \
them is often better than wandering off. Otherwise, something new for you. Both are fine: choose.
- Small: something you could finish in a few minutes, with a clear end.
- Not one of "Failed before" unless you have a move or item now that you didn't have then.
- Not one of "Just offered" (you didn't take them).
- check: if the end can be counted, say how: has/gained N of an item ("planks", "logs" and Minecraft \
ids work), killed N of a mob, top_here (on top of what's around you), peak (the highest ground near), \
near_player (next to the user). Otherwise none.
"""


@dataclass(frozen=True)
class Challenge:
    challenge: str
    why: str
    aim: str
    check: dict | None


def text_for(summary: str, done: list[str], failed: list[str], offered: list[str]) -> str:
    def block(title: str, items: list[str]) -> str:
        return f"{title}:\n" + ("\n".join(f"- {item}" for item in items) if items else "- (none)")

    return "\n\n".join([
        "Your game right now:\n" + summary.strip(),
        block("Done (challenges you finished; don't repeat)", done),
        block("Failed before (with why)", failed),
        block("Just offered (not taken)", offered),
    ])


def check_from(output: dict) -> dict | None:
    kind = output.get("check_kind")
    if kind not in CHECK_KINDS:
        return None
    check: dict = {"kind": kind}
    if kind in ("has", "gained", "killed"):
        what = str(output.get("check_what") or "").strip()[:60]
        count = int(output.get("check_count") or 0)
        if not what or count < 1:
            return None
        check.update(what=what, count=min(count, 2304))
    return check


def pick(model: DriverModel, summary: str, *, done: list[str], failed: list[str], offered: list[str],
         game: str = "Minecraft") -> Challenge | None:
    """One challenge, or None when the model gave nothing usable."""
    reply = model.step(PROMPT.format(game=game), text_for(summary, done, failed, offered), [], SCHEMA)
    out = reply.output
    challenge = " ".join(str(out.get("challenge", "")).split())[:200]
    if not challenge:
        return None
    aim = out.get("aim") if out.get("aim") in ("discover", "for_us") else "discover"
    return Challenge(challenge, " ".join(str(out.get("why", "")).split())[:300], aim, check_from(out))
