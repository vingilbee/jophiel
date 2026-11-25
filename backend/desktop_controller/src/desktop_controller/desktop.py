"""General desktop control: her hands on the VM as an ordinary Windows desktop
(docs/GENERAL_DESKTOP_CONTROL_DESIGN.md).

The orchestrator decides everything; this module only carries out one bounded action at a time and says
what became of it. No profile, no game window, no session: the VM is a dedicated machine, and input goes to
whatever is in front, the way a person's would.

- **Coordinates are Windows screen pixels** (physical, virtual-desktop space). The backend's tool layer
  maps a point on the screenshot she looked at (scaled, maybe cropped) to these before sending.
- **Bounded.** Every action has hard limits (text length, drag time, keys in a chord, scroll notches) and a
  total time budget. Anything it pressed is released before it returns, also when it fails.
- **Stoppable.** ``run`` takes a ``stop`` event (the connection closed): it is checked before every input
  and wakes every pause, so nothing more is sent once it is set and what is held is let go at once.
- **Never behind a sign-in prompt.** While a Windows credential or elevation prompt is in front, nothing is
  pressed (``Refused``): those are for the user.

- **Commands** (``run_command``, 0.13.0+): one shell command, its output back as text (``commands.py``). It
  doesn't touch the screen, so no sign-in check; the user's view switch still governs it (connection.py).

``DesktopHands.run`` takes the raw action dict and returns a ``DesktopResult``; it never raises.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .commands import MAX_COMMAND, MAX_TIMEOUT_S, CommandFailed, CommandRunner
from .link import DesktopResult, ScreenInfo

MAX_TEXT = 2000
MAX_CHORD = 5
MAX_PRESSES = 20
MAX_DRAG_MS = 5000
MAX_HOVER_MS = 5000
MAX_SCROLL = 30
MAX_PATH = 12
# Pauses inside an action, so apps see a press and a release as separate events.
KEY_GAP_S = 0.02
CLICK_GAP_S = 0.06
TEXT_CHAR_S = 0.004
TEXT_CHUNK = 40  # typed text goes in pieces this long, so a stop lands between them

Button = Literal["left", "right", "middle"]
Modifier = Literal["shift", "ctrl", "alt", "win"]
Coord = Annotated[int, Field(ge=-32768, le=32767)]
Point = tuple[Coord, Coord]

# Key names she may press (lower case). Letters and digits are added below.
NAMED_KEYS = frozenset({
    "shift", "ctrl", "alt", "win", "enter", "tab", "esc", "space", "backspace", "delete", "insert",
    "home", "end", "pageup", "pagedown", "up", "down", "left", "right", "capslock", "printscreen",
    "menu", "minus", "equals", "lbracket", "rbracket", "backslash", "semicolon", "quote", "backquote",
    "comma", "period", "slash", "volumeup", "volumedown", "volumemute", "playpause",
    *(f"f{i}" for i in range(1, 13)),
})
KEY_ALIASES = {
    "control": "ctrl", "cmd": "win", "windows": "win", "super": "win", "meta": "win", "option": "alt",
    "return": "enter", "escape": "esc", "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
    "page_up": "pageup", "page_down": "pagedown", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
    "arrowright": "right", "bksp": "backspace", "-": "minus", "=": "equals", "[": "lbracket", "]": "rbracket",
    "\\": "backslash", ";": "semicolon", "'": "quote", "`": "backquote", ",": "comma", ".": "period",
    "/": "slash", "apps": "menu", "prtsc": "printscreen",
}
KEYS = NAMED_KEYS | frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
MODIFIERS = ("ctrl", "shift", "alt", "win")


def parse_chord(text: str) -> tuple[str, ...]:
    """``"Ctrl+Shift+T"`` -> ``("ctrl", "shift", "t")``. Raises ValueError for an unknown key."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+")]
    if text.strip().endswith("++"):  # "ctrl++": the plus key itself is "equals" with shift on US layouts
        parts = [p for p in parts if p] + ["equals"]
    keys = []
    for part in parts:
        if not part:
            raise ValueError(f"empty key in {text!r}")
        name = KEY_ALIASES.get(part, part)
        if name not in KEYS:
            raise ValueError(f"unknown key {part!r}")
        if name not in keys:
            keys.append(name)
    if not 1 <= len(keys) <= MAX_CHORD:
        raise ValueError(f"a chord has 1 to {MAX_CHORD} keys")
    # Modifiers go down first, whatever order she wrote them in.
    return tuple(sorted(keys, key=lambda k: (k not in MODIFIERS, MODIFIERS.index(k) if k in MODIFIERS else 0)))


