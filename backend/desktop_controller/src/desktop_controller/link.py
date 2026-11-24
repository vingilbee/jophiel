"""The controller <-> relay link: messages and pairing proofs (plan §5.1, §10, §19).

Shared by both ends, so the relay and the guest controller cannot disagree on
the wire format or the proof math.

Pairing, in one paragraph. The relay uses a self-signed certificate. The
first time a controller connects it cannot verify that certificate, so the
user's one-time pairing code does the verifying instead: the controller sends
``HMAC(code, "client" | certificate fingerprint it saw | nonce)``, never the
code. The real relay recomputes that with its own fingerprint; a machine in
the middle has a different certificate, so the proof it relays fails, and it
cannot forge one without the code. The relay answers with the mirror proof
(``"server"``), which only a holder of the code can produce, and only then
does the controller pin the certificate and keep the device token it was
given. Codes carry 60 bits, so the proof can't be brute-forced in the minutes
a pairing lives.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL = "desktop-link/1"
HEARTBEAT_SECONDS = 2.0
# While a session is set up, beats come faster so the lease can stay short.
HEARTBEAT_ACTIVE_SECONDS = 0.4
LEASE_MS = 1500
# The relay drops a controller after this long without a heartbeat.
HEARTBEAT_TIMEOUT_SECONDS = 6.0

# Crockford base32 without the look-alikes: no I, L, O, U.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_CHARS = 12  # 60 bits
_CODE_SHAPE = re.compile(rf"^[{_ALPHABET}]{{{_CODE_CHARS}}}$")


# --------------------------------------------------------------------------- #
# Pairing codes and proofs
# --------------------------------------------------------------------------- #


def new_pairing_code() -> str:
    """A fresh code, formatted for reading aloud/typing: ``ABCD-EFGH-JKMN``."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_CHARS))
    return "-".join(raw[i:i + 4] for i in range(0, _CODE_CHARS, 4))


def normalize_code(code: str) -> str:
    """Accept what people type: any case, spaces or dashes, and O/I/L as 0/1/1."""
    cleaned = re.sub(r"[\s-]", "", code).upper().translate(str.maketrans("OIL", "011"))
    if not _CODE_SHAPE.match(cleaned):
        raise ValueError("a pairing code is 12 letters and digits, like ABCD-EFGH-JKMN")
    return cleaned


def code_lookup(code: str) -> str:
    """How the relay finds the pairing without receiving the code itself."""
    return hashlib.sha256(b"desktop-link/1|lookup|" + normalize_code(code).encode()).hexdigest()


def _proof(code: str, role: str, fingerprint: str, nonce: str) -> str:
    message = f"{PROTOCOL}|{role}|{fingerprint.lower()}|{nonce}".encode()
    return hmac.new(normalize_code(code).encode(), message, hashlib.sha256).hexdigest()


def client_proof(code: str, fingerprint: str, nonce: str) -> str:
    return _proof(code, "client", fingerprint, nonce)


def server_proof(code: str, fingerprint: str, nonce: str) -> str:
    return _proof(code, "server", fingerprint, nonce)


def proofs_match(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode(), given.encode())


def cert_fingerprint(der: bytes) -> str:
    """SHA-256 of the DER certificate, lowercase hex (what both ends compare)."""
    return hashlib.sha256(der).hexdigest()


def display_fingerprint(fingerprint: str) -> str:
    """``ab12cd...`` -> ``AB:12:CD:...`` for showing a person."""
    return ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2)).upper()


