"""Source-backed history (docs/COLUMBINA_SOURCE_BACKED_RECALL.md): how one thing changed, rebuilt for one
question from the passages evidence expansion collected. Pure: contracts, the reader's prompt and
schema, the check of its answer, and rendering. The database side is ``services/recall_expand.py`` and
``services/recall_history.py``.

The reader (one structured model call) proposes the history; code keeps only what it can trace. Every
step, the current value and every conflict must cite exact words of a passage it was given. A citation
is traceability, not truth: an exact quote can sit under a wrong reading. What code does enforce:

- a citation that isn't word for word in its passage is dropped; a step left with none is dropped, and
  the account is ``partial``;
- a step resting only on Jophiel's own earlier words is kept but not established (what she said is not
  what the user told her; she has been wrong before);
- the current value is ``unsettled`` when a step it rests on, or any step after those, was dropped or
  rests only on her words, and ``not_established`` when nothing traceable supports it; under an explicit
  scope it must rest on something other than memories from outside that scope;
- steps are ordered by when their sources were said, not by where the reader listed them.

Nothing here writes memory: the account is a reading for this question, never a fact.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.ai.recall.contracts import MemoryRecord, MemoryRef

PROMPT_VERSION = "recall-history-v1"
QUESTION_LIMIT = 1000

Speaker = Literal["user", "columbina", "source", "memory"]
StepKind = Literal["first_recorded", "change", "correction", "considered", "unclear"]
STEP_KINDS: tuple[str, ...] = ("first_recorded", "change", "correction", "considered", "unclear")
Stop = Literal["exhausted", "depth_budget", "node_budget", "char_budget", "time_budget", "failed", "no_seeds"]
SOURCE_KINDS = ("message", "observation", "experience", "session_event", "thought")


# --------------------------------------------------------------------------- #
# What expansion collected
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NodeRef:
    """A memory (fact, episode, event, item) or a source row (message, observation, ...)."""

    kind: str
    id: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def is_memory(self) -> bool:
        return self.kind in ("fact", "episode", "event", "item")

    def memory_ref(self) -> MemoryRef:
        return MemoryRef(self.kind, self.id)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Passage:
    label: str  # "p1": how the reader and the rendering cite it
    node: NodeRef
    speaker: Speaker  # who asserted it: the user, Jophiel, a source reporting, or a memory record
    text: str  # whitespace-normalized; citations are spans into exactly this
    truncated: bool
    said_at: datetime | None  # said, reported, or (memories) recorded
    fingerprint: str  # the source's version when it was read
    depth: int  # 0: found by search
    context_label: str | None = None
    in_scope: bool | None = None  # None: no scope asked, or the record has no context (messages, facts)
    status: str | None = None  # a memory's status and lifecycle, as recall shows it
    source_label: str | None = None  # "Minecraft", for a source's report
    links: tuple[tuple[str, str], ...] = ()  # (role, other passage label): how expansion connected them
    record: MemoryRecord | None = None


@dataclass
class Expansion:
    seeds: list[MemoryRef]
    passages: list[Passage]
    visited: int
    depth_reached: int
    stop: Stop
    truncated: list[str] = field(default_factory=list)  # what a budget cut, in plain words
    gaps: list[str] = field(default_factory=list)  # sources gone or unreadable
    unavailable: int = 0  # linked rows that weren't this user's or no longer exist: never shown
    ms: int = 0
    queries: int = 0

    def by_label(self) -> dict[str, Passage]:
        return {p.label: p for p in self.passages}


# --------------------------------------------------------------------------- #
# What the reader proposes, after the check
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cite:
    passage: str
    start: int
    end: int
    quote: str


@dataclass(frozen=True)
class Step:
    kind: str
    before: str
    after: str
    cites: tuple[Cite, ...]
    support: Literal["user", "source", "memory", "her_words_only"]
    said_at: datetime | None
    listed_as: int  # its 1-based position in the reader's answer
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Current:
    state: Literal["established", "unsettled", "not_established"]
    value: str
    cites: tuple[Cite, ...]
    note: str | None = None


@dataclass
class HistoryAccount:
    status: Literal["complete", "partial", "no_history", "failed"]
    subject: str = ""
    property: str = ""
    same_thing: bool = True
    identity_note: str | None = None
    steps: list[Step] = field(default_factory=list)
    current: Current | None = None
    gaps: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, tuple[Cite, ...]]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # what the check removed, and why
    reordered: bool = False
    error: str | None = None

    def cited_labels(self) -> set[str]:
        cites = [c for s in self.steps for c in s.cites] + list(self.current.cites if self.current else ())
        cites += [c for _, group in self.conflicts for c in group]
        return {c.passage for c in cites}


# --------------------------------------------------------------------------- #
# The reader's input
# --------------------------------------------------------------------------- #

INSTRUCTIONS = """You rebuild the history of one thing from passages, for one question. You don't answer the \
user: Jophiel will, using what you return.

