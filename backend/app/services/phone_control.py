"""Phone control (frontend-phone/PLAN_PHASE_9.md): the backend's part of her using the phone's screen.

The phone does the seeing and the pressing (an accessibility service, on the real screen in front of the user);
the tools are ``app/ai/tools/phone_control.py`` and every action travels the phase 7 channel
(``services/phone_actions.act``). This module keeps what lives between those:

- **Whose turn it is.** Each reply turn that can use the phone takes the user's control turn (``begin_turn``): the
  newest turn id in Redis, announced on a per-user channel. An action of an older turn is answered ``taken_over``
  before it's sent, and one already waiting stops waiting (``phone_actions.act``): the user's new words end the old
  turn's control, she starts the new one from a fresh screen. The phone keeps the same rule on its side.
- **Screens as she reads them** (``render``): the numbered items and the text, in reading order, with a first line
  that names only the screen and the app (that line alone is what any record of her turn keeps:
  ``record_line``).
- **Short-term screen memory** (``remember`` / ``recent``): her last ``MEMORY_SCREENS`` screens as text only (no
  pictures), in this process's memory, each for ``MEMORY_SECONDS``, so "what was that song you saw?" can be answered
  in a later turn. Never in the database, never in Redis; a restart loses them. Wiped at once when the phone
  reports the master switch or Phone control off (``wipe``, from ``services/phone_state.report``: a per-user epoch
  in Redis, since the report reaches the API process and the memory lives in the chat worker). Remembered screens
  are for recall only: their numbers can't be acted on.
- **The turn's chat line** (``turn_line``): one per turn ("Used your phone in Spotify, 9 steps"), never one per tap.
- **A turn's steps** (``note_step`` / ``steps``): what her hands did, one line per action ("tapped [4] → Phone screen
  s12: Spotify in front"): the action, its numbers or points, and the screen's first line only, never what's on it
  (``record_line``). Kept in this process's memory like her screens (``STEP_TURNS`` turns, ``MEMORY_SECONDS``,
  wiped with them), so a turn the user's new words cut off is read back step by step by the next one
  (services/chat.py), not as "16 steps".
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

import redis

from app.core.config import get_settings

log = logging.getLogger(__name__)

MEMORY_SCREENS = 10
MEMORY_SECONDS = 30 * 60
TURN_TTL_SECONDS = 60 * 60
TEXT_CHARS = 12_000  # the phone's own cap on a screen's text; kept here too
FIELD_CHARS = 300



def _pointing() -> bool:
    """Whether her phone_tap_on is offered (its pointing model has a key: ai/tools/phone_grounding.available)."""
    return bool(get_settings().openrouter_api_key)

def _turn_key(user_id: uuid.UUID | str) -> str:
    return f"phone:control:turn:{user_id}"


def turns_channel(user_id: uuid.UUID | str) -> str:
    return f"phone:control:turns:{user_id}"


def _wipe_key(user_id: uuid.UUID | str) -> str:
    return f"phone:control:wipe:{user_id}"


def _client() -> redis.Redis | None:
    from app.services import phone_actions
    return phone_actions._client()


# --------------------------------------------------------------------------- #
# Whose turn it is
# --------------------------------------------------------------------------- #


def begin_turn(user_id: uuid.UUID, turn: str, *, client: redis.Redis | None = None) -> None:
    """This reply turn now holds the user's phone control: an older turn's actions answer ``taken_over``."""
    client = client or _client()
    if client is None:
        return
    try:
        previous = client.set(_turn_key(user_id), turn, ex=TURN_TTL_SECONDS, get=True)
        if previous is None or previous.decode() != turn:
            client.publish(turns_channel(user_id), turn)
    except redis.RedisError:
        log.warning("phone_control: the control turn couldn't be recorded")


def superseded(user_id: uuid.UUID, turn: str, *, client: redis.Redis | None = None) -> bool:
    """A newer reply turn holds the phone (unknown, or none recorded: not superseded)."""
    client = client or _client()
    if client is None:
        return False
    try:
        newest = client.get(_turn_key(user_id))
    except redis.RedisError:
        return False
    return newest is not None and newest.decode() != turn


def _last_key(user_id: uuid.UUID | str) -> str:
    return f"phone:control:last:{user_id}"


def touch(user_id: uuid.UUID | str, *, client: redis.Redis | None = None) -> None:
    """She's using the phone's screen now (services/coordinator: a wake ``on`` the phone moves instead of firing)."""
    client = client or _client()
    if client is None:
        return
    try:
        client.set(_last_key(user_id), str(time.time()), ex=TURN_TTL_SECONDS * 24)
    except redis.RedisError:
        pass


def last_used(user_id: uuid.UUID | str, *, client: redis.Redis | None = None) -> float | None:
    client = client or _client()
    if client is None:
        return None
    try:
        raw = client.get(_last_key(user_id))
        return float(raw) if raw else None
    except (redis.RedisError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# A screen as she reads it
# --------------------------------------------------------------------------- #


def _short(value, limit: int = FIELD_CHARS) -> str:
    words = " ".join(str(value or "").split())
    return words if len(words) <= limit else words[: limit - 1] + "…"


def header(screen: dict) -> str:
    """The screen's first line: its id and the app in front, nothing of what's on it."""
    app = _short(screen.get("app"), 80) or _short(screen.get("package"), 120) or "an unknown app"
    package = _short(screen.get("package"), 120)
    named = f"{app} ({package})" if package and package != app else app
    return f"Phone screen {screen.get('id')}: {named} in front"


def _item(item: dict) -> str:
    role = item.get("role") or "item"
    text, label, hint = _short(item.get("text")), _short(item.get("label")), _short(item.get("hint"))
    states = [name for name in ("focused", "selected", "disabled") if item.get(name)]
    if item.get("checked") is not None:
        states.append("checked" if item["checked"] else "unchecked")
    if item.get("password"):
        states.append("password: its text is hidden")
    if item.get("n") is None:
        what = text or label
        if role == "image":
            return f"  (image{': ' + what if what else ''})"
        return f"  {what}" if what else ""
    parts = [f"[{item['n']}] {role}"]
    if label:
        parts.append(f'"{label}"')
    if text and text != label:
        parts.append(f'text "{text}"' if role == "text_field" else f'"{text}"')
    elif role == "text_field" and not text:
        parts.append("(empty)")
    if hint:
        parts.append(f"hint: {hint}")
    if states:
        parts.append(f"({', '.join(states)})")
    return " ".join(parts)


def render(screen: dict, *, picture: bool) -> str:
    """The screen as text for her: the header, how she sees it, then the items in reading order."""
    lines = [header(screen) + "."]
    image, missing = screen.get("image") or {}, screen.get("image_missing")
    notes = ["keyboard up" if screen.get("keyboard") else "keyboard down"]
    if picture and image:
        width, height = image.get("width"), image.get("height")
        held = "landscape" if width and height and width > height else "portrait"
        step = image.get("grid")
        drawn = (f"numbered boxes and a coordinate grid drawn on: a line every {step} px, its value on every "
                 "edge (x along the top and bottom, y down the sides); read a point's pixels off those lines"
                 if step else "numbered boxes drawn on")
        notes.append(f"the phone is in {held}; its picture ({width}x{height}, {drawn}) follows; "
                     "tap_point, tap_points and swipe take that picture's pixels"
                     + ("; phone_tap_on takes what to tap, described, and a pointing model finds it" if _pointing() else ""))
    elif missing == "secure":
        notes.append("no picture: this app forbids screenshots (text only)")
    elif missing == "not_supported":
        notes.append("no picture: this phone's Android can't take one (text only)")
    elif missing == "failed" or not image:
        notes.append("no picture this time (text only)")
    lines.append("; ".join(notes).capitalize() + ".")
    lines.append("Items in reading order ([n] = act on it by that number; the rest is text to read):")
    used = sum(len(line) for line in lines)
    cut = bool(screen.get("truncated"))
    for item in screen.get("items") or []:
        line = _item(item)
        if not line:
            continue
        if used + len(line) > TEXT_CHARS:
            cut = True
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) == 3:
        lines.append("  (nothing readable: an app with little accessibility; use the picture and tap_point)")
    if cut:
        lines.append("(More on screen than this: the rest was cut. Scroll to read on.)")
    return "\n".join(lines)


def record_line(result: str) -> str:
    """What a record of her turn keeps of a phone-control tool's answer: its first line (the screen and the app),
    never what's on the screen (screens are never stored)."""
    first = (result or "").split("\n", 1)[0]
    return first[:200]


