"""Her effort: the story of what she is working toward (docs/COLUMBINA_EFFORT_AND_STUCK_PLAN.md, stage 1).

A snapshot of the moment says where she is; it can't say how long she has been at something, what she
already tried, whether she carried out what she concluded, or that the plan she follows is only one way to
the outcome. This is that account, for her mind and her planner, so they can look back like a person
does after half an hour ("I said I'd explore the cave twice, and never went in").

Four layers are kept apart: the **outcome** (what the user wants, a goal in working memory), the
**approach** (her current plan), her **actions** (her hands' runs, on the timeline), and the user's
**rules and references** for it. Code records and counts; it never judges. Which action came from which
conclusion is hers to say (``follows``), and whether any of it is progress is hers to judge.

General: any source can build an ``Effort``; the source adds what only it knows (what she can sense
there, what the current view covers, how it counts change). Pure: nothing here touches the database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Active time shown in full on the timeline; older entries are only counted (decided 2026-11-04).
FULL_WINDOW_S = 30 * 60
# The newest entries listed at most; earlier ones in the window are counted with the older part.
MAX_LISTED = 100
ENTRY_CHARS = 200
# Her conclusions carry her judgment of the effort then (getting anywhere, cause, check): room for both.
CONCLUSION_CHARS = 360
DETAILS_SHOWN = 4
# A stretch with nothing recorded at least this long is marked on the timeline. Whether what came before
# belongs to this attempt (another session, another world) is hers to judge from it and from what was said.
GAP_MARK_S = 10 * 60
# The kinds of timeline entries, and the word each is counted under when it's older than the window.
KIND_WORDS = {"user": "things the user said", "said": "things you said", "conclusion": "conclusions of yours",
              "plan": "plan changes", "action": "actions", "state": "pauses and resumes", "note": "notes",
              "summary": "stretches of play"}


@dataclass(frozen=True)
class Entry:
    """One thing on the timeline. ``ref``: a short id she can name (her conclusions); ``count``/``until``:
    the same thing repeated back to back, collapsed by code."""

    at: datetime
    kind: str  # user | said | conclusion | plan | action | state | note
    text: str
    ref: str | None = None
    follows: str | None = None  # the earlier conclusion it says it carries out, as the model said it
    failed: bool = False
    count: int = 1
    until: datetime | None = None
    key: str | None = None  # what makes two entries "the same" for collapsing (actions)
    details: tuple[str, ...] = ()  # what differed between collapsed repeats (each run's target), in order


@dataclass(frozen=True)
class Effort:
    outcome: str
    whose: str  # "the user's" / "your own" / "shared"
    outcome_ref: str
    since: datetime
    active_s: float
    paused_s: float
    approach: str | None = None  # the plan she follows now, with its current step
    approach_since: datetime | None = None
    approach_changes: int = 0  # how many times the plan's own goal changed since the outcome was set
    rules: tuple[tuple[str, str, str], ...] = ()  # (ref, rule, the user's words) in force
    places: tuple[tuple[str, str | None], ...] = ()  # (name, where)
    counted: tuple[str, ...] = ()  # changes counted by the source, side by side ("mine: coal +8, ...")
    spots: str | None = None  # where her actions ended, counted
    senses: tuple[str, ...] = ()  # how she perceives here (fixed per source, checked against its code)
    coverage: tuple[str, ...] = ()  # what the current view does and doesn't cover
    timeline: tuple[Entry, ...] = ()
    full_since: datetime | None = None  # entries before this are counted, not listed
    status_note: str | None = None  # the outcome's own status, when not simply active


def collapse(entries: list[Entry]) -> list[Entry]:
    """Back-to-back repeats of the same action with the same result become one entry with a count. Anything
    in between (a conclusion, the user speaking) breaks the run, so what followed a conclusion stays visible."""
    out: list[Entry] = []
    for entry in sorted(entries, key=lambda e: e.at):
        last = out[-1] if out else None
        if last is not None and entry.key and last.key == entry.key:
            out[-1] = Entry(at=last.at, kind=last.kind, text=last.text, ref=last.ref, follows=last.follows,
                            failed=last.failed, count=last.count + entry.count, until=entry.until or entry.at,
                            key=last.key, details=last.details + entry.details)
        else:
            out.append(entry)
    return out


def _clock(at: datetime) -> str:
    return at.strftime("%H:%M")


def _span(seconds: float) -> str:
    minutes = round(max(0.0, seconds) / 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def _ago(at: datetime, now: datetime) -> str:
    return _span((now - at).total_seconds()) + " ago"


def _entry_line(entry: Entry) -> str:
    when = _clock(entry.at) + (f"–{_clock(entry.until)}" if entry.until and _clock(entry.until) != _clock(entry.at)
                               else "")
    text = " ".join(entry.text.split())[:CONCLUSION_CHARS if entry.kind == "conclusion" else ENTRY_CHARS]
    if entry.kind == "conclusion":
        follows = f", carrying out {entry.follows}" if entry.follows else ""
        return f"  {when} [{entry.ref}]{follows} {text}"
    if entry.kind == "plan" and entry.ref:
        follows = f", carrying out {entry.follows}" if entry.follows else ""
        return f"  {when} [{entry.ref}] planner{follows}: {text}"
    times = f" ×{entry.count}" if entry.count > 1 else ""
    return f"  {when} {text}{times}{_details(entry.details)}"


# The story of an effort: never folded into counts while they're in the window.
KEPT_KINDS = ("conclusion", "plan", "user", "note")


def _trim(entries: list[Entry]) -> list[Entry]:
    """At most about ``MAX_LISTED`` lines. From the oldest, each stretch of hand runs, replies and pauses
    between two story entries becomes one summary line (so what followed a conclusion stays visible);
    her conclusions, plans, the user's words and stuck notes are always listed."""
    out = list(entries)
    i = 0
    while len(out) > MAX_LISTED and i < len(out):
        if out[i].kind in KEPT_KINDS or out[i].kind == "summary":
            i += 1
            continue
        j = i
        while j < len(out) and out[j].kind not in KEPT_KINDS and out[j].kind != "summary":
            j += 1
        out[i:j] = [_summary(out[i:j])]
        i += 1
    return out


