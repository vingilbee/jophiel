"""Her learner in a game pack (stage H1): she writes a program for a move she
doesn't have, runs it, checks it, and fixes it.

Her hands (``skill_driver``) choose one skill at a time; when the move she
needs isn't a skill ("no skill for: crossing the river") she can learn one,
only after the user said yes (``services/game_practice``). One attempt here:

1. waits until her body is free (her hands hold while she learns; a skill of
   theirs still running is stopped; her breathing reflex is left to finish);
2. asks the model for a program: the body of an async function over the pack's
   skills (``await skills.mine({...})``), single actions (``await act.attack(id)``,
   ``act.dig``/``place``/``look``/``equip``/``jump``: what a move no skill has is
   built from), ``state()``, ``log()`` and ``wait()``, run in the pack's sandbox
   (game_packs/minecraft/src/sandbox.js), which can reach nothing else;
3. runs it in the world and waits for it, checking every few seconds that the
   session still plays and nobody asked her to stop;
4. counts what changed, by code (inventory, blocks around where the try began,
   how far she moved, health, and what each skill itself reported), and asks
   a critic whether that did the task: the facts are counted, the verdict is the
   model's;
5. on a miss, the program's errors, each skill's outcome and the critic's reason
   go into the next try, up to ``max_tries``.

Getting hurt or being stopped ends the attempt; a program that errors or misses
is just a failed try. A program that finds the task can't be done even with
single actions says so (``impossible``, ``missing``); said by two tries in a row
it ends the attempt, instead of spending the rest of the tries saying it again
(seen live: "attack a rabbit", tries 3 and 4). Once isn't enough: in a trial she
called climbing out of a stone shaft impossible ("no blocks") on try 1, though
digging the stone gives her the blocks. Every try is reported (``save``) as it ends, so the user
can read what she wrote even if the attempt is cut short.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.desktop.driver import DriverModel, DriverModelError
from app.ai.desktop.pack_client import PackError
from app.ai.desktop.skill_driver import _limits, inventory_change, inventory_counts

MAX_TRIES = 4
MAX_MODEL_ERRORS = 2  # the writer failing this many times in a row ends the attempt (a config error repeats)
PROGRAM_SECONDS = 180
MAX_CODE_CHARS = 8000
POLL_SECONDS = 5.0
IDLE_WAIT_SECONDS = 20.0
# What a try keeps of a program's result, for the next try's prompt and the record.
CALLS_SHOWN = 12
LOG_SHOWN = 12
CENSUS_RADIUS = 10  # blocks counted around where a try began, before and after: what she built or broke


WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string", "description": "one or two sentences: how the program does the task"},
        "code": {"type": "string", "description": "the body of an async function (JavaScript)"},
    },
    "required": ["approach", "code"],
    "additionalProperties": False,
}

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "reason": {"type": "string", "description": "one or two sentences, citing the counted facts"},
    },
    "required": ["success", "reason"],
    "additionalProperties": False,
}


def skill_manual(manifest: list[dict]) -> str:
    """Her skills as functions her code can call."""
    lines = []
    for skill in manifest:
        params = ", ".join(
            f"{name}{'' if rule.get('required') else '?'}: {rule.get('description', rule['type'])}{_limits(rule)}"
            + (f" [one of {', '.join(map(str, rule['values']))}]" if rule.get("values") else "")
            for name, rule in skill["params"].items()
        )
        lines.append(f"- skills.{skill['name']}({{ {params} }}): {skill['description']} "
                     f"Done when {skill['done_when']}. Stops when {skill['stops_when']}.")
    return "\n".join(lines)


def writer_prompt(manifest: list[dict], game: str = "Minecraft") -> str:
    return f"""You are Columbina, playing {game} as your own player. You can't do something yet with the \
skills you have, so you are teaching yourself: write a short program that does the task, out of your skills.

