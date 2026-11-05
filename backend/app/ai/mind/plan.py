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
(each open step with its id), her effort, what it takes (worked out by code from the game's \
own data: trust it), what is happening, what she can do, what she has learned, her recent \
thoughts and the talk.

Her effort is the story of the outcome she's working toward, recorded by code: the outcome \
(in her memory), the plan as her current approach, {user}'s rules for it, how long it has \
taken, what was counted, how she perceives here and what her current view covers, and a \
timeline of what she concluded, what her plans and hands did and what was said. Read it like \
a person looking back over the last half hour: how long this has taken and whether it's \
getting anywhere, what was already tried, whether earlier conclusions were carried out and what \
came of them, and, when it isn't working, why (what she can do, what she can or can't perceive \
from here, what's really there, or whether she has understood {user}). Code only recorded it; \
judging it is yours. Parts of it may belong to an earlier session or another place: the times, \
the gaps and what {user} said tell you which.

The outcome and the plan are different things. The plan's goal is her current approach, or \
what {user} asked just now ("follow me", "go over there"); the outcome stays until {user} says \
it changed. When {user} cancels an action ("don't follow me"), that approach goes and the \
outcome stays, with what they showed her.

Look first, like a person does before deciding what to do next (stage 2 of \
docs/learning-system/AUDIT-2026-11-04-learning-from-experience.md):
- sees: what she perceives right now that bears on the goal, in a few plain sentences: where she is (outdoors, \
underground, in an open space and how big, which way it goes on), what's in view that matters, how her height \
has changed. Only from what's below.
- cannot_see: what her senses or her current view don't cover that matters here (what's behind rock, below \
her, past where the open space was counted, an ore she hasn't seen). Not seeing something isn't it being absent.
- progress: is her effort getting anywhere toward the outcome? "yes", "no" or "unclear", judged by you from \
the whole record, not this moment alone. No rule of time or tries decides it. Empty when there's no effort.
Then plan from what she sees, not from the recipe alone.

Decide one:
- keep: the plan still leads to the goal. Usually right: a slow step, news or a \
thought is no reason to change a plan that still works.
- revise: the plan can't reach the goal as it is (a prerequisite missing, a step that \
failed for a reason that will happen again, a step no skill can do): fix it. In steps, \
list the open steps in order: an existing step by its id in "keep" (its do and \
done_when are then ignored), a new step with "keep" empty. Open steps you don't list \
stay, at the end; to remove one, put it in drop with why. In goal, the plan's goal: her \
approach now, in a few words. When the approach changed (it stopped working, or {user} \
cancelled it), write the new one; never keep an approach {user} called off. Empty keeps it.
- new_goal: {user} wants something new (use their words for the goal), or the goal is \
done and she plays on by herself. Give the goal and all its steps. If what {user} \
wants is what she is already doing, keep or revise instead.
- wait_for_user: nothing she can do moves the goal on without {user} (something only \
they can give or do). She stops and asks.
- done: the goal is finished and she doesn't play on by herself.

How to plan:
- Start from where she is and what she sees: what would a person standing there do next to get closer to the \
outcome? "What it takes" says what is needed (every prerequisite before the step that needs it; nothing she \
already has); it is the list of ingredients, not the plan. Getting to where those things are is part of the \
plan: a search, a way down, a way in, from what she sees and can't see.
- A step is an intention in plain words, the way a person would say it to themselves ("dig straight down here \
until I break into open space", "walk the passage that goes down", "mine the iron ore in view"), as the \
source's rules for steps say. When {user} told her how to do something, the step keeps their way, with their \
end. Her hands carry each step out with their skills.
- done_when: what will be true when the step is done, something her hands can tell from their state: a count \
("gained 3 raw_iron") or a situation they can see ("in an open space that goes on", "iron ore in view", \
"height below 40"). Never a count that stands in for an intention ("gained 1 dirt" for "dig down until a \
cave"). At most {max_steps} open steps.
- If no skill of hers can do an intention, keep the intention anyway: her hands will say what move is \
missing, and she can teach herself that move. Don't shrink it into something a skill happens to do.
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
follows: when this decision carries out one of her earlier conclusions or plans on the \
timeline, its id there (like "D1a2b3c"); otherwise empty.
user_rules: a rule {user} gave for the outcome that should hold beyond this plan ("this is your \
task, don't follow me"), each with {user}'s exact words from the talk. Only new ones: the rules \
already in force are listed in her effort.
lift_rules: the ids of rules in force that {user} has taken back.
places: places or things {user} pointed out that matter to the outcome (a name, where it is if \
known, their exact words). Only new ones.
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
    sees: str | None = None  # what she perceives now that bears on the goal, in the planner's words
    cannot_see: str | None = None  # what her view doesn't cover that matters
    progress: str | None = None  # her judgment of the effort: yes / no / unclear
    goal: str | None = None
    target: str | None = None
    steps: list[PlanStep] = field(default_factory=list)
    drop: dict[str, str] = field(default_factory=dict)  # step id -> why
    rules: list[str] = field(default_factory=list)
    ask: str | None = None
    say: str | None = None
    follows: str | None = None  # the earlier conclusion or plan it says it carries out (a timeline id)
    user_rules: list[tuple[str, str]] = field(default_factory=list)  # (rule, the user's words)
    lift_rules: list[str] = field(default_factory=list)  # ids of rules in force
    places: list[tuple[str, str, str]] = field(default_factory=list)  # (name, where, the user's words)
    latency_ms: float = 0.0
    input_tokens: int | None = None
    error: str | None = None
    # As asked and answered, for the learning capture (services/game_planning.py): the exact system text, the
    # model and its settings, and the raw answer (None: no answer came back).
    system: str | None = None
    model: str | None = None
    settings: dict = field(default_factory=dict)
    raw: str | None = None
    output_tokens: int | None = None


SCHEMA = {
    "type": "object",
    "properties": {
        # Looking comes first, so it's written before the decision.
        "sees": {"type": "string"},
        "cannot_see": {"type": "string"},
        "progress": {"type": "string", "enum": ["yes", "no", "unclear", ""]},
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
        "follows": {"type": "string"},
        "user_rules": {"type": "array", "items": {
            "type": "object", "properties": {"rule": {"type": "string"}, "words": {"type": "string"}},
            "required": ["rule", "words"], "additionalProperties": False,
        }},
        "lift_rules": {"type": "array", "items": {"type": "string"}},
        "places": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "where": {"type": "string"}, "words": {"type": "string"}},
            "required": ["name", "where", "words"], "additionalProperties": False,
        }},
    },
    "required": ["sees", "cannot_see", "progress", "decision", "why", "goal", "target", "steps", "drop", "rules", "ask", "say", "follows",
                 "user_rules", "lift_rules", "places"],
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
    from app.ai.mind.hard import follows_id

    user_rules = [(_clean(r.get("rule")), _clean(r.get("words"))) for r in out.get("user_rules") or []
                  if isinstance(r, dict)]
    places = [(_clean(p.get("name")), _clean(p.get("where")) or "", _clean(p.get("words")) or "")
              for p in out.get("places") or [] if isinstance(p, dict)]
    return PlanDecision(
        decision, why=_clean(out.get("why")), sees=_clean(out.get("sees")), cannot_see=_clean(out.get("cannot_see")),
        progress=out.get("progress") if out.get("progress") in ("yes", "no", "unclear") else None,
        goal=_clean(out.get("goal")), target=target, steps=steps,
        drop=drop, rules=[rule for rule in (_clean(r) for r in out.get("rules") or []) if rule],
        ask=ask, say=None if ask else say, follows=follows_id(out.get("follows")),
        user_rules=[(rule, words) for rule, words in user_rules if rule and words],
        lift_rules=[ref for ref in (_clean(r) for r in out.get("lift_rules") or []) if ref],
        places=[(name, where, words) for name, where, words in places if name],
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
        system = self.instructions(user, situation, max_steps)
        asked = {"system": system, "model": self.model,
                 "settings": {**kwargs, "max_completion_tokens": 6000, "schema": "plan_decision (strict)"}}
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": text}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "plan_decision", "strict": True, "schema": SCHEMA}},
                max_completion_tokens=6000,
                store=False,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - the caller retries; a plan is never half applied
            return PlanDecision(None, latency_ms=_ms(started), error=f"{type(exc).__name__}: {exc}"[:300], **asked)
        raw = response.choices[0].message.content
        try:
            decision = parse(json.loads(raw or "{}"))
        except Exception as exc:  # noqa: BLE001 - an answer that isn't a decision is no decision (the caller retries)
            decision = PlanDecision(None, error=f"{type(exc).__name__}: {exc}"[:300])
        decision.latency_ms = _ms(started)
        decision.input_tokens = getattr(response.usage, "prompt_tokens", None)
        decision.output_tokens = getattr(response.usage, "completion_tokens", None)
        decision.system, decision.model, decision.settings = asked["system"], asked["model"], asked["settings"]
        decision.raw = raw if raw is not None else ""
        return decision


def _clean(value) -> str | None:
    return " ".join(str(value or "").split()) or None


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
