"""``desktop.DesktopInput`` on macOS: Quartz events through ctypes (docs/VM_CONNECTION_PLAN.md, step 4).

Needs *Accessibility* permission for the program running the controller (``machine.input_capability``
checks it and says so when it's missing; without it macOS silently drops the events). Coordinates are
screen **points**, the space ``capture.ScreenCapture`` scales its pictures to, so a point on a screenshot
is where a click lands. Key names are the controller's own (``desktop.KEYS``); "win" is Command and "alt"
is Option. Keys a Mac keyboard doesn't have (Print Screen, Menu) are refused, not guessed.
"""
from __future__ import annotations

import ctypes
import time

# kVK_* virtual key codes (ANSI layout positions; Carbon's Events.h).
MAC_KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08,
    "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12,
    "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "equals": 0x18, "9": 0x19, "7": 0x1A,
    "minus": 0x1B, "8": 0x1C, "0": 0x1D, "rbracket": 0x1E, "o": 0x1F, "u": 0x20, "lbracket": 0x21,
    "i": 0x22, "p": 0x23, "enter": 0x24, "l": 0x25, "j": 0x26, "quote": 0x27, "k": 0x28, "semicolon": 0x29,
    "backslash": 0x2A, "comma": 0x2B, "slash": 0x2C, "n": 0x2D, "m": 0x2E, "period": 0x2F, "tab": 0x30,
    "space": 0x31, "backquote": 0x32, "backspace": 0x33, "esc": 0x35, "win": 0x37, "shift": 0x38,
    "capslock": 0x39, "alt": 0x3A, "ctrl": 0x3B, "volumeup": 0x48, "volumedown": 0x49, "volumemute": 0x4A,
    "f5": 0x60, "f6": 0x61, "f7": 0x62, "f3": 0x63, "f8": 0x64, "f9": 0x65, "f11": 0x67, "f10": 0x6D,
    "f12": 0x6F, "insert": 0x72, "home": 0x73, "pageup": 0x74, "delete": 0x75, "f4": 0x76, "end": 0x77,
    "f2": 0x78, "pagedown": 0x79, "f1": 0x7A, "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
}

# CGEventType and CGMouseButton values.
_DOWN = {"left": 1, "right": 3, "middle": 25}
_UP = {"left": 2, "right": 4, "middle": 26}
_DRAGGED = {"left": 6, "right": 7, "middle": 27}
_MOVED = 5
_BUTTON = {"left": 0, "right": 1, "middle": 2}
_HID_TAP = 0  # kCGHIDEventTap
_LINE_UNITS = 1  # kCGScrollEventUnitLine
_TEXT_CHUNK = 20  # UTF-16 units per synthetic key event


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def mac_keycode(key: str) -> int:
    from .desktop import Failed

    try:
        return MAC_KEYCODES[key]
    except KeyError:
        raise Failed(f"a Mac keyboard has no {key!r} key") from None


def utf16_chunks(text: str, size: int = _TEXT_CHUNK) -> list[list[int]]:
    """Text as UTF-16 code units, in chunks that never split a surrogate pair."""
    units = list(memoryview(text.encode("utf-16-le")).cast("H"))
    chunks, i = [], 0
    while i < len(units):
        end = min(i + size, len(units))
        if end < len(units) and 0xD800 <= units[end - 1] <= 0xDBFF:
            end -= 1  # keep a high surrogate with its low half
        chunks.append(units[i:end])
        i = end
    return chunks


