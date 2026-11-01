"""When a memory is about (docs/COLUMBINA_MEMORY_SYSTEM_IMPLEMENTATION_PLAN.md §4, stage 3).

The extractor reads the user's time words against the moment they were said, on the user's calendar
(``anchor``), and proposes an interval (``TimeReading``). Code checks the reading before anything is kept
(``ground``): the words must be the user's own, the dates must fit the stated precision, a plan can't be
over before it was made and an episode can't happen after it was told. A reading that fails keeps its words
and loses its bounds ("unknown"), so nothing is invented. Stored, it's the words, a half-open interval of
local midnights, whether part of it (usually the year) was chosen, the zone, and when it was said: "last
month" stays the month before it was said, whenever it's recalled. ``span`` and ``describe`` give readers the
same thing back in words.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ai.recall.contracts import TimeSpan
from app.schemas.long_term_memory import TimeReading

log = logging.getLogger(__name__)

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_NAMES = dict(zip(WEEKDAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# days: a few named days, or the few an approximate day could be ("two weeks ago").
_SPAN_DAYS = {"day": (1, 1), "days": (2, 10), "week": (5, 7), "season": (60, 93)}
# What a reading is about: a plan is ahead of when it was said, an episode behind it; a fact can be either.
ROLES = ("future", "past", "any")


def zone_of(name: str | None) -> tuple[ZoneInfo, str | None]:
    """The zone to read in, and the name to store: None when the client never said (read as UTC)."""
    if name:
        try:
            return ZoneInfo(name), name
        except Exception:  # noqa: BLE001 - an unknown name is "not known", never the server's zone
            log.warning("memory time: unknown zone %r; reading in UTC", name)
    return ZoneInfo("UTC"), None


def anchor(said_at: datetime, zone: str | None) -> str:
    """The line the extractor reads times against: the day, date and hour it was said, where."""
    tz, name = zone_of(zone)
    local = _aware(said_at).astimezone(tz)
    where = name or "time zone unknown, read as UTC"
    return f"SAID: {local:%A %d %B %Y, %H:%M} ({where})"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9:]+", (text or "").lower()))


def _said(words: str, user_text: str) -> bool:
    wanted = _norm(words)
    return bool(wanted) and f" {wanted} " in f" {_norm(user_text)} "


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _fits(precision: str, start: date, end: date) -> bool:
    days = (end - start).days + 1
    if precision in _SPAN_DAYS:
        low, high = _SPAN_DAYS[precision]
        return low <= days <= high
    if precision == "month":
        return (start.day == 1 and end.year == start.year and end.month == start.month
                and end.day == calendar.monthrange(start.year, start.month)[1])
    if precision == "year":
        return start == date(start.year, 1, 1) and end == date(start.year, 12, 31)
    return False


def _repeats(reading: TimeReading) -> dict | None:
    if reading.precision != "repeating" and not (reading.repeats or reading.weekdays or reading.local_time):
        return None
    repeats = reading.repeats or ("weekly" if reading.weekdays else None)
    if repeats is None:
        return None
    weekdays = [d for d in WEEKDAYS if d in set(reading.weekdays)]
    if repeats == "weekly" and not weekdays:
        return None
    return {"repeats": repeats, "weekdays": weekdays, "local_time": reading.local_time}


def ground(reading: TimeReading | None, *, user_text: str, said_at: datetime, zone: str | None,
           role: str = "any") -> dict:
    """The ``time_*`` column values for a reading the user's words support; ``{}`` when there's no reading or
    its words aren't the user's (nothing is kept that they didn't say)."""
    if reading is None:
        return {}
    words = " ".join(reading.words.split()).strip(" .,;:!?")
    if not words or not _said(words, user_text):
        log.info("memory time: %r is not in the user's message; no time kept", reading.words[:60])
        return {}
    tz, zone_name = zone_of(zone)
    said = _aware(said_at)
    values = {"time_words": words[:200], "time_precision": "unknown", "time_start": None, "time_end": None,
              "time_inferred": False, "time_zone": zone_name, "time_repeats": None, "time_said_at": said}
    schedule = _repeats(reading)
    if schedule is not None:
        values["time_precision"], values["time_repeats"] = "repeating", schedule
        return values
    start, end = _day(reading.start), _day(reading.end)
    precision = reading.precision
    if precision in ("unknown", "repeating") or start is None or end is None or start > end:
        return values
    if not _fits(precision, start, end):
        log.info("memory time: %s..%s doesn't fit %s; kept as unknown", start, end, precision)
        return values
    today = said.astimezone(tz).date()
    if (role == "future" and end < today) or (role == "past" and start > today):
        log.info("memory time: %s..%s can't be %s of %s; kept as unknown", start, end, role, today)
        return values
    values.update(time_precision=precision,
                  time_start=datetime.combine(start, time(), tz),
                  time_end=datetime.combine(end + timedelta(days=1), time(), tz),
                  time_inferred=bool(reading.year_inferred) and not _YEAR.search(words))
    return values


def span(row) -> TimeSpan | None:
    """A stored reading (any row with the ``time_*`` fields) for readers."""
    if not getattr(row, "time_words", None):
        return None
    schedule = row.time_repeats or None
    return TimeSpan(words=row.time_words, precision=row.time_precision, start=row.time_start, end=row.time_end,
                    inferred=bool(row.time_inferred), zone=row.time_zone, said_at=row.time_said_at,
                    repeats=_schedule_words(schedule) if schedule else None)


def _schedule_words(schedule: dict) -> str:
    days = [_WEEKDAY_NAMES[d] for d in schedule.get("weekdays") or [] if d in _WEEKDAY_NAMES]
    out = schedule.get("repeats") or "repeating"
    if days:
        out += " on " + ", ".join(days)
    if schedule.get("local_time"):
        out += f" at {schedule['local_time']}"
    return out


def _interval_words(t: TimeSpan, tz: ZoneInfo) -> str:
    start = t.start.astimezone(tz).date()
    last = (t.end.astimezone(tz) - timedelta(days=1)).date()
    if t.precision == "day":
        return f"{start:%a %d %b %Y}"
    if t.precision == "month":
        return f"{start:%B %Y}"
    if t.precision == "year":
        return f"{start:%Y}"
    if start.year == last.year:
        return f"{start:%d %b} to {last:%d %b %Y}"
    return f"{start:%d %b %Y} to {last:%d %b %Y}"


def describe(t: TimeSpan, now: datetime) -> str:
    """The reading in words for her: the user's words and when they said them, what that was read as, and
    where it stands now. A chosen year or an unknown zone is said, never hidden."""
    tz, _ = zone_of(t.zone)
    said = f' on {t.said_at.astimezone(tz):%d %b %Y}' if t.said_at else ""
    quoted = f'"{t.words}" (said{said})'
    if t.precision == "repeating":
        where = t.zone or "time zone unknown"
        return f"{quoted}: {t.repeats} ({where})"
    if t.start is None or t.end is None:
        return f"{quoted}: when exactly isn't known"
    out = f"{quoted}: {_interval_words(t, tz)}"
    if t.inferred:
        out += " (the year wasn't said; assumed)"
    if not t.zone:
        out += " (their time zone wasn't known)"
    now = _aware(now)
    if t.end <= now:
        out += ", now past"
    elif t.start <= now:
        out += ", that's now"
    else:
        out += ", still ahead"
    return out