def token_hash(token: str) -> str:
    """Device tokens are stored hashed, like session tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #

_Hex64 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Nonce = Annotated[str, Field(pattern=r"^[0-9a-f]{32,64}$")]
_Version = Annotated[str, Field(min_length=1, max_length=32)]


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# controller -> relay

class Pair(_Msg):
    type: Literal["pair"] = "pair"
    protocol: Literal["desktop-link/1"] = PROTOCOL
    lookup: _Hex64
    nonce: _Nonce
    proof: _Hex64
    controller_version: _Version


class Hello(_Msg):
    type: Literal["hello"] = "hello"
    protocol: Literal["desktop-link/1"] = PROTOCOL
    environment_id: str = Field(min_length=36, max_length=36)
    token: str = Field(min_length=32, max_length=128)
    controller_version: _Version


# "sensitive": a Windows sign-in or admin-permission prompt is in front, so the
# picture is withheld even in whole-screen mode.
TargetState = Literal["ok", "not_found", "not_foreground", "minimized", "sensitive", "capture_off", "error"]


class SessionReady(_Msg):
    type: Literal["session_ready"] = "session_ready"
    session_id: str
    viewport: tuple[int, int]
    context: str = Field(max_length=32)


class SessionProblem(_Msg):
    """Not ready yet (the game isn't running) or never (the profile is bad)."""

    type: Literal["session_problem"] = "session_problem"
    session_id: str
    reason: Literal["target_missing", "profile_invalid", "no_input"]


class ReflexReport(_Msg):
    """A reflex that fired during a move (``acted`` False: watch mode, it only would have)."""

    name: str = Field(max_length=32)
    at_ms: int = Field(ge=0)
    acted: bool


class DecisionReport(_Msg):
    type: Literal["decision_report"] = "decision_report"
    session_id: str
    decision_id: str = Field(max_length=64)
    state: str = Field(max_length=16)
    ended_by: str | None = Field(default=None, max_length=48)
    ran_ms: int | None = None
    rejection_code: str | None = Field(default=None, max_length=48)
    rejection_reason: str | None = Field(default=None, max_length=300)
    reflexes: list[ReflexReport] = Field(default_factory=list, max_length=8)
    # A pursue move: {"seen": samples it was found in, "attacks": taps, "lost": bool}.
    pursuit: dict[str, int | bool] | None = None


class PauseRequest(_Msg):
    """The controller stopped on its own (game window lost, watchdog): the
    session must pause until the user resumes."""

    type: Literal["pause_request"] = "pause_request"
    session_id: str
    reason: str = Field(max_length=48)


class ScreenInfo(_Msg):
    """What a desktop screenshot covers: ``region`` is the screen rectangle the picture shows, in
    Windows screen pixels (physical, virtual-desktop coordinates), ``size`` the picture's own pixels."""

    region: tuple[int, int, int, int]  # left, top, width, height
    size: tuple[int, int]
    screen: tuple[int, int, int, int]  # the primary screen, same units
    foreground_process: str | None = Field(default=None, max_length=64)
    foreground_title: str | None = Field(default=None, max_length=200)


DesktopStatus = Literal["done", "failed", "refused"]
# A command's output carried back (0.13.0+): kept well inside the relay's 64 KiB message limit.
COMMAND_OUTPUT_CHARS = 12000


class DesktopResult(_Msg):
    """What became of one desktop action (general desktop control). ``done``: every input was delivered
    (or the picture taken); ``failed``: it started and Windows refused part of it (everything held was
    released); ``refused``: nothing was sent (capture off, a sign-in prompt in front, a bad action)."""

    type: Literal["desktop_result"] = "desktop_result"
    action_id: str = Field(min_length=1, max_length=64)
    status: DesktopStatus
    reason: str | None = Field(default=None, max_length=300)
    ran_ms: int = Field(default=0, ge=0)
    # For a screenshot: what the picture shows (the picture itself went just before, as a frame).
    screen: ScreenInfo | None = None
    # What was in front once the action finished, when known.
    foreground_process: str | None = Field(default=None, max_length=64)
    foreground_title: str | None = Field(default=None, max_length=200)
    # For a command (0.13.0+): its exit code (None: it didn't finish) and its output (stdout and stderr together,
    # as text, cut in the middle when long: ``output_cut``).
    exit_code: int | None = None
    output: str | None = Field(default=None, max_length=COMMAND_OUTPUT_CHARS + 200)
    output_cut: bool = False


# --- Security activity (docs/DESKTOP_NETWORK_AWARENESS_PLAN.md): the VM's event logs, read only ---------

SECURITY_MAX_CHANNELS = 16
SECURITY_MAX_EVENTS = 100
SECURITY_MESSAGE_CHARS = 600
SECURITY_DATA_FIELDS = 24
SECURITY_FIELD_CHARS = 240
_Channel = Annotated[str, Field(min_length=1, max_length=128)]


class SecurityEvent(_Msg):
    """One record of a Windows event log, as the log rendered it. Its text is the machine's own words."""

    record_id: int = Field(ge=0)
    time_created: str = Field(min_length=1, max_length=40)  # ISO 8601, UTC, from the log
    provider: str = Field(default="", max_length=128)
    event_id: int = Field(ge=0, le=65535)
    version: int | None = Field(default=None, ge=0, le=255)
    level: int | None = Field(default=None, ge=0, le=255)
    level_name: str | None = Field(default=None, max_length=32)
    task: str | None = Field(default=None, max_length=64)
    opcode: str | None = Field(default=None, max_length=64)
    keywords: list[Annotated[str, Field(max_length=48)]] = Field(default_factory=list, max_length=8)
    audit: Literal["success", "failure"] | None = None
    computer: str | None = Field(default=None, max_length=128)
    user_sid: str | None = Field(default=None, max_length=96)
    process_id: int | None = Field(default=None, ge=0)
    message: str = Field(default="", max_length=SECURITY_MESSAGE_CHARS)
    data: dict[Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=SECURITY_FIELD_CHARS)]] = Field(
        default_factory=dict, max_length=SECURITY_DATA_FIELDS)