The passages were collected by following links from memories that matched the question to the messages \
they came from, and from those messages to other memories they touched. So they are candidates: many are \
about other things, and one message can talk about several topics. Use only what is about the thing the \
question asks about, and the property of it the question cares about (a time, a place, a name, a colour, a \
preference...). If the question is about several properties, choose the one it most needs and say the \
others in "gaps".

Who said what matters:
- "the user said": their own words. This is what they told Jophiel.
- "Jophiel said": her own earlier words, NOT the user's. She can have been wrong. Never treat her words \
as the user's, and never let them settle a value the user's words don't support.
- "memory record": a note written from a conversation (a goal, a fact, an event). It can restate a value; \
a finished goal's wording still shows what was planned at the time.
- "<source> reported": what an app or game reported.

Steps, oldest first, each one of:
- first_recorded: the earliest value the passages show (before is "").
- change: the value really changed (a new plan or arrangement replaced the old one).
- correction: an earlier statement was a mistake and the user corrected it.
- considered: an alternative was mentioned as a possibility and not adopted. Never a change.
- unclear: something about the value was said but you can't tell which of the above it is.
Say each value in the passages' own words. Order steps by when they were said; don't invent dates. A \
passage may say when a change takes effect: keep that in the value's words only if the passage says it.

current: what holds now, only if the passages establish it. "follows" lists the numbers (1-based) of the \
steps it rests on. If the latest value is unclear, conflicting, or comes only from Jophiel's words, set \
known to false and say why in gaps. If a scope is given, the current value must hold in that scope: \
passages marked OUTSIDE SCOPE can explain history but never what holds there now.

Citations: every step, the current value and every conflict cites at least one passage with an exact \
quote, copied character for character from that passage (3 to 30 words). Cite the words that show the \
step, not just a mention of the thing.

