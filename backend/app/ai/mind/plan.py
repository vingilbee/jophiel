"""Planning: the one author of her plan (docs/COLUMBINA_MIND_DESIGN.md "Planning").

Before this, every turn that held the plan tools wrote plans: replies to the user,
background turns on game events, hard thoughts. Live (2026-10-19), a hard thought wrote
"I still need a stone pickaxe to mine iron" and in the same turn replaced a correct
wood -> stone -> iron plan with "mine iron ore", while she held a wooden pickaxe: 16 plans
in 11 minutes, reasoning off, prerequisites dropped.

Now one call decides, with reasoning on and no tools: keep the plan (the usual answer),
revise it (steps kept by id, new ones where they belong, removals named), take a new goal,
wait for the user, or be done. Its "why" is her thought, so what she thinks and what she
plans can't come apart. Code applies the decision (services/game_planning).

General, like the rest of the mind: the source (the Minecraft pack) supplies the facts,
how its steps are written, what it takes to get something, and her voice there.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from app.ai.mind.situation import GENERAL, Situation

DECISIONS = ("keep", "revise", "new_goal", "wait_for_user", "done")

INSTRUCTIONS = """\
You are Jophiel's mind, planning. Right now she is {doing}. You are the only one \
who writes her plan: her hands do its steps one at a time, and her replies to {user} \
only pass on what {user} wants. Below: why you are planning now, the goal, the plan \
(each open step with its id), what it takes (worked out by code from the game's own \
data: trust it), what is happening, what she can do, what she has learned, her recent \
thoughts and the talk.

Decide one:
- keep: the plan still leads to the goal. Usually right: a slow step, news or a \
thought is no reason to change a plan that still works.
- revise: the plan can't reach the goal as it is (a prerequisite missing, a step that \
failed for a reason that will happen again, a step no skill can do): fix it. In steps, \
list the open steps in order: an existing step by its id in "keep" (its do and \
done_when are then ignored), a new step with "keep" empty. Open steps you don't list \
stay, at the end; to remove one, put it in drop with why.
- new_goal: {user} wants something new (use their words for the goal), or the goal is \
done and she plays on by herself. Give the goal and all its steps. If what {user} \
wants is what she is already doing, keep or revise instead.
- wait_for_user: nothing she can do moves the goal on without {user} (something only \
they can give or do). She stops and asks.
- done: the goal is finished and she doesn't play on by herself.

