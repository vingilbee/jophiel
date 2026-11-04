"""The driver loop: observe, decide, act, repeat (plan §8, §8.1).

One ``DriverRun`` per playing session, in the gameplay worker. Each ``step``:

1. reads the session (must be playing, with a plan step left);
2. takes the newest observation frame, which must be fresh, newer than the
   last one used, and taken under the current capture setting;
3. asks the driver model for one step output;
4. drops the answer if the session was paused/resumed or the plan replaced
   while the model was thinking, or if the frame grew too old meanwhile;
5. sends the decision to the controller (which validates it again);
6. records progress (step done / stuck / needs human), notes, facts, reports.

Pipelining comes from the loop itself: the next model call starts as soon as
a decision is sent, while that decision is still running in the game.

Everything the model writes except the decision, status and step id is
untrusted game observation: it is stored for display and for the next step's
prompt, and never changes a plan, a permission, or memory.
"""
from __future__ import annotations

import io
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image, ImageChops

from app.ai.desktop.driver import (
    DriverModel,
    DriverModelError,
    driver_schema,
    supports_pursue,
    system_prompt,
    to_step_output,
)
from app.ai.desktop.play_log import Move, PlayLog, turns_of
from app.services.desktop_preview import StoredFrame
from app.services.desktop_sessions import DriverView
from desktop_controller.profile import UNKNOWN_CONTEXT, Profile
from desktop_controller.pursuit import cut_template
from desktop_controller.protocol import Rejection
from desktop_controller.validate import validate_decision

# No usable frame for this long while playing: the session pauses.
WAIT_LIMIT_SECONDS = 10.0
MAX_MODEL_ERRORS = 3
MAX_SAME_REJECTIONS = 3
MAX_REPORTS = 8
MAX_FACTS = 40
# A decision that changed less than this share of the screen "did nothing".
STILL_CHANGE = 0.02
# After this many still decisions in a row, the prompt says so plainly.
STILL_WARN_AFTER = 3
# After a decision that *does* something discrete (a tap, click, turn), the
# next decision waits for a frame received at least this long after it was
# sent, so a toggle isn't judged on a picture from before it happened (the
# inventory flip-flop seen in the guest). Pure holds keep pipelining.
SETTLE_SECONDS = 0.35
# After the VM stopped a move itself (a reflex or the move's own stop rule),
# the next decision waits for a picture from after the stop.
AFTER_STOP_SECONDS = 0.1
# Reporting danger turns careful mode on for this long.
DANGER_CAREFUL_SECONDS = 30.0
# Recent model latencies kept for the Timing line.
LATENCY_KEEP = 5
# This many moves in a row, over at least this long, without the game computer
# confirming any of them: the controls aren't arriving. Pause and say so
# instead of playing blind (a reused move id once made the VM drop every move).
SILENT_MOVES = 6
SILENT_SECONDS = 15.0
FINAL_STATES = frozenset({"completed", "expired", "superseded", "cancelled", "rejected"})
DEFAULT_MOVE_BUTTONS = ("forward", "back", "left", "right")
# With observation.frames >= 2, the model also gets the frame from this long before the latest.
EARLIER_FRAME_SECONDS = 1.0
# A pursue runs itself: at least this long, at most this (it ends early when the target is lost).
PURSUE_MIN_MS = 4000
PURSUE_MAX_MS = 6000
REFLEX_TEXT = {
    "status_changed": "your health/status bar changed (hurt?)",
    "jolt": "the whole view jumped while you weren't turning (a fall or a hit?)",
    "blocked": "you were walking but the view didn't move (blocked)",
}


