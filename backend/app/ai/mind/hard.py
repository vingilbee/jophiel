"""Hard thoughts: slow thinking about a problem, ending in one conclusion (docs/COLUMBINA_HARD_THINKING_PLAN.md).

Slow thinking works on something that stays true for minutes (a step that keeps failing,
a surprise, something big ahead, what the user meant, the user and the session when
nothing is happening), not on the last second. It reads the problem with its counted
history, the conversation, memories found for the problem, what she learned, her earlier
thoughts on it and what she cares about, and ends in one typed conclusion. It has no
tools and never speaks: services/mind_hard.py sends the conclusion on (her hands, her
mouth, a question the mouth asks).

General like the rest of the mind: the source's ``Situation`` says what she is doing;
the problem text is the source's own. Nothing here touches the database.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from app.ai.mind.situation import GENERAL, Situation

TRIGGERS = ("failing", "surprise", "big", "handoff", "open", "quiet", "promise", "intention", "conflict", "review")
TRIGGER_LINES = {
    "failing": "the same step keeps failing",
    "surprise": "something didn't go the way she expected",
    "big": "she's about to start something big",
    "handoff": "{user} said something she wants to think over properly",
    "open": "{user} asked her something that is still open after her reply",
    "quiet": "nothing is happening, so she has time to think",
    "promise": "she told {user} she would do or come back to something, and hasn't yet",
    "intention": "something she meant to do is about to lapse",
    "conflict": "something {user} said doesn't match what she remembers, and nothing has settled which holds",
    "review": "one part of what she's working on just ended, a good moment to look back over the whole effort",
}
# Is the effort getting anywhere? Hers to judge from the record, never code's (stage 2 of
# docs/COLUMBINA_EFFORT_AND_STUCK_PLAN.md): no time limit or count decides she's stuck.
PROGRESS = ("yes", "no", "unclear")
# When it isn't, why: the four causes she weighs.
CAUSES = {
    "ability": "what she can do: the skill or tool can't do this (a search that reaches too short, a move she "
               "doesn't have)",
    "perception": "what she can perceive: it may be there, but her senses or her current view don't cover it",
    "situation": "what's really there: it may not be here at all",
    "understanding": "what {user} meant: she may have the outcome, the approach or one of {user}'s rules wrong "
                     "(asking {user} is usually the check)",
}
CONCLUSIONS = {
    "decision": "what she'll do differently, concretely (\"follow the water out instead of digging up\")",
    "guess": "an explanation to test, and how she'd tell (\"the move does kill them: check the loot, not "
             "what it reports\")",
    "question": "something only {user} can answer, that she'll ask them",
    "intention": "something to keep in mind for later (\"keep 5 planks to get back down\")",
    "feeling": "how she feels about {user} or what they're doing together, when that's what matters here",
}
# Where no body acts (just talking): the same kinds, without examples from play.
CONCLUSIONS_TALK = dict(CONCLUSIONS, decision="what she'll do or say differently, concretely",
                        guess="an explanation to test, and how she'd tell",
                        intention="something to keep in mind for later")
CONVERSATION_KEPT = 16
SCHEMA = {
    "type": "object",
    "properties": {"thinking": {"type": "string"}, "kind": {"type": "string", "enum": [*CONCLUSIONS, ""]},
                   "conclusion": {"type": "string"}, "for_hands": {"type": "string"},
                   "question": {"type": "string"}, "follows": {"type": "string"},
                   "progress": {"type": "string", "enum": [*PROGRESS, ""]},
                   "cause": {"type": "string", "enum": [*CAUSES, ""]},
                   "check": {"type": "string"}, "if_supported": {"type": "string"},
                   "leaves_unknown": {"type": "string"}},
    "required": ["thinking", "kind", "conclusion", "for_hands", "question", "follows", "progress", "cause", "check",
                 "if_supported", "leaves_unknown"],
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You are Jophiel's slow thinking. Right now she is {doing}. Something needs real thought, \
and there's time for it: {trigger}.

Below: the problem, with what was tried and what happened (counted by {counter}, so it's \
true); her effort, when there is one; what's going on now; the conversation; memories that may \
bear on it; what she learned before; her earlier thoughts on this; and what she cares about.

Her effort is the story of what she's working toward, recorded by code: the outcome ({user}'s \
or her own), her current approach (the plan: one way to the outcome, not the outcome itself), \
{user}'s rules for it, how long it has taken, what was counted, how she perceives here and what \
her current view covers, and a timeline of what she concluded, what her plan and hands did and \
what was said. Code only recorded it; judging it is hers. Parts of it may belong to an earlier \
session or another place: the times, the gaps and what {user} said tell her which.

Think it through like a person who has a minute and looks back over the last half hour: how \
long this has taken and whether it's getting anywhere; what she already concluded, whether she \
actually carried it out and what came of it; whether the approach still serves the outcome and \
{user}'s rules; and, when it isn't working, why: what she can do, what she can or can't perceive \
from here, what's really there, or whether she has understood {user}. What's really going on, \
what explains it, what she could do differently, what she needs from {user}. Then reach ONE \
conclusion, of one kind:
{kinds}

Rules:
- Conclude something new. Never describe the moment back, never say what she's doing right \
now ("I'm heading to..."), never just say she's stuck or paused.
- Conclude about the problem above, not about something else in the conversation.
- Build on her earlier thoughts; don't repeat them. If the timeline shows she concluded the same \
before, say what happened with it instead of concluding it again.
- "follows": when this carries out one of her earlier conclusions or plans on the timeline, its id \
there (like "D1a2b3c"); otherwise empty.
- When {user} asks her to forget or not keep something, it covers what they named. It never means hiding a \
standing safety constraint, like an allergy, when it bears on what she says.
- A decision names what she'd do differently. It can't change the outcome by itself: if the \
outcome should change, make it a question for {user}. Changing the approach is hers.
- Only use what's here. Memories may be old or about something else.
- "conclusion": first person, at most 30 words.
- "thinking": a few private sentences on how she got there.
{for_hands}
- "question": for a question, the exact words she'd say to {user}, short, in her voice. Otherwise empty.
{effort_rules}
- If nothing here is worth concluding, leave "kind" empty."""
# Only when she has an effort to judge (her effort is in what she reads).
EFFORT_RULES = """- "progress": is her effort getting anywhere toward the outcome? "yes", "no" or "unclear", judged by her \
from the whole record (what was found, what failed, what she concluded and whether it was carried out), \
not from this moment alone. No rule of time or tries decides it; she does.
- "cause": when progress is "no" or "unclear" and she can see why, the cause she favours, one of:
{causes}
  Otherwise empty.
- "check": when she names a cause, how she'd find out if it's right, concretely (asking {user} counts). \
Otherwise empty.
- "if_supported": what the check showing it would support, in a few words. Otherwise empty.
- "leaves_unknown": what the check NOT showing it would still leave unknown ("no cave seen from the peak \
doesn't mean there's none here"). Otherwise empty.
- Being stuck is for her to know, not to announce: say it to {user} only if that helps them."""
NO_EFFORT_RULES = '- "progress", "cause", "check", "if_supported", "leaves_unknown": always empty (no effort here).'
FOR_HANDS = """\
- "for_hands": for a decision, guess or intention, one tip her hands can follow while playing \
(how to play, not a new goal), at most 25 words. Otherwise empty."""
NO_HANDS = '- "for_hands": always empty (nothing acts on its own here).'


