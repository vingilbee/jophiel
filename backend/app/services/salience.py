"""When is it worth speaking first?

This is the difference between an assistant and a chatbot, and it is almost
entirely a restraint problem. An agent that can interrupt will feel alive if it
does so twice a day and unbearable if it does so twice an hour — so the
interesting code here is the rate limiting, not the rules.

Two deliberate constraints:

  * **The tick is rules, not a model.** Evaluating salience runs every 30
    seconds on every open connection; it reads a struct and returns None. If
    deciding whether to speak cost an LLM call, an idle user would cost money
    all day, which is the exact thing the awareness design exists to avoid.
    A model is only involved *after* a rule fires, to write the sentence.
  * **Nothing here can nudge a stranger.** Every rule requires the user to have
    talked to the agent already this session and to have gone quiet since.
    Speaking into a conversation that hasn't started is presumptuous, and
    interrupting one mid-flow is worse.

Adding a trigger is one `Rule` in `RULES`. The rule decides *whether*; the
returned `Nudge.directive` tells the agent *what* to say, in its own words.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.config import get_settings
from app.services.awareness import AwarenessState

# Nothing before this hour, local time. There is no message worth waking
# someone for, and a 6am nudge poisons the feature permanently.
_QUIET_UNTIL_HOUR = 8
_QUIET_FROM_HOUR = 23

# Don't interrupt a live exchange. The user has to have been quiet this long
# before an unprompted message is anything other than talking over them.
_MIN_QUIET_SECONDS = 90

# Words in a screen caption that suggest the user is looking at food. Crude on
# purpose — this only decides whether to *offer*, and a false positive costs
# one ignorable sentence.
_FOOD_HINTS = (
    "food", "meal", "recipe", "menu", "restaurant", "dish", "calorie",
    "nutrition", "ingredient", "cooking", "takeaway", "delivery",
)


@dataclass(frozen=True, slots=True)
class Nudge:
    """A decision to speak, plus what to say it about."""

    # Rule name. Doubles as the cooldown key and is sent to the client so the
    # UI can label (and the user can mute) a specific kind of interruption.
    reason: str
    # Written *to the agent*, not to the user — it produces the actual wording
    # in its own voice, with the awareness block and history already in hand.
    directive: str
    # How long before this same rule may fire again.
    cooldown_seconds: float


@dataclass
class NudgeMemory:
    """Per-user rate-limiting state.

    Keyed by user rather than by connection on purpose: a flaky network that
    reconnects every few minutes must not reset the cooldowns and turn one
    nudge into ten.
    """

    last_nudge_at: datetime | None = None
    fired_at: dict[str, datetime] = field(default_factory=dict)
    # A nudge is "ignored" if the user says nothing for a while after it. Two
    # in a row and we stop for the session — being ignored twice is an answer.
    awaiting_reply_since: datetime | None = None
    ignored_streak: int = 0
    muted: bool = False

    def note_sent(self, nudge: Nudge, now: datetime) -> None:
        self.last_nudge_at = now
        self.fired_at[nudge.reason] = now
        self.awaiting_reply_since = now

    def note_user_spoke(self) -> None:
        """The user said something: whatever we asked, it landed."""
        self.awaiting_reply_since = None
        self.ignored_streak = 0

    def note_dismissed(self) -> None:
        """The user explicitly said not now. That's a stronger signal than
        silence, so it mutes immediately rather than counting toward a streak."""
        self.muted = True
        self.awaiting_reply_since = None


_MEMORY: dict[uuid.UUID, NudgeMemory] = {}


def memory_for(user_id: uuid.UUID) -> NudgeMemory:
    return _MEMORY.setdefault(user_id, NudgeMemory())


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def _intake_unlogged(state: AwarenessState) -> Nudge | None:
    """Past the middle of the day with an empty diary, for someone who logs."""
    if state.entries_today or state.now.hour < 14:
        return None
    # Never logged at all ⇒ they aren't using the diary; nagging is not onboarding.
    if state.days_since_last_log is None:
        return None
    return Nudge(
        reason="intake_unlogged",
        directive=(
            "The user normally logs their intake but hasn't logged anything "
            "today, and most of the day has gone. Offer, in one short "
            "sentence, to help them catch up — a photo, or just telling you "
            "what they ate. Do not lecture, do not list, and do not imply "
            "they've done something wrong."
        ),
        cooldown_seconds=6 * 3600,
    )


def _late_and_under(state: AwarenessState) -> Nudge | None:
    """Logging today, but a total low enough to be worth flagging before bed."""
    if state.now.hour < 21 or not state.entries_today:
        return None
    if state.calories_today >= 1200:
        return None
    return Nudge(
        reason="late_and_under",
        directive=(
            f"It's late and the user has only {state.calories_today:.0f} kcal "
            "logged today. Mention it in one sentence and ask whether that's "
            "the whole picture or just what got logged — under-logging is far "
            "more likely than under-eating, so ask rather than conclude. Do "
            "not give advice about eating more unless they say it's accurate."
        ),
        cooldown_seconds=12 * 3600,
    )


def _streak_broken(state: AwarenessState) -> Nudge | None:
    """Someone who was logging has stopped for a few days."""
    if state.days_since_last_log is None or state.days_since_last_log < 3:
        return None
    return Nudge(
        reason="streak_broken",
        directive=(
            f"The user hasn't logged anything for {state.days_since_last_log} "
            "days, after previously logging. Say you noticed, in one sentence, "
            "and ask if they want to pick it back up or leave it for now. "
            "Genuinely offer the second option — no guilt, no streak talk."
        ),
        cooldown_seconds=48 * 3600,
    )


def _food_on_screen(state: AwarenessState) -> Nudge | None:
    """Their screen is showing food and they haven't asked about it.

    Only fires when a caption already exists — captions are made lazily at turn
    start, so this costs nothing extra and simply notices what the eye happened
    to see the last time it was asked.
    """
    caption = (state.screen_caption or "").lower()
    if not state.senses.vision_enabled or not caption:
        return None
    if not any(hint in caption for hint in _FOOD_HINTS):
        return None
    return Nudge(
        reason="food_on_screen",
        directive=(
            "There is food on the user's screen (see your awareness block for "
            "what). Offer once, in one sentence, to estimate its macros or log "
            "it. If they don't take you up on it, drop it."
        ),
        cooldown_seconds=1800,
    )


# Order is priority: the first rule that fires wins, and the rest wait for
# their next window. Never send two things at once.
RULES = (_streak_broken, _intake_unlogged, _late_and_under, _food_on_screen)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def evaluate(state: AwarenessState, memory: NudgeMemory) -> Nudge | None:
    """Decide whether to speak first, right now. Cheap: no I/O, no model.

    Returns the winning `Nudge`, or None — which is the overwhelmingly common
    answer and should stay that way.
    """
    settings = get_settings()
    if not settings.nudges_enabled or memory.muted:
        return None

    now = datetime.now(timezone.utc)

    # An unanswered nudge ages into an ignored one. Two and we stop asking.
    if memory.awaiting_reply_since is not None:
        waited = (now - memory.awaiting_reply_since).total_seconds()
        if waited < settings.nudge_ignored_after_seconds:
            return None  # still waiting; don't stack another on top
        memory.awaiting_reply_since = None
        memory.ignored_streak += 1
        if memory.ignored_streak >= settings.nudge_ignored_before_mute:
            memory.muted = True
            return None

    if not _may_speak_now(state, memory, now, settings.nudge_min_gap_seconds):
        return None

    for rule in RULES:
        nudge = rule(state)
        if nudge is None:
            continue
        fired = memory.fired_at.get(nudge.reason)
        if fired and (now - fired).total_seconds() < nudge.cooldown_seconds:
            continue
        return nudge
    return None


def _may_speak_now(
    state: AwarenessState,
    memory: NudgeMemory,
    now: datetime,
    min_gap: float,
) -> bool:
    """The conditions that have nothing to do with *what* we'd say."""
    # Never at night, in the user's own timezone.
    if not _QUIET_UNTIL_HOUR <= state.now.hour < _QUIET_FROM_HOUR:
        return False
    # Only into a conversation that's already started, and only once it's
    # paused. Both halves matter: no ambushing, no talking over.
    quiet_for = state.since_last_message_seconds
    if quiet_for is None or quiet_for < _MIN_QUIET_SECONDS:
        return False
    # Mid-utterance or mid-reply is the worst possible moment.
    if state.senses.voice_state in ("listening", "thinking", "speaking"):
        return False
    if memory.last_nudge_at is not None:
        # Each ignored nudge doubles the gap before the next attempt — an
        # assistant that isn't landing should get quieter, not keep its rhythm.
        gap = min_gap * (2**memory.ignored_streak)
        if (now - memory.last_nudge_at).total_seconds() < gap:
            return False
    return True
