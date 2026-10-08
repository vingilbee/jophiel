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

from app.ai.desktop.driver import DriverModel, DriverModelError, driver_schema, system_prompt, to_step_output
from app.services.desktop_preview import StoredFrame
from app.services.desktop_sessions import DriverView
from desktop_controller.profile import Profile

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
    still_steps: int = 0
    still_since: float | None = None
    step_started: tuple[str, float, int] | None = None  # (plan step id, time, driver step count)
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
            deps.pause(self.session_id, "plan_complete")
            self._save()
            return False
        if view.capture_gate != "allowed":
            return self._wait("capture_off")

        frame = deps.observation(view.environment_id)
        now = deps.now()
        if (
            frame is None
            or frame.epoch != view.capture_epoch
            or (self.last_frame is not None and frame.seq <= self.last_frame.seq)
            or (now - frame.received_at) * 1000 > self.freshness_ms
            or (self.last is not None and self.last.get("discrete")
                and frame.received_at < self.last["sent_at"] + SETTLE_SECONDS)
        ):
            return self._wait("observation_stale")
        self.waiting_since = None

        outcome = self._last_outcome()
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
            self._system, self._schema = system_prompt(profile), driver_schema(profile)
        text = self.observation_text(view, current, frame, outcome)
        asked_at = deps.now()
        try:
            reply = deps.model.step(self._system, text, frame.jpeg, self._schema)
            output = to_step_output(reply.output)
        except (DriverModelError, ValueError) as exc:
            self.errors += 1
            deps.metric({"session": str(self.session_id), "event": "model_error", "error": str(exc)[:200]})
            if self.errors >= MAX_MODEL_ERRORS:
                deps.pause(self.session_id, "driver_error")
                self._save()
                return False
            return True
        self.errors = 0

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
        if frame_age_ms > self.freshness_ms:
            deps.metric({"session": str(self.session_id), "event": "discarded", "why": "frame_too_old",
                         "frame_age_ms": frame_age_ms})
            return True

        self.steps += 1
        decision = output.decision.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        decision_id = f"drv{self.steps}-{self.session_id.hex[:6]}"
        deps.publish(view.environment_id, self.session_id, decision_id, view.execution_generation, decision)
        change = screen_change(self.last_frame.jpeg, frame.jpeg) if self.last_frame is not None else None
        self.last = {
            "decision_id": decision_id, "intent": decision.get("intent", ""), "sent_at": sent_at,
            "valid_for_ms": decision.get("valid_for_ms", profile.limits.default_valid_for_ms),
            "discrete": bool(decision.get("events")),
        }
        self.last_frame = frame

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
        })
        self._save(model_ms=reply.latency_ms, frame_age_ms=frame_age_ms)
        if status in ("stuck", "needs_human"):
            return False
        if self.steps >= self.max_steps:
            deps.pause(self.session_id, "budget")
            return False
        return True

    # -- helpers ----------------------------------------------------------------

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

    def _last_outcome(self) -> dict | None:
        if self.last is None:
            return None
        return next(
            (r for r in self.deps.outcomes(self.session_id)
             if r.get("decision_id") == self.last["decision_id"] and r.get("state") != "sent"),
            None,
        )

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
            return f"{head}: ran {ran}/{self.last['valid_for_ms']} ms, finished"
        if state == "superseded":
            return f"{head}: replaced by your next decision after {ran} ms"
        if state == "completed":
            return f"{head}: done early after {ran} ms"
        return f"{head}: stopped ({outcome.get('ended_by') or state})"

    def observation_text(self, view: DriverView, current: dict, frame: StoredFrame, outcome: dict | None) -> str:
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
        lines.append(f'Your notes: "{self.notes}"' if self.notes else "Your notes: (none)")
        lines.append("Last decision: " + self.describe_outcome(outcome))
        if self.last_frame is not None:
            lines.append(f"Screen change since then: {round(screen_change(self.last_frame.jpeg, frame.jpeg) * 100)}%")
        if self.still_steps >= STILL_WARN_AFTER and self.still_since is not None:
            seconds = round(self.deps.now() - self.still_since)
            lines.append(
                f"WARNING: the screen has barely changed for {self.still_steps} decisions (~{seconds} s). "
                "What you are doing is not working. If attacking: you are too far or not aimed at the "
                "block, so walk closer or re-aim. If walking: you are blocked, so turn, jump or back up."
            )
        if self.step_started is not None and self.step_started[0] == current["id"]:
            on_step = round(self.deps.now() - self.step_started[1])
            lines.append(f"Time on this step: {on_step} s ({self.steps - self.step_started[2]} decisions)")
        age = int((self.deps.now() - frame.received_at) * 1000)
        lines.append(f"Context: {frame.context or 'unknown'}   Frame age: {age} ms")
        return "\n".join(lines)

    def _save(self, **extra) -> None:
        self.deps.save_state(self.session_id, {
            "steps": self.steps, "notes": self.notes, "facts": self.facts, "reports": self.reports,
            "last": self.last, **extra,
        })
