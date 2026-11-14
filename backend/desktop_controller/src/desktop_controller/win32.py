"""Win32 plumbing shared by screen capture and input (Windows guest only).

Every function signature is declared: ctypes defaults to C ``int``, which
truncates 64-bit window and process handles. Import only on Windows.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PureWindowsPath

GA_ROOTOWNER = 3
CURSOR_SHOWING = 0x00000001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

# --- SendInput structures (sizes must match the OS's, so all three union members are declared) ---
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hCursor", w.HANDLE), ("ptScreenPos", w.POINT)]


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


class Win32:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k = self.user32, self.kernel32
        self.enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        for fn, args, res in (
            (u.EnumWindows, [self.enum_proc, w.LPARAM], w.BOOL),
            (u.IsWindowVisible, [w.HWND], w.BOOL),
            (u.IsIconic, [w.HWND], w.BOOL),
            (u.GetForegroundWindow, [], w.HWND),
            (u.GetAncestor, [w.HWND, w.UINT], w.HWND),
            (u.GetWindowThreadProcessId, [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            (u.GetClientRect, [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            (u.ClientToScreen, [w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            (u.GetSystemMetrics, [ctypes.c_int], ctypes.c_int),
            (u.GetCursorInfo, [ctypes.POINTER(CURSORINFO)], w.BOOL),
            (u.GetWindowTextLengthW, [w.HWND], ctypes.c_int),
            (u.GetWindowTextW, [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.SendInput, [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int], w.UINT),
            (k.OpenProcess, [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (k.QueryFullProcessImageNameW, [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            (k.CloseHandle, [w.HANDLE], w.BOOL),
        ):
            fn.argtypes, fn.restype = args, res
        # Physical pixels, so window coordinates match the screen on scaled displays.
        try:
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            pass

    # -- windows -------------------------------------------------------------

    def process_name(self, hwnd) -> str | None:
        pid = w.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            size = w.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return PureWindowsPath(buffer.value).name.lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def find_window(self, process: str):
        """The visible top-level window of ``process`` (an exe name), if any."""
        found = []
        want = process.lower()

        @self.enum_proc
        def visit(hwnd, _):
            if self.user32.IsWindowVisible(hwnd) and self.process_name(hwnd) == want:
                found.append(hwnd)
                return False
            return True

        self.user32.EnumWindows(visit, 0)
        return found[0] if found else None

    def foreground(self):
        return self.user32.GetForegroundWindow()

    def is_foreground(self, hwnd, foreground=None) -> bool:
        """``hwnd`` or one of its own popups is the active window."""
        foreground = self.foreground() if foreground is None else foreground
        if not foreground:
            return False
        return foreground == hwnd or self.user32.GetAncestor(foreground, GA_ROOTOWNER) == hwnd

    def window_title(self, hwnd) -> str | None:
        length = self.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(min(length, 512) + 1)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value or None

    def is_minimized(self, hwnd) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def client_rect(self, hwnd) -> Rect | None:
        """The window's client area in screen pixels."""
        rect = w.RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        origin = w.POINT(0, 0)
        self.user32.ClientToScreen(hwnd, ctypes.byref(origin))
        return Rect(origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)

    def cursor_visible(self) -> bool:
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(CURSORINFO)
        if not self.user32.GetCursorInfo(ctypes.byref(info)):
            return True  # unknown: assume a pointer is showing (the more restrictive context)
        return bool(info.flags & CURSOR_SHOWING)

    def virtual_screen(self) -> Rect:
        m = self.user32.GetSystemMetrics
        return Rect(m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN), m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))

    # -- input ---------------------------------------------------------------

    def send(self, *inputs: INPUT) -> None:
        array = (INPUT * len(inputs))(*inputs)
        sent = self.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise OSError(ctypes.get_last_error(), "SendInput was blocked")


@lru_cache(maxsize=1)
def win32() -> Win32:
    return Win32()