# --------------------------------------------------------------------------- #
# The actions (the wire shape of DesktopAction.action)
# --------------------------------------------------------------------------- #


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Screenshot(_Action):
    kind: Literal["screenshot"]
    # A part of the screen, in screen pixels (left, top, width, height); None: the whole primary screen.
    region: tuple[Coord, Coord, Annotated[int, Field(ge=8, le=16384)], Annotated[int, Field(ge=8, le=16384)]] | None = None
    max_width: Annotated[int, Field(ge=64, le=1920)] = 1280
    max_height: Annotated[int, Field(ge=64, le=1080)] = 800
    # A crop may be enlarged up to this much, so small text can be read.
    max_zoom: Annotated[float, Field(ge=1.0, le=3.0)] = 2.0


class Move(_Action):
    kind: Literal["move"]
    point: Point
    hover_ms: Annotated[int, Field(ge=0, le=MAX_HOVER_MS)] = 0


class Click(_Action):
    kind: Literal["click"]
    point: Point
    button: Button = "left"
    count: Annotated[int, Field(ge=1, le=3)] = 1
    modifiers: list[Modifier] = Field(default_factory=list, max_length=4)


class Drag(_Action):
    kind: Literal["drag"]
    path: list[Point] = Field(min_length=2, max_length=MAX_PATH)
    button: Button = "left"
    duration_ms: Annotated[int, Field(ge=50, le=MAX_DRAG_MS)] = 400
    modifiers: list[Modifier] = Field(default_factory=list, max_length=4)


class Scroll(_Action):
    kind: Literal["scroll"]
    point: Point
    # Notches: + down / right, - up / left.
    down: Annotated[int, Field(ge=-MAX_SCROLL, le=MAX_SCROLL)] = 0
    right: Annotated[int, Field(ge=-MAX_SCROLL, le=MAX_SCROLL)] = 0
    modifiers: list[Modifier] = Field(default_factory=list, max_length=4)


class Keyboard(_Action):
    kind: Literal["keyboard"]
    # Either text to type (as typed characters, any language) or chords to press in order.
    text: Annotated[str, Field(max_length=MAX_TEXT)] | None = None
    keys: list[Annotated[str, Field(min_length=1, max_length=48)]] = Field(default_factory=list, max_length=MAX_PRESSES)
    repeat: Annotated[int, Field(ge=1, le=MAX_PRESSES)] = 1


class RunCommand(_Action):
    """One shell command on the machine (0.13.0+); its output comes back as text (``commands``)."""

    kind: Literal["run_command"]
    command: Annotated[str, Field(min_length=1, max_length=MAX_COMMAND)]
    timeout_s: Annotated[int, Field(ge=1, le=MAX_TIMEOUT_S)] = 60
    # 0.14.0+: still running when timeout_s ends, it isn't stopped but kept as a job (``commands.Job``).
    background: bool = False


class CommandStatus(_Action):
    """A background command: still running (what it printed so far) or how it ended (0.14.0+)."""

    kind: Literal["command_status"]
    job: Annotated[str, Field(min_length=1, max_length=16)]


class StopCommand(_Action):
    """Stop a background command and what it started (0.14.0+)."""

    kind: Literal["stop_command"]
    job: Annotated[str, Field(min_length=1, max_length=16)]


Action = Annotated[Union[Screenshot, Move, Click, Drag, Scroll, Keyboard, RunCommand, CommandStatus, StopCommand],
                   Field(discriminator="kind")]
action_adapter = TypeAdapter(Action)
INPUT_KINDS = frozenset({"move", "click", "drag", "scroll", "keyboard"})


class Refused(Exception):
    """Nothing was (or will be) sent; the reason is for her."""


class Failed(Exception):
    """Windows refused input part way (everything held is released)."""


class Stopped(Exception):
    """The connection closed part way: nothing more is sent and everything held is released."""


def parse_action(raw: object) -> Action:
    """Raises Refused for anything that doesn't fit."""
    try:
        action = action_adapter.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise Refused(f"bad action: {first.get('msg')} ({where})") from None
    if isinstance(action, Keyboard):
        if (action.text is None) == (not action.keys):
            raise Refused("bad action: give either text to type or keys to press")
        if action.keys and len(action.keys) * action.repeat > MAX_PRESSES:
            raise Refused(f"bad action: at most {MAX_PRESSES} key presses in one call")
        for chord in action.keys:
            try:
                parse_chord(chord)
            except ValueError as exc:
                raise Refused(f"bad action: {exc}") from None
    return action


