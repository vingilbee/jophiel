"""What each kind of observation looks like and what working memory makes of it
(docs/COLUMBINA_MEMORY_SESSION2_SPEC.md §5, §8). Pure: no database, no model.

An entry per ``(source, kind, schema_version)``: the payload's shape, who acted, how it's known
(observed or reported), where its words came from, how much it matters, a one-line summary written by
code, and the memory rule. A rule promotes a short memory event or skips with a reason; it never
explains a failure or turns a report into a fact. Adding a kind is an entry here, not a migration.

Text a source sent (a game's failure reason, a notice) stays quoted and attributed ("Minecraft
reported: ..."): it is data about what the source said, never an instruction.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

RULE_VERSION = "observation-memory-v1"
PAYLOAD_LIMIT = 16 * 1024
SUMMARY_LIMIT = 500
QUOTE_LIMIT = 120

# A skill ending one of these ways failed (app/ai/desktop/skill_driver.FAILED, kept apart so this
# module stays pure).
SKILL_FAILED = frozenset({"failed", "lost", "blocked", "refused", "timeout"})
SOURCE_LABELS = {"conversation": "the conversation", "minecraft_pack": "Minecraft"}


class ObservationInvalid(ValueError):
    """A report that doesn't fit its kind: never stored."""


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UserMessage(_Payload):
    message_id: str
    sequence: int = Field(ge=1)
    turn_id: str | None = None
    channel: str = Field(max_length=32)
    chars: int = Field(ge=0)
    attachments: int = Field(default=0, ge=0)


class ReplyRecorded(_Payload):
    message_id: str
    sequence: int = Field(ge=1)
    turn_id: str | None = None
    channel: str = Field(max_length=32)
    status: str = Field(max_length=16)
    chars: int = Field(ge=0)
    # Spoken first (a nudge, something she said on her own), not an answer to a message.
    proactive: bool = False


class ReplyInterrupted(_Payload):
    message_id: str
    turn_id: str | None = None
    heard_chars: int = Field(ge=0)
    total_chars: int = Field(ge=0)


class DeliveryStatus(_Payload):
    delivery_id: str
    message_id: str
    channel: str = Field(max_length=32)
    part_index: int = Field(ge=0)
    status: Literal["delivered", "read", "failed"]


class SkillOutcome(_Payload):
    experience_id: int
    skill: str = Field(max_length=80)
    status: str = Field(max_length=16)
    reason: str | None = Field(default=None, max_length=300)
    seconds: float | None = None
    step_id: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


class GameNotice(_Payload):
    experience_id: int
    notice: str = Field(max_length=48)
    urgency: str | None = Field(default=None, max_length=16)
    text: str = Field(default="", max_length=300)
    # Items the game said she carried when she died (name -> count), only for "died".
    dropped: dict[str, int] = Field(default_factory=dict)


class SourceAvailability(_Payload):
    availability: Literal["online", "offline"]
    previous: Literal["unknown", "online", "offline"]


# --------------------------------------------------------------------------- #
# What memory makes of one
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Promote:
    """Write one memory event. ``coalesce_key``: repeats of the same thing (the same failure on the same
    step) share one event; each repeat is still its own observation and becomes supporting evidence."""

    kind: str
    text: str
    epistemic: str  # observed | reported
    coalesce_key: str | None = None


@dataclass(frozen=True)
class Skip:
    reason: str


Decision = Promote | Skip


@dataclass(frozen=True)
class KindSpec:
    source: str
    kind: str
    version: int
    payload: type[_Payload]
    actor: str
    epistemic: str
    origin: str
    salience: Callable[[Any], str]
    summary: Callable[[Any], str]
    memory: Callable[[Any, str], Decision]  # (payload, source_instance) -> decision
    # Its memory event belongs to a context, so promotion waits until it has one.
    needs_context: bool = True


def quote(text: str | None, limit: int = QUOTE_LIMIT) -> str:
    """Source text, flattened and cut, for inside quotation marks."""
    words = " ".join((text or "").split())
    return words if len(words) <= limit else words[: limit - 1].rstrip() + "…"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _user_message_summary(p: UserMessage) -> str:
    extra = f", {_plural(p.attachments, 'attachment')}" if p.attachments else ""
    return f"The user sent a message ({_plural(p.chars, 'character')}{extra}) on {p.channel}"


def _reply_summary(p: ReplyRecorded) -> str:
    if p.proactive:
        return f"Columbina spoke first ({_plural(p.chars, 'character')}, {p.status})"
    return f"Columbina's reply was recorded ({p.status}, {_plural(p.chars, 'character')})"


def _interrupted_summary(p: ReplyInterrupted) -> str:
    return f"The user cut off a spoken reply after hearing {p.heard_chars} of {p.total_chars} characters"


def _interrupted_memory(p: ReplyInterrupted, _instance: str) -> Decision:
    return Promote("reply_interrupted",
                   f"The user cut off your spoken reply (they heard {p.heard_chars} of {p.total_chars} "
                   "characters; the rest was never heard)", "reported")


def _delivery_summary(p: DeliveryStatus) -> str:
    return f"{p.channel.capitalize()} reply part {p.part_index + 1}: {p.status}"


def _delivery_memory(p: DeliveryStatus, _instance: str) -> Decision:
    if p.status != "failed":
        return Skip("delivered replies are part of the conversation")
    return Promote("delivery_failed", f"Your {p.channel.capitalize()} reply couldn't be delivered", "reported",
                   coalesce_key=f"delivery_failed:{p.message_id}")


def _skill_summary(p: SkillOutcome) -> str:
    reason = f' (reason: "{quote(p.reason)}")' if p.reason else ""
    return f"Skill {p.skill} ended: {p.status}{reason}"


