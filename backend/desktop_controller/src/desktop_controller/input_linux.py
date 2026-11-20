"""``desktop.DesktopInput`` on Linux X11: the XTest extension through ctypes (docs/VM_CONNECTION_PLAN.md,
step 4).

Only for an X11 session (``machine.input_capability`` refuses Wayland and says why). Needs libX11 and
libXtst (``libxtst6`` on Debian/Ubuntu). Coordinates are X screen pixels, the space ``mss`` captures in.
Typed text goes character by character through the keyboard's current layout; a character the layout
can't produce is refused rather than mistyped.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import time

X11_KEYSYMS: dict[str, str] = {
    "enter": "Return", "tab": "Tab", "esc": "Escape", "space": "space", "backspace": "BackSpace",
    "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right", "capslock": "Caps_Lock", "printscreen": "Print",
    "menu": "Menu", "minus": "minus", "equals": "equal", "lbracket": "bracketleft", "rbracket": "bracketright",
    "backslash": "backslash", "semicolon": "semicolon", "quote": "apostrophe", "backquote": "grave",
    "comma": "comma", "period": "period", "slash": "slash", "volumeup": "XF86AudioRaiseVolume",
    "volumedown": "XF86AudioLowerVolume", "volumemute": "XF86AudioMute", "playpause": "XF86AudioPlay",
    "shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L", "win": "Super_L",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}
# X pointer buttons: 4/5 are the wheel up/down, 6/7 left/right.
_BUTTONS = {"left": 1, "middle": 2, "right": 3}
_WHEEL = {(False, -1): 4, (False, 1): 5, (True, -1): 6, (True, 1): 7}


def keysym_name(key: str) -> str:
    """The X keysym name for one of the controller's key names (letters and digits are their own)."""
    return X11_KEYSYMS.get(key, key)


def char_keysym(char: str) -> int:
    """The keysym for a typed character: Latin-1 maps directly, anything else is 0x01000000 + code point."""
    code = ord(char)
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code
    return 0x01000000 | code


class X11DesktopInput:
    def __init__(self, sleep=None) -> None:
        from .desktop import Failed

        x11_path, xtst_path = ctypes.util.find_library("X11"), ctypes.util.find_library("Xtst")
        if not x11_path or not xtst_path:
            raise Failed("libX11 / libXtst aren't installed")
        x11, xtst = ctypes.cdll.LoadLibrary(x11_path), ctypes.cdll.LoadLibrary(xtst_path)
        x11.XInitThreads()
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XkbKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int, ctypes.c_int]
        x11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                              ctypes.c_ulong]
        display = x11.XOpenDisplay(None)
        if not display:
            raise Failed("couldn't open the X display (is DISPLAY set?)")
        self._x11, self._xtst, self._display = x11, xtst, display
        self._sleep = sleep or time.sleep

    def _keycode(self, keysym: int) -> int:
        return int(self._x11.XKeysymToKeycode(self._display, keysym))

    def _key(self, key: str, down: bool) -> None:
        from .desktop import Failed

        keysym = self._x11.XStringToKeysym(keysym_name(key).encode())
        code = self._keycode(keysym) if keysym else 0
        if not code:
            raise Failed(f"this keyboard layout has no {key!r} key")
        self._xtst.XTestFakeKeyEvent(self._display, code, int(down), 0)
        self._x11.XFlush(self._display)

    def key_down(self, key: str) -> None:
        self._key(key, True)

    def key_up(self, key: str) -> None:
        try:
            self._key(key, False)
        except Exception:  # noqa: BLE001 - releasing is best effort
            pass

    def type_text(self, text: str) -> None:
        from .desktop import Failed

        shift = self._keycode(self._x11.XStringToKeysym(b"Shift_L"))
        for char in text.replace("\r\n", "\n"):
            if char in "\n\t":
                name = "enter" if char == "\n" else "tab"
                self.key_down(name)
                self.key_up(name)
                continue
            keysym = char_keysym(char)
            code = self._keycode(keysym)
            if not code:
                raise Failed(f"this keyboard layout can't type {char!r}")
            plain = self._x11.XkbKeycodeToKeysym(self._display, code, 0, 0)
            shifted = plain != keysym
            if shifted and self._x11.XkbKeycodeToKeysym(self._display, code, 0, 1) != keysym:
                raise Failed(f"this keyboard layout can't type {char!r}")
            if shifted:
                self._xtst.XTestFakeKeyEvent(self._display, shift, 1, 0)
            self._xtst.XTestFakeKeyEvent(self._display, code, 1, 0)
            self._xtst.XTestFakeKeyEvent(self._display, code, 0, 0)
            if shifted:
                self._xtst.XTestFakeKeyEvent(self._display, shift, 0, 0)
            self._x11.XFlush(self._display)
            self._sleep(0.004)

    def move_to(self, x: int, y: int) -> None:
        self._xtst.XTestFakeMotionEvent(self._display, -1, x, y, 0)
        self._x11.XFlush(self._display)

    def button_down(self, button: str) -> None:
        self._xtst.XTestFakeButtonEvent(self._display, _BUTTONS[button], 1, 0)
        self._x11.XFlush(self._display)

    def button_up(self, button: str) -> None:
        try:
            self._xtst.XTestFakeButtonEvent(self._display, _BUTTONS[button], 0, 0)
            self._x11.XFlush(self._display)
        except Exception:  # noqa: BLE001
            pass

    def wheel(self, notches: int, horizontal: bool = False) -> None:
        step = 1 if notches > 0 else -1
        button = _WHEEL[(horizontal, step)]
        for _ in range(abs(notches)):
            self._xtst.XTestFakeButtonEvent(self._display, button, 1, 0)
            self._xtst.XTestFakeButtonEvent(self._display, button, 0, 0)
            self._x11.XFlush(self._display)
            self._sleep(0.015)

    def screen_bounds(self) -> tuple[int, int, int, int]:
        screen = self._x11.XDefaultScreen(self._display)
        return 0, 0, int(self._x11.XDisplayWidth(self._display, screen)), int(
            self._x11.XDisplayHeight(self._display, screen))

    def sensitive_in_front(self) -> bool:
        return False  # not detectable: the owner's "I'm signing in" switch covers password prompts

    def foreground(self) -> tuple[str | None, str | None]:
        return None, None
