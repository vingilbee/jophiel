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


# --------------------------------------------------------------------------- #
# General desktop control (desktop_controller.desktop): the whole desktop, no game window.
# Desktop apps read virtual-key codes, so keys go as VK codes; text goes as typed Unicode
# characters (any keyboard layout, any language); the pointer moves in absolute screen pixels.
# --------------------------------------------------------------------------- #

KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_HWHEEL = 0x1000

VK: dict[str, int] = {
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "printscreen": 0x2C, "menu": 0x5D, "minus": 0xBD, "equals": 0xBB, "lbracket": 0xDB,
    "rbracket": 0xDD, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE, "backquote": 0xC0, "comma": 0xBC,
    "period": 0xBE, "slash": 0xBF, "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD, "playpause": 0xB3,
}
# Keys Windows treats as "extended" (they share scancodes with the number pad otherwise).
EXTENDED_VK = frozenset({0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D, 0x2C})
DESKTOP_BUTTONS = {"left": "MOUSE_LEFT", "right": "MOUSE_RIGHT", "middle": "MOUSE_MIDDLE"}


def _vk_input(vk: int, up: bool) -> INPUT:
    item = INPUT(type=INPUT_KEYBOARD)
    item.u.ki.wVk = vk
    item.u.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VK else 0)
    return item


def _unicode_inputs(text: str) -> list[INPUT]:
    """Each character as typed text (UTF-16 units; newlines as Enter, tabs as Tab)."""
    out: list[INPUT] = []
    for char in text.replace("\r\n", "\n"):
        if char == "\n":
            out += [_vk_input(0x0D, False), _vk_input(0x0D, True)]
            continue
        if char == "\t":
            out += [_vk_input(0x09, False), _vk_input(0x09, True)]
            continue
        data = char.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            for up in (False, True):
                item = INPUT(type=INPUT_KEYBOARD)
                item.u.ki.wScan = unit
                item.u.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
                out.append(item)
    return out


def screen_absolute(x: int, y: int, desktop: Rect) -> tuple[int, int]:
    """A screen pixel -> SendInput's 0..65535 virtual-desktop coordinates."""
    nx = round((x - desktop.left) * 65535 / max(desktop.width - 1, 1))
    ny = round((y - desktop.top) * 65535 / max(desktop.height - 1, 1))
    return nx, ny


class WindowsDesktopInput:
    """``desktop.DesktopInput`` over the real Windows desktop."""

    def __init__(self, sleep=None):
        import time

        from .capture import SENSITIVE_PROCESSES

        self._win = win32()
        self._sensitive = SENSITIVE_PROCESSES
        self._sleep = sleep or time.sleep

    def _send(self, *inputs: INPUT) -> None:
        from .desktop import Failed

        try:
            self._win.send(*inputs)
        except OSError as exc:
            # Usually UIPI: the window in front runs as administrator and this controller doesn't.
            raise Failed("Windows blocked the input (the app in front may be running as administrator)") from exc

    def key_down(self, key: str) -> None:
        self._send(_vk_input(VK[key], up=False))

    def key_up(self, key: str) -> None:
        try:
            self._win.send(_vk_input(VK[key], up=True))
        except OSError:
            pass

    def type_text(self, text: str) -> None:
        inputs = _unicode_inputs(text)
        # In small batches with a short pause, so slow apps don't drop characters.
        for i in range(0, len(inputs), 40):
            self._send(*inputs[i:i + 40])
            self._sleep(0.01)

    def move_to(self, x: int, y: int) -> None:
        nx, ny = screen_absolute(x, y, self._win.virtual_screen())
        self._send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))

    def button_down(self, button: str) -> None:
        down, _, data = MOUSE_BUTTON_FLAGS[DESKTOP_BUTTONS[button]]
        self._send(_mouse_input(down, data=data))

    def button_up(self, button: str) -> None:
        _, up, data = MOUSE_BUTTON_FLAGS[DESKTOP_BUTTONS[button]]
        try:
            self._win.send(_mouse_input(up, data=data))
        except OSError:
            pass

    def wheel(self, notches: int, horizontal: bool = False) -> None:
        # The vertical wheel's positive delta is away from you (up); "down" notches are positive here.
        flag, sign = (MOUSEEVENTF_HWHEEL, 1) if horizontal else (MOUSEEVENTF_WHEEL, -1)
        step = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            self._send(_mouse_input(flag, data=sign * step * WHEEL_DELTA))
            self._sleep(0.015)

    def screen_bounds(self) -> tuple[int, int, int, int]:
        rect = self._win.virtual_screen()
        return rect.left, rect.top, rect.width, rect.height

    def sensitive_in_front(self) -> bool:
        foreground = self._win.foreground()
        return bool(foreground) and self._win.process_name(foreground) in self._sensitive

    def foreground(self) -> tuple[str | None, str | None]:
        hwnd = self._win.foreground()
        if not hwnd:
            return None, None
        return self._win.process_name(hwnd), self._win.window_title(hwnd)