SecurityIntervalStatus = Literal["covered", "gap", "reset", "unavailable"]


class SecurityInterval(_Msg):
    """What the collector can say about one channel over ``[start, end]`` (the VM's clock, UTC):
    ``covered``: every record written then was read (continuity checked by record number);
    ``gap``: records from then were gone before they could be read (overwritten);
    ``reset``: the log was cleared or recreated, so what was in it then is gone;
    ``unavailable``: the channel couldn't be read (not installed, access denied)."""

    status: SecurityIntervalStatus
    start: str = Field(min_length=1, max_length=40)
    end: str = Field(min_length=1, max_length=40)
    reason: str | None = Field(default=None, max_length=200)
    first_record: int | None = Field(default=None, ge=0)
    last_record: int | None = Field(default=None, ge=0)


class SecurityChannelRead(_Msg):
    channel: _Channel
    # Bumped each time the collector sees the log reset (its record numbers start again).
    stream: int = Field(default=0, ge=0)
    intervals: list[SecurityInterval] = Field(default_factory=list, max_length=4)
    events: list[SecurityEvent] = Field(default_factory=list, max_length=SECURITY_MAX_EVENTS)
    # More records were waiting than one read takes: the collector reads again right away.
    more: bool = False


class SecurityBatch(_Msg):
    """Controller -> relay: one read of the monitored channels, answered with one ``SecurityAck``. The
    collector only moves its bookmarks on ``stored``, so a lost batch is read again (and deduplicated)."""

    type: Literal["security_batch"] = "security_batch"
    batch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    epoch: int = Field(ge=1)
    sent_at: str = Field(min_length=1, max_length=40)  # the VM's clock, UTC: the relay measures skew from it
    channels: list[SecurityChannelRead] = Field(default_factory=list, max_length=SECURITY_MAX_CHANNELS)


class Capability(_Msg):
    """Whether this machine can do one thing now (see the screen, press keys), and why not."""

    available: bool
    reason: str | None = Field(default=None, max_length=200)


ControllerPlatform = Literal["windows", "macos", "linux", "other"]