@dataclass
class DriverDeps:
    view: Callable[[uuid.UUID], DriverView | None]
    progress: Callable[..., bool]
    pause: Callable[[uuid.UUID, str], None]
    observation: Callable[[uuid.UUID], StoredFrame | None]
    publish: Callable[[uuid.UUID, uuid.UUID, str, int, dict], None]
    outcomes: Callable[[uuid.UUID], list[dict]]
    save_state: Callable[[uuid.UUID, dict], None]
    metric: Callable[[dict], None]
    profile: Callable[[str], Profile]
    model: DriverModel
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    # A report worth waking Columbina for (danger; notable in commentary mode).
    notice: Callable[[uuid.UUID, str, str], None] | None = None
    # Goals Columbina already picked herself this session (playing on her own).
    auto_goals: Callable[[uuid.UUID], int] | None = None
    # Columbina's messages to her hands, oldest first: [{"id", "text", "at"}].
    messages: Callable[[uuid.UUID], list[dict]] | None = None
    # Careful mode as Columbina set it: {"on": True, "at"} or None.
    careful: Callable[[uuid.UUID], dict | None] | None = None
    # An earlier observation frame: (environment, time) -> the frame showing that moment.
    history: Callable[[uuid.UUID, float], StoredFrame | None] | None = None


def screen_change(before: bytes, after: bytes) -> float:
    """Share of pixels that changed noticeably (0..1), on small grayscale copies."""
    try:
        a = Image.open(io.BytesIO(before)).convert("L").resize((64, 36))
        b = Image.open(io.BytesIO(after)).convert("L").resize((64, 36))
    except OSError:
        return 1.0
    diff = ImageChops.difference(a, b)
    return sum(1 for value in diff.getdata() if value > 16) / (64 * 36)


