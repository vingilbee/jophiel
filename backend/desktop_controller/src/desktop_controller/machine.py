"""This machine, behind one interface (docs/VM_CONNECTION_PLAN.md, step 4).

Everything platform-specific the rest of the controller needs: which OS it is, whether screen capture and
input are available right now (and, when they aren't, why: a missing OS permission, Wayland, no display),
and the real capture and input backends. Discovery, pairing, the link and status reporting are shared;
only what is behind these functions differs.

- **Windows**: whole-screen and game-window capture, desktop and game input (the original controller).
- **macOS**: whole-screen capture and desktop input. Capture needs *Screen Recording* permission and
  input needs *Accessibility* permission for the program running the controller; each is reported
  separately when missing. No game-window capture or game sessions (those report ``no_input``).
- **Linux**: whole-screen capture and desktop input on **X11**. Wayland is reported as unavailable for
  both: it has no general way for a program to see the screen or press keys without a portal prompt.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform as _platform
import socket
import sys
from functools import lru_cache

from .link import Capability, ControllerPlatform, ControllerStatus


def current_platform() -> ControllerPlatform:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def machine_name() -> str:
    name = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "this machine").strip()
    return name.removesuffix(".local")[:64] or "this machine"


def os_description() -> str:
    plat = current_platform()
    if plat == "macos":
        return f"macOS {_platform.mac_ver()[0]}"[:80]
    if plat == "windows":
        return f"Windows {_platform.release()} ({_platform.version()})"[:80]
    return f"{_platform.system()} {_platform.release()}"[:80]


def linux_display(env: dict[str, str] | None = None) -> str | None:
    """``"wayland"``, ``"x11"``, or None (no graphical session) for a Linux desktop session."""
    env = os.environ if env is None else env
    session = (env.get("XDG_SESSION_TYPE") or "").lower()
    if session == "wayland" or (env.get("WAYLAND_DISPLAY") and session != "x11"):
        return "wayland"
    if session == "x11" or env.get("DISPLAY"):
        return "x11"
    return None


WAYLAND_REASON = ("this is a Wayland session: seeing the screen and pressing keys aren't supported there yet "
                  "(sign in with an X11 / \"Xorg\" session instead)")
NO_DISPLAY_REASON = "no graphical session (DISPLAY isn't set): start the controller from your desktop session"


# --------------------------------------------------------------------------- #
# macOS permissions (checked, never assumed)
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _mac_frameworks():
    core_graphics = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    services = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    core_graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    core_graphics.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    return core_graphics, services


def mac_screen_recording_allowed() -> bool | None:
    try:
        return bool(_mac_frameworks()[0].CGPreflightScreenCaptureAccess())
    except (OSError, AttributeError):
        return None


def mac_accessibility_allowed() -> bool | None:
    try:
        return bool(_mac_frameworks()[1].AXIsProcessTrusted())
    except (OSError, AttributeError):
        return None


def mac_request_permissions() -> None:
    """Setup only: ask macOS to show its Screen Recording prompt, and open the Accessibility settings."""
    try:
        _mac_frameworks()[0].CGRequestScreenCaptureAccess()
    except (OSError, AttributeError):
        pass
    if mac_accessibility_allowed() is False:
        import subprocess

        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                       check=False)


MAC_SCREEN_REASON = ("macOS hasn't given this program Screen Recording permission: System Settings › Privacy & "
                     "Security › Screen Recording, turn on {program}, then restart the controller")
MAC_INPUT_REASON = ("macOS hasn't given this program Accessibility permission: System Settings › Privacy & "
                    "Security › Accessibility, turn on {program}, then restart the controller")


def _program() -> str:
    return os.path.basename(sys.executable) or "Python"


# --------------------------------------------------------------------------- #
# Capabilities
# --------------------------------------------------------------------------- #


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def _has_library(name: str) -> bool:
    found = ctypes.util.find_library(name)
    if not found:
        return False
    try:
        ctypes.cdll.LoadLibrary(found)
    except OSError:
        return False
    return True


def capture_capability() -> Capability:
    plat = current_platform()
    if not _has_module("mss"):
        return Capability(available=False, reason="the screen-capture package (mss) isn't installed")
    if plat == "windows":
        return Capability(available=True)
    if plat == "macos":
        allowed = mac_screen_recording_allowed()
        if allowed is False:
            return Capability(available=False, reason=MAC_SCREEN_REASON.format(program=_program()))
        return Capability(available=True, reason=None if allowed else "couldn't check Screen Recording permission")
    if plat == "linux":
        display = linux_display()
        if display == "wayland":
            return Capability(available=False, reason=WAYLAND_REASON)
        if display is None:
            return Capability(available=False, reason=NO_DISPLAY_REASON)
        return Capability(available=True)
    return Capability(available=False, reason=f"screen capture isn't supported on {sys.platform}")


def input_capability() -> Capability:
    plat = current_platform()
    if plat == "windows":
        return Capability(available=True)
    if plat == "macos":
        allowed = mac_accessibility_allowed()
        if allowed is False:
            return Capability(available=False, reason=MAC_INPUT_REASON.format(program=_program()))
        return Capability(available=True, reason=None if allowed else "couldn't check Accessibility permission")
    if plat == "linux":
        display = linux_display()
        if display == "wayland":
            return Capability(available=False, reason=WAYLAND_REASON)
        if display is None:
            return Capability(available=False, reason=NO_DISPLAY_REASON)
        if not (_has_library("X11") and _has_library("Xtst")):
            return Capability(available=False,
                              reason="the X11 test extension library (libXtst) isn't installed: install libxtst6")
        return Capability(available=True)
    return Capability(available=False, reason=f"input isn't supported on {sys.platform}")


def status(*, startup: str = "unknown", startup_method: str | None = None) -> ControllerStatus:
    plat = current_platform()
    return ControllerStatus(
        machine=machine_name(), platform=plat, os=os_description(),
        display=linux_display() if plat == "linux" else None,
        capture=capture_capability(), input=input_capability(),
        startup=startup, startup_method=startup_method,
    )


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


def capture_backend():
    """The screen, or None when this machine can't capture (the status report says why)."""
    plat = current_platform()
    if plat == "windows":
        from .capture import WindowsCapture

        return WindowsCapture()
    if plat in ("macos", "linux") and capture_capability().available:
        from .capture import ScreenCapture

        return ScreenCapture(retina_to_points=plat == "macos")
    return None


def desktop_input():
    """Keyboard and mouse on the whole desktop, or None (actions are then refused)."""
    plat = current_platform()
    if plat == "windows":
        from .input_windows import WindowsDesktopInput

        return WindowsDesktopInput()
    if not input_capability().available:
        return None
    if plat == "macos":
        from .input_macos import MacDesktopInput

        return MacDesktopInput()
    if plat == "linux":
        from .input_linux import X11DesktopInput

        return X11DesktopInput()
    return None


def game_input_factory():
    """Game sessions' input (bound to a game window): Windows only. Elsewhere sessions report ``no_input``."""
    if current_platform() != "windows":
        return None
    from .input_windows import WindowsInput

    return lambda profile: WindowsInput(profile.target.process)
