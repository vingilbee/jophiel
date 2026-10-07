"""Seeing the VM's screen (plan §10, §23). Two scopes, chosen by the owner:

- ``desktop``: the VM's whole primary screen. The VM is a dedicated machine,
  so this is the default; the owner presses "I'm signing in" before typing a
  password. As a backstop, while a Windows sign-in or admin-permission prompt
  (``SENSITIVE_PROCESSES``) is in front, no picture is taken at all.
- ``game_window``: only the window of the profile's game process, and only
  while it is the **foreground** window. Anything covering the game (the
  launcher's login page, a browser) means no picture, so signing in is safe
  even without the button.

In both, the caller re-checks the capture setting after each grab and throws
away a frame taken under an older one (see ``connection``).

``WindowsCapture`` is the real backend (Win32 via ctypes + ``mss`` for pixels);
``FakeCapture`` draws a test picture anywhere, for tests and dry runs.
"""
from __future__ import annotations

import io
import sys
import threading
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol

from PIL import Image

from .link import CaptureConfig, TargetState

# Windows' own credential and elevation prompts. Whole-screen capture withholds
# the picture while one of these is the foreground window.
SENSITIVE_PROCESSES = frozenset({
    "credentialuibroker.exe",   # "Windows Security" / Microsoft account sign-in prompts
    "consent.exe",              # UAC (normally on the secure desktop, which can't be captured anyway)
    "logonui.exe",              # lock and sign-in screen
    "lockapp.exe",              # lock screen
})

JPEG_QUALITY = 70


@dataclass(frozen=True)
class Grab:
    state: TargetState
    jpeg: bytes | None = None
    width: int = 0
    height: int = 0


class CaptureBackend(Protocol):
    def grab(self, config: CaptureConfig) -> Grab: ...


def encode(image: Image.Image, max_width: int, max_height: int) -> Grab:
    """Downscale to fit (never upscale) and JPEG-encode."""
    image = image.convert("RGB")
    image.thumbnail((max_width, max_height), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY)
    return Grab("ok", out.getvalue(), image.width, image.height)


class FakeCapture:
    """A stand-in game window: a gradient with a counter, always foreground."""

    def __init__(self, state: TargetState = "ok"):
        self.state = state
        self.grabs = 0

    def grab(self, config: CaptureConfig) -> Grab:
        self.grabs += 1
        if self.state != "ok":
            return Grab(self.state)
        image = Image.new("RGB", (1920, 1080), (30, 90, 40))
        image.paste((200, 200, 60), (60 * (self.grabs % 20), 450, 60 * (self.grabs % 20) + 120, 570))
        return encode(image, config.max_width, config.max_height)


class WindowsCapture:
    """Win32: find the game's window by process, require it to be foreground,
    grab its client area from the screen."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        import mss

        self._ctypes, self._wt = ctypes, wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declare every signature: ctypes defaults to C int, which truncates
        # 64-bit window and process handles.
        u, k, w = self._user32, self._kernel32, wintypes
        self._enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        for fn, args, res in (
            (u.EnumWindows, [self._enum_proc, w.LPARAM], w.BOOL),
            (u.IsWindowVisible, [w.HWND], w.BOOL),
            (u.IsIconic, [w.HWND], w.BOOL),
            (u.GetForegroundWindow, [], w.HWND),
            (u.GetAncestor, [w.HWND, w.UINT], w.HWND),
            (u.GetWindowThreadProcessId, [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            (u.GetClientRect, [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            (u.ClientToScreen, [w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            (k.OpenProcess, [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (k.QueryFullProcessImageNameW, [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            (k.CloseHandle, [w.HANDLE], w.BOOL),
        ):
            fn.argtypes, fn.restype = args, res
        # Physical pixels, so window coordinates match the screen on scaled displays.
        try:
            self._user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            pass
        # mss 10.2 renamed mss.mss to mss.MSS; support both. Its Windows GDI
        # handles belong to the thread that made them, and grabs run on a
        # worker thread, so each thread gets its own instance.
        self._mss_factory = getattr(mss, "MSS", None) or mss.mss
        self._local = threading.local()

    def _process_name(self, hwnd) -> str | None:
        ctypes, wt = self._ctypes, self._wt
        pid = wt.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self._kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wt.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return PureWindowsPath(buffer.value).name.lower()
        finally:
            self._kernel32.CloseHandle(handle)

    def _game_window(self, target_process: str):
        """The visible top-level window of the game process, if any."""
        found = []
        want = target_process.lower()

        @self._enum_proc
        def visit(hwnd, _):
            if self._user32.IsWindowVisible(hwnd) and self._process_name(hwnd) == want:
                found.append(hwnd)
                return False
            return True

        self._user32.EnumWindows(visit, 0)
        return found[0] if found else None

    def _shooter(self):
        if not hasattr(self._local, "sct"):
            self._local.sct = self._mss_factory()
        return self._local.sct

    def grab(self, config: CaptureConfig) -> Grab:
        if config.scope == "desktop":
            return self._grab_desktop(config)
        if not config.target_process:
            return Grab("not_found")
        return self._grab_window(config)

    def _grab_desktop(self, config: CaptureConfig) -> Grab:
        foreground = self._user32.GetForegroundWindow()
        if foreground and self._process_name(foreground) in SENSITIVE_PROCESSES:
            return Grab("sensitive")
        sct = self._shooter()
        shot = sct.grab(sct.monitors[1])  # the primary screen
        after = self._user32.GetForegroundWindow()
        if after != foreground and after and self._process_name(after) in SENSITIVE_PROCESSES:
            return Grab("sensitive")  # a prompt appeared during the grab: drop it
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return encode(image, config.max_width, config.max_height)

    def _grab_window(self, config: CaptureConfig) -> Grab:
        ctypes, wt = self._ctypes, self._wt
        max_width, max_height = config.max_width, config.max_height
        hwnd = self._game_window(config.target_process)
        if hwnd is None:
            return Grab("not_found")
        if self._user32.IsIconic(hwnd):
            return Grab("minimized")
        foreground = self._user32.GetForegroundWindow()
        root = self._user32.GetAncestor(foreground, 3) if foreground else None  # GA_ROOTOWNER
        if foreground != hwnd and root != hwnd:
            return Grab("not_foreground")
        rect = wt.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return Grab("error")
        origin = wt.POINT(0, 0)
        self._user32.ClientToScreen(hwnd, ctypes.byref(origin))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 16 or height < 16:
            return Grab("minimized")
        shot = self._shooter().grab({"left": origin.x, "top": origin.y, "width": width, "height": height})
        # Re-check after grabbing: if another window came to the front during
        # the grab, the pixels may show it, so drop them.
        if self._user32.GetForegroundWindow() != foreground:
            return Grab("not_foreground")
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return encode(image, max_width, max_height)


def default_backend() -> CaptureBackend | None:
    """The real backend on Windows; None elsewhere (no capture at all)."""
    if sys.platform != "win32":
        return None
    return WindowsCapture()
