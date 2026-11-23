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
SOURCE_LABELS = {"conversation": "the conversation", "minecraft_pack": "Minecraft", "desktop": "your desktop",
                 "desktop_security": "your desktop's security logs"}
DESKTOP_NOT_DONE = frozenset({"failed", "refused", "unconfirmed"})


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


class FileShared(_Payload):
    """A file the user attached to a message (services/attachment_text.py), as code read it: its name, type and
    how much of it has text. The filename is the user's own text, quoted."""

    message_id: str
    asset_id: str = Field(max_length=16)
    filename: str = Field(max_length=220)
    media_type: str = Field(max_length=120)
    status: Literal["readable", "no_text", "unsupported", "failed"]
    unit: str | None = Field(default=None, max_length=16)
    total: int | None = Field(default=None, ge=0)
    no_text: int = Field(default=0, ge=0)  # pages with no text (scanned pictures or empty)


class FileRead(_Payload):
    """One read of a file by her (``read_attachment``): which file, how, and which pages she was shown."""

    asset_id: str = Field(max_length=16)
    filename: str = Field(max_length=220)
    mode: Literal["overview", "pages", "find"]
    pages: str = Field(default="", max_length=200)  # "120-128, 140"
    query: str | None = Field(default=None, max_length=200)
    status: Literal["read", "partial", "no_text"]
    unit: str = Field(default="page", max_length=16)
    total: int = Field(default=0, ge=0)


class DeliveryStatus(_Payload):
    delivery_id: str
    message_id: str
    channel: str = Field(max_length=32)
    part_index: int = Field(ge=0)
    status: Literal["delivered", "read", "failed"]


class ThoughtBasis(_Payload):
    """What she read when she thought it: message ids, memory references (``event:42``, ``item:<uuid>``,
    ``fact:<uuid>``), observation ids."""

    messages: list[str] = Field(default_factory=list, max_length=40)
    memories: list[str] = Field(default_factory=list, max_length=40)
    observations: list[int] = Field(default_factory=list, max_length=40)


class ThoughtConcluded(_Payload):
    """A hard thought of hers that reached a conclusion, handed to memory whole
    (docs/COLUMBINA_THOUGHT_MEMORY_SPEC.md §3). Her own reasoning: never a report of what's true."""

    thought_id: str
    conclusion_kind: Literal["decision", "guess", "question", "intention", "feeling"]
    conclusion: str = Field(min_length=1, max_length=4000)
    question: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=8000)  # her private thinking, whole
    trigger: str | None = Field(default=None, max_length=32)
    topic: str | None = Field(default=None, max_length=200)
    status: Literal["completed"] = "completed"
    basis: ThoughtBasis = Field(default_factory=ThoughtBasis)
    supersedes: str | None = None  # the unfinished thought it carried on from
    premise_conflict: str | None = None  # the unresolved disagreement it was about (its problem's conflict)
    # Her judgment of the effort (progress, cause, check, if_supported, leaves_unknown), when she had one.
    assessment: dict[str, str] | None = None


class LearningFollowUp(_Payload):
    """A reviewer's follow-up (services/learning_review.propose): a question to ask the user, or something to
    observe or practise, that could settle what records can't. A proposal for her mind, handed to memory like her
    own thoughts, so it's matched against what she already holds (``thought_memory``) instead of added again."""

    assessment_id: str
    revision: int = Field(ge=1)
    kind: Literal["question", "observation", "practice"]
    text: str = Field(min_length=1, max_length=2000)
    resolves: str | None = Field(default=None, max_length=1000)
    alternatives: list[str] = Field(default_factory=list, max_length=6)
    # The messages of the stretch it was about (ids), most recent last: which occurrence it is about.
    messages: list[str] = Field(default_factory=list, max_length=20)
    session_id: str | None = None


class CaptureLink(_Payload):
    """Where the detail of a game event is kept (docs/learning-system/POST-AUDIT-01-implementation.md): the
    learning capture's decision and the pack's run id for an outcome; the pack's notice seq and its own time for
    a notice (her trajectory holds that moment while raw retention lasts). Ids only, no content."""
    attempt_id: str | None = Field(default=None, max_length=40)
    run_id: str | None = Field(default=None, max_length=40)
    pack_seq: int | None = None
    pack_at: str | None = Field(default=None, max_length=40)


class SkillOutcome(_Payload):
    experience_id: int
    skill: str = Field(max_length=80)
    status: str = Field(max_length=16)
    reason: str | None = Field(default=None, max_length=300)
    seconds: float | None = None
    step_id: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)
    detail: CaptureLink | None = None


