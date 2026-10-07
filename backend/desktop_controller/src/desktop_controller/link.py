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


class Heartbeat(_Msg):
    type: Literal["heartbeat"] = "heartbeat"


# relay -> controller

class Paired(_Msg):
    type: Literal["paired"] = "paired"
    environment_id: str
    token: str
    proof: _Hex64


class Welcome(_Msg):
    type: Literal["welcome"] = "welcome"
    controller_generation: int


class HeartbeatAck(_Msg):
    type: Literal["heartbeat_ack"] = "heartbeat_ack"


class LinkError(_Msg):
    """Sent just before the relay closes the connection."""

    type: Literal["error"] = "error"
    code: Literal[
        "bad_message", "unsupported_protocol", "pairing_invalid", "not_authorized",
        "superseded", "revoked", "heartbeat_timeout",
    ]


ControllerMessage = Annotated[Union[Pair, Hello, Heartbeat], Field(discriminator="type")]
RelayMessage = Annotated[Union[Paired, Welcome, HeartbeatAck, LinkError], Field(discriminator="type")]
controller_message = TypeAdapter(ControllerMessage)
relay_message = TypeAdapter(RelayMessage)