The program is the body of an async JavaScript function. It runs in a sealed sandbox that has exactly these:
- await skills.<name>({{...params}}): runs one of your skills and resolves with its outcome \
{{ status, reason, data }} when it ends. status is one of done, failed, lost, blocked, timeout \
(the skill gave up; you may try something else), or invalid (bad params: reason says why; nothing ran). \
If a skill ends hurt or stopped, the whole program ends at once.
- state(): what you see right now, as an object: position {{x, y, z}} (your feet), facing {{compass}}, \
standing_on (block name), on_ground, in_water, health, food, oxygen, held {{name, count}}, hotbar \
[{{slot (1-9), name, count}}], hotbar_selected (the slot in your hand), off_hand, inventory [{{name, count}}], armor, nearby [{{id, label, kind, distance, side, dy, position {{x, y, z}}}}] (players and mobs: \
label is the mob ("rabbit", "zombie") or the player's name; kind is the game's type: "player", "hostile", \
"animal", "passive", "water_creature", "ambient", "projectile" or "other", so find a mob by its label), blocks [{{kind, name, distance, side, dy}}] (the nearest log, ore, \
water, lava, crafting table, furnace, bed, chest), players [{{name, holding, health, moving, pose}}], time {{phase}}, \
weather.
- blockAt(x, y, z): the block at a spot, {{ name, solid, x, y, z }} (null where the world isn't loaded). \
Blocks sit at whole-number coordinates: the block your feet are in is (Math.floor(x), Math.floor(y), \
Math.floor(z)) of your position, and the ground under you is one lower. You are 2 blocks tall: your feet's \
block and your head's (y + 1); the first block over your head is y + 2.
- findBlocks(name, {{ radius, count }}): the nearest blocks of that kind, [{{ x, y, z, distance }}], nearest \
first (radius up to 32, count up to 64; a name like "_log" matches every kind of log).
- act.<name>(...): single actions, what any move is made of. Each does one thing at once and resolves with \
{{ status: "done" | "failed" | "busy", reason, data }}:
  - act.attack(id): one swing at the entity with that id (from state().nearby), if it's within 3 m of your eyes; \
data.cooldown_ms is how long to wait before the next swing hits fully. A fight is a loop: get close, swing, wait, \
check it's still there.
  - act.dig(x, y, z): break that one block (within 4.5 m) with the best tool you carry. What you break drops \
and you pick it up when it's close (stone gives cobblestone): dig waits a moment for it (data.picked_up), so what \
you dig you can place.
  - act.place(x, y, z, item?): put the held block (or item, equipped first) there, against a solid block beside it.
  - act.look(x, y, z): face that point (a block's middle is +0.5 on each axis).
  - act.equip(item, slot?): hold an item, or wear it (slot hand, off-hand, head, torso, legs, feet).
  - act.jump(): one jump from the ground; it resolves once your feet are a block up (data.rose: how high), and you \
fall back right after. To climb a block: note your feet's block (x, y, z), act.look at the block under it, act.jump(), \
then at once act.place(x, y, z, "<a block you carry>"): you land on it, one block higher. A full jump needs the \
2 blocks over your head (y + 2 and y + 3) clear: dig them first.
  Up to 400 actions per program; never while a skill is still running (await it first).
- log(...values): a note kept with the result (use it to see what happened).
- wait(ms): pause, at most 30000.
Nothing else exists: no require, import, fetch, timers, process or files. Skills and actions run one at a time; \
await each.

Your skills:
{skill_manual(manifest)}

Rules:
- Check every outcome's status and handle failure (try another way, or stop with a clear return value). Never \
assume a skill worked.
- Look before you act: read state(), and use blockAt/findBlocks to find out where things are (water, a gap, \
the ground) instead of guessing coordinates. Use the real positions and names they give.
- When no skill does it, build it from single actions: that's what they're for. A skill refusing is not the end.
- act.attack swings with whatever is in your hand: hold your best weapon first (act.equip a sword, else an axe, \
from state().inventory). skills.fight takes any mob by name (count, 1 by default) with her best weapon.
- Keep it short: under 80 lines, at most 40 skill calls, no endless loops (bound every loop).
- Return a small object that says what you did and whether it worked, e.g. {{ done: true, crossed: 12 }}.
- If the task can't be done at all with your skills and single actions (not "not now": a mob that wandered off \
or an item you lack is just a miss), return {{ done: false, impossible: true, missing: "<the action or ability \
you'd need>" }}. Said twice in a row, that ends the learning, so be sure.
- Write only the function body: no function declaration, no markdown fences.
- The user is playing next to you. Hit only what the task is about, and don't break blocks you didn't place unless \
the task needs it.
"""


CRITIC_PROMPT = """You check whether a program Columbina wrote did its task in a game. You get the task, what \
the program returned, each skill it called with its outcome and what the skill itself reported, the single \
actions it took (swings, blocks dug or placed) with their outcomes, its log, and facts counted by code from the game \
before and after (inventory changes, blocks around where the try began, who and what was nearby, distance moved, \
health, how long she was in water while it ran). Judge from the counted facts and the skills' own reports: the program's return value \
and log are its claims, not proof. A block placed shows as +1 of it in the blocks counted. Success means the \
task is actually done in the world. If it isn't, say in one or two sentences what went wrong, so the next try \
can fix it."""


TIDY_SCHEMA = {
    "type": "object",
    "properties": {
        "changed": {"type": "boolean", "description": "false if it's already tidy: then code is ignored"},
        "code": {"type": "string", "description": "the tidied function body (JavaScript)"},
        "changes": {"type": "string", "description": "one sentence: what you changed"},
    },
    "required": ["changed", "code", "changes"],
    "additionalProperties": False,
}

TIDY_RULES = """

You have just taught yourself this task, and the program below worked. It's being kept as one of your moves, used again later in other places, sometimes several times in a row (each run should do the task once). It was written for that one moment, over several tries. Tidy it so it works anywhere, doing the same thing in the world:
- Replace anything copied from that moment (fixed coordinates, entity ids, a player's or mob's position) with look-ups: state(), findBlocks, blockAt, positions relative to where you stand. The same for items you \
happened to carry then: a block to place or a tool picked by name ("birch_planks") becomes one chosen from your \
inventory when it runs (any placeable block, the best tool you have). Names the task itself gives stay.
- Remove leftover debugging, dead branches and steps that did nothing. Keep the log() lines that say what happened.
- Keep every check of an outcome and every bound on a loop. Don't add new ideas or new ways of doing it.
- It must end with { done: true, ... } when it worked and { done: false, reason: "..." } when it didn't: a run that returns done: false stops the repeats.
- If it's already like that, say changed: false. Write only the function body: no declaration, no fences."""


@dataclass
class PracticeDeps:
    pack: object  # PackClient
    model: DriverModel  # writes the program
    critic: DriverModel  # judges it
    # A reason to stop now (the session stopped playing, the user said stop), or None.
    should_stop: Callable[[], str | None]
    # Every try so far, after each one ends, so the record is never behind.
    save: Callable[[list[dict]], None]
    metric: Callable[[dict], None] = lambda record: None
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass
class TrialSpec:
    """A bounded experiment's execution (services/learning_trials.py): one approach chosen beforehand, a
    destination code checks, and what is left of its budget when this run begins. Success is counted by code
    (she stands at the destination), never judged by the critic. ``ready``: why she isn't ready, read off the
    state right before the test, or None."""

    approach: str
    destination: dict
    radius: float
    attempts: int
    seconds: float
    blocks: int
    ready: Callable[[dict], str | None] = lambda state: None


@dataclass
class PracticeResult:
    status: str  # learned | failed | stopped
    reason: str
    code: str | None = None
    tries: list[dict] = field(default_factory=list)
    # A trial's run only: how it ended (trials.ENDINGS), the state when the test began, its seconds, and the
    # other way the writer wanted to take (which needs its own prediction first).
    ending: str | None = None
    start_state: dict | None = None
    seconds: float | None = None
    other_approach: str | None = None


class _Stopped(Exception):
    pass


@dataclass
class PracticeRun:
    task: str
    deps: PracticeDeps
    max_tries: int = MAX_TRIES
    program_seconds: float = PROGRAM_SECONDS
    game: str = "Minecraft"
    trial: TrialSpec | None = None
    tries: list[dict] = field(default_factory=list)
    model_errors: int = 0
    first_state: dict | None = None  # when the attempt began: tries can add up ("1 log, then 2 more")
    started_at: float | None = None  # a trial: when its test began
    placed: int = 0  # a trial: blocks its programs placed, as the pack counted them

    def run(self) -> PracticeResult:
        result = self._run()
        if self.trial is not None:
            if result.ending is None:
                result.ending = "completed" if result.status in ("learned", "failed") else "stopped"
            result.start_state = self.first_state
            if self.started_at is not None:
                result.seconds = round(self.deps.now() - self.started_at, 1)
        return result

    def _run(self) -> PracticeResult:
        deps = self.deps
        trial = self.trial
        try:
            manifest = deps.pack.manifest()
        except PackError as exc:
            return PracticeResult("failed", f"the game isn't reachable: {exc}", tries=self.tries,
                                  ending="not_started" if trial else None)
        system = writer_prompt(manifest, self.game)
        if trial is not None:
            from app.ai.learning.trials import WRITER_RULES

            d = trial.destination
            system += WRITER_RULES.format(approach=trial.approach, x=d["x"], y=d["y"], z=d["z"], radius=trial.radius,
                                          blocks=trial.blocks, attempts=trial.attempts)
        max_tries = min(self.max_tries, trial.attempts) if trial is not None else self.max_tries
        try:
            for number in range(1, max_tries + 1):
                self._check()
                self._wait_until_free()
                if trial is not None:
                    ended = self._trial_gate(number)
                    if ended is not None:
                        return ended
                result = self._try(number, system)
                if result is not None:
                    return result
                if self.model_errors >= MAX_MODEL_ERRORS:
                    return PracticeResult("failed", f"couldn't think it through: {self.tries[-1]['verdict']['reason']}",
                                          tries=self.tries, ending="failed" if trial else None)
        except _Stopped as exc:
            return PracticeResult("stopped", str(exc), tries=self.tries,
                                  ending=("not_started" if trial and self.started_at is None else "stopped")
                                  if trial else None)
        except PackError as exc:
            return PracticeResult("failed", f"the game stopped answering: {exc}", tries=self.tries,
                                  ending="interrupted" if trial else None)
        last = self.tries[-1]["verdict"]["reason"] if self.tries else "no try ran"
        return PracticeResult("failed", f"didn't work after {len(self.tries)} tries; last: {last}", tries=self.tries,
                              ending="completed" if trial else None)

    def _trial_gate(self, number: int) -> PracticeResult | None:
        """Before each try of an experiment: on the first, check it's really ready (read off the game now);
        on any, end it if its time or blocks are spent."""
        trial, deps = self.trial, self.deps
        if self.started_at is None:
            state = deps.pack.state()
            why = trial.ready(state)
            if why:
                return PracticeResult("stopped", f"not ready: {why}", tries=self.tries, ending="not_started")
            self.started_at = deps.now()
            self.first_state = state
            return None
        if deps.now() - self.started_at >= trial.seconds:
            return PracticeResult("failed", f"the time for this experiment ran out after {number - 1} tries",
                                  tries=self.tries, ending="limit")
        if trial.blocks > 0 and self.placed >= trial.blocks:
            return PracticeResult("failed", f"the blocks for this experiment are used up ({self.placed} placed)",
                                  tries=self.tries, ending="limit")
        return None

    # -- one try ---------------------------------------------------------------

    def _try(self, number: int, system: str) -> PracticeResult | None:
        deps = self.deps
        before = deps.pack.state()
        if self.first_state is None:
            self.first_state = before
        census_before = self._census(before)
        schema = WRITER_SCHEMA
        if self.trial is not None:
            from app.ai.learning.trials import TRIAL_WRITER_SCHEMA

            schema = TRIAL_WRITER_SCHEMA
        try:
            reply = deps.model.step(system, self._writer_text(number, before), [], schema)
        except DriverModelError as exc:
            self.model_errors += 1
            self._record({"n": number, "approach": None, "code": None, "program": None, "facts": None,
                          "verdict": {"success": False, "reason": f"couldn't write a program: {exc}"}})
            return None
        self.model_errors = 0
        code = str(reply.output.get("code", "")).strip()[:MAX_CODE_CHARS]
        approach = str(reply.output.get("approach", ""))[:400]
        deps.metric({"event": "practice_written", "task": self.task, "try": number, "model_ms": reply.latency_ms,
                     "chars": len(code)})
        entry = {"n": number, "approach": approach, "code": code, "program": None, "facts": None, "verdict": None}
        if self.trial is not None and reply.output.get("keeps_approach") is False:
            other = str(reply.output.get("other_approach") or approach)[:400]
            entry["code"] = None
            entry["verdict"] = {"success": False, "reason": f"wanted another way, which needs its own prediction: {other}"}
            self._record(entry)
            return PracticeResult("stopped", f"changed approach: {other}", tries=self.tries, ending="approach_changed",
                                  other_approach=other)
        if not code:
            entry["verdict"] = {"success": False, "reason": "the program was empty"}
            self._record(entry)
            return None

        program = self._run_program(code)
        entry["program"] = _short_program(program)
        after = deps.pack.state()
        entry["facts"] = counted_facts(before, after, first=self.first_state)
        if isinstance(program.get("body"), dict):  # counted by the pack while the program ran
            entry["facts"]["in_water_seconds"] = program["body"].get("in_water_seconds")
            entry["facts"]["lowest_health"] = program["body"].get("lowest_health")
        if census_before is not None:
            census_after = self._census(before)  # the same spot: where this try began
            if census_after is not None:
                entry["facts"]["blocks_around_start"] = census_change(census_before, census_after)
        status = program.get("status")
        if self.trial is not None:
            return self._trial_verdict(entry, program, after)
        if status == "stopped":
            entry["verdict"] = {"success": False, "reason": f"stopped: {program.get('reason')}"}
            self._record(entry)
            raise _Stopped(program.get("reason") or "stopped")
        if status == "hurt":
            entry["verdict"] = {"success": False, "reason": f"got hurt: {program.get('reason')}"}
            self._record(entry)
            return PracticeResult("failed", f"got hurt trying ({program.get('reason')})", tries=self.tries)
        if status == "error":
            entry["verdict"] = {"success": False, "reason": f"the program broke: {program.get('reason')}"}
            self._record(entry)
            return None
        value = program.get("value")
        if status == "done" and isinstance(value, dict) and value.get("impossible") is True:
            missing = str(value.get("missing") or "something no skill or action does")[:200]
            again = bool(self.tries) and bool(self.tries[-1].get("impossible"))
            entry["impossible"] = True
            if again:
                entry["verdict"] = {"success": False, "reason": f"can't be done with what I have: missing {missing}"}
                self._record(entry)
                return PracticeResult("failed", f"can't be done with my skills and single actions (missing {missing})",
                                      tries=self.tries)
            entry["verdict"] = {"success": False, "reason": (
                f"you said it can't be done (missing {missing}). Be sure: think again about what your skills and "
                "single actions can do together, and what you carry or could get on the way. If it truly can't be "
                "done, say so again.")}
            self._record(entry)
            return None
        try:
            judged = deps.critic.step(CRITIC_PROMPT, self._critic_text(entry), [], CRITIC_SCHEMA).output
            verdict = {"success": bool(judged.get("success")), "reason": str(judged.get("reason", ""))[:400]}
        except DriverModelError as exc:
            verdict = {"success": False, "reason": f"couldn't check it: {exc}"}
        entry["verdict"] = verdict
        self._record(entry)
        if verdict["success"]:
            return PracticeResult("learned", verdict["reason"], code=code, tries=self.tries)
        return None

    def _trial_verdict(self, entry: dict, program: dict, after: dict) -> PracticeResult | None:
        """An experiment's try, judged by code: does she stand at the destination? The program's own claims
        and the critic don't decide it."""
        from app.ai.learning.trials import at_destination

        trial = self.trial
        status = program.get("status")
        placed = program.get("placed")
        if isinstance(placed, int):
            self.placed += placed
        there = at_destination(after.get("position"), trial.destination, trial.radius)
        where = (entry.get("facts") or {}).get("to")
        if status == "stopped":
            entry["verdict"] = {"success": bool(there), "reason": f"stopped: {program.get('reason')}"}
            self._record(entry)
            raise _Stopped(program.get("reason") or "stopped")
        if status == "hurt":
            entry["verdict"] = {"success": bool(there), "reason": f"got hurt: {program.get('reason')}"}
            self._record(entry)
            return PracticeResult("failed", f"got hurt trying ({program.get('reason')})", tries=self.tries,
                                  ending="stopped")
        reason = ("standing at the destination (counted by code)" if there else
                  f"not at the destination (feet at {where})" if there is False else "her position wasn't read")
        if status == "limit":
            reason += f"; {program.get('reason')}"
        elif status not in ("done", None):
            reason += f"; the program ended {status}: {program.get('reason')}"
        entry["verdict"] = {"success": bool(there), "reason": reason}
        self._record(entry)
        if there:
            return PracticeResult("learned", reason, code=entry["code"], tries=self.tries, ending="completed")
        if status == "limit":
            return PracticeResult("failed", f"reached the block limit: {program.get('reason')}", tries=self.tries,
                                  ending="limit")
        return None

    def _run_program(self, code: str) -> dict:
        deps = self.deps
        seconds = self.program_seconds
        extra: dict = {}
        if self.trial is not None:
            left = self.trial.seconds - (deps.now() - (self.started_at or deps.now()))
            seconds = max(10, min(seconds, int(left)))
            extra["limits"] = {"place": max(0, self.trial.blocks - self.placed)}
        try:
            started = deps.pack.start_program(code, seconds=seconds, **extra)
        except PackError as exc:
            if exc.status == 409:  # something took her body in the meantime: one more wait
                self._wait_until_free()
                started = deps.pack.start_program(code, seconds=seconds, **extra)
            else:
                return {"status": "error", "reason": str(exc)}
        program = started
        deadline = deps.now() + seconds + 30
        while program.get("status") == "running":
            reason = deps.should_stop()
            if reason:
                deps.pack.stop()  # ends the program and the skill it's running
                program = deps.pack.get_program(started["id"], wait=10)
                if program.get("status") == "running":
                    program = {**program, "status": "stopped", "reason": reason}
                break
            if deps.now() > deadline:
                deps.pack.stop()
                program = {**deps.pack.get_program(started["id"], wait=10), "status": "timeout",
                           "reason": "the program didn't end"}
                break
            program = deps.pack.get_program(started["id"], wait=POLL_SECONDS)
        if program.get("status") == "stopped" and not program.get("reason"):
            program["reason"] = "stopped"
        return program

    def _wait_until_free(self) -> None:
        """Her hands hold while she learns; a skill of theirs still running is stopped.
        Her breathing reflex finishes by itself."""
        deps = self.deps
        deadline = deps.now() + IDLE_WAIT_SECONDS
        while True:
            self._check()
            current = deps.pack.current()
            if current is None:
                return
            if current.get("source") == "driver":
                deps.pack.stop()
            if deps.now() > deadline:
                raise _Stopped(f"her body stayed busy ({current.get('skill')})")
            deps.sleep(0.5)

    def _census(self, state: dict) -> dict | None:
        p = state.get("position")
        if not isinstance(p, dict):
            return None
        try:
            return self.deps.pack.block_census(p["x"], p["y"], p["z"], CENSUS_RADIUS)
        except (PackError, KeyError, AttributeError):
            return None  # an older pack: inventory and movement only

    def _check(self) -> None:
        reason = self.deps.should_stop()
        if reason:
            raise _Stopped(reason)

    def _record(self, entry: dict) -> None:
        self.tries.append(entry)
        self.deps.save(self.tries)
        self.deps.metric({"event": "practice_try", "task": self.task, "try": entry["n"],
                          "success": (entry.get("verdict") or {}).get("success"),
                          "status": (entry.get("program") or {}).get("status")})

    # -- prompts ---------------------------------------------------------------

    def _writer_text(self, number: int, state: dict) -> str:
        lines = [f"Task: {self.task}", "", f"Try {number} of {self.max_tries}.", "",
                 "You right now (from the game):", state.get("text", "").strip()]
        for t in self.tries:
            lines += ["", f"--- Try {t['n']} ---"]
            if t.get("approach"):
                lines.append(f"Approach: {t['approach']}")
            if t.get("code"):
                lines += ["Code:", t["code"]]
            if t.get("program"):
                lines.append(program_text(t["program"]))
            if t.get("facts"):
                lines.append(f"Counted: {facts_text(t['facts'])}")
            lines.append(f"Verdict: {t['verdict']['reason']}")
        if self.tries:
            lines += ["", "Write a better program: fix what went wrong, or try another way."]
        return "\n".join(lines)

    def _critic_text(self, entry: dict) -> str:
        return "\n".join([
            f"Task: {self.task}", "", "Program:", entry["code"], "",
            program_text(entry["program"]), "", f"Counted by code: {facts_text(entry['facts'])}",
        ])


# --------------------------------------------------------------------------- #
# Counted facts and text
# --------------------------------------------------------------------------- #


def counted_facts(before: dict, after: dict, *, first: dict | None = None) -> dict:
    """What changed in the game during this try, counted by code (never a model), and her
    inventory's change since the attempt began (``first``), when that's a different picture."""
    facts: dict = {"inventory": inventory_change(inventory_counts(before), inventory_counts(after))}
    if first is not None and first is not before:
        facts["inventory_since_start"] = inventory_change(inventory_counts(first), inventory_counts(after))
    a, b = before.get("position"), after.get("position")
    if isinstance(a, dict) and isinstance(b, dict):
        try:
            facts["moved_m"] = round(math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])), 1)
            facts["from"] = [round(a["x"]), round(a["y"]), round(a["z"])]
            facts["to"] = [round(b["x"]), round(b["y"]), round(b["z"])]
        except (KeyError, TypeError):
            pass
    for key in ("health", "food"):
        if before.get(key) is not None and after.get(key) is not None:
            facts[key] = [before[key], after[key]]
    if isinstance(before.get("nearby"), list) and isinstance(after.get("nearby"), list):
        facts["nearby"] = census_change({"counts": _nearby_counts(before)}, {"counts": _nearby_counts(after)})
    return facts


