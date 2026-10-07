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
from typing import Protocol

from PIL import Image

from .link import CaptureConfig, ObserveConfig, TargetState

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
    def observe(self, config: ObserveConfig) -> Grab: ...


def encode_exact(image: Image.Image, width: int, height: int) -> Grab:
    """Resize to exactly ``width`` x ``height`` (model observations: decision
    points are fractions of this frame, mapped per axis to the game window)."""
    image = image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY)
    return Grab("ok", out.getvalue(), width, height)


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
        return encode(self._picture(), config.max_width, config.max_height)

    def observe(self, config: ObserveConfig) -> Grab:
        self.grabs += 1
        if self.state != "ok":
            return Grab(self.state)
        return encode_exact(self._picture(), config.width, config.height)

    def _picture(self) -> Image.Image:
        image = Image.new("RGB", (1920, 1080), (30, 90, 40))
        image.paste((200, 200, 60), (60 * (self.grabs % 20), 450, 60 * (self.grabs % 20) + 120, 570))
        return image


class WindowsCapture:
    """Win32: the whole primary screen, or the game's window while it is the
    foreground window."""

    def __init__(self) -> None:
        import mss

        from .win32 import win32

        self._win = win32()
        # mss 10.2 renamed mss.mss to mss.MSS; support both. Its Windows GDI
        # handles belong to the thread that made them, and grabs run on a
        # worker thread, so each thread gets its own instance.
        self._mss_factory = getattr(mss, "MSS", None) or mss.mss
        self._local = threading.local()

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
        win = self._win
        foreground = win.foreground()
        if foreground and win.process_name(foreground) in SENSITIVE_PROCESSES:
            return Grab("sensitive")
        sct = self._shooter()
        shot = sct.grab(sct.monitors[1])  # the primary screen
        after = win.foreground()
        if after != foreground and after and win.process_name(after) in SENSITIVE_PROCESSES:
            return Grab("sensitive")  # a prompt appeared during the grab: drop it
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return encode(image, config.max_width, config.max_height)

    def _grab_window(self, config: CaptureConfig) -> Grab:
        image = self._window_image(config.target_process or "")
        return image if isinstance(image, Grab) else encode(image, config.max_width, config.max_height)

    def observe(self, config: ObserveConfig) -> Grab:
        image = self._window_image(config.target_process)
        return image if isinstance(image, Grab) else encode_exact(image, config.width, config.height)

    def _window_image(self, target_process: str) -> Image.Image | Grab:
        """The game window's pixels, or a ``Grab`` saying why there are none."""
        win = self._win
        hwnd = win.find_window(target_process)
        if hwnd is None:
            return Grab("not_found")
        if win.is_minimized(hwnd):
            return Grab("minimized")
        foreground = win.foreground()
        if not win.is_foreground(hwnd, foreground):
            return Grab("not_foreground")
        rect = win.client_rect(hwnd)
        if rect is None:
            return Grab("error")
        if rect.width < 16 or rect.height < 16:
            return Grab("minimized")
        shot = self._shooter().grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        # Re-check after grabbing: if another window came to the front during
        # the grab, the pixels may show it, so drop them.
        if win.foreground() != foreground:
            return Grab("not_foreground")
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def default_backend() -> CaptureBackend | None:
    """The real backend on Windows; None elsewhere (no capture at all)."""
    if sys.platform != "win32":
        return None
    return WindowsCapture()