# --------------------------------------------------------------------------- #
# Short-term screen memory (this process only)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Remembered:
    id: str
    app: str
    text: str
    at: float
    epoch: int


_memory: dict[str, deque[Remembered]] = {}
_lock = threading.Lock()
# user id -> turn id -> (when it started, the epoch, its step lines); oldest turns dropped past STEP_TURNS.
_steps: dict[str, dict[str, tuple[float, int, list[str]]]] = {}


def _epoch(user_id: uuid.UUID | str, client: redis.Redis | None = None) -> int | None:
    """The user's wipe count (None: Redis unreadable, so nothing remembered is shown)."""
    client = client or _client()
    if client is None:
        return 0
    try:
        raw = client.get(_wipe_key(user_id))
    except redis.RedisError:
        return None
    return int(raw) if raw else 0


def wipe(user_id: uuid.UUID | str, *, client: redis.Redis | None = None) -> None:
    """Forget every screen she saw on this user's phone, in every process (the epoch) and in this one now."""
    with _lock:
        _memory.pop(str(user_id), None)
        _steps.pop(str(user_id), None)
    client = client or _client()
    if client is None:
        return
    from app.services import practice  # what her practice review would read of those screens goes too
    practice.wipe(user_id, client=client)
    try:
        client.incr(_wipe_key(user_id))
    except redis.RedisError:
        log.warning("phone_control: the screen-memory wipe couldn't be announced")