def _summary(run: list[Entry]) -> Entry:
    actions = [e for e in run if e.kind == "action"]
    runs = sum(e.count for e in actions)
    failed: dict[str, int] = {}
    for e in actions:
        if e.failed:
            why = e.text.split("→", 1)[-1].split(" [", 1)[0].strip()
            failed[why] = failed.get(why, 0) + e.count
    parts = []
    if runs:
        worst = sorted(failed.items(), key=lambda kv: -kv[1])[:3]
        parts.append(f"hands: {runs} run{'s' if runs != 1 else ''}"
                     + (f", {sum(failed.values())} failed (" + "; ".join(f"{w} ×{n}" if n > 1 else w for w, n in worst)
                        + ")" if failed else ", none failed"))
    said = sum(e.count for e in run if e.kind == "said")
    if said:
        parts.append(f"you said {said} thing{'s' if said != 1 else ''}")
    states = [e.text for e in run if e.kind == "state"]
    if states:
        parts.append(", ".join(states))
    last = run[-1]
    return Entry(at=run[0].at, kind="summary", text="; ".join(parts) + " (summarised)",
                 until=last.until or last.at, failed=bool(failed), count=1)


def _details(details: tuple[str, ...]) -> str:
    """What the collapsed runs were each given, most frequent first: "(player:x ×5, surface ×2, 3 others)"."""
    if not details:
        return ""
    counts: dict[str, int] = {}
    for detail in details:
        counts[detail] = counts.get(detail, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    shown = [f"{d} ×{n}" if n > 1 else d for d, n in ranked[:DETAILS_SHOWN]]
    rest = len(ranked) - DETAILS_SHOWN
    return " (" + ", ".join(shown) + (f", {rest} other{'s' if rest > 1 else ''}" if rest > 0 else "") + ")"


def render(effort: Effort, now: datetime, *, user: str = "the user") -> list[str]:
    """The effort as she reads it, most important first. Code's words for what it counted; no verdicts."""
    lines = [f'Outcome ({effort.whose}, [{effort.outcome_ref}], set {_clock(effort.since)}): "{effort.outcome}"'
             + (f" ({effort.status_note})" if effort.status_note else "")]
    total = (now - effort.since).total_seconds()
    lines.append(f"Now: {_clock(now)}. Time on it: {_span(total)} since it was set ({_span(effort.active_s)} "
                 f"playing, {_span(effort.paused_s)} paused or away).")
    last = max((e.until or e.at for e in effort.timeline), default=None)
    if last is not None and (now - last).total_seconds() >= GAP_MARK_S:
        lines.append(f"Nothing was recorded for {_span((now - last).total_seconds())} before now.")
    if effort.approach:
        since = f", since {_clock(effort.approach_since)}" if effort.approach_since else ""
        changes = (f"; the plan's own goal changed {effort.approach_changes} times since the outcome was set"
                   if effort.approach_changes else "")
        lines.append(f'Your current approach (the plan, not the outcome): "{effort.approach}"{since}{changes}.')
    if effort.rules:
        lines.append(f"{user}'s rules for it, in force:")
        lines += [f'  [{ref}] {rule} (their words: "{words}")' for ref, rule, words in effort.rules]
    if effort.places:
        lines.append("Places and things that matter to it:")
        lines += [f"  {name}" + (f": {where}" if where else "") for name, where in effort.places]
    if effort.counted:
        lines.append("Counted since it was set (by code, side by side; what counts as progress is yours to judge):")
        lines += [f"  {line}" for line in effort.counted]
    if effort.spots:
        lines.append(effort.spots)
    if effort.senses:
        lines.append("How you perceive here:")
        lines += [f"  {line}" for line in effort.senses]
    if effort.coverage:
        lines.append("What your current view covers:")
        lines += [f"  {line}" for line in effort.coverage]
    timeline = list(effort.timeline)
    older = [e for e in timeline if effort.full_since and e.at < effort.full_since]
    recent = [e for e in timeline if not (effort.full_since and e.at < effort.full_since)]
    recent = _trim(recent)
    if older:
        counts: dict[str, int] = {}
        failed = 0
        for entry in older:
            counts[entry.kind] = counts.get(entry.kind, 0) + entry.count
            failed += entry.count if entry.failed else 0
        parts = [f"{n} {KIND_WORDS.get(kind, kind)}" + (f" ({failed} failed)" if kind == "action" and failed else "")
                 for kind, n in counts.items()]
        lines.append(f"Before {_clock(effort.full_since)} (over {FULL_WINDOW_S // 60} min of play ago), counted only: "
                     + ", ".join(parts) + ".")
    if recent:
        lines.append("Timeline (oldest first; [D…] are your own conclusions and plans, by id; "
                     '"carrying out" is what you said then):')
        previous = None
        for entry in recent:
            if previous is not None and (entry.at - previous).total_seconds() >= GAP_MARK_S:
                lines.append(f"  — {_span((entry.at - previous).total_seconds())} with nothing recorded —")
            lines.append(_entry_line(entry))
            previous = max(previous or entry.at, entry.until or entry.at)
    return lines


@dataclass
class ActiveTime:
    """Active vs paused time from state changes, counted by code. ``changes``: (when, running?) in order."""

    since: datetime
    running_at_start: bool
    changes: list[tuple[datetime, bool]] = field(default_factory=list)

    def split(self, now: datetime) -> tuple[float, float]:
        active = paused = 0.0
        running, at = self.running_at_start, self.since
        for when, now_running in sorted(self.changes):
            if when <= self.since:
                running = now_running
                continue
            span = (min(when, now) - at).total_seconds()
            active, paused = (active + span, paused) if running else (active, paused + span)
            running, at = now_running, min(when, now)
        span = max(0.0, (now - at).total_seconds())
        return (active + span, paused) if running else (active, paused + span)

    def window_start(self, now: datetime, seconds: float = FULL_WINDOW_S) -> datetime:
        """The moment after which ``seconds`` of active time have passed (the full part of the timeline)."""
        spans: list[tuple[datetime, datetime, bool]] = []
        running, at = self.running_at_start, self.since
        for when, now_running in sorted(self.changes):
            if when <= self.since:
                running = now_running
                continue
            spans.append((at, min(when, now), running))
            running, at = now_running, min(when, now)
        spans.append((at, now, running))
        left = seconds
        for start, end, was_running in reversed(spans):
            if not was_running:
                continue
            length = (end - start).total_seconds()
            if length >= left:
                from datetime import timedelta

                return end - timedelta(seconds=left)
            left -= length
        return self.since