def _nearby_counts(state: dict) -> dict[str, int]:
    """Who and what she sees around her, by label ("rabbit": 2), dropped items left out."""
    counts: dict[str, int] = {}
    for entity in state.get("nearby") or []:
        label = entity.get("label") if isinstance(entity, dict) else None
        if isinstance(label, str) and not label.endswith("(dropped)"):
            counts[label] = counts.get(label, 0) + 1
    return counts


def census_change(before: dict, after: dict) -> str:
    """'crafting_table 0 -> 1 (+1); dirt 4410 -> 4409 (-1)', or 'none'."""
    a, b = before.get("counts") or {}, after.get("counts") or {}
    changes = [f"{name} {a.get(name, 0)} -> {b.get(name, 0)} ({b.get(name, 0) - a.get(name, 0):+d})"
               for name in sorted(set(a) | set(b)) if a.get(name, 0) != b.get(name, 0)]
    return "; ".join(changes) if changes else "none"


def facts_text(facts: dict) -> str:
    parts = [f"inventory this try: {facts.get('inventory', 'unknown')}"]
    if "inventory_since_start" in facts:
        parts.append(f"inventory since you started learning this: {facts['inventory_since_start']}")
    if "blocks_around_start" in facts:
        parts.append(f"blocks within {CENSUS_RADIUS} of where the try began: {facts['blocks_around_start']}")
    if "nearby" in facts:
        parts.append(f"around you (players and mobs in sight): {facts['nearby']}")
    if "moved_m" in facts:
        parts.append(f"moved {facts['moved_m']} m from {facts['from']} to {facts['to']}")
    for key in ("health", "food"):
        if key in facts:
            parts.append(f"{key} {facts[key][0]:g} -> {facts[key][1]:g}")
    if facts.get("in_water_seconds") is not None:
        parts.append(f"in water for {facts['in_water_seconds']:g} s while it ran")
    if facts.get("lowest_health") is not None:
        parts.append(f"lowest health while it ran {facts['lowest_health']:g}")
    return "; ".join(parts)