def _skill_memory(p: SkillOutcome, instance: str) -> Decision:
    if p.status not in SKILL_FAILED:
        return Skip("an ordinary outcome; kept as an observation only")
    reason = f' ("{quote(p.reason)}")' if p.reason else ""
    # The same failure again on the same step is one memory with more evidence, not a new memory.
    key = f"skill_failed:{instance}:{p.step_id or '-'}:{p.skill}:{p.status}:{quote(p.reason, 60).lower()}"
    return Promote("action_failed", f"Minecraft reported: {p.skill} {p.status}{reason}", "observed",
                   coalesce_key=key[:240])


def _notice_salience(p: GameNotice) -> str:
    if p.notice == "died":
        return "critical"
    return "notable" if p.urgency in ("urgent", "heads_up") else "routine"


def _notice_summary(p: GameNotice) -> str:
    return f'Game notice ({p.notice}): "{quote(p.text)}"' if p.text else f"Game notice ({p.notice})"


def _notice_memory(p: GameNotice, _instance: str) -> Decision:
    if p.notice != "died":
        return Skip("only deaths are kept from notices for now")
    dropped = ", ".join(f"{name} x{count}" for name, count in list(p.dropped.items())[:8])
    told = f"; it reported you dropped {dropped}" if dropped else ""
    return Promote("died", f"Minecraft reported: you died{told}", "observed")


def _availability_summary(p: SourceAvailability) -> str:
    return f"Source is {p.availability} (was {p.previous})"


def _availability_memory(source: str) -> Callable[[SourceAvailability, str], Decision]:
    label = SOURCE_LABELS.get(source, source)

    def rule(p: SourceAvailability, _instance: str) -> Decision:
        if p.availability == "offline" and p.previous == "online":
            return Promote("source_offline", f"{label} went offline: what it last reported may be out of date",
                           "observed")
        if p.availability == "online" and p.previous == "offline":
            return Promote("source_online", f"{label} came back online", "observed")
        return Skip("not a change worth remembering")

    return rule


def _skip(reason: str) -> Callable[[Any, str], Decision]:
    return lambda _p, _i: Skip(reason)


def _fixed(level: str) -> Callable[[Any], str]:
    return lambda _p: level


_SPECS = [
    KindSpec("conversation", "user_message", 1, UserMessage, "user", "observed", "user", _fixed("routine"),
             _user_message_summary, _skip("kept in the conversation history"), needs_context=False),
    KindSpec("conversation", "reply_recorded", 1, ReplyRecorded, "columbina", "observed", "model",
             lambda p: "notable" if p.status == "failed" else "routine", _reply_summary,
             _skip("kept in the conversation history"), needs_context=False),
    KindSpec("conversation", "reply_interrupted", 1, ReplyInterrupted, "user", "reported", "system",
             _fixed("notable"), _interrupted_summary, _interrupted_memory),
    KindSpec("conversation", "delivery_status", 1, DeliveryStatus, "system", "reported", "external",
             lambda p: "notable" if p.status == "failed" else "routine", _delivery_summary, _delivery_memory),
    KindSpec("minecraft_pack", "skill_outcome", 1, SkillOutcome, "columbina", "observed", "external",
             lambda p: "notable" if p.status in SKILL_FAILED else "routine", _skill_summary, _skill_memory),
    KindSpec("minecraft_pack", "notice", 1, GameNotice, "environment", "observed", "external",
             _notice_salience, _notice_summary, _notice_memory),
    KindSpec("minecraft_pack", "source_availability", 1, SourceAvailability, "system", "observed", "system",
             _fixed("notable"), _availability_summary, _availability_memory("minecraft_pack")),
]
REGISTRY: dict[tuple[str, str, int], KindSpec] = {(s.source, s.kind, s.version): s for s in _SPECS}


def spec(source: str, kind: str, version: int = 1) -> KindSpec:
    found = REGISTRY.get((source, kind, version))
    if found is None:
        raise ObservationInvalid(f"unregistered observation kind {source}.{kind} v{version}")
    return found


@dataclass(frozen=True)
class Normalized:
    """A validated report, ready to store."""

    spec: KindSpec
    payload: dict
    summary: str
    salience: str


def normalize(source: str, kind: str, payload: dict, version: int = 1) -> Normalized:
    """Validate a payload against its kind and render its summary. Raises ObservationInvalid."""
    entry = spec(source, kind, version)
    try:
        model = entry.payload.model_validate(payload)
    except ValidationError as exc:
        raise ObservationInvalid(f"{source}.{kind}: {exc.errors()[0]['msg']}") from exc
    data = model.model_dump(mode="json")
    if len(json.dumps(data)) > PAYLOAD_LIMIT:
        raise ObservationInvalid(f"{source}.{kind}: payload over {PAYLOAD_LIMIT} bytes")
    summary = " ".join(entry.summary(model).split())[:SUMMARY_LIMIT] or kind
    return Normalized(entry, data, summary, entry.salience(model))


def decide(source: str, kind: str, version: int, payload: dict, source_instance: str) -> Decision:
    """What memory makes of a stored observation (re-validated, so a row that no longer fits fails loudly)."""
    entry = spec(source, kind, version)
    return entry.memory(entry.payload.model_validate(payload), source_instance)


def age_note(occurred_at: datetime | None, received_at: datetime, now: datetime) -> str:
    """How late a report is, for memory text: empty when it arrived promptly."""
    if occurred_at is None:
        return " (when it happened is unknown)"
    late = (received_at - occurred_at).total_seconds()
    return f" (reported {round(late / 60)} min after it happened)" if late > 300 else ""
