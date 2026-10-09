"""Her hands in a game pack (stage G, phase 3): pick one skill at a time.

The key-pressing driver (``driver_loop``) decides every ~2 s from a picture.
With a game pack the game does the fast part: a skill runs at 20 Hz until it
ends with an outcome, and the model only decides what to do next, from her
state as text, the outcomes so far, what she noticed, and Columbina's plan and
messages. One ``SkillDriverRun`` per playing session, in the gameplay worker.

Each ``step``:

1. reads the session (must be playing, with a plan step left);
2. if her skill is running: waits briefly for it, stops it if the session
   stopped playing or the plan changed, or if something urgent was noticed
   (a creeper behind her while she mines) so the next decision can react;
3. otherwise asks the model for one decision: a skill with parameters (or a
   short wait), and the step's status;
4. drops the answer if the session or plan changed or Columbina sent a message
   while the model was thinking;
5. starts the skill in the pack and records progress.

Notices from the pack become Columbina's game events (urgent ones as danger,
discoveries like a village always, the rest only in commentary mode). Like the
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


# --------------------------------------------------------------------------- #
# Schema and prompt, generated from the pack's skill manifest
# --------------------------------------------------------------------------- #

_JSON_TYPES = {"string": "string", "enum": "string", "number": "number", "integer": "integer"}


def _param_schema(rule: dict) -> dict:
    schema: dict = {"type": _JSON_TYPES[rule["type"]], "description": rule.get("description", "")}
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
        f"{name}{'' if rule.get('required') else '?'}: {rule.get('description', rule['type'])}"
        for name, rule in skill["params"].items() if name != "time_limit"
    )
    return (f"- {skill['name']}({params}): {skill['description']} Done when {skill['done_when']}. "
            f"Stops when {skill['stops_when']}.")


def system_prompt(manifest: list[dict], game: str = "Minecraft") -> str:
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
- Columbina's messages to you are how to play: follow them.
- If you truly can't make progress (tried several different ways), answer status "stuck" with a note saying \
why. If you need the user (e.g. they must give you something or open the world), answer "needs_human".
- note: a few plain words about what you're doing, e.g. "chopping the oak to my left for planks".
- params: use null for anything optional you don't need; names are Minecraft ids (oak_log, crafting_table)."""


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
    return skill, params


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
    planning_since: float | None = None
    step_started: tuple[str, float] | None = None
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
        try:
            self._pull_notices()
            if self.manifest is None:
                self.manifest = deps.pack.manifest()
                self._schema = skill_schema(self.manifest)
                self._system = system_prompt(self.manifest)
            self.pack_down_since = None
        except PackError as exc:
            return self._pack_down(exc)

        if self.running is not None:
            return self._watch_running(view)
        if self.waiting_until is not None:
            if deps.now() < self.waiting_until and not self._urgent():
                deps.sleep(POLL_SECONDS)
                return True
            self.waiting_until = None

        current = view.current_step()
        if current is None:
            return self._between_goals(view)
        self.planning_since = None
        if self.step_started is None or self.step_started[0] != current["id"]:
            self.step_started = (current["id"], deps.now())

        try:
            state = deps.pack.state()
        except PackError as exc:
            return self._pack_down(exc)
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

        if status != "working" or current["state"] == "pending":
            deps.progress(self.session_id, plan_revision=view.plan_revision, step_id=current["id"],
                          status=status, note=self.note)
        if status in ("stuck", "needs_human"):
            self._save()
            return False
        if action is not None:
            self._start(view, current, *action)
        if self.decisions >= self.max_decisions:
            deps.pause(self.session_id, "budget")
            self._save()
            return False
        self._save()
        return True

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
                self._outcome({"skill": skill, "params": params, "status": "refused", "reason": str(exc)})
                if self.refused >= MAX_REFUSED:
                    deps.progress(self.session_id, plan_revision=view.plan_revision, step_id=current["id"],
                                  status="stuck", note=f"{MAX_REFUSED} decisions in a row refused: {exc}")
                return
            raise
        self.refused = 0
        self.running = {"id": record["id"], "skill": skill, "params": record.get("params", params),
                        "plan_revision": view.plan_revision, "generation": view.execution_generation,
                        "started": deps.now()}

    def _watch_running(self, view: DriverView) -> bool:
        deps = self.deps
        running = self.running
        if view.plan_revision != running["plan_revision"] or view.execution_generation != running["generation"]:
            self._stop_running("the plan changed")
            return True
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
        self._outcome(record)
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
                self._outcome({**record, "status": "stopped", "reason": why})
        except PackError:
            pass
        self.running = None

    def _outcome(self, record: dict) -> None:
        self.outcomes = (self.outcomes + [{
            "skill": record.get("skill"), "params": {k: v for k, v in (record.get("params") or {}).items()
                                                     if k != "time_limit"},
            "status": record.get("status"), "reason": record.get("reason"),
            "data": record.get("data") or {}, "seconds": record.get("seconds"), "at": self.deps.now(),
        }])[-MAX_OUTCOMES:]
        self.deps.metric({"session": str(self.session_id), "event": "skill_outcome", **self.outcomes[-1]})

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
        if deps.notice is None:
            return
        for n in fresh:
            if n.get("urgency") == "urgent":
                deps.notice(self.session_id, "danger", n["text"])
            elif n.get("kind") in DISCOVERIES:
                deps.notice(self.session_id, "discovery", n["text"])
            elif n.get("urgency") == "heads_up":
                deps.notice(self.session_id, "notable", n["text"])

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
        if self.step_started:
            lines.append(f"Time on this step: {int(now - self.step_started[1])} s.")
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
        if self.notices:
            lines.append("")
            lines.append("Noticed since your last decision: "
                         + "; ".join(f"[{n['urgency']}] {n['text']}" for n in self.notices))
        lines.append("")
        lines.append(f"Current step id: {current['id']}. Choose the next action.")
        return "\n".join(lines)

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
            **extra,
        })
