"""Starting the controller at login, and only once (docs/VM_CONNECTION_PLAN.md, steps 2 and 4).

- **Windows**: a Task Scheduler task, "Jophiel Controller", triggered at logon of the signed-in user and
  running as that user (not elevated). If Windows refuses to create it (some setups only let
  administrators register logon triggers), the per-user ``Run`` registry key is used instead, which never
  needs administrator rights. Running setup again updates whichever is in place.
- **macOS**: a per-user LaunchAgent (``~/Library/LaunchAgents/ai.jophiel.controller.plist``).
- **Linux**: a desktop-session autostart entry (``~/.config/autostart/jophiel-controller.desktop``), so it
  starts inside the graphical session it needs.

Whatever starts it, ``SingleInstance`` makes sure at most one controller runs per pairing file: a second
one (setup's immediate start while the login one is already up, say) exits quietly.

Every OS call goes through ``Runner`` / the paths given, so tests check the exact commands and files
without touching the machine.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

TASK_NAME = "Jophiel Controller"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "JophielController"
LAUNCH_AGENT_LABEL = "ai.jophiel.controller"
AUTOSTART_NAME = "jophiel-controller.desktop"

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class StartupState:
    registered: bool
    method: str | None = None  # "scheduled_task", "run_key", "launch_agent", "autostart"
    detail: str | None = None


def controller_command(config_path: Path, log_path: Path, *, windowless: bool = True) -> list[str]:
    """The command a login start runs: this Python, this pairing file, output to a log (no console)."""
    python = Path(sys.executable)
    if windowless and sys.platform == "win32":
        quiet = python.with_name("pythonw.exe")
        if quiet.exists():
            python = quiet  # no console window at every login
    return [str(python), "-m", "desktop_controller", "--config", str(config_path), "run", "--background",
            "--log", str(log_path)]


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def windows_task_script(command: list[str]) -> str:
    """PowerShell that registers (or replaces) the logon task for the current user, not elevated."""
    program, arguments = command[0], subprocess.list2cmdline(command[1:])
    return "; ".join([
        "$ErrorActionPreference = 'Stop'",
        "$user = \"$env:USERDOMAIN\\$env:USERNAME\"",
        f"$action = New-ScheduledTaskAction -Execute {_ps_quote(program)} -Argument {_ps_quote(arguments)}",
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user",
        "$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited",
        # No time limit, don't start a second copy, start again after a crash.
        "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action -Trigger $trigger "
        "-Principal $principal -Settings $settings -Force | Out-Null",
    ])


def _powershell(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


class WindowsStartup:
    def __init__(self, run: Runner = _run, registry=None):
        self._run = run
        self._registry = registry  # tests pass a stand-in for winreg

    def _winreg(self):
        if self._registry is not None:
            return self._registry
        import winreg

        return winreg

    def register(self, command: list[str]) -> StartupState:
        result = self._run(_powershell(windows_task_script(command)))
        if result.returncode == 0:
            self._remove_run_key()  # one way of starting, not two
            return StartupState(True, "scheduled_task")
        why = (result.stderr or result.stdout or "").strip().splitlines()
        winreg = self._winreg()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        return StartupState(True, "run_key",
                            f"Task Scheduler refused ({why[-1] if why else 'no reason given'}); using the Run key")

    def unregister(self) -> None:
        self._run(_powershell(
            f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false -ErrorAction SilentlyContinue"))
        self._remove_run_key()

    def _remove_run_key(self) -> None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except OSError:
            pass

    def state(self) -> StartupState:
        result = self._run(_powershell(
            f"if (Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue) "
            "{ 'yes' } else { 'no' }"))
        if result.returncode == 0 and result.stdout.strip() == "yes":
            return StartupState(True, "scheduled_task")
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, RUN_VALUE)
            return StartupState(True, "run_key")
        except OSError:
            return StartupState(False)


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #


def launch_agent_plist(command: list[str]) -> bytes:
    return plistlib.dumps({
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": command,
        "RunAtLoad": True,
        # It retries the relay by itself. launchd never restarts it: a revoked pairing exits on purpose,
        # and launchd can't tell that exit from a crash.
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
    })


class MacStartup:
    def __init__(self, run: Runner = _run, home: Path | None = None, uid: int | None = None):
        self._run = run
        self._home = home or Path.home()
        self._uid = os.getuid() if uid is None else uid

    @property
    def path(self) -> Path:
        return self._home / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

    def register(self, command: list[str]) -> StartupState:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["launchctl", "bootout", f"gui/{self._uid}/{LAUNCH_AGENT_LABEL}"])  # replace a loaded one
        self.path.write_bytes(launch_agent_plist(command))
        # Loaded now so it's known to launchd; RunAtLoad also starts it (setup's own start then exits quietly).
        result = self._run(["launchctl", "bootstrap", f"gui/{self._uid}", str(self.path)])
        detail = None if result.returncode == 0 else (result.stderr or "").strip()[:200] or None
        return StartupState(True, "launch_agent", detail)

    def unregister(self) -> None:
        self._run(["launchctl", "bootout", f"gui/{self._uid}/{LAUNCH_AGENT_LABEL}"])
        self.path.unlink(missing_ok=True)

    def state(self) -> StartupState:
        return StartupState(self.path.exists(), "launch_agent" if self.path.exists() else None)


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #


def autostart_entry(command: list[str]) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Jophiel Controller",
        "Comment=Connects this machine to Jophiel",
        f"Exec={shlex.join(command)}",
        "Terminal=false",
        "NoDisplay=true",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


class LinuxStartup:
    def __init__(self, config_home: Path | None = None):
        base = config_home or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = base / "autostart" / AUTOSTART_NAME

    def register(self, command: list[str]) -> StartupState:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(autostart_entry(command), encoding="utf-8")
        return StartupState(True, "autostart")

    def unregister(self) -> None:
        self.path.unlink(missing_ok=True)

    def state(self) -> StartupState:
        return StartupState(self.path.exists(), "autostart" if self.path.exists() else None)


def for_this_machine():
    if sys.platform == "win32":
        return WindowsStartup()
    if sys.platform == "darwin":
        return MacStartup()
    if sys.platform.startswith("linux"):
        return LinuxStartup()
    return None


def start_now(command: list[str]) -> None:
    """Start the controller in the background right away (first setup), detached from this terminal."""
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    subprocess.Popen(command, start_new_session=True, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------- #
# One controller at a time
# --------------------------------------------------------------------------- #


class AlreadyRunning(Exception):
    pass


class SingleInstance:
    """An exclusive lock on ``<pairing file>.lock``, held for the controller's whole run. The OS drops it
    when the process ends, however it ends, so a crash never leaves a stale lock behind."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AlreadyRunning(str(self.path)) from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, *exc) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
