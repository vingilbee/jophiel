"""The agent's sense of the current moment.

A chatbot only knows what's in its prompt. This module assembles the rest: what
time it is where the user is, how long they've been here, what they've eaten
today, and which senses are currently switched on. It is the "subconscious"
half of the design — cheap, always available, and computed without an LLM —
while the agent itself is the expensive conscious half that only reads the
summary.

Two sources feed it, and the distinction matters:

  * **Facts we own** — the clock and the database. Trustworthy.
  * **Senses the client reports** (`SenseReport`) — route, mic state, whether
    screen sharing is on. The browser asserts these about itself, so they are
    descriptive only. Nothing is ever *authorized* on the strength of them;
    they exist so the agent can say "your mic is off" instead of guessing.

The rendered output is a short block of plain text (`describe`), not a
structure. That is deliberate: `app.ai` takes plain strings and stays free of
app state, exactly as it takes plain role/content dicts rather than ORM rows.
This service supplies the facts; `app.ai.prompts` supplies the instructions
about what to do with them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CalorieLog, ChatMessage, Conversation

# How a route maps to something the agent can say out loud. Unknown routes fall
# back to the raw path rather than being dropped — "on /whatever" is still more
# useful than silence.
_PLACES = {
    "/dashboard": "the voice dashboard",
    "/chat": "the typed chat page",
    "/predict": "the food-scanning page",
    "/integrations": "the integrations page",
    "/": "the landing page",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive timestamp.

    ``TimestampMixin.created_at`` is ``timestamp without time zone`` and gets
    its value from Postgres ``now()``, so it is already UTC in every deployment
    we run (the container's TZ is UTC) — it just doesn't say so. Rather than
    migrate the column, normalize on read.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _zone(name: str | None) -> ZoneInfo:
    """Resolve a client-supplied IANA zone, falling back to UTC.

    The name comes from the browser, so it can be missing, stale, or junk.
    A wrong clock is worse than an honest UTC one, but neither is worth a 500.
    """
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "the middle of the night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "late evening"


def _humanize(seconds: float) -> str:
    """A duration the way someone would say it out loud."""
    seconds = int(seconds)
    if seconds < 45:
        return "moments"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds / 3600
    if hours < 24:
        rounded = round(hours * 2) / 2  # nearest half hour
        text = f"{rounded:g} hour" + ("s" if rounded != 1 else "")
        return text
    days = round(hours / 24)
    return f"{days} day{'s' if days != 1 else ''}"


# --------------------------------------------------------------------------- #
# Client-reported senses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SenseReport:
    """What the client says about its own senses, as of ``reported_at``.

    Frozen because it's replaced wholesale on each update — a half-applied
    report read mid-turn would describe a state that never existed.
    """

    # IANA name, e.g. "Asia/Jakarta". The server has no other way to know it.
    timezone: str | None = None
    # Where the user is in the app — proprioception. Free, and it lets the
    # agent respond to context without any capture permission at all.
    route: str | None = None
    activity: str = "idle"  # idle | reading | typing | speaking
    # Hearing.
    hearing_enabled: bool = False
    wake_armed: bool = False
    voice_state: str | None = None
    # Eyes: whether the user is sharing their screen, and the newest frame.
    # The id is validated against this user's store before it lands here, so a
    # forged one becomes "no frame" rather than a mid-turn tool error.
    vision_enabled: bool = False
    screen_image_id: str | None = None
    screen_captured_at: datetime | None = None
    reported_at: datetime = field(default_factory=_utcnow)


class AwarenessSession:
    """One live client connection's continuity.

    Deliberately in memory and deliberately per-connection: it describes *this
    socket's* moment, and it should die with it. Nothing here is worth a table
    — if the process restarts, "you've been here 12 minutes" resetting is the
    correct answer, not lost data.
    """

    __slots__ = ("user_id", "started_at", "report", "_caption", "_caption_for")

    def __init__(self, user_id: uuid.UUID) -> None:
        self.user_id = user_id
        self.started_at = _utcnow()
        self.report = SenseReport()
        # Caption of the current screen frame, keyed by the frame it describes.
        # Cached per image id rather than per turn: frames only upload when the
        # screen actually changed, so consecutive turns over a stable screen
        # reuse one caption and cost nothing.
        self._caption: str | None = None
        self._caption_for: str | None = None

    def update(self, **fields: object) -> SenseReport:
        """Apply a partial sense update, stamping it as just-observed."""
        previous = self.report.screen_image_id
        self.report = replace(self.report, **fields, reported_at=_utcnow())
        if self.report.screen_image_id != previous:
            self._caption = self._caption_for = None
        return self.report

    @property
    def screen_caption(self) -> str | None:
        """The cached caption, but only if it describes the current frame."""
        if self._caption_for == self.report.screen_image_id:
            return self._caption
        return None

    def captioned(self, image_id: str | None) -> bool:
        """Whether a caption has already been *attempted* for this frame.

        Distinct from ``screen_caption`` being None, which is also what a
        failed attempt looks like — without this the caller would retry a
        captioner that is going to fail on the same bytes every turn.
        """
        return image_id is not None and self._caption_for == image_id

    def remember_caption(self, image_id: str, caption: str | None) -> None:
        """Cache a caption. A None caption is still cached — a captioner that
        failed on this frame will fail again on the same bytes, and retrying it
        every turn would pay for the failure repeatedly."""
        self._caption, self._caption_for = caption, image_id

    @property
    def open_for_seconds(self) -> float:
        return (_utcnow() - self.started_at).total_seconds()


# --------------------------------------------------------------------------- #
# The assembled state
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AwarenessState:
    """Everything the agent can know about *now*, without looking at anything."""

    now: datetime  # in the user's zone
    zone_name: str
    part_of_day: str
    session_seconds: float
    # None when the user has never sent a message (a brand-new account).
    since_last_message_seconds: float | None
    calories_today: float
    entries_today: int
    # None when nothing has ever been logged; 0 means "logged today".
    days_since_last_log: int | None
    senses: SenseReport
    # One-line description of the current screen frame, when one has been made.
    screen_caption: str | None = None

    # ----- rendering ----- #
    def describe(self) -> str:
        """The fact block injected into the system prompt.

        Kept to a handful of lines on purpose. This rides on *every* turn, so
        every line has to earn its tokens; anything the agent would rarely act
        on belongs in a tool, not here.
        """
        lines = [
            f"- Local time: {self.now:%H:%M} on {self.now:%A %d %B %Y} "
            f"({self.part_of_day}, {self.zone_name})."
        ]

        presence = (
            "- This session just opened"
            if self.session_seconds < 45
            else f"- This session has been open {_humanize(self.session_seconds)}"
        )
        if self.since_last_message_seconds is None:
            presence += "; the user has not said anything to you yet."
        else:
            presence += (
                f"; you last exchanged messages "
                f"{_humanize(self.since_last_message_seconds)} ago."
            )
        lines.append(presence)

        if self.entries_today:
            lines.append(
                f"- Logged intake today: {self.calories_today:,.0f} kcal across "
                f"{self.entries_today} "
                f"{'entry' if self.entries_today == 1 else 'entries'}."
            )
        elif self.days_since_last_log is None:
            lines.append("- Nothing has ever been logged to the intake diary.")
        else:
            lines.append(
                f"- Nothing logged yet today (last entry "
                f"{_humanize(self.days_since_last_log * 86400)} ago)."
                if self.days_since_last_log
                else "- Nothing logged yet today."
            )

        lines.append(f"- Eyes: {self._vision_line()}")
        lines.append(f"- Ears: {self._hearing_line()}")

        if self.senses.route:
            place = _PLACES.get(self.senses.route, f"the {self.senses.route} page")
            lines.append(f"- The user is on {place}.")

        return "\n".join(lines)

    def _vision_line(self) -> str:
        if not self.senses.vision_enabled:
            return "closed. You cannot see the user's screen or anything else."
        if self.senses.screen_image_id is None:
            return (
                "open — the user is sharing their screen, but no frame has "
                "reached you yet."
            )

        age = ""
        if self.senses.screen_captured_at is not None:
            seconds = (_utcnow() - self.senses.screen_captured_at).total_seconds()
            age = f", captured {_humanize(seconds)} ago" if seconds >= 45 else ""

        line = f"open — you can see the user's screen{age}."
        if self.screen_caption:
            line += f" On it right now: {self.screen_caption}"
        return line

    def _hearing_line(self) -> str:
        if not self.senses.hearing_enabled:
            return "closed. The microphone is off; this is a typed conversation."
        state = self.senses.voice_state or "idle"
        armed = ", wake word armed" if self.senses.wake_armed else ""
        return f"open — microphone live ({state}){armed}."

    def to_dict(self) -> dict:
        """Wire form for the WebSocket ``state`` frame the UI animates from."""
        return {
            "now": self.now.isoformat(),
            "zone": self.zone_name,
            "part_of_day": self.part_of_day,
            "session_seconds": round(self.session_seconds),
            "since_last_message_seconds": (
                None
                if self.since_last_message_seconds is None
                else round(self.since_last_message_seconds)
            ),
            "calories_today": self.calories_today,
            "entries_today": self.entries_today,
            "days_since_last_log": self.days_since_last_log,
            "senses": {
                "timezone": self.senses.timezone,
                "route": self.senses.route,
                "activity": self.senses.activity,
                "hearing_enabled": self.senses.hearing_enabled,
                "wake_armed": self.senses.wake_armed,
                "voice_state": self.senses.voice_state,
                "vision_enabled": self.senses.vision_enabled,
                "screen_image_id": self.senses.screen_image_id,
            },
            # So the UI can show the user the caption written about their own
            # screen — the one thing about the eye they can't otherwise check.
            "screen_caption": self.screen_caption,
            # Shipped so the UI can show the user exactly what the agent is
            # told. Transparency about perception is a feature, not debug output.
            "narration": self.describe(),
        }


def build(
    db: Session, *, user_id: uuid.UUID, session: AwarenessSession
) -> AwarenessState:
    """Assemble the current awareness state for one user.

    Three cheap queries and a clock read. Called once per turn and once per
    heartbeat, so it must stay cheap — if this ever needs a model call, it
    belongs in a tool instead.
    """
    zone = _zone(session.report.timezone)
    now = datetime.now(zone)
    today = now.date()

    last_message_at = db.scalar(
        select(func.max(ChatMessage.created_at))
        .join(Conversation, ChatMessage.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
    )
    since_last_message = (
        None
        if last_message_at is None
        else (_utcnow() - _as_utc(last_message_at)).total_seconds()
    )

    # "Today" is the user's today, not the server's — the whole point of
    # asking the client for its zone.
    totals = db.execute(
        select(func.coalesce(func.sum(CalorieLog.calories), 0.0), func.count())
        .where(CalorieLog.user_id == user_id, CalorieLog.date == today)
    ).one()
    calories_today, entries_today = float(totals[0]), int(totals[1])

    last_log_date = db.scalar(
        select(func.max(CalorieLog.date)).where(CalorieLog.user_id == user_id)
    )

    return AwarenessState(
        now=now,
        zone_name=str(zone),
        part_of_day=_part_of_day(now.hour),
        session_seconds=session.open_for_seconds,
        since_last_message_seconds=since_last_message,
        calories_today=calories_today,
        entries_today=entries_today,
        days_since_last_log=(
            None if last_log_date is None else (today - last_log_date).days
        ),
        senses=session.report,
        screen_caption=session.screen_caption,
    )
