"""Quick thoughts (react and light): one short thought, and maybe one line to say.

General: the source's ``Situation`` says what she is doing and how she sounds
there; nothing here names a game. A thought must think (why, next, the user,
judgement, feeling, memory), not describe the moment back.

A fast model with a strict schema, so a reaction lands within about a second.
Hard thoughts are not here: they are Luna, as a background game turn with her
game tools (services/game_events). The line, if any, still goes through the one
speaking gate (services/agent_say) before the user hears it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.ai.mind.situation import GENERAL, Situation

KINDS = {
    "why": "why it is happening, what it means",
    "next": "what will probably happen next, what she should do",
    "you": "what {user} is trying to do, or how they feel",
    "judgement": "what she thinks of it, a better way to do it",
    "feeling": "how it makes her feel",
    "memory": "what it reminds her of, from what is in front of her (her recent thoughts, the talk, the "
              "situation); never invent a past",
}

QUICK_INSTRUCTIONS = """\
You are Jophiel's mind. Right now she is {doing}. Below is one moment: what \
changed, how much time she has, her recent thoughts, and the whole situation.

The moment already says what is happening: don't repeat it back. Think about \
it, the way someone who is in it would, not like a commentator. Pick the one \
kind of thinking this moment calls for:
{kinds}

"noticed": what in the moment caught her attention, in a few words.
"thought": her thought, in the first person, at most 25 words. It must add \
something the moment doesn't already say.

{level_rule}

{repeat_rule}

Then decide whether to say something to {user}. Say something only if {user} \
would want to hear it now: a warning, a reaction to what they just did or what \
just happened to you both, a thought worth sharing. Leave "say" empty when it's \
routine, when your recent thoughts or what you said already covered it, or \
when nothing changed. Never promise something you aren't doing: only what's in \
your goal, what you're doing now, or what you're already watching for.

Acting is separate from speaking. If the moment lists WHAT YOU'RE WAITING FOR, \
and this moment makes one of those due now (what it waits for has happened), or \
shows how an attempt of yours turned out, put that promise's id (the one in \
brackets at the start of its line) in "item" and what to do about it, in a few words, in "act", even when "say" is \
empty: you'll be woken with your tools to do it. Leave both empty otherwise: a \
prediction, a passing thought, or something not due yet is no reason to act.
{voice}"""

REPEAT_RULES = {
    "moment": "Don't think the same thing twice. If your recent thoughts already cover this moment and "
              "nothing new changes it, leave every field empty.",
    # Nothing is new in a quiet moment by definition, so "nothing new" can't be the reason to stay
    # empty; nor can the schema offer an empty kind (models take that way out every time).
    "quiet": "Don't think the same thing twice: think about something your recent thoughts haven't covered.",
}

LEVEL_RULES = {
    "react": "This is a REACT moment: no time to think. The thought is a gut reaction; "
             "if anything is said, it's a few words (\"look out!\", \"ow!\").",
    "light": "This is a LIGHT moment: a short thought, no planning. If anything is said, one short line.",
    "quiet": "This is a QUIET moment: nothing new is happening, and you have time. Don't think about the "
             "present at all. Think ahead (what comes after this, what you'd like to do later), about {user} "
             "(what they might want, how they are doing), or about yourself (how it's going, what you'd like). "
             "Usually say nothing; say one line only if it's worth breaking a silence for.",
}


@dataclass
class QuickThought:
    text: str | None
    say: str | None
    latency_ms: float
    input_tokens: int | None = None
    error: str | None = None
    kind: str | None = None
    noticed: str | None = None
    act: str | None = None  # what a promise or intention of hers made due now calls for (follow_through)
    item: str | None = None  # that item's id, as the moment shows it


class QuickThinker:
    def __init__(self, model: str, *, api_key: str, timeout: float = 10.0):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def instructions(self, level: str, user: str, situation: Situation = GENERAL) -> str:
        kinds = "\n".join(f"- {kind}: {what.format(user=user)}" for kind, what in KINDS.items())
        return QUICK_INSTRUCTIONS.format(doing=situation.describe(user), user=user, kinds=kinds,
                                         level_rule=LEVEL_RULES[level].format(user=user),
                                         repeat_rule=REPEAT_RULES["quiet" if level == "quiet" else "moment"],
                                         voice=situation.voice)

    def think(self, level: str, tick_text: str, *, user: str = "the user",
              situation: Situation = GENERAL) -> QuickThought:
        started = time.perf_counter()
        schema = {
            "type": "object",
            "properties": {"noticed": {"type": "string"},
                           "kind": {"type": "string", "enum": [*KINDS] if level == "quiet" else [*KINDS, ""]},
                           "thought": {"type": "string"}, "say": {"type": "string"},
                           "item": {"type": "string"}, "act": {"type": "string"}},
            "required": ["noticed", "kind", "thought", "say", "item", "act"],
            "additionalProperties": False,
        }
        kwargs = {"reasoning_effort": "none"} if self.model.startswith(("gpt-5", "gpt-6")) else {}
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.instructions(level, user, situation)},
                          {"role": "user", "content": tick_text}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "thought", "strict": True, "schema": schema}},
                max_completion_tokens=260,
                store=False,
                **kwargs,
            )
            out = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001 - a failed thought is just no thought
            return QuickThought(None, None, _ms(started), error=f"{type(exc).__name__}: {exc}"[:300])
        text = _clean(out.get("thought"))
        act, item = _clean(out.get("act")), _clean(out.get("item"))
        return QuickThought(text, _clean(out.get("say")) if text else None, _ms(started),
                            input_tokens=getattr(response.usage, "prompt_tokens", None),
                            kind=(out.get("kind") or None) if text else None, noticed=_clean(out.get("noticed")),
                            act=act if act and item else None, item=item if act and item else None)


def _clean(value) -> str | None:
    return " ".join(str(value or "").split()) or None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