class ControllerStatus(_Msg):
    """What the controller reports about its machine (0.12.0+, only to a relay whose ``Welcome`` asks for
    it): its name, OS, whether capture and input work (a missing OS permission says so here), and
    whether it starts at login. Shown on the dashboard; never authorizes anything."""

    machine: str = Field(min_length=1, max_length=64)
    platform: ControllerPlatform
    os: str = Field(default="", max_length=80)
    # The desktop session's display server on Linux ("x11", "wayland"); None elsewhere.
    display: str | None = Field(default=None, max_length=16)
    capture: Capability
    input: Capability
    # Whether it can run commands in a shell (0.13.0+; older controllers don't say, and can't).
    commands: Capability | None = None
    startup: Literal["registered", "not_registered", "unknown"] = "unknown"
    startup_method: str | None = Field(default=None, max_length=32)


class Heartbeat(_Msg):
    type: Literal["heartbeat"] = "heartbeat"
    # The capture setting this controller is obeying (its epoch), so the app can
    # say "capture off — confirmed" rather than just "requested".
    gate_epoch_seen: int = Field(default=0, ge=0)
    # Why there is or isn't a picture: the game window missing, behind another
    # window, minimized, or capture switched off.
    target_state: TargetState = "capture_off"
    # The game's input mode during a session ("playing", "menu", "unknown"),
    # from the profile's detector; None without a session.
    context: str | None = Field(default=None, max_length=32)
    # Only when the relay asked for it (``Welcome.features``), and only when it changed or now and then.
    status: ControllerStatus | None = None


# relay -> controller

class Paired(_Msg):
    type: Literal["paired"] = "paired"
    environment_id: str
    token: str
    proof: _Hex64


RelayFeature = Literal["status"]


class Welcome(_Msg):
    type: Literal["welcome"] = "welcome"
    controller_generation: int
    # Sent only to controllers that know it (0.12.0+; older ones forbid unknown fields). "status": send
    # ``Heartbeat.status``.
    features: list[RelayFeature] | None = None


CaptureGate = Literal["allowed", "blocked", "manual_auth"]
CaptureScope = Literal["desktop", "game_window"]


class CaptureConfig(_Msg):
    """What the controller may capture. Anything but ``allowed`` means no
    pixels at all; ``epoch`` fences frames taken under an older setting."""

    gate: CaptureGate = "blocked"
    epoch: int = Field(default=0, ge=0)
    # "desktop": the VM's whole primary screen. "game_window": only the window
    # of ``target_process`` (from the trusted profile), only while it is the
    # foreground window.
    scope: CaptureScope = "desktop"
    target_process: str | None = Field(default=None, max_length=64)
    max_width: int = Field(default=1280, ge=64, le=1920)
    max_height: int = Field(default=720, ge=64, le=1080)
    fps: float = Field(default=2.0, gt=0, le=4)
    # Model observations (stage D): the game window at exactly this size,
    # separate from the preview. Only sent while capture is allowed and a
    # session is playing; None means no observations.
    observe: "ObserveConfig | None" = None


class ObserveConfig(_Msg):
    target_process: str = Field(max_length=64)
    width: int = Field(ge=160, le=1920)
    height: int = Field(ge=90, le=1080)
    fps: float = Field(default=2.0, gt=0, le=4)


CaptureConfig.model_rebuild()


class ControlGrant(_Msg):
    """The lease (plan §19): input is allowed for ``session_id`` under
    ``generation`` until ``lease_ms`` after this arrives, unless renewed. No
    grant in a heartbeat reply means no control: release everything."""

    session_id: str
    generation: int = Field(ge=1)
    lease_ms: int = Field(ge=200, le=5000)


class SecurityConfig(_Msg):
    """Security monitoring, the owner's own switch (separate from capture and input). ``epoch`` changes each
    time monitoring is switched on: the collector then starts afresh from the log's end, so nothing from the
    time it was off is ever read. ``channels`` is the active collection filter: whole channels, read only."""

    enabled: bool = False
    epoch: int = Field(default=0, ge=0)
    channels: list[_Channel] = Field(default_factory=list, max_length=SECURITY_MAX_CHANNELS)
    poll_seconds: float = Field(default=15.0, ge=2, le=300)


class HeartbeatAck(_Msg):
    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    capture: CaptureConfig = CaptureConfig()
    control: ControlGrant | None = None
    # Only sent to controllers that know it (0.11.0+); absent means monitoring is off.
    security: SecurityConfig | None = None


