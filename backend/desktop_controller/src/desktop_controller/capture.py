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

``sample`` is the reflexes' picture: the game window only, only while it is
the foreground window, small and grey, and it never leaves the VM.

``WindowsCapture`` is the real backend on Windows (Win32 via ctypes + ``mss`` for pixels), ``ScreenCapture``
the whole-screen one on macOS and Linux;
``FakeCapture`` draws a test picture anywhere, for tests and dry runs.
"""
from __future__ import annotations

import io
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
# A desktop screenshot travels as one link frame (link.MAX_FRAME_BYTES, minus room for the header).
SCREENSHOT_MAX_BYTES = 500 * 1024


@dataclass(frozen=True)
class Grab:
    state: TargetState
    jpeg: bytes | None = None
    width: int = 0
    height: int = 0


class CaptureBackend(Protocol):
    def grab(self, config: CaptureConfig) -> Grab: ...
    def observe(self, config: ObserveConfig) -> Grab: ...
    def sample(self, target_process: str, size: tuple[int, int]) -> Image.Image | None: ...
    # General desktop control (desktop.DesktopScreen); raises desktop.Refused when there's no picture.
    def screenshot(self, action): ...


def small_grey(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.convert("L").resize(size, Image.Resampling.BILINEAR)


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


def desktop_shot(image: Image.Image, screen: tuple[int, int, int, int], action, foreground=(None, None)):
    """A desktop screenshot (``desktop.Screenshot``) of ``image``, the primary screen whose rectangle is
    ``screen``: the whole screen fitted into the size limits, or a region of it, enlarged up to
    ``max_zoom`` so small text can be read. Raises ``desktop.Refused`` for a region off the screen."""
    from .desktop import Refused, Shot
    from .link import ScreenInfo

    left, top, width, height = screen
    if action.region is None:
        region = screen
        crop = image
    else:
        rx, ry, rw, rh = action.region
        x0, y0 = max(rx, left), max(ry, top)
        x1, y1 = min(rx + rw, left + width), min(ry + rh, top + height)
        if x1 - x0 < 8 or y1 - y0 < 8:
            raise Refused("that part of the screen is off the screen")
        region = (x0, y0, x1 - x0, y1 - y0)
        crop = image.crop((x0 - left, y0 - top, x1 - left, y1 - top))
    zoom = 1.0 if action.region is None else action.max_zoom
    scale = min(action.max_width / crop.width, action.max_height / crop.height, zoom)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    if size != crop.size:
        crop = crop.resize(size, Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BICUBIC)
    crop = crop.convert("RGB")
    for quality in (80, 65, 50, 35):  # it must fit in one link frame
        out = io.BytesIO()
        crop.save(out, format="JPEG", quality=quality)
        if out.tell() <= SCREENSHOT_MAX_BYTES:
            break
    else:
        raise Refused("the screenshot came out too large to send")
    process, title = foreground
    return Shot(out.getvalue(), ScreenInfo(
        region=region, size=size, screen=screen,
        foreground_process=(process or None) and process[:64], foreground_title=(title or None) and title[:200]))


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

    def sample(self, target_process: str, size: tuple[int, int]) -> Image.Image | None:
        return small_grey(self._picture(), size) if self.state == "ok" else None

    def screenshot(self, action):
        from .desktop import Refused

        self.grabs += 1
        if self.state != "ok":
            raise Refused("no picture: " + self.state)
        return desktop_shot(self._picture(), (0, 0, 1920, 1080), action, ("explorer.exe", "Fake desktop"))

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

    def screenshot(self, action):
        """General desktop control: the primary screen (or part of it) for the orchestrator. No picture
        while a Windows sign-in or permission prompt is in front."""
        from .desktop import Refused

        win = self._win
        foreground = win.foreground()
        if foreground and win.process_name(foreground) in SENSITIVE_PROCESSES:
            raise Refused("a Windows sign-in or permission prompt is in front; no picture while it is")
        sct = self._shooter()
        monitor = sct.monitors[1]  # the primary screen
        shot = sct.grab(monitor)
        after = win.foreground()
        if after != foreground and after and win.process_name(after) in SENSITIVE_PROCESSES:
            raise Refused("a Windows sign-in or permission prompt came up; no picture while it is")
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        screen = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])
        names = (win.process_name(after), win.window_title(after)) if after else (None, None)
        return desktop_shot(image, screen, action, names)

    def _grab_window(self, config: CaptureConfig) -> Grab:
        image = self._window_image(config.target_process or "")
        return image if isinstance(image, Grab) else encode(image, config.max_width, config.max_height)

    def observe(self, config: ObserveConfig) -> Grab:
        image = self._window_image(config.target_process)
        return image if isinstance(image, Grab) else encode_exact(image, config.width, config.height)

    def sample(self, target_process: str, size: tuple[int, int]) -> Image.Image | None:
        image = self._window_image(target_process)
        return None if isinstance(image, Grab) else small_grey(image, size)

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


class ScreenCapture:
    """macOS and Linux (X11): the whole primary screen only (docs/VM_CONNECTION_PLAN.md, step 4).

    There is no game-window scope here: ``game_window`` capture, observations and reflex samples say
    ``not_found``, so game sessions don't run on these machines; the preview and general desktop control do.
    On macOS the picture is scaled from Retina pixels to screen points, the space input uses, so a point
    on a screenshot is where a click lands. Neither OS's own password prompts are detected: the owner's
    "I'm signing in" switch is what stops capture there.
    """

    def __init__(self, *, retina_to_points: bool = False) -> None:
        import mss

        self._mss_factory = getattr(mss, "MSS", None) or mss.mss
        self._local = threading.local()
        self._to_points = retina_to_points

    def _shooter(self):
        if not hasattr(self._local, "sct"):
            self._local.sct = self._mss_factory()
        return self._local.sct

    def _screen(self) -> tuple[Image.Image, tuple[int, int, int, int]]:
        sct = self._shooter()
        monitor = sct.monitors[1]  # the primary screen
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        screen = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])
        if self._to_points and image.size != (screen[2], screen[3]):
            image = image.resize((screen[2], screen[3]), Image.Resampling.LANCZOS)
        return image, screen

    def grab(self, config: CaptureConfig) -> Grab:
        if config.scope != "desktop":
            return Grab("not_found")
        try:
            image, _ = self._screen()
        except Exception:  # noqa: BLE001 - permission withdrawn, display gone: no picture, said so
            return Grab("error")
        return encode(image, config.max_width, config.max_height)

    def observe(self, config: ObserveConfig) -> Grab:
        return Grab("not_found")

    def sample(self, target_process: str, size: tuple[int, int]) -> Image.Image | None:
        return None

    def screenshot(self, action):
        from .desktop import Refused

        try:
            image, screen = self._screen()
        except Exception as exc:  # noqa: BLE001
            raise Refused(f"couldn't capture the screen ({exc})") from None
        return desktop_shot(image, screen, action)


def default_backend() -> CaptureBackend | None:
    """This machine's capture backend (``machine.capture_backend``): None when it can't capture."""
    from .machine import capture_backend

    return capture_backend()
