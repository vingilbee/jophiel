"""Plan steps that end by what's true, counted by code.

A step's ``done_when`` is words her hands judge after each skill ends. That missed steps
reached some other way (seen live, 2026-10-17): a lesson killed the sheep but the step
"kill a sheep" went on for two minutes; the pickup mode grabbed the bed while her hands
kept trying to break it; she built 11 blocks past the top of an ice pillar. A ``check``
says the same thing in a form code can count, and her hands look at it every second,
even while a skill runs (app/ai/desktop/skill_driver.py).

Kinds (``what`` and ``count`` per kind):
- ``has``: she carries at least ``count`` of ``what``;
- ``gained``: ``count`` more of ``what`` than when the step began;
- ``killed``: ``count`` ``what`` killed since the step began (the pack's ``kills``);
- ``top_here``: nothing right next to her is higher than where she stands (a pillar, a wall);
- ``peak``: no higher ground anywhere near (the top of a hill or mountain);
- ``near_player``: within ``count`` m (default 3) of the player ``what``.

An item ``what`` is a Minecraft id, or a family word that matches ids ending in it: "planks"
counts birch_planks and oak_planks together, "log" every log.
"""
from __future__ import annotations

KINDS = ("has", "gained", "killed", "top_here", "peak", "near_player")
NEAR_DEFAULT = 3.0


def inventory(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state.get("inventory") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
    return counts


def kills(state: dict) -> dict[str, int]:
    return {k: int(v) for k, v in (state.get("kills") or {}).items() if isinstance(v, (int, float))}


def baseline(state: dict) -> dict:
    """What a step's counted checks count from."""
    return {"inventory": inventory(state), "kills": kills(state)}


def _matching(counts: dict[str, int], what: str) -> int:
    what = what.strip().lower().removeprefix("minecraft:").replace(" ", "_")
    if what in counts:
        return counts[what]
    words = {what, what[:-1]} if what.endswith("s") else {what}  # "logs" counts oak_log too
    return sum(n for name, n in counts.items() if any(name == w or name.endswith("_" + w) for w in words))


def met(check: dict | None, state: dict, start: dict | None) -> str | None:
    """Why the check is true now, in a few plain words, or None (not true, or not checkable)."""
    if not check or not isinstance(state, dict):
        return None
    kind, what = check.get("kind"), (check.get("what") or "").strip()
    count = check.get("count")
    start = start or {"inventory": {}, "kills": {}}
    if kind in ("has", "gained", "killed") and not what:
        return None
    need = int(count) if isinstance(count, (int, float)) and count > 0 else 1
    if kind == "has":
        have = _matching(inventory(state), what)
        return f"you have {have} {what}" if have >= need else None
    if kind == "gained":
        got = _matching(inventory(state), what) - _matching(start.get("inventory") or {}, what)
        return f"you got {got} more {what}" if got >= need else None
    if kind == "killed":
        done = _matching(kills(state), what) - _matching(start.get("kills") or {}, what)
        return f"you killed {done} {what}" if done >= need else None
    land = (state.get("terrain") or {}).get("land")
    if kind == "top_here":
        close = (land or {}).get("close")
        return "nothing next to you is higher than where you stand" if close and close.get("rel", 1) <= 0 else None
    if kind == "peak":
        return "you're on the highest ground near" if land and land.get("peak") is None else None
    if kind == "near_player":
        within = float(count) if isinstance(count, (int, float)) and count > 0 else NEAR_DEFAULT
        for entity in state.get("nearby") or []:
            if entity.get("label") == what and float(entity.get("distance", 99)) <= within:
                return f"you're {entity.get('distance')} m from {what}"
        return None
    return None


def describe(check: dict | None) -> str | None:
    """The check in words, for her hands' and her own plan lines."""
    if not check:
        return None
    kind, what, count = check.get("kind"), check.get("what"), check.get("count")
    return {
        "has": f"has {count or 1} {what}",
        "gained": f"gained {count or 1} {what} during this step",
        "killed": f"killed {count or 1} {what} during this step",
        "top_here": "nothing next to you higher than where you stand",
        "peak": "on the highest ground near",
        "near_player": f"within {count or NEAR_DEFAULT:g} m of {what}",
    }.get(kind)


def change(before: dict | None, state: dict) -> str | None:
    """What was counted while a skill ran: 'mutton +2, white_wool +1; killed sheep 2', or None."""
    if not before:
        return None
    parts = []
    inv_before, inv_now = before.get("inventory") or {}, inventory(state)
    items = [f"{name} {inv_now.get(name, 0) - inv_before.get(name, 0):+d}" for name in sorted(set(inv_before) | set(inv_now))
             if inv_now.get(name, 0) != inv_before.get(name, 0)]
    if items:
        parts.append(", ".join(items[:8]))
    kills_before, kills_now = before.get("kills") or {}, kills(state)
    killed = [f"{name} {kills_now[name] - kills_before.get(name, 0)}" for name in sorted(kills_now)
              if kills_now[name] > kills_before.get(name, 0)]
    if killed:
        parts.append("killed " + ", ".join(killed))
    return "; ".join(parts) or None