# --------------------------------------------------------------------------- #
# What the hands need from the machine
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Shot:
    jpeg: bytes
    info: ScreenInfo


class DesktopInput(Protocol):
    """Raw input over the whole desktop. Each call either sends everything or raises ``Failed``."""

    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def type_text(self, text: str) -> None: ...
    def move_to(self, x: int, y: int) -> None: ...
    def button_down(self, button: str) -> None: ...
    def button_up(self, button: str) -> None: ...
    def wheel(self, notches: int, horizontal: bool = False) -> None: ...
    def screen_bounds(self) -> tuple[int, int, int, int]: ...  # virtual desktop: left, top, width, height
    def sensitive_in_front(self) -> bool: ...
    def foreground(self) -> tuple[str | None, str | None]: ...  # (process, title)


class DesktopScreen(Protocol):
    def screenshot(self, action: Screenshot) -> Shot: ...  # raises Refused when there is no picture to give


class DesktopHands:
    """Carries out one action at a time. ``sleep`` is injectable for tests (``None``: real time, woken by a
    stop)."""

    def __init__(self, backend: DesktopInput | None, screen: DesktopScreen | None, *, sleep=None,
                 commands: CommandRunner | None = None):
        self.backend = backend
        self.screen = screen
        self.sleep = sleep
        self.commands = commands
        # Whether the connected relay takes background commands' ends (``Welcome.features``: command_jobs).
        self.jobs_reported = False
        self._stop = threading.Event()  # the running action's stop (``run`` sets it per action)

    def run(self, action_id: str, raw: object, stop: threading.Event | None = None) -> tuple[DesktopResult, bytes | None]:
        """The result, and the picture for a screenshot. Never raises. ``stop``: set when the connection closes;
        the action ends at its next step, releasing what it holds."""
        started = time.monotonic()
        self._stop = stop or threading.Event()
        jpeg: bytes | None = None
        screen: ScreenInfo | None = None
        ran: dict = {}
        note: str | None = None  # a done command job's end: why it was stopped
        try:
            action = parse_action(raw)
            if isinstance(action, RunCommand):
                if self.commands is None:
                    raise Refused("this machine can't run commands")
                if action.background and not self.jobs_reported:
                    raise Refused("the relay is too old to hear when a background command ends (update it)")
                try:
                    if action.background:
                        job, code, output, cut = self.commands.run_background(action.command, action.timeout_s,
                                                                              self._stop)
                        ran = {"exit_code": code, "output": output, "output_cut": cut}
                        if job is not None:
                            ran.update(job=job, running=True)
                    else:
                        code, output, cut, why = self.commands.run(action.command, action.timeout_s, self._stop)
                        ran = {"exit_code": code, "output": output, "output_cut": cut}
                        if why is not None:
                            raise Failed(why)
                except CommandFailed as exc:
                    raise Failed(str(exc)) from None
            elif isinstance(action, (CommandStatus, StopCommand)):
                if self.commands is None:
                    raise Refused("this machine can't run commands")
                found = (self.commands.stop_job(action.job) if isinstance(action, StopCommand)
                         else self.commands.status(action.job))
                if found is None:
                    raise Refused(f"there's no background command {action.job} (finished ones are kept an hour)")
                output, cut = found.output()
                ran = {"job": found.id, "running": found.running, "exit_code": found.exit_code, "output": output,
                       "output_cut": cut}
                note = found.why if not found.running else None
            elif isinstance(action, Screenshot):
                if self.screen is None:
                    raise Refused("this machine can't take screenshots")
                shot = self.screen.screenshot(action)
                jpeg, screen = shot.jpeg, shot.info
            else:
                self._input(action)
            status, reason = "done", note
        except Refused as exc:
            status, reason = "refused", str(exc)
        except (Failed, Stopped) as exc:
            status, reason = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - a result always goes back
            status, reason = "failed", f"{type(exc).__name__}: {exc}"
        process = title = None
        if self.backend is not None:
            try:
                process, title = self.backend.foreground()
            except Exception:  # noqa: BLE001
                pass
        return DesktopResult(
            action_id=action_id, status=status, reason=(reason or None) and reason[:300],
            ran_ms=int((time.monotonic() - started) * 1000), screen=screen,
            foreground_process=(process or None) and process[:64], foreground_title=(title or None) and title[:200],
            **ran,
        ), jpeg

    # -- input -----------------------------------------------------------------

    def _go(self) -> DesktopInput:
        """The backend, for one more input; raises Stopped once the connection closed."""
        if self._stop.is_set():
            raise Stopped("the connection closed part way; everything held was released")
        return self.backend

    def _pause(self, seconds: float) -> None:
        """A pause inside an action, cut short by a stop."""
        if self.sleep is None:
            self._stop.wait(seconds)
        else:
            self.sleep(seconds)
        self._go()

    def _input(self, action) -> None:
        backend = self.backend
        if backend is None:
            raise Refused("this machine has no input (not Windows, or started without it)")
        if backend.sensitive_in_front():
            raise Refused("a Windows sign-in or permission prompt is in front; the user handles those")
        for point in _points(action):
            self._check_point(point)
        held: list[tuple[str, str]] = []  # ("key", name) | ("button", name), in press order
        try:
            if isinstance(action, Move):
                self._go().move_to(*action.point)
                if action.hover_ms:
                    self._pause(action.hover_ms / 1000)
            elif isinstance(action, Click):
                self._go().move_to(*action.point)
                self._hold_modifiers(action.modifiers, held)
                for i in range(action.count):
                    self._go().button_down(action.button)
                    held.append(("button", action.button))
                    self._go().button_up(action.button)
                    held.pop()
                    if i + 1 < action.count:
                        self._pause(CLICK_GAP_S)
            elif isinstance(action, Drag):
                self._drag(action, held)
            elif isinstance(action, Scroll):
                self._go().move_to(*action.point)
                self._hold_modifiers(action.modifiers, held)
                if action.down:
                    self._go().wheel(action.down)
                if action.right:
                    self._go().wheel(action.right, horizontal=True)
            elif isinstance(action, Keyboard):
                self._keyboard(action, held)
        finally:
            self._release(held)

    def _check_point(self, point: tuple[int, int]) -> None:
        left, top, width, height = self.backend.screen_bounds()
        x, y = point
        if not (left <= x < left + width and top <= y < top + height):
            raise Refused(f"point {x},{y} is off the screen")

    def _hold_modifiers(self, modifiers, held) -> None:
        for name in dict.fromkeys(modifiers):
            self._go().key_down(name)
            held.append(("key", name))
            self._pause(KEY_GAP_S)

    def _drag(self, action: Drag, held) -> None:
        self._go().move_to(*action.path[0])
        self._hold_modifiers(action.modifiers, held)
        self._go().button_down(action.button)
        held.append(("button", action.button))
        self._pause(0.05)
        # Move in small steps so apps see a drag rather than a jump.
        legs = list(zip(action.path, action.path[1:]))
        steps_per_leg = max(2, min(30, action.duration_ms // (15 * len(legs))))
        pause = action.duration_ms / 1000 / (steps_per_leg * len(legs))
        for (x0, y0), (x1, y1) in legs:
            for i in range(1, steps_per_leg + 1):
                self._go().move_to(round(x0 + (x1 - x0) * i / steps_per_leg), round(y0 + (y1 - y0) * i / steps_per_leg))
                self._pause(pause)
        self._pause(0.05)
        self._go().button_up(action.button)
        held.remove(("button", action.button))

    def _keyboard(self, action: Keyboard, held) -> None:
        if action.text is not None:
            for i in range(0, len(action.text), TEXT_CHUNK):
                self._go().type_text(action.text[i:i + TEXT_CHUNK])
            return
        for _ in range(action.repeat):
            for chord in action.keys:
                keys = parse_chord(chord)
                for key in keys:
                    self._go().key_down(key)
                    held.append(("key", key))
                    self._pause(KEY_GAP_S)
                for key in reversed(keys):
                    self._go().key_up(key)
                    held.remove(("key", key))
                self._pause(KEY_GAP_S)

    def _release(self, held) -> None:
        """Let go of everything still held, newest first. Releasing never raises."""
        for kind, name in reversed(held):
            try:
                if kind == "button":
                    self.backend.button_up(name)
                else:
                    self.backend.key_up(name)
            except Exception:  # noqa: BLE001
                pass
        held.clear()


def _points(action) -> list[tuple[int, int]]:
    if isinstance(action, (Move, Click, Scroll)):
        return [action.point]
    if isinstance(action, Drag):
        return list(action.path)
    return []