@dataclass
class DriverRun:
    session_id: uuid.UUID
    deps: DriverDeps
    freshness_ms: int = 3000
    max_steps: int = 1500
    steps: int = 0
    notes: str = ""
    facts: dict[str, dict] = field(default_factory=dict)
    reports: list[dict] = field(default_factory=list)
    last: dict | None = None
    last_frame: StoredFrame | None = None
    errors: int = 0
    rejections: tuple[str | None, int] = (None, 0)
    waiting_since: float | None = None
    planning_since: float | None = None
    planning_wait_seconds: float = 60.0
    max_auto_goals: int = 10
    still_steps: int = 0
    still_since: float | None = None
    step_started: tuple[str, float, int] | None = None  # (plan step id, time, driver step count)
    play: PlayLog = field(default_factory=PlayLog)
    seen_messages: set[str] = field(default_factory=set)
    danger_until: float | None = None
    latencies: list[int] = field(default_factory=list)
    # Makes move ids unique per run: a resumed session starts a new run with
    # steps from 1 again, and the VM refuses a known id carrying a different move.
    run_tag: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    # Moves sent since the game computer last confirmed one: (count, first sent at).
    unconfirmed: tuple[int, float | None] = (0, None)
    confirmed: set[str] = field(default_factory=set)
    debug: Callable[[int, str, dict, bytes], None] | None = None
    _system: str | None = None
    _schema: dict | None = None

    # -- one step -------------------------------------------------------------

    def step(self) -> bool:
        """Run one step. False means this run is over (the session stopped,
        paused, or finished)."""
        deps = self.deps
        view = deps.view(self.session_id)
        if view is None or not view.playing or view.plan is None:
            return False
        current = view.current_step()
        if current is None:
            return self._between_goals(view)
        self.planning_since = None
        if view.capture_gate != "allowed":
            return self._wait("capture_off")

        frame = deps.observation(view.environment_id)
        now = deps.now()
        reports = deps.outcomes(self.session_id)
        outcome = self._last_outcome(reports)
        careful = self._careful(now)
        if (
            frame is None
            or frame.epoch != view.capture_epoch
            or (self.last_frame is not None and frame.seq <= self.last_frame.seq)
            or (now - frame.received_at) * 1000 > self.freshness_ms
            or frame.received_at < self._look_after(outcome, careful)
        ):
            return self._wait("observation_stale")
        self.waiting_since = None

        for move in self.play.settle(reports, now):
            if move.reflexes or (move.ended_by or "").startswith(("reflex:", "until:")):
                deps.metric({"session": str(self.session_id), "event": "reflex", "decision_id": move.decision_id,
                             "ended_by": move.ended_by, "ran_ms": move.ran_ms, "hold": list(move.hold),
                             "reflexes": list(move.reflexes)})
        self.play.prune(now)
        if self._controls_silent(reports, now):
            deps.metric({"session": str(self.session_id), "event": "controls_silent",
                         "moves": self.unconfirmed[0]})
            deps.pause(self.session_id, "controls_silent")
            self._save()
            return False
        if outcome and outcome.get("state") == "rejected":
            code = outcome.get("rejection_code")
            same, count = self.rejections
            self.rejections = (code, count + 1 if code == same else 1)
            if self.rejections[1] >= MAX_SAME_REJECTIONS:
                deps.progress(self.session_id, plan_revision=view.plan_revision, step_id=current["id"],
                              status="stuck", note=f"{MAX_SAME_REJECTIONS} decisions in a row rejected: {code}")
                self._save()
                return False
        elif outcome:
            self.rejections = (None, 0)

        if self.step_started is None or self.step_started[0] != current["id"]:
            self.step_started = (current["id"], now, self.steps)
        if self.last_frame is not None:
            if screen_change(self.last_frame.jpeg, frame.jpeg) < STILL_CHANGE:
                self.still_steps += 1
                self.still_since = self.still_since or now
            else:
                self.still_steps, self.still_since = 0, None

        profile = deps.profile(view.profile_id)
        if self._system is None:
            pursue = supports_pursue(view.controller_version)
            self._system = system_prompt(profile, pursue=pursue)
            self._schema = driver_schema(profile, pursue=pursue)
        messages = self._messages()
        text = self.observation_text(view, current, frame, outcome, messages, careful=careful)
        pictures: bytes | list[bytes] = frame.jpeg
        earlier = self._earlier(view, frame, profile)
        if earlier is not None:
            pictures = [earlier.jpeg, frame.jpeg]
            text += (f"\nPictures: 2, the first from {frame.received_at - earlier.received_at:.1f} s before "
                     "the second (the latest).")
        asked_at = deps.now()
        try:
            reply = deps.model.step(self._system, text, pictures, self._schema)
            output = to_step_output(reply.output, profile)
        except (DriverModelError, ValueError) as exc:
            self.errors += 1
            deps.metric({"session": str(self.session_id), "event": "model_error", "error": str(exc)[:200]})
            if self.errors >= MAX_MODEL_ERRORS:
                deps.pause(self.session_id, "driver_error")
                self._save()
                return False
            return True
        self.errors = 0
        self.latencies = (self.latencies + [reply.latency_ms])[-LATENCY_KEEP:]

        # The world may have moved on while the model thought.
        after = deps.view(self.session_id)
        sent_at = deps.now()
        frame_age_ms = int((sent_at - frame.received_at) * 1000)
        if (
            after is None or not after.playing
            or after.execution_generation != view.execution_generation
            or after.plan_revision != view.plan_revision
        ):
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "session_changed"})
            return True
        if [m["id"] for m in self._messages()] != [m["id"] for m in messages]:
            # Columbina said something while the model thought: ask again with it.
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "message_arrived"})
            return True
        if frame_age_ms > self.freshness_ms:
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "frame_too_old",
                         "frame_age_ms": frame_age_ms})
            return True

        self.steps += 1
        decision = output.decision.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        decision = self._sensible_until(decision, profile)
        decision = self._with_pursuit_picture(decision, frame, profile)
        if careful:
            decision = self._carefully(decision, profile, frame.context)
        decision_id = f"drv{self.steps}-{self.session_id.hex[:6]}-{self.run_tag}"
        deps.publish(view.environment_id, self.session_id, decision_id, view.execution_generation, decision)
        change = screen_change(self.last_frame.jpeg, frame.jpeg) if self.last_frame is not None else None
        self.last = {
            "decision_id": decision_id, "intent": decision.get("intent", ""), "sent_at": sent_at,
            "valid_for_ms": decision.get("valid_for_ms", profile.limits.default_valid_for_ms),
            "discrete": bool(decision.get("events")),
            "hold": list(decision.get("hold", [])),
            "pursue": any(ev.get("type") == "pursue" for ev in decision.get("events", [])),
        }
        self.last_frame = frame
        self.seen_messages.update(m["id"] for m in messages)
        count, since = self.unconfirmed
        self.unconfirmed = (count + 1, since if since is not None else sent_at)
        self.play.record(Move(
            decision_id=decision_id, sent_at=sent_at, valid_for_ms=self.last["valid_for_ms"],
            hold=tuple(decision.get("hold", [])),
            taps=tuple(ev["button"] for ev in decision.get("events", []) if ev.get("type") == "tap"),
            turns=turns_of(decision, profile),
            unknown_turns=self.last["pursue"],
        ))

        self._absorb(output, frame, sent_at)
        status = output.status.value if output.status else "working"
        if output.step_id not in (None, current["id"]):
            status = "working"  # it may only report on the step it was given
        if status != "working" or current["state"] == "pending":
            deps.progress(self.session_id, plan_revision=view.plan_revision, step_id=current["id"],
                          status=status, note=self.notes)

        if self.debug is not None:
            self.debug(self.steps, text, reply.output, frame.jpeg)
        deps.metric({
            "session": str(self.session_id), "event": "step", "step": self.steps, "plan_step": current["id"],
            "status": status, "intent": decision.get("intent", ""), "decision": decision, "model_ms": reply.latency_ms,
            "frame_age_at_ask_ms": int((asked_at - frame.received_at) * 1000), "frame_age_at_send_ms": frame_age_ms,
            "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens,
            "cached_tokens": reply.cached_tokens, "screen_change": change, "context": frame.context,
            "careful": careful,
        })
        self._save(model_ms=reply.latency_ms, frame_age_ms=frame_age_ms)
        if status in ("stuck", "needs_human"):
            return False
        if self.steps >= self.max_steps:
            deps.pause(self.session_id, "budget")
            return False
        return True

    # -- helpers ----------------------------------------------------------------

    def _between_goals(self, view: DriverView) -> bool:
        """Every step is done. Playing on her own, wait (keys released: no new
        decisions) for the goal Jophiel is choosing; otherwise stop here."""
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

    def _wait(self, reason: str) -> bool:
        now = self.deps.now()
        if self.waiting_since is None:
            self.waiting_since = now
        elif now - self.waiting_since > WAIT_LIMIT_SECONDS:
            self.deps.pause(self.session_id, reason)
            self._save(waiting=reason)
            return False
        self.deps.sleep(0.05)
        return True

    def _last_outcome(self, reports: list[dict]) -> dict | None:
        if self.last is None:
            return None
        return next(
            (r for r in reports if r.get("decision_id") == self.last["decision_id"] and r.get("state") != "sent"),
            None,
        )

    def _controls_silent(self, reports: list[dict], now: float) -> bool:
        """True when moves keep going out and the game computer confirms none."""
        mine = f"-{self.run_tag}"
        fresh = {r["decision_id"] for r in reports
                 if r.get("state") in FINAL_STATES and str(r.get("decision_id", "")).endswith(mine)} - self.confirmed
        if fresh:
            self.confirmed |= fresh
            self.unconfirmed = (0, None)
        count, since = self.unconfirmed
        return count >= SILENT_MOVES and since is not None and now - since >= SILENT_SECONDS

    def _earlier(self, view: DriverView, frame: StoredFrame, profile: Profile) -> StoredFrame | None:
        """A frame from about a second before ``frame``, when the profile asks
        for more than one (so the model can see what moves)."""
        if profile.observation.frames < 2 or self.deps.history is None:
            return None
        earlier = self.deps.history(view.environment_id, frame.received_at - EARLIER_FRAME_SECONDS)
        if earlier is None or earlier.seq >= frame.seq or earlier.epoch != frame.epoch:
            return None
        return earlier

    @staticmethod
    def _with_pursuit_picture(decision: dict, frame: StoredFrame, profile: Profile) -> dict:
        """A pursue gets the target's picture cut from the frame the model saw
        (the model only names the spot), and time to run: it ends itself."""
        events = decision.get("events", [])
        if not any(ev.get("type") == "pursue" for ev in events):
            return decision
        width, height = profile.observation.size
        image = Image.open(io.BytesIO(frame.jpeg))
        out = []
        for ev in events:
            if ev.get("type") == "pursue":
                w, h = (max(8, min(200, v)) for v in ev.get("size", (40, 40)))
                x = max(w // 2, min(width - w // 2, ev["point"][0]))
                y = max(h // 2, min(height - h // 2, ev["point"][1]))
                ev = {**ev, "point": [x, y], "size": [w, h], "template": cut_template(image, (x, y), (w, h))}
            out.append(ev)
        valid = decision.get("valid_for_ms") or 0
        return {**decision, "events": out, "valid_for_ms": min(max(valid, PURSUE_MIN_MS), PURSUE_MAX_MS)}

    @staticmethod
    def _sensible_until(decision: dict, profile: Profile) -> dict:
        """A "changed" stop rule fires on the move's own walking or turning
        (seen in the guest: most walks cut short within a second). Keep it only
        for moves that stand still."""
        until = decision.get("until")
        if not until:
            return decision
        moves = set(profile.reflexes.move_buttons or DEFAULT_MOVE_BUTTONS)
        moving = (bool(moves & set(decision.get("hold", []))) or bool(decision.get("axes"))
                  or any(ev.get("type") in ("turn", "look_at", "pursue") for ev in decision.get("events", [])))
        if not moving:
            return decision
        kept = [c for c in until.get("any", []) if c.get("condition") != "changed"]
        rest = {k: v for k, v in decision.items() if k != "until"}
        return rest | {"until": {"any": kept}} if kept else rest

    def _careful(self, now: float) -> str | None:
        """Why careful mode is on, or None."""
        if self.deps.careful is not None and (self.deps.careful(self.session_id) or {}).get("on"):
            return "Jophiel turned it on"
        if self.danger_until is not None and now < self.danger_until:
            return "you reported danger"
        return None

    def _look_after(self, outcome: dict | None, careful: str | None) -> float:
        """The next decision needs a picture received after this time."""
        last = self.last
        if last is None:
            return 0.0
        after = last["sent_at"] + SETTLE_SECONDS if last.get("discrete") else 0.0
        if outcome is not None and str(outcome.get("ended_by") or "").startswith(("reflex:", "until:")):
            after = max(after, outcome.get("at", 0.0) + AFTER_STOP_SECONDS)
        if careful or last.get("pursue"):
            # No planning ahead: look again only once the last move is over
            # (a pursue runs itself until it loses the target).
            ended = outcome.get("at", 0.0) if outcome else last["sent_at"] + last["valid_for_ms"] / 1000
            after = max(after, ended + SETTLE_SECONDS)
        return after

    def _carefully(self, decision: dict, profile: Profile, context: str | None) -> dict:
        """Careful mode, enforced by code: walking moves are cut short (no sprint,
        no long stop rules) and the profile's careful buttons (e.g. sneak) are
        held, except with the profile's ``not_with`` buttons (e.g. jump). If the move's own turns or taps don't fit in the short time, it
        does them standing still instead."""
        moves = set(profile.reflexes.move_buttons or DEFAULT_MOVE_BUTTONS) & set(profile.buttons)
        hold = [b for b in decision.get("hold", []) if b != "sprint"]
        if not moves & set(hold):
            return decision
        taps = {ev.get("button") for ev in decision.get("events", []) if ev.get("type") == "tap"}
        extra = [] if set(profile.careful.not_with) & (set(hold) | taps) else profile.careful.hold
        for button in extra:
            if button not in hold and len(hold) < profile.limits.max_hold_buttons:
                hold.append(button)
        limit = profile.careful.move_ms
        valid = decision.get("valid_for_ms") or profile.limits.default_valid_for_ms
        short = {k: v for k, v in decision.items() if k != "until"} | {"hold": hold, "valid_for_ms": min(valid, limit)}
        if not isinstance(validate_decision(short, profile, context or UNKNOWN_CONTEXT), Rejection):
            return short
        still = [b for b in decision.get("hold", []) if b not in moves and b != "sprint"]
        return {k: v for k, v in decision.items() if k != "until"} | {"hold": still}

    def _timing_text(self, frame: StoredFrame, outcome: dict | None) -> str | None:
        if not self.latencies:
            return None
        lag = sorted(self.latencies)[len(self.latencies) // 2] / 1000
        text = f"Timing: your next move starts about {lag:.1f} s after this picture was taken."
        last = self.last
        if last is not None and last.get("hold"):
            shot = frame.received_at
            ended = outcome.get("at", shot) if outcome else last["sent_at"] + last["valid_for_ms"] / 1000
            more = max(0.0, min(shot + lag, ended) - max(shot, last["sent_at"]))
            if more >= 0.2:
                text += (f" Your last move holds {', '.join(last['hold'])} for about {more:.1f} s more after "
                         "this picture, so you will be further along than it shows.")
        return text

    def _messages(self) -> list[dict]:
        return self.deps.messages(self.session_id) if self.deps.messages is not None else []

    def _absorb(self, output, frame: StoredFrame, at: float) -> None:
        if output.notes is not None:
            self.notes = output.notes
        for fact in output.facts:
            key = fact.key
            # "inventory.oak_logs" and "inventory.oak_log" are one thing: keep
            # one key (the singular), with the newest value.
            if key.endswith("s") and key[:-1] in self.facts:
                key = key[:-1]
            self.facts.pop(key + "s", None)
            self.facts[key] = {"value": fact.value, "at": at, "frame_seq": frame.seq}
        while len(self.facts) > MAX_FACTS:
            self.facts.pop(min(self.facts, key=lambda k: self.facts[k]["at"]))
        for entry in output.report:
            self.reports.append({"kind": entry.kind, "text": entry.text, "at": at})
            if entry.kind == "danger":
                self.danger_until = at + DANGER_CAREFUL_SECONDS
            if self.deps.notice is not None and entry.kind in ("danger", "notable"):
                self.deps.notice(self.session_id, entry.kind, entry.text)
        self.reports = self.reports[-MAX_REPORTS:]

    def describe_outcome(self, outcome: dict | None) -> str:
        if self.last is None:
            return "none yet"
        head = f'"{self.last["intent"]}"'
        if outcome is None:
            running = int((self.deps.now() - self.last["sent_at"]) * 1000)
            return f"{head}: still running ({running} ms so far, of {self.last['valid_for_ms']} ms)"
        state = outcome.get("state")
        ran = outcome.get("ran_ms")
        if state == "rejected":
            return f"{head}: REJECTED: {outcome.get('rejection_code')}: {outcome.get('rejection_reason')}"
        if state == "expired":
            return f"{head}: ran {ran}/{self.last['valid_for_ms']} ms, finished{self._pursuit_text(outcome)}"
        if state == "superseded":
            return f"{head}: replaced by your next decision after {ran} ms"
        if state == "completed":
            ended_by = outcome.get("ended_by") or ""
            if ended_by.startswith("reflex:"):
                why = REFLEX_TEXT.get(ended_by.removeprefix("reflex:"), ended_by)
                return f"{head}: STOPPED by a reflex after {ran} ms: {why}. Look carefully before moving."
            if ended_by.startswith("until:"):
                return f"{head}: your stop rule fired after {ran} ms"
            if ended_by == "pursue:lost":
                return f"{head}: lost sight of the target after {ran} ms{self._pursuit_text(outcome)}"
            return f"{head}: done early after {ran} ms"
        return f"{head}: stopped ({outcome.get('ended_by') or state})"

    @staticmethod
    def _pursuit_text(outcome: dict) -> str:
        pursuit = outcome.get("pursuit")
        if not pursuit:
            return ""
        return f" (chasing: seen in {pursuit.get('seen', 0)} checks, hit {pursuit.get('attacks', 0)} times)"

    def observation_text(
        self, view: DriverView, current: dict, frame: StoredFrame, outcome: dict | None,
        messages: list[dict] | None = None, careful: str | None = None,
    ) -> str:
        plan = view.plan or {}
        upcoming = view.upcoming()
        lines = [
            f"Plan goal: {plan.get('goal', '')}",
            f"Current step {current['id']}: {current['do']} (done when: {current['done_when']})",
            "Next steps: " + ("; ".join(f"{st['id']}: {st['do']}" for st in upcoming[:3]) or "none"),
        ]
        if plan.get("if_stuck"):
            lines.append(f"If stuck: {plan['if_stuck']}")
        if plan.get("constraints"):
            lines.append("Constraints: " + "; ".join(plan["constraints"]))
        if messages:
            lines.append("Messages from Jophiel (follow them; newest last):")
            for message in messages:
                age = round(self.deps.now() - message.get("at", self.deps.now()))
                new = ", NEW" if message["id"] not in self.seen_messages else ""
                lines.append(f'- ({age} s ago{new}) "{message["text"]}"')
        lines.append(f'Your notes: "{self.notes}"' if self.notes else "Your notes: (none)")
        lines.append("Last decision: " + self.describe_outcome(outcome))
        timing = self._timing_text(frame, outcome)
        if timing:
            lines.append(timing)
        if careful:
            profile = self.deps.profile(view.profile_id)
            held = f"; {', '.join(profile.careful.hold)} is held for you while moving" if profile.careful.hold else ""
            lines.append(f"Careful mode is ON ({careful}): moves are cut to {profile.careful.move_ms} ms and you "
                         f"get a fresh picture after each{held}. Take small steps toward safety.")
        if self.last_frame is not None:
            lines.append(f"Screen change since then: {round(screen_change(self.last_frame.jpeg, frame.jpeg) * 100)}%")
        if self.still_steps >= STILL_WARN_AFTER and self.still_since is not None:
            seconds = round(self.deps.now() - self.still_since)
            lines.append(
                f"WARNING: the screen has barely changed for {self.still_steps} decisions (~{seconds} s). "
                "What you are doing is not working. If attacking: you are too far or not aimed at the "
                "block, so walk closer or re-aim. If walking: you are blocked, so turn, jump or back up."
            )
        if self.deps.profile(view.profile_id).look_axes() is not None:
            lines.append(self.play.view_text())
        recent = self.play.recent_text(self.deps.now())
        if recent:
            lines.append(recent)
        if self.step_started is not None and self.step_started[0] == current["id"]:
            on_step = round(self.deps.now() - self.step_started[1])
            lines.append(f"Time on this step: {on_step} s ({self.steps - self.step_started[2]} decisions)")
        age = int((self.deps.now() - frame.received_at) * 1000)
        lines.append(f"Context: {frame.context or 'unknown'}   Frame age: {age} ms")
        return "\n".join(lines)

    def _save(self, **extra) -> None:
        self.deps.save_state(self.session_id, {
            "steps": self.steps, "notes": self.notes, "facts": self.facts, "reports": self.reports,
            "last": self.last, "play": self.play.state(self.deps.now()),
            "careful": self._careful(self.deps.now()), **extra,
        })
