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
import time
from dataclasses import dataclass, field

from app.ai.mind.situation import GENERAL, Situation

TRIGGERS = ("failing", "surprise", "big", "handoff", "open", "quiet")
TRIGGER_LINES = {
    "failing": "the same step keeps failing",
    "surprise": "something didn't go the way she expected",
    "big": "she's about to start something big",
    "handoff": "{user} said something she wants to think over properly",
    "open": "{user} asked her something that is still open after her reply",
    "quiet": "nothing is happening, so she has time to think",
}
CONCLUSIONS = {
    "decision": "what she'll do differently, concretely (\"follow the water out instead of digging up\")",
    "guess": "an explanation to test, and how she'd tell (\"the move does kill them: check the loot, not "
             "what it reports\")",
    "question": "something only {user} can answer, that she'll ask them",
    "intention": "something to keep in mind for later (\"keep 5 planks to get back down\")",
    "feeling": "how she feels about {user} or what they're doing together, when that's what matters here",
}
CONVERSATION_KEPT = 16
SCHEMA = {
    "type": "object",
    "properties": {"thinking": {"type": "string"}, "kind": {"type": "string", "enum": [*CONCLUSIONS, ""]},
                   "conclusion": {"type": "string"}, "for_hands": {"type": "string"},
                   "question": {"type": "string"}},
    "required": ["thinking", "kind", "conclusion", "for_hands", "question"],
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You are Columbina's slow thinking. Right now she is {doing}. Something needs real thought, \
and there's time for it: {trigger}.

Below: the problem, with what was tried and what happened (counted by the game, so it's \
true); what's going on now; the conversation; memories that may bear on it; what she learned \
before; her earlier thoughts on this; and what she cares about.

Think it through like a person who has a minute: what's really going on, what explains it, \
what she could do differently, what she needs from {user}. Then reach ONE conclusion, of one kind:
{kinds}

Rules:
- Conclude something new. Never describe the moment back, never say what she's doing right \
now ("I'm heading to..."), never just say she's stuck or paused.
- Build on her earlier thoughts; don't repeat them.
- A decision names what she'd do differently. It can't change the goal by itself: if the goal \
should change, make it a question for {user}.
- Only use what's here. Memories may be old or about something else.
- "conclusion": first person, at most 30 words.
- "thinking": a few private sentences on how she got there.
- "for_hands": for a decision, guess or intention, one tip her hands can follow while playing \
(how to play, not a new goal), at most 25 words. Otherwise empty.
- "question": for a question, the exact words she'd say to {user}, short, in her voice. Otherwise empty.
- If nothing here is worth concluding, leave "kind" empty."""


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
    goal: str | None = None
    conversation: tuple[dict, ...] = ()  # {"role", "content", "ago"} oldest first
    memories: str | None = None
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
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


def render(inp: HardInput) -> str:
    """Everything she reads, as one text (also what the eval replays)."""
    lines = [f"The problem ({inp.problem.trigger}):", inp.problem.text.strip()]
    if inp.goal:
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

    def instructions(self, trigger: str, user: str, situation: Situation = GENERAL) -> str:
        kinds = "\n".join(f"- {kind}: {what.format(user=user)}" for kind, what in CONCLUSIONS.items())
        return INSTRUCTIONS.format(doing=situation.describe(user), trigger=TRIGGER_LINES[trigger].format(user=user),
                                   user=user, kinds=kinds) + (f"\n{situation.voice}" if situation.voice else "")

    def think(self, inp: HardInput, situation: Situation = GENERAL) -> HardThought:
        started = time.perf_counter()
        kwargs: dict = {}
        if self.model.startswith(("gpt-5", "gpt-6", "o")) and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        reasoning = self.reasoning_effort not in (None, "none")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.instructions(inp.problem.trigger, inp.user, situation)},
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
        return HardThought(None, None, thinking=_clean(out.get("thinking")), **extra)
    return HardThought(
        kind, conclusion, thinking=_clean(out.get("thinking")),
        for_hands=_clean(out.get("for_hands")) if kind in ("decision", "guess", "intention") else None,
        question=(_clean(out.get("question")) or conclusion) if kind == "question" else None,
        **extra,
    )


def _clean(value) -> str | None:
    return " ".join(str(value or "").split()) or None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
