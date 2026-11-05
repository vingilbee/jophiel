"""Student 2 live: the situation it reads, built from live state, and the decision it writes (pure).

The trained decision model (``scripts/student_model.py``; qwen-4 today) learned from situations rendered by
``Cognitive/simple/events.py``. Live, the same snapshot is built from what the stores hold (conversation, working
memory goals, background work, her thoughts, the game session) and rendered by the same ``events.render``, so the
model reads live state in the exact shape it was trained on. What live state doesn't hold yet stays empty, as
"none": requests, restrictions, proposals and assessments have no live store, so the model can't act on them (and
``events.check`` rejects a decision that points at something absent).

Since snapshot v5 it also reads MEMORY: what the memory check before cognition found for a new message (the same
records the mouth gets, by section, an unresolved disagreement with the user's words, whether more than one memory
fits, whether the check failed), so it can notice a message that doesn't match what she remembers. A model trained
before v5 (``rendering`` older than ``memory-v1``) gets the situation without that section.

Since lessons (docs/learning-system/03-implementation.md) it can also read LESSONS: a bounded selection of accepted
lessons from earlier conversations, each saying whose it is, where it applies and its exceptions, placed after
MEMORY. Only a model whose rendering shows them gets them (``LESSON_RENDERINGS``: the teacher's ``lessons-v1``);
without any lessons the text is exactly ``memory-v1``. Which lessons a decision had, and whether it received them,
is recorded by ``services/memory_lessons.record_uses``.

Four triggers are live: a user message; a thought that just finished (her background thinking for a request, or a
thought of her own proposed to be said: [T1] with the thinking work behind it, for [T1]); work that finished
without a conclusion (failed or ran out of time); and a component proposal (learning's bounded game trial,
docs/learning-system/04-implementation.md: each alternative is one [P*], pursuing one chooses it, declining or
deferring any declines or defers the trial). The last three have no new message: what she decides is whether and
how to bring it up (and, for a proposal, whether and when to run it).

Refs are positions in the rendered snapshot ([G1], [W2], [A1]); ``Situation.refs`` maps each back to the live
record it came from, so the effect lands on the right row. The model's output is parsed and checked here, never
repaired: an invalid decision is reported as invalid, not fixed up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.ai.Cognitive.simple import events as E
from app.ai.learning import lessons as LS

TRIGGERS = ("user_message", "thought_result", "work_outcome", "component_proposal")
PRODUCERS = ("mind", "planner", "curriculum")
MESSAGE_CHARS = 700  # one conversation line, as the student sees it
WINDOW = 12  # earlier lines shown

# The student's output, filled out to the full decision (events_qwen.DEFAULTS, kept here so the live path never
# imports the training code).
DEFAULTS: dict[str, Any] = dict(E._QUIET) | dict(work="no_change", target="none", resolution="not_applicable",
                                                 basis="none", other_goal="none",
                                                 other_goal_handling="not_applicable")
RECALL_WORK = frozenset({"request_recall", "recall_then_thinking"})
# Renderings that show MEMORY; for any other the section is left out (that model never saw it), and its recall choice
# means "search further" either way: the check already ran.
MEMORY_RENDERINGS = frozenset({"memory-v1", LS.RENDERING})
# Renderings that show LESSONS (after MEMORY); for any other the section is left out (that model never saw it).
LESSON_RENDERINGS = frozenset({LS.RENDERING})
SNAPSHOT_MEMORY_NOTE = "this model has no memory input (trained before snapshot v5); its recall choice means searching further"


@dataclass(frozen=True)
class Line:
    speaker: str  # user / columbina
    text: str
    at: datetime


@dataclass(frozen=True)
class Goal:
    key: str  # the working item id
    text: str
    set_by: str  # user / columbina
    pursuit: str  # active / on_hold / waiting
    condition: str
    started_at: datetime
    completion: str = "subjective"


@dataclass(frozen=True)
class Work:
    key: str  # what it is in its own store: "coordinator:<id>" or "step:<session id>:<step id>"
    kind: str  # events.WORK_KINDS
    subject: str
    state: str  # events.WORK_STATES
    started_at: datetime
    goal_key: str | None = None  # the goal it serves, by Goal.key
    outcome: str = ""
    unresolved: str = ""
    for_thought: bool = False  # the thinking behind [T1]


@dataclass(frozen=True)
class Thought:
    key: str
    kind: str  # conclusion / question / intention
    text: str
    at: datetime
    said: bool = False
    origin: str = "deferred_earlier"  # just_finished: the thought this decision is about


@dataclass(frozen=True)
class Activity:
    key: str  # the desktop session id
    state: str  # running / paused / ended
    situation: str
    paused_by: str = "none"  # none / user / columbina


@dataclass(frozen=True)
class Proposal:
    """A proposal not taken up yet ([P*]): ``key`` is its own store's ("trial:<id>:<revision>:<alternative>");
    ``conflicts_with``: the Goal.key it competes with for her hands, if any."""

    key: str
    producer: str  # PRODUCERS: learning's trials are her curriculum
    goal: str
    purpose: str
    conflicts_with: str | None = None


@dataclass(frozen=True)
class Remembered:
    """One record of the memory check, as the student reads it."""

    section: str  # events.MEMORY_SECTIONS: always / holds / open / past
    text: str
    recorded_at: datetime
    disagreed_words: str = ""  # an unresolved disagreement: what the user said that doesn't match
    disagreed_at: datetime | None = None


@dataclass(frozen=True)
class Memory:
    search: str  # events.MEMORY_SEARCH: found / nothing_found / failed
    records: tuple[Remembered, ...] = ()
    several_fit: bool = False


@dataclass(frozen=True)
class Capability:
    name: str
    actions: tuple[str, ...]
    enabled: bool
    connection: str
    control: str | None = None


@dataclass(frozen=True)
class Live:
    """Everything the snapshot is made from, read by the services adapter."""

    trigger: str
    domain: str  # events.DOMAINS key: chat / minecraft
    now: datetime  # the user's local time, aware
    lines: tuple[Line, ...]  # earlier conversation, oldest first
    new_message: str
    goals: tuple[Goal, ...] = ()
    work: tuple[Work, ...] = ()
    thought: Thought | None = None
    earlier_thoughts: tuple[Thought, ...] = ()
    activity: Activity | None = None
    memory: Memory | None = None  # the check for the new message; None: not checked
    # What the snapshot was read from ("message:<id>", "item:<id>", "fact:<id>", ...): never rendered; the learning
    # capture keeps it so a forgotten or corrected source finds the copies made from it.
    sources: tuple[str, ...] = ()
    # Accepted lessons offered to this decision (refs L1..), and those left out by the budget: (id, revision, why).
    lessons: tuple[LS.LessonView, ...] = ()
    lessons_left_out: tuple[tuple[str, int, str], ...] = ()
    lessons_note: str | None = None  # why there are none when retrieval didn't run or failed
    capabilities: tuple[Capability, ...] = ()
    hands_now: str | None = None
    activity_state: str | None = None
    proposals: tuple[Proposal, ...] = ()  # component_proposal: what is offered, not taken up yet


@dataclass
class Situation:
    snapshot: dict[str, Any]
    text: str
    refs: dict[str, str] = field(default_factory=dict)  # "G1" -> goal key, "W1" -> work key, "A1" -> session id
    # Lessons offered with it (rendered only for LESSON_RENDERINGS); kept apart from the event snapshot, whose shape
    # is the trained models' contract.
    lessons: tuple[LS.LessonView, ...] = ()
    lessons_cut: int = 0
    capabilities: tuple[Capability, ...] = ()
    lessons_shown: bool = False
    activity_context: str = ""  # set by ``render``: whether the text it made shows them

    @property
    def lesson_refs(self) -> list[str]:
        return [v.ref for v in self.lessons] if self.lessons_shown else []


def _time_of_day(hour: int) -> str:
    return next((name for name, hours in E._HOURS.items() if hour in hours), "late_night")


def _ago(now: datetime, at: datetime) -> int:
    return max(1, int((now - at).total_seconds()))


def snapshot(live: Live) -> Situation:
    """The event snapshot ``events.assemble`` would make, from live state: same fields, same derived values."""
    if live.trigger not in TRIGGERS:
        raise ValueError(f"no live snapshot for trigger {live.trigger}")
    now = live.now
    lines = list(live.lines)[-WINDOW:]
    messages, user_refs = [], {}
    for i, line in enumerate(lines, 1):
        ref = ""
        if line.speaker == "user":
            ref = f"U{len(user_refs) + 1}"
            user_refs[i] = ref
        messages.append(dict(speaker=line.speaker, text=" ".join(line.text.split())[:MESSAGE_CHARS],
                             gap_before_seconds=0, seconds_ago=_ago(now, line.at), ref=ref))
    last = {sp: next((m["seconds_ago"] for m in reversed(messages) if m["speaker"] == sp), -1)
            for sp in ("user", "columbina")}
    refs: dict[str, str] = {}
    goals = []
    for i, g in enumerate(live.goals, 1):
        refs[f"G{i}"] = g.key
        goals.append(dict(text=g.text, set_by=g.set_by, from_request=0, constraints=[], completion=g.completion,
                          condition=g.condition, status="open", pursuit=g.pursuit, waiting_on="",
                          started_seconds_ago=_ago(now, g.started_at), verified_by=False))
    goal_index = {g.key: i for i, g in enumerate(live.goals, 1)}
    work = []
    for i, w in enumerate(live.work, 1):
        refs[f"W{i}"] = w.key
        parent = goal_index.get(w.goal_key or "")
        owner = "goal" if parent else "thought" if w.for_thought and live.thought is not None else "none"
        work.append(dict(kind=w.kind, subject=w.subject, parent=owner,
                         parent_index=parent or 0, state=w.state, started_seconds_ago=_ago(now, w.started_at),
                         outcome=w.outcome, verifies_goal=False, unresolved=w.unresolved))
    activity = dict(present=False, state="running", situation="", paused_by="none")
    if live.activity is not None and E.DOMAINS[live.domain]["activity"] is not None:
        refs["A1"] = live.activity.key
        activity = dict(present=True, state=live.activity.state, situation=live.activity.situation,
                        paused_by=live.activity.paused_by)
    thought = dict(kind="none", text="", origin="none", age_seconds=0, about_new_message=False)
    if live.thought is not None:
        refs["T1"] = live.thought.key
        thought = dict(kind=live.thought.kind, text=live.thought.text, origin=live.thought.origin,
                       age_seconds=_ago(now, live.thought.at), about_new_message=False)
    earlier = []
    for i, t in enumerate(live.earlier_thoughts, 2):
        refs[f"T{i}"] = t.key
        earlier.append(dict(text=t.text, seconds_ago=_ago(now, t.at), said_to_user=t.said, unresolved=""))
    s = dict(
        version=E.SNAPSHOT_VERSION, trigger=live.trigger, domain=live.domain, time_of_day=_time_of_day(now.hour),
        clock=now.strftime(E.CLOCK_FORMAT), messages=messages, new_message=" ".join(live.new_message.split()),
        quiet_seconds=last["user"] if last["user"] > 0 else 1, requests=[], thought=thought,
        earlier_thoughts=earlier, activity=activity, goals=goals, work=work, unavailable=[], restrictions=[],
        proposals=_proposals(live, refs, goal_index), assessments=[], user_refs=user_refs, seconds_since_user_message=0,
        seconds_since_her_last_message=last["columbina"], normalized=[])
    if E.ADDED_CAPABILITIES.get(live.domain):
        s["added_capabilities"] = sorted(E.ADDED_CAPABILITIES[live.domain])
    s["memory"] = _memory(live, now) if s["new_message"] else E.no_memory(checked=False)
    if not s["new_message"]:
        s["seconds_since_user_message"] = last["user"]
    return Situation(s, "", refs, lessons=tuple(live.lessons), lessons_cut=len(live.lessons_left_out),
                     capabilities=live.capabilities, activity_context=_activity_context(live))


def _proposals(live: Live, refs: dict[str, str], goal_index: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for i, p in enumerate(live.proposals, 1):
        refs[f"P{i}"] = p.key
        out.append(dict(producer=p.producer if p.producer in PRODUCERS else "curriculum",
                        goal=" ".join(p.goal.split())[:MESSAGE_CHARS], purpose=" ".join(p.purpose.split())[:900],
                        conflicts_with_goal=goal_index.get(p.conflicts_with or "", 0)))
    return out


def _days(now: datetime, at: datetime) -> int:
    return max(0, (now.date() - at.astimezone(now.tzinfo).date()).days)


def _memory(live: Live, now: datetime) -> dict[str, Any]:
    m = live.memory or Memory("nothing_found")
    records = [dict(section=r.section if r.section in E.MEMORY_SECTIONS else "holds",
                    text=" ".join(r.text.split())[:MESSAGE_CHARS], recorded_days_ago=_days(now, r.recorded_at),
                    disagreed_words=" ".join(r.disagreed_words.split())[:300],
                    disagreed_days_ago=_days(now, r.disagreed_at) if r.disagreed_words and r.disagreed_at else 0)
               for r in m.records[:E.MEMORY_RECORDS]]
    search = "found" if m.search == "found" and records else "failed" if m.search == "failed" else "nothing_found"
    return dict(search=search, several_fit=m.several_fit and len(records) >= 2, records=records)


def _activity_context(live: Live) -> str:
    if live.activity is None or live.activity_state is None:
        return ""
    lines = [
        "ACTIVITY AT THE START OF THIS TURN",
        f"- {live.activity.situation}. Session state: {live.activity_state}.",
        f"- Hands running: {live.hands_now or 'no skill reported'}.",
    ]
    if live.thought is not None:
        lines.append(f"- Latest thought ({_ago(live.now, live.thought.at)}s ago): {live.thought.text}")
    lines.append("A thought can suggest a next step. Treat it as a thought until the plan or Hands confirms the change. "
                 "When the user asks what you are thinking, use the relevant thought and distinguish it from what Hands is doing.")
    return "\n".join(lines)


def _capability_context(capabilities: tuple[Capability, ...]) -> str:
    if not capabilities:
        return ""
    lines = [
        "CURRENT CAPABILITIES",
        "Use the recent conversation to interpret what the user is referring to. "
        "A missing activity link does not mean a capability is disconnected. "
        "Connection does not imply permission, readiness, or that any action succeeded.",
    ]
    for item in capabilities:
        state = ", ".join(("enabled" if item.enabled else "disabled", item.connection))
        if item.control:
            state += f", controls: {item.control}"
        lines.append(f"- {item.name} ({state}): {'; '.join(item.actions)}")
    return "\n".join(lines)


def render(situation: Situation, rendering: str | None) -> str:
    """The text the student reads. ``rendering`` is how its training data was rendered (the model's manifest):
    None = before the exchange line existed (qwen-4), so the line is left out; anything before ``memory-v1`` never
    saw MEMORY, so the section is left out. LESSONS (after MEMORY, before the requests) only for
    ``LESSON_RENDERINGS`` and only when there are lessons: otherwise the text is the rendering without them."""
    text = E.render(situation.snapshot, memory=rendering in MEMORY_RENDERINGS)
    if rendering is None:
        text = "\n".join(line for line in text.split("\n") if line not in E.EXCHANGE)
    situation.lessons_shown = False
    if rendering in LESSON_RENDERINGS and situation.lessons:
        lines = text.split("\n")
        at = next(i for i, line in enumerate(lines) if line.startswith("REQUESTS FROM THE USER"))
        lines[at:at] = LS.decision_lines(situation.lessons, cut=situation.lessons_cut)
        text = "\n".join(lines)
        situation.lessons_shown = True
    if context := _capability_context(situation.capabilities):
        text += "\n\n" + context
    if situation.activity_context:
        text += "\n\n" + situation.activity_context
    situation.text = text
    return text


def parse(output: str) -> tuple[dict[str, Any], str]:
    """The decision filled out to the full field set, and what went wrong reading it ('' when nothing)."""
    start, end = output.find("{"), output.rfind("}")
    try:
        got = json.loads(output[start:end + 1])
    except (ValueError, TypeError):
        return dict(DEFAULTS), "not_json"
    if not isinstance(got, dict):
        return dict(DEFAULTS), "not_json"
    d = dict(DEFAULTS) | {k: v for k, v in got.items() if k in DEFAULTS}
    if d["communication"] in E.SPEAKING and not d["dials"]:
        d["dials"] = None
    d["second_move"] = d["second_move"] or E.NO_SECOND
    return d, ""


def check(d: dict[str, Any], situation: Situation) -> list[str]:
    return E.check(d, situation.snapshot)


def recall_need(d: dict[str, Any]) -> str:
    return "required" if d["work"] in RECALL_WORK else "not_required"


def thinking_need(d: dict[str, Any]) -> str:
    return "required" if d["work"] in E.THINKING else "not_required"


def speaks(d: dict[str, Any]) -> bool:
    return d["communication"] in E.SPEAKING


# --- For the mouth ------------------------------------------------------------------------------

_HOW = {"respond": "answer them now", "acknowledge": "acknowledge it briefly, without inventing progress",
        "partial": "say what you can now, and that part of it is still open",
        "ask": "ask them something", "silent": "say nothing now", "defer": "say nothing now; come back to it later"}
_DOES = {"pursue": "take up {target}", "pause": "pause {target}", "resume": "resume {target}",
         "cancel": "stop {target}", "resolve_goal": "close {target} ({resolution})", "revise_goal": "revise {target}",
         "request_replanning": "work out another way to do {target}",
         "request_recall": "look up your memory about {target}",
         "request_thinking": "think {target} over in the background: {think_about}",
         "decline_proposal": "turn down {target}", "defer_proposal": "put off {target} for now",
         "recall_then_thinking": "look up your memory, then think {target} over: {think_about}"}


def _describe(ref: str, s: dict[str, Any]) -> str:
    if ref == "M":
        return "their new message"
    if ref.startswith("U"):
        return "an earlier message of theirs"
    if ref.startswith("G"):
        return f'the goal "{s["goals"][int(ref[1:]) - 1]["text"]}"'
    if ref.startswith("W"):
        return f'the work "{s["work"][int(ref[1:]) - 1]["subject"]}"'
    if ref == "A1":
        return "the activity"
    if ref.startswith("T"):
        return "your thought"
    if ref.startswith("P"):
        return f'the proposal "{s["proposals"][int(ref[1:]) - 1]["goal"]}"'
    return ref


def brief(d: dict[str, Any], situation: Situation, *, effect: str | None = None) -> str:
    """Her decision, in words for the mouth that says it. The mouth words it; it doesn't decide again."""
    s = situation.snapshot
    lines = [f"- What you want here: {d['intention'].strip() or '(not stated)'}",
             f"- How: {_HOW.get(d['communication'], d['communication'])}."]
    if d["what_to_communicate"].strip():
        lines.append(f"- What to get across: {d['what_to_communicate'].strip()}")
    if d["move"] != "none":
        dials = ", ".join(f"{k} {v}" for k, v in (d["dials"] or {}).items())
        lines.append(f"- Style: {d['move'].replace('_', ' ')}" + (f" ({dials})" if dials else ""))
    if d["work"] == "pursue" and d["target"].startswith("P"):
        lines.append(f"- What you're doing about it: take up {_describe(d['target'], s)}" + (f" ({effect})" if effect
                     else "") + ". It starts after you speak: don't say it has already happened.")
    elif d["work"] == "pursue":
        # The reply is where taking something up happens today: the mouth holds the tools and specialists.
        lines.append(f"- What you're doing about it: take up {_describe(d['target'], s)} now, with your tools or "
                     "specialists if one fits; say only what they actually return.")
    elif d["work"] != "no_change":
        does = _DOES.get(d["work"], d["work"]).format(target=_describe(d["target"], s),
                                                      resolution=d["resolution"].replace("_", " "),
                                                      think_about=d["think_about"].strip())
        lines.append(f"- What you're doing about it: {does}" + (f" ({effect})" if effect else "") + ". It "
                     "starts after you speak: don't say it has already happened.")
    if situation.activity_context:
        lines.append(situation.activity_context)
    if context := _capability_context(situation.capabilities):
        lines.append(context)
    return "\n".join(lines)
