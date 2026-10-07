"""Windows input driver: ``SendInput`` with scancodes, guarded to the game window.

Keys are sent as **scancodes** (games read those, not virtual-key codes), mouse
look as relative motion (games with raw input read that), pointer moves as
absolute positions over the virtual desktop.

**Input only reaches the game** (stage C policy, plan §7.5): before every press
or movement the game's window must be the foreground window. If it isn't, the
driver raises ``TargetLost`` and sends nothing; the runtime then releases
everything and the session pauses. Releasing a key is never guarded: letting go
must always work, whatever is in front.
"""
from __future__ import annotations

from .runtime import TargetLost
from .win32 import INPUT, INPUT_KEYBOARD, INPUT_MOUSE, Rect, win32


KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120
# Large relative moves are split so no single event jumps further than this.
MAX_RELATIVE_STEP = 127

# Scancode set 1. Extended keys carry 0xE0 in the high byte.
SCANCODES: dict[str, int] = {
    **{k: v for k, v in zip("QWERTYUIOP", range(0x10, 0x1A))},
    **{k: v for k, v in zip("ASDFGHJKL", range(0x1E, 0x27))},
    **{k: v for k, v in zip("ZXCVBNM", range(0x2C, 0x33))},
    **{k: v for k, v in zip("1234567890", range(0x02, 0x0C))},
    **{f"F{i}": 0x3A + i for i in range(1, 11)},
    "F11": 0x57, "F12": 0x58,
    "ESC": 0x01, "MINUS": 0x0C, "EQUALS": 0x0D, "BACKSPACE": 0x0E, "TAB": 0x0F,
    "LBRACKET": 0x1A, "RBRACKET": 0x1B, "ENTER": 0x1C, "CTRL": 0x1D,
    "SEMICOLON": 0x27, "QUOTE": 0x28, "BACKQUOTE": 0x29, "SHIFT": 0x2A,
    "COMMA": 0x33, "PERIOD": 0x34, "SLASH": 0x35, "ALT": 0x38, "SPACE": 0x39,
    "UP": 0xE048, "LEFT": 0xE04B, "RIGHT": 0xE04D, "DOWN": 0xE050,
}

# (down flag, up flag, mouseData)
MOUSE_BUTTON_FLAGS: dict[str, tuple[int, int, int]] = {
    "MOUSE_LEFT": (0x0002, 0x0004, 0),
    "MOUSE_RIGHT": (0x0008, 0x0010, 0),
    "MOUSE_MIDDLE": (0x0020, 0x0040, 0),
    "MOUSE_X1": (0x0080, 0x0100, 1),
    "MOUSE_X2": (0x0080, 0x0100, 2),
}


def _key_input(scancode: int, up: bool) -> INPUT:
    item = INPUT(type=INPUT_KEYBOARD)
    item.u.ki.wScan = scancode & 0xFF
    item.u.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (
        KEYEVENTF_EXTENDEDKEY if scancode > 0xFF else 0
    )
    return item


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    item = INPUT(type=INPUT_MOUSE)
    item.u.mi.dx, item.u.mi.dy, item.u.mi.dwFlags = dx, dy, flags
    item.u.mi.mouseData = data & 0xFFFFFFFF  # wheel deltas are signed in a DWORD
    return item


def relative_steps(dx: int, dy: int, step: int = MAX_RELATIVE_STEP) -> list[tuple[int, int]]:
    """Split a relative move into pieces no larger than ``step`` on either axis."""
    count = max(1, -(-max(abs(dx), abs(dy)) // step))
    out, done_x, done_y = [], 0, 0
    for i in range(1, count + 1):
        x, y = round(dx * i / count), round(dy * i / count)
        out.append((x - done_x, y - done_y))
        done_x, done_y = x, y
    return out


def absolute_coords(x: int, y: int, client: Rect, desktop: Rect) -> tuple[int, int]:
    """Client-area pixel -> SendInput's 0..65535 virtual-desktop coordinates."""
    sx, sy = client.left + x, client.top + y
    nx = round((sx - desktop.left) * 65535 / max(desktop.width - 1, 1))
    ny = round((sy - desktop.top) * 65535 / max(desktop.height - 1, 1))
    return nx, ny


class WindowsInput:
    """``InputBackend`` for the scheduler, bound to one game process."""

    def __init__(self, target_process: str):
        self._win = win32()
        self._target = target_process
        self._hwnd = None

    # -- the guard -----------------------------------------------------------

    def window(self):
        """The game window if it is usable for input right now, else raise."""
        win = self._win
        if self._hwnd is None or win.process_name(self._hwnd) != self._target.lower():
            self._hwnd = win.find_window(self._target)
        if self._hwnd is None:
            raise TargetLost("not_found")
        if win.is_minimized(self._hwnd):
            raise TargetLost("minimized")
        if not win.is_foreground(self._hwnd):
            raise TargetLost("not_foreground")
        return self._hwnd

    def client_rect(self) -> Rect:
        rect = self._win.client_rect(self.window())
        if rect is None or rect.width < 16 or rect.height < 16:
            raise TargetLost("minimized")
        return rect

    def viewport_size(self) -> tuple[int, int]:
        rect = self.client_rect()
        return rect.width, rect.height

    def cursor_visible(self) -> bool:
        return self._win.cursor_visible()

    # -- InputBackend --------------------------------------------------------

    def key_down(self, key: str) -> None:
        self.window()
        if key in MOUSE_BUTTON_FLAGS:
            down, _, data = MOUSE_BUTTON_FLAGS[key]
            self._win.send(_mouse_input(down, data=data))
        else:
            self._win.send(_key_input(SCANCODES[key], up=False))

    def key_up(self, key: str) -> None:
        # Never guarded: releasing must always work.
        if key in MOUSE_BUTTON_FLAGS:
            _, up, data = MOUSE_BUTTON_FLAGS[key]
            self._win.send(_mouse_input(up, data=data))
        elif key in SCANCODES:
            self._win.send(_key_input(SCANCODES[key], up=True))

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        self.window()
        self._win.send(*(_mouse_input(MOUSEEVENTF_MOVE, x, y) for x, y in relative_steps(dx, dy)))

    def mouse_move_to(self, x: int, y: int) -> None:
        client = self.client_rect()
        nx, ny = absolute_coords(x, y, client, self._win.virtual_screen())
        self._win.send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))

    def scroll(self, amount: int) -> None:
        """Positive ``amount`` scrolls down (towards you), negative up."""
        self.window()
        self._win.send(_mouse_input(MOUSEEVENTF_WHEEL, data=-amount * WHEEL_DELTA))