class GameNotice(_Payload):
    experience_id: int
    notice: str = Field(max_length=48)
    urgency: str | None = Field(default=None, max_length=16)
    text: str = Field(default="", max_length=300)
    # Items the game said she carried when she died (name -> count), only for "died".
    dropped: dict[str, int] = Field(default_factory=dict)
    detail: CaptureLink | None = None


class DesktopAction(_Payload):
    """One action of her hands on her own Windows desktop (services/desktop_control), and what was in front
    once it finished. ``summary`` is code's words for what she meant to do; window titles are the desktop's
    own text, quoted."""

    action_id: str = Field(max_length=40)
    tool: Literal["screenshot", "move_mouse", "click", "drag", "scroll", "keyboard"]
    status: Literal["done", "failed", "refused", "unconfirmed"]
    reason: str | None = Field(default=None, max_length=300)
    summary: str = Field(max_length=200)
    foreground_process: str | None = Field(default=None, max_length=64)
    foreground_title: str | None = Field(default=None, max_length=200)
    # What's in front differs from what she last saw there.
    app_changed: bool = False
    turn_id: str | None = Field(default=None, max_length=40)
    step: int = Field(default=1, ge=1)


class SecurityEventReport(_Payload):
    """One record of a Windows event log on her desktop (services/security_activity.py), as the log rendered it.
    Everything but the numbers is the machine's own text: evidence of what the log said, never an instruction.
    ``occurred_at`` is the log's own time (the VM's clock)."""

    environment_id: str = Field(max_length=40)
    channel: str = Field(max_length=128)
    stream: int = Field(default=0, ge=0)
    record_id: int = Field(ge=0)
    provider: str = Field(default="", max_length=128)
    event_id: int = Field(ge=0, le=65535)
    version: int | None = None
    level: int | None = None
    level_name: str | None = Field(default=None, max_length=32)
    task: str | None = Field(default=None, max_length=64)
    opcode: str | None = Field(default=None, max_length=64)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    audit: Literal["success", "failure"] | None = None
    computer: str | None = Field(default=None, max_length=128)
    user_sid: str | None = Field(default=None, max_length=96)
    process_id: int | None = None
    message: str = Field(default="", max_length=600)
    data: dict[str, str] = Field(default_factory=dict)


class SecurityCoverageChange(_Payload):
    """What the security collector could or couldn't see changed: records lost (``gap``), a log cleared
    (``reset``), a channel unreadable or readable again, monitoring switched on or off by the owner."""

    environment_id: str = Field(max_length=40)
    channel: str = Field(max_length=128)  # "*": every channel (the monitoring switch)
    status: Literal["gap", "reset", "unavailable", "available", "monitoring_on", "monitoring_off"]
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    reason: str | None = Field(default=None, max_length=200)


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
    # Who processes it (one receipt each). Her thoughts go to ``thought_memory`` alone, which can judge
    # with a model; ``memory`` is code only. ``learning`` (services/learning_assessment.triage, code only) decides
    # whether a report starts or adds to a retrospective assessment.
    consumers: tuple[str, ...] = ("memory",)


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
        return f"Jophiel spoke first ({_plural(p.chars, 'character')}, {p.status})"
    return f"Jophiel's reply was recorded ({p.status}, {_plural(p.chars, 'character')})"


def _interrupted_summary(p: ReplyInterrupted) -> str:
    return f"The user cut off a spoken reply after hearing {p.heard_chars} of {p.total_chars} characters"


def _interrupted_memory(p: ReplyInterrupted, _instance: str) -> Decision:
    return Promote("reply_interrupted",
                   f"The user cut off your spoken reply (they heard {p.heard_chars} of {p.total_chars} "
                   "characters; the rest was never heard)", "reported")


def _file_size(unit: str | None, total: int | None, no_text: int = 0) -> str:
    if not unit or total is None:
        return ""
    size = _plural(total, unit)
    return f", {size}" + (f", {no_text} without readable text" if no_text else "")


def _file_shared_summary(p: FileShared) -> str:
    words = {"readable": "", "no_text": "; it has no readable text", "unsupported": "; not a file she can read as text",
             "failed": "; it couldn't be opened"}[p.status]
    return f'The user shared a file: "{quote(p.filename)}" ({p.media_type}{_file_size(p.unit, p.total, p.no_text)}){words}'