def _short_program(program: dict) -> dict:
    return {
        "status": program.get("status"), "reason": program.get("reason"), "value": program.get("value"),
        "calls": [{**{k: c.get(k) for k in ("skill", "params", "status", "reason")}, "data": _short_data(c.get("data"))}
                  for c in
                  (program.get("calls") or [])[-CALLS_SHOWN:]],
        "calls_total": len(program.get("calls") or []),
        "actions": _short_actions(program.get("actions")),
        "placed": program.get("placed") if isinstance(program.get("placed"), int) else None,
        "log": (program.get("log") or [])[-LOG_SHOWN:],
        "seconds": program.get("seconds"),
    }


def _short_actions(actions) -> dict | None:
    """Her single actions, counted by the pack: how many of each, and the last few."""
    if not isinstance(actions, dict) or not actions.get("total"):
        return None
    return {"total": actions.get("total"), "by": actions.get("by") or {},
            **({"done": actions["done"]} if isinstance(actions.get("done"), dict) else {}),
            "last": [{k: a.get(k) for k in ("action", "args", "status", "reason")} for a in (actions.get("last") or [])][-8:]}


def _short_data(data) -> dict | None:
    """What a skill reported about its run (from the pack's runner, not her code), kept short."""
    if not isinstance(data, dict) or not data:
        return None
    out = {}
    for key, value in data.items():
        if key == "interruptions":
            continue
        text = value if isinstance(value, (int, float, bool)) or value is None else str(value)[:80]
        out[key] = text
        if len(out) >= 8:
            break
    return out or None