def remember(user_id: uuid.UUID | str, screen: dict, text: str, *, now: float | None = None) -> None:
    epoch = _epoch(user_id)
    if epoch is None:
        return
    app = _short(screen.get("app"), 80) or _short(screen.get("package"), 120) or "an unknown app"
    entry = Remembered(str(screen.get("id")), app, text, time.time() if now is None else now, epoch)
    with _lock:
        kept = _memory.setdefault(str(user_id), deque(maxlen=MEMORY_SCREENS))
        kept.append(entry)


def _control_off(user_id: uuid.UUID | str) -> bool:
    """The phone reports the master switch or Phone control off (then nothing of its screen is kept)."""
    try:
        from app.services import phone_state
        state = phone_state.read(user_id)
        return state is not None and state.get("control_on") is False
    except Exception:  # noqa: BLE001 - an unreadable state hides nothing it didn't already
        return False


def recent(user_id: uuid.UUID | str, *, now: float | None = None) -> list[Remembered]:
    """Her remembered screens, oldest first: none past ``MEMORY_SECONDS``, none from before a wipe, none while the
    phone reports control off."""
    now = time.time() if now is None else now
    epoch = _epoch(user_id)
    off = _control_off(user_id)
    with _lock:
        kept = _memory.get(str(user_id))
        if kept is None:
            return []
        if off:
            _memory.pop(str(user_id), None)
            _steps.pop(str(user_id), None)
            return []
        if epoch is None:  # whether a wipe happened can't be read: show nothing, keep nothing new either
            return []
        fresh = [e for e in kept if now - e.at < MEMORY_SECONDS and e.epoch == epoch]
        if len(fresh) != len(kept):
            kept.clear()
            kept.extend(fresh)
        return fresh


# --------------------------------------------------------------------------- #
# A turn's steps (this process only)
# --------------------------------------------------------------------------- #

STEP_TURNS = 20
STEPS_SHOWN = 30
_POINTS_SHOWN = 6


def _xy(x, y) -> str:
    return f"({x}, {y})"


