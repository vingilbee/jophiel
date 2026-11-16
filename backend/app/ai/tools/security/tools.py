"""``get_security_activity``: one window of her desktop's security records, with what she could see of it."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

from langchain_core.tools import BaseTool, tool

from app.db.session import SessionLocal
from app.services import security_activity

MAX_WINDOW = timedelta(days=15)
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


class BadMoment(ValueError):
    pass


def parse_moment(text: str, *, zone: str | None, now: datetime) -> datetime:
    """An ISO time (with or without a zone; without one it's the user's), or "HH:MM" today in the user's zone
    (yesterday if that's still ahead)."""
    words = (text or "").strip()
    local = security_activity.zone_of(zone)
    match = _CLOCK.match(words)
    if match:
        hour, minute, second = int(match[1]), int(match[2]), int(match[3] or 0)
        if hour > 23 or minute > 59 or second > 59:
            raise BadMoment(f"{words!r} isn't a time of day")
        today = now.astimezone(local).replace(hour=hour, minute=minute, second=second, microsecond=0)
        return (today - timedelta(days=1) if today > now else today).astimezone(timezone.utc)
    try:
        moment = datetime.fromisoformat(words.replace("Z", "+00:00"))
    except ValueError:
        raise BadMoment(f"{words!r} isn't a time: use ISO like 2026-11-10T14:30 or HH:MM") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local)
    return moment.astimezone(timezone.utc)


def build_security_tools(user_id: uuid.UUID, *, zone: str | None = None) -> list[BaseTool]:
    @tool
    async def get_security_activity(hours_back: float = 1.0, start: str | None = None, end: str | None = None,
                                    channels: list[str] | None = None, event_ids: list[int] | None = None,
                                    contains: str | None = None, notable_only: bool = False, limit: int = 30,
                                    evidence: list[str] | None = None) -> str:
        """Read the security records of your desktop (your own Windows computer's event logs, collected read
        only while the owner has monitoring on) for one time window. Returns the matching records with evidence
        ids, counts by kind, and how much of the window you could actually see on each channel (and why not the
        rest). start / end: ISO times or "HH:MM", in the user's time zone unless they carry one (default: the
        last hours_back hours, up to now). channels: e.g. ["Security"] (default: every monitored channel).
        event_ids: Windows event ids to match. contains: text to match anywhere in a record. notable_only: only
        records the log itself marked critical, error, warning or audit failure. limit: records listed (up to
        60; counts always cover every match). evidence: ids like "obs:123" to see those records in full."""
        now = datetime.now(timezone.utc)
        try:
            until = parse_moment(end, zone=zone, now=now) if end else now
            since = parse_moment(start, zone=zone, now=now) if start else until - timedelta(
                hours=max(0.05, min(float(hours_back or 1.0), MAX_WINDOW.total_seconds() / 3600)))
        except BadMoment as exc:
            return f"Not read: {exc}."
        if since > until:
            since, until = until, since
        if until - since > MAX_WINDOW:
            return f"Not read: that window is longer than {MAX_WINDOW.days} days (nothing older is kept)."
        if event_ids and any(not 0 <= int(i) <= 65535 for i in event_ids):
            return "Not read: event ids are numbers from 0 to 65535."

        def read() -> str:
            with SessionLocal() as db:
                result = security_activity.activity(
                    db, user_id, start=since, end=until, channels=[c.strip() for c in channels or [] if c.strip()],
                    event_ids=[int(i) for i in event_ids or []][:50], contains=(contains or "").strip()[:200] or None,
                    notable_only=bool(notable_only), limit=int(limit or 30), evidence=evidence, now=now)
                if isinstance(result, str):
                    return f"Not read: {result}."
                return security_activity.render(result, zone=zone, now=now)

        return await asyncio.to_thread(read)

    return [get_security_activity]
