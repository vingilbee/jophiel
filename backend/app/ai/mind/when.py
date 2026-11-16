"""When she means to come back to something (pure): a relative duration or an exact time, resolved to one UTC
instant, or refused with the reason. Nothing is rounded and no time zone is assumed: a local time without an
offset needs the user's IANA zone, and a local time that doesn't exist or happens twice there (a clock change) is
refused rather than guessed. Rendering goes back through the same zone, so what she tells them is what was kept.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_AHEAD = timedelta(days=60)
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def zone_of(name: str | None) -> ZoneInfo | None:
    """The user's IANA zone, or None when it isn't known (never a guess, never the host's zone)."""
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        return None


def resolve(*, in_seconds: int | None, at: str | None, zone: str | None, now: datetime) -> datetime:
    """The UTC instant meant. Raises ``ValueError`` with words she can act on."""
    if (in_seconds is None) == (at is None or not str(at).strip()):
        raise ValueError("give exactly one of in_seconds (from now) or at (an exact time)")
    if in_seconds is not None:
        if in_seconds < 1:
            raise ValueError("in_seconds must be at least 1")
        if timedelta(seconds=in_seconds) > MAX_AHEAD:
            raise ValueError("too far ahead: at most 60 days")
        return now + timedelta(seconds=in_seconds)
    return _exact(str(at).strip(), zone, now)


def _exact(text: str, zone_name: str | None, now: datetime) -> datetime:
    tz = zone_of(zone_name)
    time_only = bool(_TIME_ONLY.match(text))
    try:
        if time_only:
            if tz is None:
                raise ValueError
            parts = [int(p) for p in text.split(":")]
            today = now.astimezone(tz).date()
            parsed = datetime(today.year, today.month, today.day, parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        if time_only and tz is None:
            raise ValueError("their time zone isn't known, so a local time can't be placed: give it with its UTC "
                             "offset (2026-11-14T20:30:00-08:00) or use in_seconds") from None
        raise ValueError(f'"{text}" isn\'t a time I can read: use YYYY-MM-DD HH:MM (their local time), with an '
                         "optional UTC offset, or HH:MM for today") from None
    if parsed.tzinfo is None:
        if tz is None:
            raise ValueError("their time zone isn't known, so a local time can't be placed: give it with its UTC "
                             "offset (2026-11-14T20:30:00-08:00) or use in_seconds")
        first, second = parsed.replace(tzinfo=tz, fold=0), parsed.replace(tzinfo=tz, fold=1)
        # A skipped wall time doesn't survive a round trip through UTC (both folds differ in offset there too,
        # so this is checked first); a repeated one survives it with either offset.
        if first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != parsed:
            raise ValueError(f"{parsed:%Y-%m-%d %H:%M} doesn't exist in {tz.key} (the clocks jump forward then): "
                             "choose another time")
        if first.utcoffset() != second.utcoffset():
            raise ValueError(f"{parsed:%Y-%m-%d %H:%M} happens twice in {tz.key} (the clocks go back): give the UTC "
                             f"offset you mean ({_offset(first)} or {_offset(second)})")
        parsed = first
    instant = parsed.astimezone(timezone.utc)
    if instant <= now:
        local = describe(now, zone_name)
        what = "that time today has passed" if time_only else "that time has passed"
        raise ValueError(f"{what} (it's now {local}): give a later time" + (" with its date" if time_only else ""))
    if instant - now > MAX_AHEAD:
        raise ValueError("too far ahead: at most 60 days")
    return instant


def _offset(value: datetime) -> str:
    total = int(value.utcoffset().total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    return f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


def describe(instant: datetime, zone: str | None, *, date: bool = True) -> str:
    """An instant as they'd read it: local time in their zone (with the zone's name), or UTC said as UTC."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    tz = zone_of(zone)
    local = instant.astimezone(tz or timezone.utc)
    when = f"{local:%Y-%m-%d %H:%M:%S}" if date else f"{local:%H:%M:%S}"
    return f"{when} {tz.key}" if tz is not None else f"{when} UTC"


def span(seconds: float) -> str:
    """A duration, exact enough to tell 30 seconds from a minute."""
    seconds = int(round(abs(seconds)))
    if seconds < 120:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 120:
        rest = seconds % 60
        return f"{minutes} min" + (f" {rest} s" if rest and minutes < 10 else "")
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h {minutes % 60} min"
    return f"{hours // 24} days"