def _file_shared_memory(p: FileShared, _instance: str) -> Decision:
    return Promote("file_shared", _file_shared_summary(p).replace("she can", "you can") + f" [file {p.asset_id}]",
                   "observed", coalesce_key=f"file_shared:{p.asset_id}")


def _file_read_words(p: FileRead) -> str:
    name = f'"{quote(p.filename)}"'
    unit = f"{p.unit}s" if "," in p.pages or "-" in p.pages else p.unit
    if p.status == "no_text":
        return f"tried to read {name}, but what you asked for has no readable text"
    if p.mode == "overview":
        shown = f" and {unit} {p.pages}" if p.pages else ""
        return f"looked over {name} (its outline{shown}, of {_plural(p.total, p.unit)})"
    if p.mode == "find":
        found = f"found it on {unit} {p.pages}" if p.pages else "found no match"
        return f'searched {name} for "{quote(p.query, 80)}" and {found}'
    partial = " (stopped at the size limit)" if p.status == "partial" else ""
    return f"read {unit} {p.pages} of {name}, of {_plural(p.total, p.unit)}{partial}"


def _file_read_summary(p: FileRead) -> str:
    return f"Jophiel {_file_read_words(p)}"


def _file_read_memory(p: FileRead, _instance: str) -> Decision:
    """What she read is kept (the same read again is one memory with more evidence); the file's text is not."""
    key = f"file_read:{p.asset_id}:{p.mode}:{p.pages}:{(p.query or '').lower()}"
    return Promote("file_read", f"You {_file_read_words(p)} [file {p.asset_id}]", "observed",
                   coalesce_key=key[:240])


def _delivery_summary(p: DeliveryStatus) -> str:
    return f"{p.channel.capitalize()} reply part {p.part_index + 1}: {p.status}"


def _delivery_memory(p: DeliveryStatus, _instance: str) -> Decision:
    if p.status != "failed":
        return Skip("delivered replies are part of the conversation")
    return Promote("delivery_failed", f"Your {p.channel.capitalize()} reply couldn't be delivered", "reported",
                   coalesce_key=f"delivery_failed:{p.message_id}")


_CONCLUSION_WORDS = {"decision": "decided", "guess": "suspected", "question": "wondered",
                     "intention": "meant to", "feeling": "felt"}


def _follow_up_summary(p: LearningFollowUp) -> str:
    return f'Learning proposed a follow-up ({p.kind}): "{quote(p.text, 300)}"'


def _thought_summary(p: ThoughtConcluded) -> str:
    return f'Jophiel {_CONCLUSION_WORDS[p.conclusion_kind]} (her own thinking): "{quote(p.conclusion, 300)}"'


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


def _front(p: DesktopAction) -> str:
    if not p.foreground_process and not p.foreground_title:
        return "an unknown window"
    title = f' "{quote(p.foreground_title)}"' if p.foreground_title else ""
    return f"{p.foreground_process or 'a window'}{title}"


def _desktop_summary(p: DesktopAction) -> str:
    if p.status == "done":
        return f"On her desktop, Jophiel {p.summary}; in front: {_front(p)}"
    reason = f' ("{quote(p.reason)}")' if p.reason else ""
    return f"On her desktop, Jophiel {p.summary}: {p.status}{reason}"


def _desktop_salience(p: DesktopAction) -> str:
    return "notable" if p.status in ("failed", "unconfirmed") else "routine"


def _desktop_memory(p: DesktopAction, instance: str) -> Decision:
    """A failure is kept (repeats of the same failure on the same tool are one memory); seeing a new window
    in front is kept as what she saw; everything else stays an observation."""
    if p.status in ("failed", "unconfirmed"):
        key = f"desktop_failed:{instance}:{p.tool}:{p.status}:{quote(p.reason, 60).lower()}"
        reason = f' ("{quote(p.reason)}")' if p.reason else ""
        word = "failed" if p.status == "failed" else "may not have happened (no answer)"
        return Promote("action_failed", f"Your desktop reported: {p.summary} {word}{reason}", "observed",
                       coalesce_key=key[:240])
    if p.status == "done" and p.app_changed and (p.foreground_process or p.foreground_title):
        return Promote("desktop_seen", f"On your desktop you had {_front(p)} in front", "observed",
                       coalesce_key=f"desktop_seen:{instance}:{p.foreground_process}:{quote(p.foreground_title, 80)}"[:240])
    return Skip("an ordinary desktop action; kept as an observation only")