class SecurityAck(_Msg):
    type: Literal["security_ack"] = "security_ack"
    batch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["stored", "refused"]
    reason: str | None = Field(default=None, max_length=200)


class SessionStart(_Msg):
    """Prepare to play: the trusted profile (validated again on arrival) and
    the session it belongs to. Repeated until the controller reports ready."""

    type: Literal["session_start"] = "session_start"
    session_id: str
    profile: dict


class SessionStop(_Msg):
    type: Literal["session_stop"] = "session_stop"
    session_id: str


class DecisionCommand(_Msg):
    type: Literal["decision"] = "decision"
    session_id: str
    decision_id: str = Field(min_length=1, max_length=64)
    generation: int = Field(ge=1)
    decision: dict


class Cancel(_Msg):
    """Stop now (pause, take over, end, new plan). Faster than waiting for the
    lease to lapse; the lease is the backstop if this is lost."""

    type: Literal["cancel"] = "cancel"
    session_id: str
    reason: str = Field(default="cancelled", max_length=48)


class DesktopAction(_Msg):
    """One bounded desktop action for the general desktop (``desktop_controller.desktop``). Checked again
    on arrival; answered with exactly one ``DesktopResult``."""

    type: Literal["desktop_action"] = "desktop_action"
    action_id: str = Field(min_length=1, max_length=64)
    action: dict


class LinkError(_Msg):
    """Sent just before the relay closes the connection."""

    type: Literal["error"] = "error"
    code: Literal[
        "bad_message", "unsupported_protocol", "pairing_invalid", "not_authorized",
        "superseded", "revoked", "heartbeat_timeout",
    ]


ControllerMessage = Annotated[
    Union[Pair, Hello, Heartbeat, SessionReady, SessionProblem, DecisionReport, PauseRequest, DesktopResult,
          SecurityBatch],
    Field(discriminator="type"),
]
RelayMessage = Annotated[
    Union[Paired, Welcome, HeartbeatAck, LinkError, SessionStart, SessionStop, DecisionCommand, Cancel,
          DesktopAction, SecurityAck],
    Field(discriminator="type"),
]
controller_message = TypeAdapter(ControllerMessage)
relay_message = TypeAdapter(RelayMessage)


# --------------------------------------------------------------------------- #
# Frames: binary messages, [4-byte header length][JSON FrameHeader][JPEG]
# --------------------------------------------------------------------------- #

MAX_FRAME_BYTES = 512 * 1024


class FrameHeader(_Msg):
    seq: int = Field(ge=0)
    # Controller's monotonic clock at capture, ms (never compared across machines).
    captured_ms: int = Field(ge=0)
    width: int = Field(ge=1, le=1920)
    height: int = Field(ge=1, le=1080)
    encoding: Literal["jpeg"] = "jpeg"
    gate_epoch: int = Field(ge=0)
    # "preview" for the card; "observation" for the driver model (game window,
    # exact observation size, with the input context at capture); "screenshot" for a
    # desktop action's picture (``action_id`` says which one).
    kind: Literal["preview", "observation", "screenshot"] = "preview"
    context: str | None = Field(default=None, max_length=32)
    action_id: str | None = Field(default=None, max_length=64)


def pack_frame(header: FrameHeader, image: bytes) -> bytes:
    head = header.model_dump_json().encode()
    return len(head).to_bytes(4, "big") + head + image


def unpack_frame(data: bytes) -> tuple[FrameHeader, bytes]:
    """Raises ``ValueError`` on anything malformed; frames are untrusted input."""
    if len(data) > MAX_FRAME_BYTES or len(data) < 4:
        raise ValueError("frame size out of bounds")
    size = int.from_bytes(data[:4], "big")
    if not 0 < size <= 1024 or len(data) < 4 + size:
        raise ValueError("bad frame header length")
    header = FrameHeader.model_validate_json(data[4:4 + size])
    image = data[4 + size:]
    if not image:
        raise ValueError("empty frame")
    return header, image
