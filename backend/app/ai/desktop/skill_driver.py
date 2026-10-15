"""Her hands in a game pack (stage G, phase 3): pick one skill at a time.

The key-pressing driver (``driver_loop``) decides every ~2 s from a picture.
With a game pack the game does the fast part: a skill runs at 20 Hz until it
ends with an outcome, and the model only decides what to do next, from her
state as text, the outcomes so far, what she noticed, and Columbina's plan and
messages. One ``SkillDriverRun`` per playing session, in the gameplay worker.

Each ``step``:

1. reads the session (must be playing, with a plan step left);
2. if her skill is running: waits briefly for it, stops it if the session
   stopped playing or if something urgent was noticed (a creeper behind her
   while she mines) so the next decision can react. If the plan changed, it
   keeps running while the model decides under the new plan, and carries on
   if the model chooses it again (a follow under "follow them to the cave");
   anything else stops it;
3. otherwise asks the model for one decision: a skill with parameters (or a
   short wait), and the step's status;
4. drops the answer if the session or plan changed or Columbina sent a message
   while the model was thinking;
5. starts the skill in the pack and records progress.

Every outcome and notice is also recorded as an experience (game learning),
and the lessons she learned in earlier sessions are part of the system prompt.

While she teaches herself a move (stage H1, ``practice``) her hands hold: they
decide nothing and start nothing until it's over (the learner stops a skill of
theirs that is still running), so her body does one thing at a time.

Notices from the pack become Columbina's game events (urgent ones as danger,
discoveries like a village and warnings like the sun setting unless she's been
told to be quiet, the rest only in commentary mode). Like the
key driver's notes, everything the model writes besides the decision, status
and step id is untrusted text for display.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.desktop.driver import DriverModel, DriverModelError
from app.ai.desktop.pack_client import PackError
from app.services.desktop_sessions import DriverView

SCHEMA_NAME = "skill_decision"
STATUSES = ("working", "step_done", "stuck", "needs_human")
WAIT_SKILL = "wait"
MAX_WAIT_SECONDS = 30
POLL_SECONDS = 0.5  # how long one step waits on a running skill before looking around again
MAX_MODEL_ERRORS = 3
MAX_REFUSED = 3  # the pack refused this many decisions in a row: stuck
MAX_OUTCOMES = 8  # outcomes kept for the prompt
PACK_DOWN_SECONDS = 15.0  # the pack unreachable this long while playing: pause
# Skills that are themselves the answer to danger: an urgent notice doesn't cut them short.
DANGER_SKILLS = frozenset({"fight", "eat"})
# Notice kinds worth telling Columbina about even outside commentary mode.
DISCOVERIES = frozenset({"village", "spawner", "ore"})
# Heads-ups worth waking her for, so she can warn the user or get ready: in the 2026-10-11
# cave session she lost 9 hearts and night fell without a word, because these were
# "notable" and the session was in occasional mode. Urgent ones are danger anyway. A "mode"
# heads-up is something a background mode did that the user may like to hear (the pack decides).
# The user getting hit or getting into bed (src/watching.js) is the same kind of moment.
WARNINGS = frozenset({"hurt", "night_coming", "hostile_near_player", "mode", "player_hurt", "player_sleeping"})
MODES_KEPT = 5  # what background modes did lately, for her summary
# She respawns by herself, so a death isn't a reason to stop for the user: "needs_human" this
# soon after one is overruled and she decides again.
DEATH_GRACE_SECONDS = 60.0
# The same action ending the same way this many times in a row: told to try something else;
# REPEAT_STUCK times: stuck.
REPEAT_WARN = 3
REPEAT_STUCK = 6
# Outcomes that count as a failed try at the current step. Failing in different ways never
# reaches REPEAT_STUCK (last session: a bed craft failed 4 times, explore 3 times), so after
# this many failed tries on one step Columbina is told (she keeps playing) and can fix the plan
# or ask the user for what's missing.
FAILED = frozenset({"failed", "lost", "blocked", "refused", "timeout"})
STRUGGLING_AT = (4, 10)
STEP_FAILURES_KEPT = 6
# When the plan changes, the running skill carries on if the hands choose it again: these
# params (how long it runs) may differ.
CARRY_IGNORES = frozenset({"time_limit", "seconds"})


# --------------------------------------------------------------------------- #
# Schema and prompt, generated from the pack's skill manifest
# --------------------------------------------------------------------------- #

_JSON_TYPES = {"string": "string", "enum": "string", "number": "number", "integer": "integer"}


def _limits(rule: dict) -> str:
    """The manifest's numeric bounds in words. Without them the hands guessed
    (seconds=900 for a skill capped at 600) and the pack refused the decision."""
    low, high = rule.get("min"), rule.get("max")
    if low is not None and high is not None:
        return f" ({low} to {high})"
    if high is not None:
        return f" (at most {high})"
    return f" (at least {low})" if low is not None else ""


def _param_schema(rule: dict) -> dict:
    schema: dict = {"type": _JSON_TYPES[rule["type"]], "description": rule.get("description", "") + _limits(rule)}
    if rule.get("values"):
        schema["enum"] = rule["values"]
    if not rule.get("required"):
        # Strict schemas need every key; optional ones may be null (the pack's default).
        schema = {**schema, "type": [schema["type"], "null"]}
        if "enum" in schema:
            schema["enum"] = [*schema["enum"], None]
    return schema


def skill_schema(manifest: list[dict]) -> dict:
    """Strict JSON schema: a note, the step status and id, and one action
    (a skill from the manifest with its parameters, or a wait), or none."""
    actions = []
    for skill in manifest:
        params = {name: _param_schema(rule) for name, rule in skill["params"].items()}
        actions.append({
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": [skill["name"]]},
                "params": {"type": "object", "properties": params, "required": list(params),
                           "additionalProperties": False},
            },
            "required": ["skill", "params"],
            "additionalProperties": False,
        })
    actions.append({
        "type": "object",
        "properties": {
            "skill": {"type": "string", "enum": [WAIT_SKILL]},
            "params": {"type": "object", "properties": {"seconds": {"type": "number"}},
                       "required": ["seconds"], "additionalProperties": False},
        },
        "required": ["skill", "params"],
        "additionalProperties": False,
    })
    return {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "a few words on what you're doing and why"},
            "status": {"type": "string", "enum": list(STATUSES)},
            "step_id": {"type": ["string", "null"]},
            "action": {"anyOf": [*actions, {"type": "null"}]},
        },
        "required": ["note", "status", "step_id", "action"],
        "additionalProperties": False,
    }


def _skill_line(skill: dict) -> str:
    params = ", ".join(
        f"{name}{'' if rule.get('required') else '?'}: {rule.get('description', rule['type'])}{_limits(rule)}"
        for name, rule in skill["params"].items() if name != "time_limit"
    )
    return (f"- {skill['name']}({params}): {skill['description']} Done when {skill['done_when']}. "
            f"Stops when {skill['stops_when']}.")


def lessons_text(lessons: list[dict]) -> str:
    """Lessons from earlier sessions (game learning), confirmed ones first: how
    skills behave, then planning lessons as background (Columbina plans with
    those; the ones she wants kept arrive as the plan's rules)."""
    def line(lesson: dict) -> str:
        return (f"- When {lesson['situation']}: {lesson['advice']}"
                + (" (tentative: seen once)" if lesson.get("status") == "trial" else ""))

    skill = [line(lesson) for lesson in lessons if lesson.get("level", "skill") == "skill"]
    plan = [line(lesson) for lesson in lessons if lesson.get("level") == "plan"]
    text = ""
    if skill:
        text += ("\n\nWhat you learned in earlier sessions (follow it unless this situation is clearly different):\n"
                 + "\n".join(skill))
    if plan:
        text += ("\n\nPlanning lessons Columbina learned (she plans with these; keep them in mind):\n"
                 + "\n".join(plan))
    return text


def system_prompt(manifest: list[dict], game: str = "Minecraft", lessons: list[dict] | None = None) -> str:
    skills = "\n".join(_skill_line(s) for s in manifest)
    return f"""You are Columbina's hands in {game}: her own player in the world. Columbina sets the goal and \
the plan; you carry out the current plan step by choosing ONE skill at a time. A skill runs by itself until \
it ends with an outcome (done, failed and why, lost, blocked, hurt, timeout, stopped); then you choose again.

Skills:
{skills}
- wait(seconds): do nothing for a few seconds (at most {MAX_WAIT_SECONDS}), e.g. waiting for the user.
Every skill also takes time_limit (seconds; null for its default).

How to decide:
- Work only on the current plan step. When its done_when is already true (check your state and the last \
outcomes), answer status "step_done" with no action, or with the first action of the next step if it's shown.
- An amount to get ("gained 10 oak_log", "chop 10 oak logs", "collect 5 wool") counts only what you gained \
during this step: read "Inventory change since this step began", never the total you hold. Only "have N" or \
"N in inventory" means the total.
- Ongoing steps ("follow the user", "stay near them", "keep watch") are only done when their done_when \
says so (e.g. "the user says to stop"), never because you happen to be close: keep doing them (follow_player \
again whenever one ends).
- Your state says who you are ("You are ..."). Never target yourself; "the user" is one of the other players.
- Read the last outcome before choosing. If a skill failed, its reason says what's missing: fix that first \
(e.g. "no tool that can harvest stone (needs wooden_pickaxe...)" -> get logs, craft planks, sticks, a crafting \
table, place it, craft the pickaxe). Don't repeat the same failing action unchanged.
- Chain small steps toward the goal: collect, craft, place, then use.
- Danger first: a hostile mob close by (see Noticed and Nearby) -> fight it or move away before carrying on. \
Low health -> eat, or get away. Night on the surface -> finish quickly, shelter, or sleep in a bed.
- Never attack players. Stay in the game; you can't do anything outside it.
- If you die you respawn by yourself (at your bed, or the world spawn): never answer "needs_human" for it. \
Carry on with the step; the user is probably far away now, so find them again.
- A player in "Other players online" is in the game even when you can't see them. "Out of sight" means \
only that they're too far for you to see: go_to player:<name> or follow_player walks to where you last saw \
them. Never call someone offline unless they're missing from "Other players online".
- "Come to me" when the user is below or above you (in a hole, a tunnel, on a ledge): go_to player:<name>, \
which only counts as arrived at their level. If it fails saying they're below or above, go_to again with \
dig="yes" to dig down (or through) to them.
- An action that ends instantly with nothing changed (you were already there) did nothing: don't choose it \
again. Move somewhere else, look for the user, or wait.
- Background modes (the "Background modes" line in your state) run by themselves while you follow, walk, \
explore or mine: guard fights hostiles close to you or the player you're with, gather mines the listed blocks \
close by, pickup grabs drops. Don't spend decisions on what a mode that's on already does; an outcome's \
"interruptions" say what they did along the way.
- Columbina's messages to you are how to play: follow them.
- If you truly can't make progress (tried several different ways), answer status "stuck" with a note saying \
why. If no skill of yours can do this step at all (e.g. pillar up, build a whole house), don't try things: \
answer "stuck" now with a note starting "no skill for:" and the move that's missing. If you need the user \
(e.g. they must give you something you can't get, or open the world), answer "needs_human" saying exactly what.
- note: a few plain words about what you're doing, e.g. "chopping the oak to my left for planks".
- params: use null for anything optional you don't need; names are Minecraft ids (oak_log, crafting_table).\
{lessons_text(lessons or [])}"""


def to_action(raw: dict, manifest: list[dict]) -> tuple[str, dict] | None:
    """(skill, params) with nulls dropped, or None for no action. Raises ValueError for junk."""
    action = raw.get("action")
    if action is None:
        return None
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    skill = action.get("skill")
    names = {s["name"] for s in manifest} | {WAIT_SKILL}
    if skill not in names:
        raise ValueError(f"unknown skill {skill!r}")
    params = {k: v for k, v in (action.get("params") or {}).items() if v is not None}
    rules = next((s["params"] for s in manifest if s["name"] == skill), {})
    for key, value in params.items():
        rule = rules.get(key) or {}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Out of bounds is a near miss, not a reason to lose the decision.
            if rule.get("min") is not None:
                value = max(rule["min"], value)
            if rule.get("max") is not None:
                value = min(rule["max"], value)
            params[key] = value
    return skill, params


def same_action(running: dict, skill: str, params: dict) -> bool:
    """The decision repeats the running skill: same skill and target. How long it runs doesn't
    count, and a setting the decision leaves out is the default the run already has."""
    if skill != running["skill"]:
        return False
    held = running.get("params") or {}
    return all(held.get(key) == value for key, value in params.items() if key not in CARRY_IGNORES)


def failure_summary(failures: list[dict]) -> str:
    """Failed tries, grouped: 'craft(item=white_bed) -> missing ingredients {"needs":...} x3; ...'."""
    groups: dict[str, int] = {}
    for f in failures:
        params = ", ".join(f"{k}={v}" for k, v in (f.get("params") or {}).items())
        data = json.dumps(f["data"], separators=(",", ":")) if f.get("data") else ""
        text = f"{f['skill']}({params}) -> {f.get('reason') or f['status']}" + (f" {data}" if data else "")
        groups[text[:200]] = groups.get(text[:200], 0) + 1
    return "; ".join(text + (f" x{n}" if n > 1 else "") for text, n in groups.items())


def struggle_text(log: dict) -> str:
    return f'{log["failed"]} failed tries at "{log["do"][:80]}": {failure_summary(log["failures"])}'


def inventory_counts(state: dict) -> dict[str, int]:
    """{item: count} from the pack's state (``inventory: [{name, count}]``)."""
    counts: dict[str, int] = {}
    for item in state.get("inventory") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
    return counts


def inventory_change(before: dict[str, int], after: dict[str, int]) -> str:
    """'apple 1 -> 0 (-1); oak_log 60 -> 63 (+3)', or 'none yet'."""
    changes = []
    for name in sorted(set(before) | set(after)):
        was, now = before.get(name, 0), after.get(name, 0)
        if was != now:
            changes.append(f"{name} {was} -> {now} ({now - was:+d})")
    return "; ".join(changes) if changes else "none yet"


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


@dataclass
class SkillDriverDeps:
    view: Callable[[uuid.UUID], DriverView | None]
    progress: Callable[..., bool]
    pause: Callable[[uuid.UUID, str], None]
    pack: object  # PackClient
    model: DriverModel
    metric: Callable[[dict], None]
    save_state: Callable[[uuid.UUID, dict], None]
    # (session, kind, text): danger / discovery / notable for Columbina.
    notice: Callable[[uuid.UUID, str, str], None] | None = None
    auto_goals: Callable[[uuid.UUID], int] | None = None
    messages: Callable[[uuid.UUID], list[dict]] | None = None
    # Game learning: (session, kind, payload) appends an experience; lessons(session) loads her lessons.
    experience: Callable[[uuid.UUID, str, dict], None] | None = None
    lessons: Callable[[uuid.UUID], list[dict]] | None = None
    # True while she's teaching herself a move (services/game_practice.holding): the hands hold.
    learning: Callable[[uuid.UUID], bool] | None = None
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass
class SkillDriverRun:
    session_id: uuid.UUID
    deps: SkillDriverDeps
    max_decisions: int = 400
    planning_wait_seconds: float = 60.0
    max_auto_goals: int = 10
    decisions: int = 0
    running: dict | None = None  # {"id", "skill", "params", "plan_revision", "generation", "step_id"}
    waiting_until: float | None = None  # a "wait" decision in progress
    outcomes: list[dict] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)  # new since the last decision
    notice_seq: int | None = None
    note: str = ""
    errors: int = 0
    refused: int = 0
    pack_down_since: float | None = None
    died_at: float | None = None
    planning_since: float | None = None
    step_started: tuple[str, float] | None = None
    # The current step's tries: {"id", "do", "since", "tries", "failures": [...], "failed"}.
    step_log: dict | None = None
    # Inventory counts at the current step's first look, so "gained 10 oak_log"
    # counts from then: with 60 logs already held, "10 oak_log" was true at once.
    step_inventory: dict[str, int] | None = None
    # What her background modes did lately: [{"text", "at"}], newest last.
    modes_did: list[dict] = field(default_factory=list)
    manifest: list[dict] | None = None
    _schema: dict | None = None
    _system: str | None = None

    def step(self) -> bool:
        deps = self.deps
        view = deps.view(self.session_id)
        if view is None or not view.playing or view.plan is None:
            self._stop_running("session stopped playing")
            self._save()
            return False
        if self._learning():
            # The learner has her body: it stops a skill of ours still running, and we start nothing.
            self.running = None
            self.waiting_until = None
            deps.sleep(1.0)
            return True
        try:
            self._pull_notices()
            if self.manifest is None:
                self.manifest = deps.pack.manifest()
                self._schema = skill_schema(self.manifest)
                self._system = system_prompt(self.manifest, lessons=self._load_lessons())
            self.pack_down_since = None
        except PackError as exc:
            return self._pack_down(exc)

        if self.running is not None and not self.running.get("carried"):
            watched = self._watch_running(view)
            if watched is not None:
                return watched
        if self.waiting_until is not None:
            if deps.now() < self.waiting_until and not self._urgent():
                deps.sleep(POLL_SECONDS)
                return True
            self.waiting_until = None

        current = view.current_step()
        if current is None:
            self._stop_running("the plan changed")
            return self._between_goals(view)
        self.planning_since = None
        if self.step_started is None or self.step_started[0] != current["id"]:
            self.step_started = (current["id"], deps.now())
            self.step_log = {"id": current["id"], "do": current.get("do", ""), "since": deps.now(),
                             "tries": 0, "failed": 0, "failures": []}
            self.step_inventory = None

        try:
            state = deps.pack.state()
        except PackError as exc:
            return self._pack_down(exc)
        if self.step_inventory is None:
            self.step_inventory = inventory_counts(state)
        messages = deps.messages(self.session_id) if deps.messages else []
        text = self.prompt_text(view, current, state, messages)
        asked_at = deps.now()
        try:
            reply = deps.model.step(self._system, text, [], self._schema)
            action = to_action(reply.output, self.manifest)
            status = reply.output.get("status", "working")
            if status not in STATUSES:
                raise ValueError(f"bad status {status!r}")
        except (DriverModelError, ValueError) as exc:
            self.errors += 1
            deps.metric({"session": str(self.session_id), "event": "skill_model_error", "error": str(exc)[:200]})
            if self.errors >= MAX_MODEL_ERRORS:
                deps.pause(self.session_id, "driver_error")
                self._save()
                return False
            return True
        self.errors = 0

        # The world may have moved on while the model thought.
        after = deps.view(self.session_id)
        if (after is None or not after.playing or after.execution_generation != view.execution_generation
                or after.plan_revision != view.plan_revision):
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "session_changed"})
            return True
        if deps.messages and [m["id"] for m in deps.messages(self.session_id)] != [m["id"] for m in messages]:
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "message_arrived"})
            return True
        if self._learning():
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "learning"})
            return True

        self.decisions += 1
        self.note = str(reply.output.get("note", ""))[:200]
        self.notices = []
        if reply.output.get("step_id") not in (None, current["id"]):
            status = "working"  # it may only report on the step it was given
        deps.metric({"session": str(self.session_id), "event": "skill_decision", "decision": self.decisions,
                     "plan_step": current["id"], "status": status, "action": action, "note": self.note,
                     "model_ms": reply.latency_ms, "input_tokens": reply.input_tokens,
                     "output_tokens": reply.output_tokens, "cached_tokens": reply.cached_tokens,
                     "think_ms": int((deps.now() - asked_at) * 1000)})

        if status == "needs_human" and self.died_at is not None \
                and deps.now() - self.died_at < DEATH_GRACE_SECONDS:
            deps.metric({"session": str(self.session_id), "event": "overruled", "why": "needs_human_after_death"})
            status = "working"
        repeats = self._repeats()
        if status == "working" and repeats >= REPEAT_STUCK:
            status = "stuck"
            self.note = f"tried the same thing {repeats} times with the same result"
        if status != "working" or current["state"] == "pending":
            deps.progress(self.session_id, plan_revision=view.plan_revision, step_id=current["id"],
                          status=status, note=self.note)
        if status in ("stuck", "needs_human"):
            self._save()
            return False
        if self.running is not None and self.running.get("carried"):
            if action is not None and same_action(self.running, *action):
                # Still fits the new plan: it carries on without a break.
                self.running = {key: value for key, value in self.running.items() if key != "carried"} | {
                    "plan_revision": view.plan_revision, "generation": view.execution_generation,
                    "step_id": current["id"]}
                action = None
            else:
                self._stop_running("the plan changed")
        if action is not None:
            self._start(view, current, *action)
        if self.decisions >= self.max_decisions:
            deps.pause(self.session_id, "budget")
            self._save()
            return False
        self._save()
        return True

    def _learning(self) -> bool:
        if self.deps.learning is None:
            return False
        try:
            return bool(self.deps.learning(self.session_id))
        except Exception:  # noqa: BLE001 - a failed check never stops play
            return False

    # -- running a skill --------------------------------------------------------

    def _start(self, view: DriverView, current: dict, skill: str, params: dict) -> None:
        deps = self.deps
        if skill == WAIT_SKILL:
            seconds = max(1.0, min(MAX_WAIT_SECONDS, float(params.get("seconds", 5))))
            self.waiting_until = deps.now() + seconds
            self._outcome({"skill": WAIT_SKILL, "params": {"seconds": seconds}, "status": "done", "reason": None})
            return
        try:
            record = deps.pack.run(skill, params, source="driver")
        except PackError as exc:
            if exc.status == 409:
                deps.pack.stop()  # something else was running (a hand-run skill): ours goes next time
                return
            if exc.status == 400:
                self.refused += 1
                self._outcome({"skill": skill, "params": params, "status": "refused", "reason": str(exc)},
                              step_id=current["id"])
                if self.refused >= MAX_REFUSED:
                    deps.progress(self.session_id, plan_revision=view.plan_revision, step_id=current["id"],
                                  status="stuck", note=f"{MAX_REFUSED} decisions in a row refused: {exc}")
                return
            raise
        self.refused = 0
        self.running = {"id": record["id"], "skill": skill, "params": record.get("params", params),
                        "plan_revision": view.plan_revision, "generation": view.execution_generation,
                        "started": deps.now(), "step_id": current["id"]}

    def _watch_running(self, view: DriverView) -> bool | None:
        """True/False as ``step`` returns; None: the plan changed, so decide under the new one
        while the skill keeps running (carried), and stop it only if something else is chosen."""
        deps = self.deps
        running = self.running
        if view.plan_revision != running["plan_revision"] or view.execution_generation != running["generation"]:
            running["carried"] = True
            return None
        if running["skill"] not in DANGER_SKILLS and self._urgent():
            self._stop_running("something urgent was noticed")
            return True
        try:
            record = deps.pack.get_run(running["id"], wait=POLL_SECONDS)
        except PackError as exc:
            return self._pack_down(exc)
        if record.get("status") == "running":
            return True
        self.running = None
        self._outcome(record, step_id=running.get("step_id"))
        if self.deps.notice and record.get("status") == "done" and running["skill"] == "fight" \
                and (record.get("data") or {}).get("killed"):
            self.deps.notice(self.session_id, "notable", f"killed {record['data']['killed']} "
                             f"{'mob' if record['data']['killed'] == 1 else 'mobs'}")
        return True

    def _stop_running(self, why: str) -> None:
        if self.running is None:
            return
        try:
            record = self.deps.pack.stop() or {}
            if record.get("id") == self.running["id"]:
                self._outcome({**record, "status": "stopped", "reason": why}, step_id=self.running.get("step_id"))
        except PackError:
            pass
        self.running = None

    def _outcome(self, record: dict, step_id: str | None = None) -> None:
        self.outcomes = (self.outcomes + [{
            "skill": record.get("skill"), "params": {k: v for k, v in (record.get("params") or {}).items()
                                                     if k != "time_limit"},
            "status": record.get("status"), "reason": record.get("reason"),
            "data": record.get("data") or {}, "seconds": record.get("seconds"), "at": self.deps.now(),
        }])[-MAX_OUTCOMES:]
        self.deps.metric({"session": str(self.session_id), "event": "skill_outcome", **self.outcomes[-1]})
        if record.get("skill") != WAIT_SKILL:
            outcome = self.outcomes[-1]
            self._experience("skill_outcome", {
                "skill": outcome["skill"], "params": outcome["params"], "status": outcome["status"],
                "reason": outcome["reason"], "data": outcome["data"], "seconds": outcome["seconds"],
                "step_id": step_id,
            })
            self._count_try(outcome, step_id)

    def _count_try(self, outcome: dict, step_id: str | None) -> None:
        log = self.step_log
        if log is None or step_id is None or log["id"] != step_id:
            return
        log["tries"] += 1
        if outcome["status"] not in FAILED:
            return
        log["failed"] += 1
        log["failures"] = (log["failures"] + [{k: outcome[k] for k in ("skill", "params", "status", "reason", "data")}]
                           )[-STEP_FAILURES_KEPT:]
        if log["failed"] in STRUGGLING_AT and self.deps.notice is not None:
            self._save()  # her summary shows the counted tries when she wakes
            self.deps.notice(self.session_id, "struggling", struggle_text(log))

    # -- notices ----------------------------------------------------------------

    def _pull_notices(self) -> None:
        deps = self.deps
        if self.notice_seq is None:
            # Start from now: what she noticed before this session isn't news.
            self.notice_seq = deps.pack.notices(0).get("next", 0)
            return
        batch = deps.pack.notices(self.notice_seq)
        fresh = batch.get("notices", [])
        if not fresh:
            return
        self.notice_seq = batch.get("next", self.notice_seq)
        self.notices = (self.notices + fresh)[-12:]
        if any(n.get("kind") == "died" for n in fresh):
            self.died_at = deps.now()
        did = [{"text": n.get("text", ""), "at": deps.now()} for n in fresh if n.get("kind") == "mode"]
        if did:
            self.modes_did = (self.modes_did + did)[-MODES_KEPT:]
            self._save()  # a follow runs for minutes between decisions: her summary shouldn't wait
        for n in fresh:
            self._experience("notice", {"kind": n.get("kind"), "urgency": n.get("urgency"),
                                        "text": n.get("text", ""), "data": n.get("data") or {}})
        if deps.notice is None:
            return
        for n in fresh:
            if n.get("urgency") == "urgent":
                deps.notice(self.session_id, "danger", n["text"])
            elif n.get("kind") in DISCOVERIES:
                deps.notice(self.session_id, "discovery", n["text"])
            elif n.get("kind") in WARNINGS and n.get("urgency") == "heads_up":
                deps.notice(self.session_id, "warning", n["text"])
            elif n.get("urgency") == "heads_up":
                deps.notice(self.session_id, "notable", n["text"])

    def _repeats(self) -> int:
        """How many of the last outcomes in a row were the same action ending the same way
        without getting anywhere. A wait, or a skill that did its job over time (a follow that
        ran its course), isn't a repeat."""
        def key(o: dict) -> tuple | None:
            if o["skill"] == WAIT_SKILL or (o["status"] == "done" and (o.get("seconds") or 0) >= 1):
                return None
            return (o["skill"], json.dumps(o["params"], sort_keys=True), o["status"], o.get("reason"))

        last = key(self.outcomes[-1]) if self.outcomes else None
        if last is None:
            return 0
        n = 0
        for o in reversed(self.outcomes):
            if key(o) != last:
                break
            n += 1
        return n

    def _urgent(self) -> bool:
        """Something urgent noticed since the last decision (the list is cleared at each decision)."""
        return any(n.get("urgency") == "urgent" for n in self.notices)

    # -- the prompt ---------------------------------------------------------------

    def prompt_text(self, view: DriverView, current: dict, state: dict, messages: list[dict]) -> str:
        now = self.deps.now()
        plan = view.plan or {}
        lines = [f"Goal: {plan.get('goal', '')}"]
        for st in plan.get("steps", []):
            mark = {"done": "[done]", "active": "[now]", "pending": "[next]"}.get(st["state"], "")
            if st["id"] == current["id"]:
                mark = "[now]"
            lines.append(f"  {mark} {st['id']}: {st['do']} (done when: {st['done_when']})")
        if plan.get("constraints"):
            lines.append("Rules for this goal (keep them the whole time): " + "; ".join(plan["constraints"]))
        if plan.get("if_stuck"):
            lines.append(f"If stuck: {plan['if_stuck']}")
        if self.step_started:
            lines.append(f"Time on this step: {int(now - self.step_started[1])} s.")
        if self.step_inventory is not None:
            lines.append(f"Inventory change since this step began (counted): "
                         f"{inventory_change(self.step_inventory, inventory_counts(state))}.")
        if self.running is not None and self.running.get("carried"):
            params = ", ".join(f"{k}={v}" for k, v in (self.running.get("params") or {}).items() if k != "time_limit")
            lines.append(f"Still running from before the plan changed: {self.running['skill']}({params}), for "
                         f"{int(now - self.running['started'])} s. If it fits the current step, choose it again "
                         "with the same target and it carries on without a break; anything else stops it.")
        if messages:
            lines.append("Columbina's messages to you (follow them): "
                         + "; ".join(f'"{m["text"]}"' for m in messages))
        lines.append("")
        lines.append("Your state:")
        lines.append(state.get("text", ""))
        if self.outcomes:
            lines.append("")
            lines.append("Last outcomes (oldest first):")
            for o in self.outcomes:
                params = ", ".join(f"{k}={v}" for k, v in o["params"].items())
                reason = f": {o['reason']}" if o.get("reason") else ""
                data = json.dumps(o["data"], separators=(",", ":")) if o["data"] else ""
                lines.append(f"- {o['skill']}({params}) -> {o['status']}{reason} {data} "
                             f"({int(now - o['at'])} s ago)")
        repeats = self._repeats()
        if repeats >= REPEAT_WARN:
            last = self.outcomes[-1]
            lines.append(f"You chose {last['skill']} the same way {repeats} times in a row and it ended the same "
                         f"way each time ({last['status']}): it isn't working. Choose something different.")
        if self.died_at is not None and now - self.died_at < DEATH_GRACE_SECONDS:
            lines.append(f"You died {int(now - self.died_at)} s ago and respawned: carry on with the step.")
        if self.notices:
            lines.append("")
            lines.append("Noticed since your last decision: "
                         + "; ".join(f"[{n['urgency']}] {n['text']}" for n in self.notices))
        lines.append("")
        lines.append(f"Current step id: {current['id']}. Choose the next action.")
        return "\n".join(lines)

    # -- game learning ----------------------------------------------------------

    def _experience(self, kind: str, payload: dict) -> None:
        """Record an experience. Learning never stops play: failures are only logged."""
        if self.deps.experience is None:
            return
        try:
            self.deps.experience(self.session_id, kind, payload)
        except Exception as exc:  # noqa: BLE001
            self.deps.metric({"session": str(self.session_id), "event": "experience_error", "error": str(exc)[:200]})

    def _load_lessons(self) -> list[dict]:
        if self.deps.lessons is None:
            return []
        try:
            return self.deps.lessons(self.session_id)
        except Exception as exc:  # noqa: BLE001
            self.deps.metric({"session": str(self.session_id), "event": "lessons_error", "error": str(exc)[:200]})
            return []

    # -- helpers ----------------------------------------------------------------

    def _between_goals(self, view: DriverView) -> bool:
        """Every step is done: wait for Columbina's next goal when playing on
        her own, otherwise pause (plan complete)."""
        deps = self.deps
        picked = deps.auto_goals(self.session_id) if deps.auto_goals else 0
        if view.autonomy != "continue" or picked >= self.max_auto_goals:
            deps.pause(self.session_id, "plan_complete")
            self._save()
            return False
        now = deps.now()
        if self.planning_since is None:
            self.planning_since = now
            self._save(waiting="next_goal")
        elif now - self.planning_since > self.planning_wait_seconds:
            deps.pause(self.session_id, "planning_timeout")
            self._save()
            return False
        deps.sleep(0.25)
        return True

    def _pack_down(self, exc: PackError) -> bool:
        now = self.deps.now()
        self.pack_down_since = self.pack_down_since or now
        self.deps.metric({"session": str(self.session_id), "event": "pack_error", "error": str(exc)[:200]})
        if now - self.pack_down_since > PACK_DOWN_SECONDS:
            self.deps.pause(self.session_id, "pack_offline")
            self._save()
            return False
        self.deps.sleep(1.0)
        return True

    def _save(self, **extra) -> None:
        self.deps.save_state(self.session_id, {
            "kind": "skills",
            "notes": self.note,
            "running": ({"skill": self.running["skill"], "params": self.running["params"]}
                        if self.running else None),
            "outcomes": self.outcomes[-5:],
            "notices": self.notices[-5:],
            "decisions": self.decisions,
            "step": self.step_log,
            "modes_did": self.modes_did,
            **extra,
        })