class MacDesktopInput:
    """Keyboard and mouse on the Mac's desktop."""

    def __init__(self, sleep=None) -> None:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ref = ctypes.c_void_p
        cg.CGEventCreateKeyboardEvent.argtypes = [ref, ctypes.c_uint16, ctypes.c_bool]
        cg.CGEventCreateKeyboardEvent.restype = ref
        cg.CGEventCreateMouseEvent.argtypes = [ref, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        cg.CGEventCreateMouseEvent.restype = ref
        cg.CGEventCreateScrollWheelEvent2.argtypes = [ref, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                                                      ctypes.c_int32, ctypes.c_int32]
        cg.CGEventCreateScrollWheelEvent2.restype = ref
        cg.CGEventKeyboardSetUnicodeString.argtypes = [ref, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ref]
        cg.CGEventCreate.argtypes = [ref]
        cg.CGEventCreate.restype = ref
        cg.CGEventGetLocation.argtypes = [ref]
        cg.CGEventGetLocation.restype = CGPoint
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        cg.CGDisplayBounds.restype = CGRect
        cf.CFRelease.argtypes = [ref]
        self._cg, self._cf = cg, cf
        self._sleep = sleep or time.sleep
        self._held: set[str] = set()

    def _post(self, event) -> None:
        from .desktop import Failed

        if not event:
            raise Failed("macOS refused to make the input event")
        try:
            self._cg.CGEventPost(_HID_TAP, event)
        finally:
            self._cf.CFRelease(event)

    def _position(self) -> CGPoint:
        probe = self._cg.CGEventCreate(None)
        try:
            return self._cg.CGEventGetLocation(probe)
        finally:
            if probe:
                self._cf.CFRelease(probe)

    def key_down(self, key: str) -> None:
        self._post(self._cg.CGEventCreateKeyboardEvent(None, mac_keycode(key), True))

    def key_up(self, key: str) -> None:
        try:
            self._post(self._cg.CGEventCreateKeyboardEvent(None, mac_keycode(key), False))
        except Exception:  # noqa: BLE001 - releasing is best effort, never raises
            pass

    def type_text(self, text: str) -> None:
        for line_number, line in enumerate(text.replace("\r\n", "\n").split("\n")):
            if line_number:
                self.key_down("enter")
                self.key_up("enter")
            for chunk in utf16_chunks(line):
                buffer = (ctypes.c_uint16 * len(chunk))(*chunk)
                for down in (True, False):
                    event = self._cg.CGEventCreateKeyboardEvent(None, 0, down)
                    self._cg.CGEventKeyboardSetUnicodeString(event, len(chunk), buffer)
                    self._post(event)
                self._sleep(0.01)

    def move_to(self, x: int, y: int) -> None:
        dragging = next((b for b in ("left", "right", "middle") if b in self._held), None)
        kind = _DRAGGED[dragging] if dragging else _MOVED
        button = _BUTTON[dragging] if dragging else 0
        self._post(self._cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), button))

    def button_down(self, button: str) -> None:
        self._post(self._cg.CGEventCreateMouseEvent(None, _DOWN[button], self._position(), _BUTTON[button]))
        self._held.add(button)

    def button_up(self, button: str) -> None:
        self._held.discard(button)
        try:
            self._post(self._cg.CGEventCreateMouseEvent(None, _UP[button], self._position(), _BUTTON[button]))
        except Exception:  # noqa: BLE001
            pass

    def wheel(self, notches: int, horizontal: bool = False) -> None:
        # Here "down" / "right" notches are positive; Quartz scrolls content up with positive wheel1 values.
        step = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            vertical, sideways = (0, -step) if horizontal else (-step, 0)
            self._post(self._cg.CGEventCreateScrollWheelEvent2(None, _LINE_UNITS, 2, vertical, sideways, 0))
            self._sleep(0.015)

    def screen_bounds(self) -> tuple[int, int, int, int]:
        rect = self._cg.CGDisplayBounds(self._cg.CGMainDisplayID())
        return (int(rect.origin.x), int(rect.origin.y), int(rect.size.width), int(rect.size.height))

    def sensitive_in_front(self) -> bool:
        return False  # not detectable without more permissions: the owner's "I'm signing in" switch covers it

    def foreground(self) -> tuple[str | None, str | None]:
        return None, None