@dataclass(frozen=True)
class Problem:
    """What a hard thought is about. ``anchor``: what it depends on (goal, step id, the time
    it was handed over), so it can be dropped when that's gone."""

    trigger: str
    text: str
    topic: str  # short: the panel's label, and how earlier thoughts on it are found
    query: str  # words to search her memories with
    anchor: dict = field(default_factory=dict)


@dataclass(frozen=True)
class HardInput:
    problem: Problem
    user: str = "the user"
    now: str = ""  # a short "right now": health, what she holds and carries, around her
    goal: str | None = None  # the plan's goal, when there's no effort to show it in
    effort: tuple[str, ...] = ()  # her effort as lines (app/ai/mind/effort.render)
    conversation: tuple[dict, ...] = ()  # {"role", "content", "ago", "id"?} oldest first
    memories: str | None = None
    memory_refs: tuple[str, ...] = ()  # the memories supplied ("event:42"), for what she read
    learned: tuple[str, ...] = ()  # lessons and learned moves, as lines
    earlier: tuple[str, ...] = ()  # her earlier conclusions on this problem
    character: tuple[str, ...] = ()  # what she cares about


@dataclass
class HardThought:
    kind: str | None
    conclusion: str | None
    thinking: str | None = None
    for_hands: str | None = None
    question: str | None = None
    follows: str | None = None  # the earlier conclusion it says it carries out (a timeline id)
    # Her judgment of the effort, when there is one: progress, cause, check, if_supported, leaves_unknown.
    assessment: dict | None = None
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


