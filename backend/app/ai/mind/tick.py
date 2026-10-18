"""The Minecraft source of her mind: one tick, what changed, how much time she has, the text.

Pure functions over plain dicts, so recording and replay build exactly the same
text. Inputs are the Minecraft pack's ``GET /state`` JSON, the pack notices that
arrived since the last tick, her plan, and the recent conversation. This module
is where Minecraft lives; the gate and the thinking only see its text and its
``MINECRAFT`` situation.

Pressure is decided by code, never by a model: it's how much time she has, and
it caps how hard she may think (no deep thinking while a zombie hits her).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.ai.mind.situation import Situation

TICK_SCHEMA = "mind-tick-v2"  # v2: her recent thoughts and what she is still thinking about
PRESSURES = ("urgent", "busy", "calm")
COMBAT_RADIUS = 8.0  # a hostile this close to her
LOW_AIR = 10  # of 20
MOVED = 2.0  # metres before "you moved" is listed
NEWS_RADIUS = 16.0  # something appearing or leaving farther away than this is background, not news
WORSE_BY = 2.0  # metres closer before the same hostile counts as a worse danger
TALK_KEPT = 6
THOUGHTS_KEPT = 5
HOSTILES = frozenset({
    "zombie", "husk", "drowned", "skeleton", "stray", "bogged", "creeper", "spider", "cave_spider", "enderman",
    "witch", "slime", "magma_cube", "phantom", "pillager", "vindicator", "evoker", "ravager", "silverfish",
    "zombie_villager", "blaze", "ghast", "piglin_brute", "hoglin", "zoglin", "wither_skeleton", "guardian",
    "elder_guardian", "shulker", "vex", "warden", "breeze",
})
MINECRAFT = Situation(
    "minecraft", "playing Minecraft as her own player next to {user}",
    examples={
        "none": "walking, following, mining as planned",
        "react": "a mob on her or the user, losing health, drowning (\"creeper!\")",
        "light": "the user placed, broke or threw something, a find, night coming",
        "hard": "her hands say it failed but she got the loot anyway, something she carried or placed is gone, "
                "the way her plan takes is blocked",
    },
)


@dataclass(frozen=True)
class TickInput:
    at: float  # Unix seconds
    state: dict  # the pack's GET /state (structured fields plus "text")
    notices: list[dict] = field(default_factory=list)  # pack notices since the last tick
    plan: dict | None = None  # the session plan: goal, steps (with state), constraints
    session_state: str = "running"
    talk: list[dict] = field(default_factory=list)  # [{"role", "content", "at"}], oldest first
    hands_note: str | None = None
    # Her own recent thoughts, oldest first ({"level", "text", "said", "at"}), and the hard
    # thought still running, if any: a problem she's already on isn't new every second.
    thoughts: list[dict] = field(default_factory=list)
    thinking: str | None = None
    # What she's teaching herself (services/game_practice.tick_line): "learning X: try 2 of 4",
    # or how it just ended. Each new try is news.
    learning: str | None = None


def is_hostile(entity: dict) -> bool:
    return entity.get("kind") == "hostile" or entity.get("label") in HOSTILES


def _air_at_risk(state: dict) -> float | None:
    """Her air, when it can run out: only in water. Out of water a low reading is
    either her air coming back after she surfaced or a bad reading (the pack used
    to report other creatures' air as hers), and neither is danger."""
    oxygen = state.get("oxygen")
    if not state.get("in_water") or not isinstance(oxygen, (int, float)):
        return None
    return oxygen


def pressure(tick: TickInput, previous: TickInput | None) -> tuple[str, str]:
    """(level, why): urgent when danger needs a reaction now, busy while her
    hands run a skill, calm otherwise."""
    state = tick.state
    near = [e for e in state.get("nearby") or [] if is_hostile(e) and e.get("distance", 99) <= COMBAT_RADIUS]
    if near:
        e = min(near, key=lambda e: e.get("distance", 99))
        return "urgent", f"{_name(e['label'])} {e['distance']:g} m from you"
    if previous is not None and _num(state.get("health")) < _num(previous.state.get("health")):
        return "urgent", f"you took damage ({_hearts(previous.state.get('health'))} -> {_hearts(state.get('health'))} hearts)"
    oxygen = _air_at_risk(state)
    if oxygen is not None and oxygen < LOW_AIR:
        return "urgent", f"running out of air ({oxygen}/20)"
    skill = (state.get("skill") or {}).get("name")
    if skill == "fight":
        return "urgent", "you're fighting"
    urgent = [n for n in tick.notices if n.get("urgency") == "urgent"]
    if urgent:
        return "urgent", urgent[-1].get("text", "something urgent")
    if any(n.get("kind") in ("player_hurt", "hostile_near_player") for n in tick.notices):
        n = next(n for n in reversed(tick.notices) if n.get("kind") in ("player_hurt", "hostile_near_player"))
        return "urgent", n.get("text", "danger near the user")
    if skill:
        return "busy", f"your hands are running {skill}"
    return "calm", "nothing running"


def changes(tick: TickInput, previous: TickInput | None) -> list[str]:
    """What's different since the previous tick, in short plain lines. The first
    tick of a session has no previous, so everything is new and it says so."""
    return [line for line, _ in _changes(tick, previous)]


def news(tick: TickInput, previous: TickInput | None) -> list[str]:
    """The changes worth a new thought. Background is left out: her own walking,
    the user switching between walking and running, things far away coming and
    going, air still above LOW_AIR, and her own words (she already knows them)."""
    return [line for line, is_news in _changes(tick, previous) if is_news]


def danger(state: dict) -> dict:
    """The danger she is in, to tell whether it got worse since she last reacted."""
    near = {e.get("label"): e.get("distance", 99) for e in state.get("nearby") or []
            if is_hostile(e) and e.get("distance", 99) <= COMBAT_RADIUS}
    return {"hostiles": near, "health": _num(state.get("health")), "oxygen": _air_at_risk(state)}


def worse(now: dict, before: dict) -> bool:
    """A new hostile close by, one WORSE_BY closer, health lost, or air running out."""
    if now["health"] < before["health"]:
        return True
    for label, distance in now["hostiles"].items():
        if label not in before["hostiles"] or distance <= before["hostiles"][label] - WORSE_BY:
            return True
    air, air_before = now.get("oxygen"), before.get("oxygen")
    return isinstance(air, (int, float)) and isinstance(air_before, (int, float)) and air <= 3 < air_before


def _changes(tick: TickInput, previous: TickInput | None) -> list[tuple[str, bool]]:
    """(line, is news) pairs; see ``changes`` and ``news``."""
    if previous is None:
        return [(line, True) for line in ["(first tick: everything below is new)"]
                + [f"noticed: {n.get('text', '')}" for n in tick.notices]]
    cur, old = tick.state, previous.state
    out: list[tuple[str, bool]] = []
    dt = max(1.0, tick.at - previous.at)

    for key, label in (("health", "health"), ("food", "food")):
        a, b = old.get(key), cur.get(key)
        if a is not None and b is not None and a != b:
            out.append((f"your {label} {a:g} -> {b:g}/20", True))
    a, b = old.get("oxygen"), cur.get("oxygen")
    if isinstance(b, (int, float)) and a != b and b < 20:
        out.append((f"your air {a} -> {b}/20", b < LOW_AIR and _air_at_risk(cur) is not None))
    if old.get("position") and cur.get("position"):
        moved = math.dist(_xyz(old["position"]), _xyz(cur["position"]))
        if moved >= MOVED:
            out.append((f"you moved {moved:.0f} m", False))
    if (old.get("held") or {}).get("name") != (cur.get("held") or {}).get("name"):
        out.append((f"you're now holding {_name((cur.get('held') or {}).get('name') or 'nothing')}", True))
    out.extend((line, True) for line in _inventory_changes(old.get("inventory") or [], cur.get("inventory") or []))

    before = {e.get("label"): e for e in old.get("nearby") or []}
    after = {e.get("label"): e for e in cur.get("nearby") or []}
    for label_, e in after.items():
        if label_ not in before and label_ not in _player_names(cur):
            out.append((f"{_name(label_)} appeared {e.get('distance', '?'):g} m {e.get('side', '')}".rstrip(),
                        _num(e.get("distance", 99)) <= NEWS_RADIUS))
    for label_ in sorted(set(before) - set(after), key=str):
        if label_ and label_ not in _player_names(old):
            e = before[label_]  # a threat leaving is news ("skeleton down"); a fish swimming off isn't
            out.append((f"{_name(label_)} is gone", is_hostile(e) and _num(e.get("distance", 99)) <= NEWS_RADIUS))

    old_players = {p["name"]: p for p in old.get("players") or []}
    for p in cur.get("players") or []:
        was = old_players.get(p["name"])
        if was is None:
            out.append((f"{p['name']} came into view", True))
            continue
        for key, word in (("moving", ""), ("holding", "holding "), ("pose", "")):
            if p.get(key) != was.get(key):
                out.append((f"{p['name']}: {word}{_name(str(was.get(key) or 'nothing'))} -> "
                            f"{word}{_name(str(p.get(key) or 'nothing'))}", key != "moving"))
        seen = {r["text"] for r in was.get("recent") or []}
        for r in p.get("recent") or []:
            if r["text"] not in seen or r.get("seconds_ago", 99) < dt:
                out.append((f"{p['name']} {_name(r['text'])}", True))
    for name in sorted(set(old_players) - {p["name"] for p in cur.get("players") or []}):
        out.append((f"{name} went out of view", True))

    for key, label in (("phase", "it's now"),):
        a, b = (old.get("time") or {}).get(key), (cur.get("time") or {}).get(key)
        if a != b:
            out.append((f"{label} {b}", True))
    if old.get("weather") != cur.get("weather"):
        out.append((f"weather: {cur.get('weather')}", True))
    out.extend(_place_changes(old.get("surroundings"), cur.get("surroundings")))

    a, b = (old.get("skill") or {}).get("name"), (cur.get("skill") or {}).get("name")
    if a != b:
        out.append((f"your hands: {a or 'idle'} -> {b or 'idle'}", True))
    goal_a, goal_b = (previous.plan or {}).get("goal"), (tick.plan or {}).get("goal")
    if goal_a != goal_b:
        out.append((f"new goal: {goal_b}", True))
    elif _active_step(previous.plan) != _active_step(tick.plan):
        out.append((f"next step: {_active_step(tick.plan) or 'none'}", True))
    if previous.session_state != tick.session_state:
        out.append((f"session {previous.session_state} -> {tick.session_state}", True))
    if tick.learning and tick.learning != previous.learning:
        out.append((f"learning: {tick.learning}", True))

    for n in tick.notices:
        out.append((f"noticed: {n.get('text', '')}", True))
    seen_talk = {(m.get("role"), m.get("content")) for m in previous.talk}
    for m in tick.talk:
        if (m.get("role"), m.get("content")) not in seen_talk:
            user = m.get("role") == "user"
            out.append((f"{'the user said' if user else 'you said'}: \"{m.get('content', '')}\"", user))
    return out


OUTDOORS = {"sky", "trees"}
PLACE_WORDS = {"underground": "you went underground", "roof": "you went under a roof"}


def _place_changes(old: dict | None, cur: dict | None) -> list[tuple[str, bool]]:
    """Going underground or back out, and a cave mouth coming into view (the pack's
    surroundings). A mouth counts as new when none was within 4 blocks of it last tick."""
    if not old or not cur:
        return []
    out: list[tuple[str, bool]] = []
    a, b = (old.get("overhead") or {}).get("kind"), (cur.get("overhead") or {}).get("kind")
    if a and b and a != b and not (a in OUTDOORS and b in OUTDOORS):
        out.append((PLACE_WORDS.get(b, "you're outdoors again"), True))
    if b in OUTDOORS:
        before = [o.get("position") or {} for o in old.get("openings") or []]
        for o in cur.get("openings") or []:
            p = o.get("position") or {}
            if any(math.dist((p.get("x", 0), p.get("z", 0)), (q.get("x", 0), q.get("z", 0))) < 4 for q in before):
                continue
            out.append((f"a cave or tunnel mouth came into view {o.get('distance', '?')} m {o.get('side', '')}".rstrip(),
                        _num(o.get("distance", 99)) <= NEWS_RADIUS))
    return out


def render(tick: TickInput, previous: TickInput | None) -> tuple[str, str, list[str], str]:
    """(text, pressure, changes, why): the whole tick as one text, changes first."""
    level, why = pressure(tick, previous)
    changed = changes(tick, previous)
    lines = ["Changed since last tick:"]
    lines += [f"- {c}" for c in changed] if changed else ["- nothing"]
    lines.append(f"Pressure: {level} ({why})")
    if tick.session_state != "running":
        lines.append(f"Session: {tick.session_state}")
    plan = tick.plan or {}
    if plan.get("goal"):
        step = _active_step(plan)
        lines.append(f"Goal: {plan['goal']}" + (f"; current step: {step}" if step else ""))
    if tick.hands_note:
        lines.append(f"Your hands' note: {tick.hands_note}")
    if tick.thoughts:
        lines.append("Your recent thoughts (oldest first):")
        for t in tick.thoughts[-THOUGHTS_KEPT:]:
            ago = max(0, round(tick.at - t.get("at", tick.at)))
            said = f' (you said: "{t["said"]}")' if t.get("said") else ""
            lines.append(f"  [{t.get('level')}] {t.get('text', '')}{said} ({ago} s ago)")
    if tick.thinking:
        lines.append(f"Still thinking hard about: {tick.thinking}")
    if tick.learning:
        lines.append(f"Teaching yourself a move: {tick.learning}")
    if tick.talk:
        lines.append("Recent talk (oldest first):")
        for m in tick.talk[-TALK_KEPT:]:
            ago = max(0, round(tick.at - m.get("at", tick.at)))
            who = "user" if m.get("role") == "user" else "you"
            lines.append(f"  {who} ({ago} s ago): {m.get('content', '')}")
    lines.append("Game state:")
    lines.append(tick.state.get("text", "").strip())
    return "\n".join(lines), level, changed, why


def _inventory_changes(old: list[dict], cur: list[dict]) -> list[str]:
    a = {i["name"]: i["count"] for i in old}
    b = {i["name"]: i["count"] for i in cur}
    out = []
    for name in sorted(set(a) | set(b)):
        diff = b.get(name, 0) - a.get(name, 0)
        if diff:
            out.append(f"you {'got' if diff > 0 else 'lost'} {_name(name)} x{abs(diff)}")
    return out


def _active_step(plan: dict | None) -> str | None:
    for step in (plan or {}).get("steps") or []:
        if step.get("state") == "active":
            return step.get("do")
    return None


def _player_names(state: dict) -> set[str]:
    return {p["name"] for p in state.get("players") or []}


def _xyz(p: dict) -> tuple[float, float, float]:
    return (p.get("x", 0), p.get("y", 0), p.get("z", 0))


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _hearts(health) -> str:
    return f"{_num(health) / 2:g}"


def _name(text: str) -> str:
    return text.replace("_", " ")