How to plan:
- Work backwards from the goal: what does the last step need, and what does that \
need? Every prerequisite under "What it takes" comes before the step that needs it. \
Don't plan for what she already has.
- Steps use only her skills, as the source's rules for steps say, each with a \
done_when the game can confirm. At most {max_steps} open steps.
- A failed step: read why first (counted failures, her hands' notes). Her hands' notes \
are what they saw, not proof: before deciding something can't be done, check whether \
a skill does it another way (digging through, going around, somewhere else).
- Her hands stuck on a step: never keep that step as it is, since it fails the same \
way again. Replace it with a different way (another skill, place or target) or drop \
it. Saying you'll do something else counts only if the steps say it.
- rules: short rules her hands keep for the whole goal (how, not what). Keep the ones \
in force that still apply. Never a rule about asking {user}: asking is yours.
- target: the one item or block the goal is about, as the game's own id, or empty. \
Code works out what it takes from it next time.

Asking {user} (ask): people playing together ask each other all the time, not only \
when stuck: where something is ("seen any iron around?"), whether they have something \
("got any cobblestone?"), which way to go, for help ("can you cover me?"). Ask when an \
answer would save real time or only they can help, in one short line in her voice. \
Don't ask what the game state already answers. She keeps playing while she waits, \
unless the decision is wait_for_user (then ask is required).
say: one short line to {user} about the plan, only if they'd want to know now (a goal \
she picked herself, being done, a change of approach they'd notice). Usually empty. \
Never both ask and say.
why: her thinking, first person, one or two sentences: what she concluded and why \
("I still need the right tool before I can get that, so the tool comes first."). It is \
shown as her thought.
{voice}"""


@dataclass
class PlanStep:
    keep: str | None = None  # an existing open step's id
    do: str = ""
    done_when: str = ""


@dataclass
class PlanDecision:
    decision: str | None
    why: str | None = None
    goal: str | None = None
    target: str | None = None
    steps: list[PlanStep] = field(default_factory=list)
    drop: dict[str, str] = field(default_factory=dict)  # step id -> why
    rules: list[str] = field(default_factory=list)
    ask: str | None = None
    say: str | None = None
    latency_ms: float = 0.0
    input_tokens: int | None = None
    error: str | None = None


SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "why": {"type": "string"},
        "goal": {"type": "string"},
        "target": {"type": "string"},
        "steps": {"type": "array", "items": {
            "type": "object",
            "properties": {"keep": {"type": "string"}, "do": {"type": "string"}, "done_when": {"type": "string"}},
            "required": ["keep", "do", "done_when"], "additionalProperties": False,
        }},
        "drop": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
            "required": ["id", "why"], "additionalProperties": False,
        }},
        "rules": {"type": "array", "items": {"type": "string"}},
        "ask": {"type": "string"},
        "say": {"type": "string"},
    },
    "required": ["decision", "why", "goal", "target", "steps", "drop", "rules", "ask", "say"],
    "additionalProperties": False,
}


def parse(out: dict) -> PlanDecision:
    """The model's answer as a decision; anything malformed becomes no decision (an error)."""
    decision = out.get("decision")
    if decision not in DECISIONS:
        return PlanDecision(None, error=f"unknown decision {decision!r}")
    steps = []
    for raw in out.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        keep = _clean(raw.get("keep"))
        do = _clean(raw.get("do")) or ""
        if keep or do:
            steps.append(PlanStep(keep=keep, do=do, done_when=_clean(raw.get("done_when")) or ""))
    drop = {}
    for raw in out.get("drop") or []:
        if isinstance(raw, dict) and _clean(raw.get("id")):
            drop[_clean(raw["id"])] = _clean(raw.get("why")) or ""
    ask, say = _clean(out.get("ask")), _clean(out.get("say"))
    target = (_clean(out.get("target")) or "").lower().replace(" ", "_") or None
    return PlanDecision(
        decision, why=_clean(out.get("why")), goal=_clean(out.get("goal")), target=target, steps=steps,
        drop=drop, rules=[rule for rule in (_clean(r) for r in out.get("rules") or []) if rule],
        ask=ask, say=None if ask else say,
    )


class Planner:
    def __init__(self, model: str, *, api_key: str, reasoning_effort: str | None = "low", timeout: float = 60.0):
        from openai import OpenAI

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def instructions(self, user: str, situation: Situation = GENERAL, max_steps: int = 12) -> str:
        return INSTRUCTIONS.format(doing=situation.describe(user), user=user, max_steps=max_steps,
                                   voice=situation.voice)

    def plan(self, text: str, *, user: str = "the user", situation: Situation = GENERAL,
             max_steps: int = 12) -> PlanDecision:
        started = time.perf_counter()
        kwargs = {}
        if self.reasoning_effort and self.model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.instructions(user, situation, max_steps)},
                          {"role": "user", "content": text}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "plan_decision", "strict": True, "schema": SCHEMA}},
                max_completion_tokens=6000,
                store=False,
                **kwargs,
            )
            decision = parse(json.loads(response.choices[0].message.content or "{}"))
        except Exception as exc:  # noqa: BLE001 - the caller retries; a plan is never half applied
            return PlanDecision(None, latency_ms=_ms(started), error=f"{type(exc).__name__}: {exc}"[:300])
        decision.latency_ms = _ms(started)
        decision.input_tokens = getattr(response.usage, "prompt_tokens", None)
        return decision


def _clean(value) -> str | None:
    return " ".join(str(value or "").split()) or None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