def render(inp: HardInput) -> str:
    """Everything she reads, as one text (also what the eval replays)."""
    lines = [f"The problem ({inp.problem.trigger}):", inp.problem.text.strip()]
    if inp.effort:
        lines += ["", "Your effort (recorded by code; judging it is yours):", *inp.effort]
    elif inp.goal:
        lines += ["", f"Your goal: {inp.goal}"]
    if inp.now:
        lines += ["", "Right now:", inp.now.strip()]
    if inp.conversation:
        lines += ["", "The conversation (oldest first):"]
        for m in inp.conversation[-CONVERSATION_KEPT:]:
            who = inp.user if m.get("role") == "user" else "you"
            ago = m.get("ago")
            lines.append(f"  {who}" + (f" ({_ago(ago)})" if ago is not None else "") + f": {m.get('content', '')}")
    if inp.memories:
        lines += ["", "Memories that may bear on it:", inp.memories.strip()]
    if inp.learned:
        lines += ["", "What you learned before:"] + [f"  {line}" for line in inp.learned]
    if inp.earlier:
        lines += ["", "Your earlier thoughts on this (oldest first):"] + [f"  - {t}" for t in inp.earlier]
    if inp.character:
        lines += ["", "What you care about: " + "; ".join(inp.character)]
    return "\n".join(lines)


def _ago(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds} s ago" if seconds < 120 else f"{seconds // 60} min ago"


class HardThinker:
    def __init__(self, model: str, *, api_key: str, reasoning_effort: str | None = "low", timeout: float = 60.0):
        from openai import OpenAI

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def instructions(self, trigger: str, user: str, situation: Situation = GENERAL, *, effort: bool = False) -> str:
        conclusions = CONCLUSIONS if situation.hands else CONCLUSIONS_TALK
        kinds = "\n".join(f"- {kind}: {what.format(user=user)}" for kind, what in conclusions.items())
        causes = "\n".join(f'    - "{cause}": {what.format(user=user)}' for cause, what in CAUSES.items())
        effort_rules = EFFORT_RULES.format(user=user, causes=causes) if effort else NO_EFFORT_RULES
        return INSTRUCTIONS.format(doing=situation.describe(user), trigger=TRIGGER_LINES[trigger].format(user=user),
                                   user=user, kinds=kinds, counter="the game" if situation.hands else "code",
                                   for_hands=FOR_HANDS if situation.hands else NO_HANDS,
                                   effort_rules=effort_rules) + (
            f"\n{situation.voice}" if situation.voice else "")

    def think(self, inp: HardInput, situation: Situation = GENERAL) -> HardThought:
        started = time.perf_counter()
        kwargs: dict = {}
        if self.model.startswith(("gpt-5", "gpt-6", "o")) and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        reasoning = self.reasoning_effort not in (None, "none")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.instructions(inp.problem.trigger, inp.user, situation,
                                                                           effort=bool(inp.effort))},
                          {"role": "user", "content": render(inp)}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "hard_thought", "strict": True, "schema": SCHEMA}},
                max_completion_tokens=4000 if reasoning else 600,
                store=False,
                **kwargs,
            )
            out = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001 - a failed thought is just no thought
            return HardThought(None, None, latency_ms=_ms(started), error=f"{type(exc).__name__}: {exc}"[:300])
        return parse(out, latency_ms=_ms(started),
                     input_tokens=getattr(response.usage, "prompt_tokens", None),
                     output_tokens=getattr(response.usage, "completion_tokens", None))


def parse(out: dict, **extra) -> HardThought:
    """The model's answer, cleaned: no conclusion means no thought; a tip or question only
    where its kind allows one."""
    kind = out.get("kind") or None
    conclusion = _clean(out.get("conclusion"))
    if kind not in CONCLUSIONS or not conclusion:
        return HardThought(None, None, thinking=_clean(out.get("thinking")), assessment=assessment(out), **extra)
    return HardThought(
        kind, conclusion, thinking=_clean(out.get("thinking")),
        for_hands=_clean(out.get("for_hands")) if kind in ("decision", "guess", "intention") else None,
        question=(_clean(out.get("question")) or conclusion) if kind == "question" else None,
        follows=follows_id(out.get("follows")),
        assessment=assessment(out),
        **extra,
    )


def assessment(out: dict) -> dict | None:
    """Her judgment of the effort, as she gave it: a cause only with progress not "yes", a check only with a
    cause. None when she judged nothing."""
    progress = out.get("progress") if out.get("progress") in PROGRESS else None
    cause = out.get("cause") if out.get("cause") in CAUSES and progress in ("no", "unclear") else None
    check = _clean(out.get("check")) if cause else None
    found = {"progress": progress, "cause": cause, "check": check,
             "if_supported": _clean(out.get("if_supported")) if check else None,
             "leaves_unknown": _clean(out.get("leaves_unknown")) if check else None}
    found = {k: v for k, v in found.items() if v}
    return found or None


FOLLOWS = re.compile(r"\bD[0-9a-f]{6}\b")


def follows_id(value) -> str | None:
    """The timeline id the model named, if it named one in the right form; never inferred."""
    match = FOLLOWS.search(str(value or ""))
    return match.group(0) if match else None


def _clean(value) -> str | None:
    return " ".join(str(value or "").split()) or None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