def _security_salience(p: SecurityEventReport) -> str:
    """The source's own severity, never a judgment of what the record means: Windows' Critical level is
    critical; Error, Warning and audit failures are notable; everything else is routine."""
    if p.level == 1:
        return "critical"
    return "notable" if p.level in (2, 3) or p.audit == "failure" else "routine"


def _security_summary(p: SecurityEventReport) -> str:
    traits = [t for t in (p.provider, p.level_name if p.level not in (0, 4, None) else None,
                          "audit failure" if p.audit == "failure" else None) if t]
    said = f': "{quote(p.message, 200)}"' if p.message else ""
    return f"{p.channel} event {p.event_id}" + (f" ({', '.join(traits)})" if traits else "") + said


def _coverage_summary(p: SecurityCoverageChange) -> str:
    where = "every channel" if p.channel == "*" else p.channel
    words = {"gap": f"{where}: records were lost before they could be read",
             "reset": f"{where}: the log was cleared or recreated",
             "unavailable": f"{where} can't be read",
             "available": f"{where} can be read again",
             "monitoring_on": "Security monitoring was switched on",
             "monitoring_off": "Security monitoring was switched off"}[p.status]
    span = f" ({p.start} to {p.end})" if p.start and p.end and p.start != p.end else ""
    reason = f' ("{quote(p.reason)}")' if p.reason else ""
    return words + span + reason


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
             _user_message_summary, _skip("kept in the conversation history"), needs_context=False,
             consumers=("memory", "learning", "questions")),
    KindSpec("conversation", "reply_recorded", 1, ReplyRecorded, "columbina", "observed", "model",
             lambda p: "notable" if p.status == "failed" else "routine", _reply_summary,
             _skip("kept in the conversation history"), needs_context=False, consumers=("memory", "questions")),
    KindSpec("conversation", "reply_interrupted", 1, ReplyInterrupted, "user", "reported", "system",
             _fixed("notable"), _interrupted_summary, _interrupted_memory, consumers=("memory", "learning")),
    KindSpec("conversation", "file_shared", 1, FileShared, "user", "observed", "user", _fixed("notable"),
             _file_shared_summary, _file_shared_memory),
    KindSpec("conversation", "file_read", 1, FileRead, "columbina", "observed", "system", _fixed("routine"),
             _file_read_summary, _file_read_memory),
    KindSpec("conversation", "delivery_status", 1, DeliveryStatus, "system", "reported", "external",
             lambda p: "notable" if p.status == "failed" else "routine", _delivery_summary, _delivery_memory,
             consumers=("memory", "learning")),
    KindSpec("minecraft_pack", "skill_outcome", 1, SkillOutcome, "columbina", "observed", "external",
             lambda p: "notable" if p.status in SKILL_FAILED else "routine", _skill_summary, _skill_memory,
             consumers=("memory", "learning")),
    KindSpec("minecraft_pack", "notice", 1, GameNotice, "environment", "observed", "external",
             _notice_salience, _notice_summary, _notice_memory, consumers=("memory", "learning")),
    KindSpec("minecraft_pack", "source_availability", 1, SourceAvailability, "system", "observed", "system",
             _fixed("notable"), _availability_summary, _availability_memory("minecraft_pack")),
    KindSpec("desktop", "desktop_action", 1, DesktopAction, "columbina", "observed", "external",
             _desktop_salience, _desktop_summary, _desktop_memory, consumers=("memory", "learning")),
    # Security records stay in observation (routine ones until retention): her mind's investigations read them, and
    # what she concludes reaches memory as her thought. No per-record receipts: nothing processes them one by one.
    KindSpec("desktop_security", "security_event", 1, SecurityEventReport, "environment", "observed", "external",
             _security_salience, _security_summary,
             _skip("security records stay in observation; what her mind concludes about them reaches memory"),
             needs_context=False, consumers=()),
    KindSpec("desktop_security", "security_coverage", 1, SecurityCoverageChange, "system", "observed", "system",
             lambda p: "routine" if p.status == "available" else "notable", _coverage_summary,
             _skip("what the collector could see is source health, shown with every answer about it"),
             needs_context=False, consumers=()),
    KindSpec("mind", "thought_concluded", 1, ThoughtConcluded, "columbina", "inferred", "model", _fixed("notable"),
             _thought_summary, _skip("her thoughts go to thought memory"), consumers=("thought_memory",)),
    KindSpec("learning", "follow_up_proposed", 1, LearningFollowUp, "columbina", "inferred", "model",
             _fixed("notable"), _follow_up_summary, _skip("matched against her open items by thought memory"),
             consumers=("thought_memory",)),
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