same_thing: false if you're not sure the passages are about one and the same thing (two clubs, two \
markers, two worlds with similar names), with a note. gaps: what the passages don't show (an earlier \
value that must have existed, a step between two values, a missing source). conflicts: statements that \
disagree and that you can't order. Don't fill a gap by guessing. Return JSON only."""

_CITES = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                     "required": ["passage", "quote"],
                                     "properties": {"passage": {"type": "string"}, "quote": {"type": "string"}}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["subject", "property", "same_thing", "steps", "current", "gaps", "conflicts"],
    "properties": {
        "subject": {"type": "string"},
        "property": {"type": "string"},
        "same_thing": {"type": "object", "additionalProperties": False, "required": ["sure", "note"],
                       "properties": {"sure": {"type": "boolean"}, "note": {"type": "string"}}},
        "steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "before", "after", "cites"],
            "properties": {"kind": {"type": "string", "enum": list(STEP_KINDS)}, "before": {"type": "string"},
                           "after": {"type": "string"}, "cites": _CITES}}},
        "current": {"type": "object", "additionalProperties": False, "required": ["known", "value", "follows", "cites"],
                    "properties": {"known": {"type": "boolean"}, "value": {"type": "string"},
                                   "follows": {"type": "array", "items": {"type": "integer"}}, "cites": _CITES}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["note", "cites"],
            "properties": {"note": {"type": "string"}, "cites": _CITES}}},
    },
}

_SPEAKER = {"user": "the user said", "columbina": "Jophiel said (her own words, not the user's)",
            "memory": "memory record", "source": "reported"}


def _when(moment: datetime | None, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime("%a %d %b %Y, %H:%M") if moment else "time unknown"


def passage_head(p: Passage, zone: ZoneInfo, labels: dict[str, str] | None = None) -> str:
    who = _SPEAKER[p.speaker]
    if p.speaker == "source":
        who = f"{p.source_label or 'a source'} reported"
    if p.speaker == "memory" and p.record is not None:
        who = f"memory record [{p.record.ref}] {p.record.subkind}"
    parts = [who, ("recorded " if p.speaker == "memory" else "") + _when(p.said_at, zone)]
    if p.context_label:
        parts.append(f"in {p.context_label}")
    if p.in_scope is False:
        parts.append("OUTSIDE SCOPE")
    if p.status:
        parts.append(p.status)
    if p.links:
        parts.append("linked to " + ", ".join(f"{other} ({role})" for role, other in p.links))
    if p.truncated:
        parts.append("cut short")
    return f"[{p.label}] " + " · ".join(parts)


def reader_input(question: str, about: str | None, scope: list[str] | None, expansion: Expansion,
                 *, now: datetime, zone: str) -> str:
    tz = ZoneInfo(zone)
    lines = [f"QUESTION (the user's words): {question[:QUESTION_LIMIT]}",
             f"ABOUT (as Jophiel put it): {about}" if about else "ABOUT: not given; work it out from the question",
             (f"SCOPE: only {', '.join(scope)} was asked about." if scope else "SCOPE: none given."),
             f"NOW: {_when(now, tz)} ({zone})", "", "PASSAGES (oldest first):"]
    for p in expansion.passages:
        lines += [passage_head(p, tz), f'"{p.text}"']
    lines += ["", "COLLECTION: " + coverage_line(expansion)]
    return "\n".join(lines)


def coverage_line(expansion: Expansion) -> str:
    parts = [f"{expansion.visited} records and messages looked at, {len(expansion.passages)} shown",
             _STOP_WORDS[expansion.stop]]
    parts += expansion.truncated + expansion.gaps
    return "; ".join(parts)


_STOP_WORDS = {
    "exhausted": "every link was followed",
    "depth_budget": "stopped at the link-distance limit: older steps may lie further out",
    "node_budget": "stopped at the record limit: some linked records weren't read",
    "char_budget": "stopped at the text limit: some linked passages weren't shown",
    "time_budget": "stopped at the time limit: some links weren't followed",
    "failed": "collecting failed part way: what's here is incomplete",
    "no_seeds": "search found nothing to start from",
}


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _cites(raw, passages: dict[str, Passage], dropped: list[str], where: str) -> tuple[Cite, ...]:
    out: list[Cite] = []
    for entry in raw or []:
        label = _norm(entry.get("passage", "")).strip("[]")
        quote = _norm(entry.get("quote", "")).strip('"')
        passage = passages.get(label)
        if passage is None:
            dropped.append(f"{where}: cited {label or 'nothing'}, which wasn't given")
            continue
        start = passage.text.find(quote) if quote else -1
        if start < 0:
            dropped.append(f"{where}: quote not found word for word in {label}")
            continue
        cite = Cite(label, start, start + len(quote), quote)
        if cite not in out:
            out.append(cite)
    return tuple(out)


def _support(cites: tuple[Cite, ...], passages: dict[str, Passage]) -> str:
    speakers = {passages[c.passage].speaker for c in cites}
    for strongest in ("user", "source", "memory"):
        if strongest in speakers:
            return strongest
    return "her_words_only"


def _said_at(cites: tuple[Cite, ...], passages: dict[str, Passage]) -> datetime | None:
    moments = [passages[c.passage].said_at for c in cites if passages[c.passage].said_at is not None]
    return min(moments) if moments else None


def check(raw: dict, expansion: Expansion, *, scoped: bool) -> HistoryAccount:
    """The reader's answer, kept only as far as it can be traced to the passages (module docstring)."""
    passages = expansion.by_label()
    dropped: list[str] = []
    account = HistoryAccount(status="complete", subject=_norm(raw.get("subject"))[:200],
                             property=_norm(raw.get("property"))[:200])
    same = raw.get("same_thing") or {}
    account.same_thing = bool(same.get("sure", True))
    account.identity_note = _norm(same.get("note")) or None
    lost: set[int] = set()  # 1-based positions of steps the check dropped or couldn't establish
    steps: list[Step] = []
    for position, entry in enumerate(raw.get("steps") or [], start=1):
        kind = entry.get("kind") if entry.get("kind") in STEP_KINDS else "unclear"
        cites = _cites(entry.get("cites"), passages, dropped, f"step {position}")
        if not cites:
            dropped.append(f"step {position} ({kind}) dropped: nothing it cited could be found")
            lost.add(position)
            continue
        after, before = _norm(entry.get("after"))[:200], _norm(entry.get("before"))[:200]
        warnings = []
        quoted = " ".join(c.quote.lower() for c in cites)
        if after and after.lower() not in quoted:
            warnings.append("its value isn't in the quoted words")  # traceability only; not dropped
        support = _support(cites, passages)
        if support == "her_words_only":
            lost.add(position)
        steps.append(Step(kind, before, after, cites, support, _said_at(cites, passages), position, tuple(warnings)))
    ordered = sorted(steps, key=lambda s: (s.said_at is None, s.said_at or datetime.min, s.listed_as))
    account.reordered = [s.listed_as for s in ordered] != [s.listed_as for s in steps]
    account.steps = ordered

    current = raw.get("current") or {}
    value = _norm(current.get("value"))[:200]
    cites = _cites(current.get("cites"), passages, dropped, "current value")
    follows = {int(n) for n in current.get("follows") or [] if isinstance(n, int)}
    if not current.get("known") or not value:
        account.current = Current("not_established", value, cites, "the passages don't establish it")
    elif not cites:
        account.current = Current("not_established", value, (), "nothing it cited could be found")
    else:
        note = None
        state = "established"
        latest_relied = max(follows) if follows else 0
        if lost & follows or any(n > latest_relied for n in lost):
            state, note = "unsettled", "a step it rests on, or one after it, couldn't be supported"
        if follows and all(n in lost or n not in {s.listed_as for s in steps} for n in follows):
            state, note = "unsettled", "the steps it rests on couldn't be supported"
        if _support(cites, passages) == "her_words_only":
            state, note = "unsettled", "only Jophiel's own earlier words say it"
        if scoped and all(passages[c.passage].in_scope is False for c in cites):
            state, note = "not_established", "only memories outside the asked scope say it"
        account.current = Current(state, value, cites, note)

    for index, entry in enumerate(raw.get("conflicts") or [], start=1):
        group = _cites(entry.get("cites"), passages, dropped, f"conflict {index}")
        note = _norm(entry.get("note"))[:300]
        if note and group:
            account.conflicts.append((note, group))
        elif note:
            dropped.append(f"conflict {index} dropped: nothing it cited could be found")
    account.gaps = [_norm(g)[:300] for g in raw.get("gaps") or [] if _norm(g)]
    account.dropped = dropped
    if not account.steps:
        account.status = "no_history"
    elif dropped or lost or account.gaps or account.conflicts or not account.same_thing or expansion.stop != "exhausted":
        account.status = "partial"
    return account


