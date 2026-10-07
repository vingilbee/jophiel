"""Global safety rules (plan §7.5). No profile can override these.

Physical keys form a closed vocabulary: a profile can only bind a key listed in
``ALLOWED_KEYS``. Keys that reach the operating system rather than the game are
listed separately in ``BLOCKED_KEYS`` so a profile that tries to bind one gets a
clear ``blocked_os_key`` error instead of "unknown key".

Some keys are harmless alone but dangerous together (Alt+F4 closes the window,
Ctrl+Esc opens the Start menu). Those are ``BLOCKED_COMBINATIONS``: a decision
whose keys could be down at the same time may never contain a whole combination.
The check is deliberately conservative: it looks at every key a decision touches,
not at exact timing overlaps.
"""

from __future__ import annotations

import string
from collections.abc import Iterable

MOUSE_BUTTONS: frozenset[str] = frozenset(
    {"MOUSE_LEFT", "MOUSE_RIGHT", "MOUSE_MIDDLE", "MOUSE_X1", "MOUSE_X2"}
)

KEYBOARD_KEYS: frozenset[str] = frozenset(
    set(string.ascii_uppercase)
    | set(string.digits)
    | {f"F{i}" for i in range(1, 13)}
    | {
        "SPACE", "ENTER", "TAB", "ESC", "BACKSPACE",
        "SHIFT", "CTRL", "ALT",
        "UP", "DOWN", "LEFT", "RIGHT",
        "MINUS", "EQUALS", "COMMA", "PERIOD", "SLASH", "SEMICOLON", "QUOTE",
        "LBRACKET", "RBRACKET", "BACKQUOTE",
    }
)

ALLOWED_KEYS: frozenset[str] = KEYBOARD_KEYS | MOUSE_BUTTONS

# Keys that act on the OS or session, never on the game. Not bindable at all.
BLOCKED_KEYS: frozenset[str] = frozenset(
    {
        "WIN", "LWIN", "RWIN", "META", "CMD", "SUPER", "APPS", "MENU",
        "DELETE", "DEL", "INSERT", "PRINTSCREEN", "PAUSE", "BREAK",
        "SLEEP", "POWER", "WAKE", "CAPSLOCK", "NUMLOCK", "SCROLLLOCK",
        "BROWSER_BACK", "BROWSER_HOME", "LAUNCH_MAIL", "LAUNCH_APP1", "LAUNCH_APP2",
    }
)

# Every key in a combination down together reaches the OS (window switch,
# close, Start menu, Task Manager).
BLOCKED_COMBINATIONS: tuple[frozenset[str], ...] = (
    frozenset({"ALT", "TAB"}),
    frozenset({"ALT", "F4"}),
    frozenset({"ALT", "ESC"}),
    frozenset({"ALT", "SPACE"}),
    frozenset({"CTRL", "ESC"}),
    frozenset({"CTRL", "SHIFT", "ESC"}),
)


def blocked_combination(keys: Iterable[str]) -> frozenset[str] | None:
    """Return the first blocked combination fully contained in ``keys``, if any."""
    present = set(keys)
    for combo in BLOCKED_COMBINATIONS:
        if combo <= present:
            return combo
    return None


def is_mouse_button(key: str) -> bool:
    return key in MOUSE_BUTTONS


# Hard ceilings that every profile's limits must stay under.
GLOBAL_MAX_VALID_FOR_MS = 5_000
GLOBAL_MAX_UNTIL_MS = 15_000
GLOBAL_MAX_EVENTS = 32
GLOBAL_MAX_TAPS_PER_S = 20
GLOBAL_MAX_DRAG_POINTS = 64
GLOBAL_MAX_UNTIL_CONDITIONS = 3
GLOBAL_MAX_HOLD_BUTTONS = 8
GLOBAL_MAX_TAP_HOLD_MS = 1_000
GLOBAL_MAX_AXIS_DEG_PER_S = 720.0
GLOBAL_MAX_AXIS_COUNTS_PER_S = 20_000.0
GLOBAL_MAX_SCROLL = 20