def program_text(program: dict) -> str:
    lines = [f"Program ended: {program.get('status')}"
             + (f" ({program['reason']})" if program.get("reason") else "")
             + (f" after {program['seconds']} s" if program.get("seconds") is not None else "")]
    if program.get("value") is not None:
        lines.append(f"It returned: {program['value']}")
    calls = program.get("calls") or []
    if calls:
        shown = f" (last {len(calls)} of {program.get('calls_total')})" if program.get("calls_total", 0) > len(calls) else ""
        lines.append(f"Skill calls{shown}:")
        lines += [f"  {c['skill']}({c.get('params')}) -> {c['status']}" + (f": {c['reason']}" if c.get("reason") else "")
                  + (f" [the skill reported: {c['data']}]" if c.get("data") else "") for c in calls]
    else:
        lines.append("No skill calls.")
    actions = program.get("actions")
    if actions:
        counts = ", ".join(f"{name} x{n}" for name, n in actions.get("by", {}).items())
        lines.append(f"Single actions: {actions.get('total')} ({counts}); the last:")
        lines += [f"  act.{a['action']}({', '.join(map(str, a.get('args') or []))}) -> {a['status']}"
                  + (f": {a['reason']}" if a.get("reason") else "") for a in actions.get("last") or []]
    if program.get("log"):
        lines.append("Log: " + " | ".join(program["log"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tidying a move that worked
# --------------------------------------------------------------------------- #


@dataclass
class Tidied:
    code: str
    changes: str


def tidy(model: DriverModel, manifest: list[dict], task: str, code: str, last_try: dict | None = None,
         game: str = "Minecraft") -> Tidied | None:
    """A cleaner copy of a program that worked (fixed positions and ids looked up instead,
    leftovers gone), or None when it's already tidy or the model can't. Not run here:
    ``game_skills`` keeps the original until the tidied copy has worked once."""
    text = f"Task: {task}\n\nThe program that worked:\n{code}"
    if last_try:
        if last_try.get("facts"):
            text += f"\n\nWhat it did, counted by the game: {facts_text(last_try['facts'])}"
        if (last_try.get("verdict") or {}).get("reason"):
            text += f"\nWhy it counted as done: {last_try['verdict']['reason']}"
    reply = model.step(writer_prompt(manifest, game) + TIDY_RULES, text, [], TIDY_SCHEMA)
    tidied = str(reply.output.get("code", "")).strip()[:MAX_CODE_CHARS]
    if not reply.output.get("changed") or not tidied or tidied == code.strip():
        return None
    return Tidied(tidied, str(reply.output.get("changes", ""))[:400])