# --------------------------------------------------------------------------- #
# Rendering, for her
# --------------------------------------------------------------------------- #

_KIND_WORDS = {"first_recorded": "first recorded as", "change": "changed", "correction": "corrected",
               "considered": "considered, not adopted:", "unclear": "unclear step:"}
_SUPPORT_WORDS = {"user": "", "source": " (reported by a source)", "memory": " (only in memory records)",
                  "her_words_only": " (ONLY in your own earlier words, never confirmed by the user)"}


def render(account: HistoryAccount, expansion: Expansion, *, zone: str) -> str:
    tz = ZoneInfo(zone)
    head = ("History for this question, rebuilt from the passages below (it covers only this question, "
            "not everything you remember; the passages are the source, this summary is a reading of them)")
    if account.status == "failed":
        lines = [f"{head}: rebuilding FAILED ({account.error or 'error'}). The passages are still below; read "
                 "them yourself. Don't say nothing is recorded."]
    elif account.status == "no_history":
        lines = [f"{head}: no history of it could be traced in these passages."]
    else:
        lines = [f"{head}. Status: {account.status}."]
        if account.subject:
            lines.append(f"About: {account.subject}" + (f", {account.property}" if account.property else ""))
        if not account.same_thing:
            lines.append(f"Not sure these are all the same thing: {account.identity_note or 'see passages'}")
        for number, step in enumerate(account.steps, start=1):
            change = f"{step.before} -> {step.after}" if step.before else step.after
            cites = ", ".join(f'{c.passage} "{c.quote}"' for c in step.cites)
            when = f", said {_when(step.said_at, tz)}" if step.said_at else ""
            lines.append(f"{number}. {_KIND_WORDS.get(step.kind, step.kind)} {change}{when}"
                         f"{_SUPPORT_WORDS[step.support]} [{cites}]")
        if account.current is not None:
            c = account.current
            cites = ", ".join(f'{x.passage} "{x.quote}"' for x in c.cites)
            if c.state == "established":
                lines.append(f"Now: {c.value} [{cites}]")
            elif c.state == "unsettled":
                lines.append(f"Now: probably {c.value}, but unsettled: {c.note}" + (f" [{cites}]" if cites else ""))
            else:
                lines.append(f"Now: not established ({c.note})")
        for note, group in account.conflicts:
            lines.append(f"Conflict: {note} [" + ", ".join(f'{c.passage} "{c.quote}"' for c in group) + "]")
        for gap in account.gaps:
            lines.append(f"Gap: {gap}")
        if account.dropped:
            lines.append(f"({len(account.dropped)} part(s) of the rebuilt history couldn't be traced to the "
                         "passages and were left out.)")
    lines.append(f"Collected: {coverage_line(expansion)}.")
    lines.append("")
    lines.append("Passages (source text; who said each matters: the user's words are theirs, yours are only what "
                 "you said):")
    for p in expansion.passages:
        lines += [passage_head(p, tz), f'  "{p.text}"']
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The reader
# --------------------------------------------------------------------------- #


@dataclass
class ReaderAnswer:
    raw: dict | None
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


class HistoryReader:
    def __init__(self, model: str, *, api_key: str, reasoning_effort: str | None = "low", timeout: float = 60.0):
        from openai import OpenAI

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = OpenAI(api_key=api_key or None, timeout=timeout, max_retries=0)

    def read(self, text: str) -> ReaderAnswer:
        started = time.perf_counter()
        kwargs: dict = {}
        if self.model.startswith(("gpt-5", "gpt-6", "o")) and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        reasoning = self.reasoning_effort not in (None, "none")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": text}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "history", "strict": True, "schema": SCHEMA}},
                max_completion_tokens=6000 if reasoning else 2000,
                store=False,
                **kwargs,
            )
            raw = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001 - a failed reading is reported, never guessed
            return ReaderAnswer(None, _ms(started), error=f"{type(exc).__name__}: {exc}"[:300])
        return ReaderAnswer(raw, _ms(started), getattr(response.usage, "prompt_tokens", None),
                            getattr(response.usage, "completion_tokens", None))


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