def step_action(action: str, args: dict) -> str:
    """One action of hers as a step: what she did and where, never text she typed or anything on the screen."""
    n = f"[{args['n']}]" if args.get("n") is not None else ""
    if action == "read_screen":
        wait = int(args.get("wait_ms") or 0)
        return f"read the screen{f' after {wait} ms' if wait else ''}"
    if action in ("tap", "long_press"):
        return f"{'tapped' if action == 'tap' else 'long-pressed'} {n}"
    if action == "type_text":
        chars = len(str(args.get("text") or ""))
        return f"typed {chars} character{'s' if chars != 1 else ''} into {n}{' and pressed enter' if args.get('enter') else ''}"
    if action == "scroll":
        return f"scrolled {args.get('direction')}{f' in {n}' if n else ''}"
    if action == "tap_point":
        return f"tapped point {_xy(args.get('x'), args.get('y'))}"
    if action == "tap_points":
        points = list(args.get("points") or [])
        shown = ", ".join(_xy(*p) for p in points[:_POINTS_SHOWN]) + (", …" if len(points) > _POINTS_SHOWN else "")
        return f"tapped {len(points)} points {shown}"
    if action == "swipe":
        return f"swiped {_xy(args.get('x1'), args.get('y1'))} → {_xy(args.get('x2'), args.get('y2'))}"
    if action == "gesture":
        return f"{len(args.get('fingers') or [])}-finger gesture"
    if action == "press":
        return f"pressed {args.get('key')}"
    return action


def note_step(user_id: uuid.UUID | str, turn: str, index: int, line: str, *, now: float | None = None) -> None:
    """Step ``index`` (0-based) of a turn: added, or replaced once its answer is known."""
    epoch = _epoch(user_id)
    if epoch is None:
        return
    now = time.time() if now is None else now
    with _lock:
        turns = _steps.setdefault(str(user_id), {})
        started, kept_epoch, lines = turns.get(turn) or (now, epoch, [])
        if kept_epoch != epoch:
            started, lines = now, []
        if index < len(lines):
            lines[index] = line
        else:
            lines.append(line)
        turns[turn] = (started, epoch, lines)
        while len(turns) > STEP_TURNS:
            turns.pop(min(turns, key=lambda t: turns[t][0]))


def steps(user_id: uuid.UUID | str, turn: uuid.UUID | str, *, now: float | None = None) -> list[str]:
    """A turn's steps, oldest first (none past ``MEMORY_SECONDS``, none from before a wipe)."""
    now = time.time() if now is None else now
    epoch = _epoch(user_id)
    off = _control_off(user_id)
    with _lock:
        if off:
            _steps.pop(str(user_id), None)
            return []
        kept = (_steps.get(str(user_id)) or {}).get(str(turn))
        if kept is None or epoch is None or kept[1] != epoch or now - kept[0] >= MEMORY_SECONDS:
            return []
        return list(kept[2])


def steps_text(lines: list[str]) -> str:
    """The steps as one clause for her history: the last ``STEPS_SHOWN`` of them, numbered."""
    if not lines:
        return ""
    start = max(0, len(lines) - STEPS_SHOWN)
    shown = "; ".join(f"{i + 1}. {line}" for i, line in enumerate(lines[start:], start))
    return f"step by step{f' (the first {start} not shown)' if start else ''}: {shown}"


# --------------------------------------------------------------------------- #
# The turn's one chat line
# --------------------------------------------------------------------------- #


def _apps(apps: list[str]) -> str:
    if len(apps) == 1:
        return apps[0]
    if len(apps) <= 3:
        return ", ".join(apps[:-1]) + " and " + apps[-1]
    return ", ".join(apps[:2]) + f" and {len(apps) - 2} more"


def turn_line_id(turn: uuid.UUID | str) -> uuid.UUID:
    """The id of a turn's one chat line: the same for the turn however it ended, so her history can find it."""
    return uuid.uuid5(uuid.UUID(str(turn)), "phone-control")


def turn_line(*, apps: list[str], steps: int, acted: bool, taken_over: bool = False,
              at_limit: bool = False) -> str:
    """What the chat shows for a turn of hers on the phone: where and how many steps, never what she saw."""
    where = f" in {_apps(apps)}" if apps else ""
    if acted:
        text = f"Used your phone{where}, {steps} step{'s' if steps != 1 else ''}"
    else:
        text = f"Looked at your phone's screen{where}"
    if taken_over:
        text += " (you took over)"
    elif at_limit:
        text += " (stopped at the step limit)"
    return text
