"""When she means to come back (app/ai/mind/when.py): relative and exact times resolve to one UTC instant, never
rounded, never in an assumed zone; ambiguous and nonexistent local times are refused, not guessed."""
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.mind import when as W

# 20:10:00 on 14 Nov in America/Vancouver, which the installed tz database keeps on UTC-7 all year from 2026
# (British Columbia stays on daylight time); clock-change cases use America/Los_Angeles.
NOW = datetime(2026, 11, 15, 3, 10, 0, tzinfo=timezone.utc)


def test_thirty_seconds_is_thirty_seconds():
    assert W.resolve(in_seconds=30, at=None, zone="America/Vancouver", now=NOW) == NOW + timedelta(seconds=30)
    assert W.span(30) == "30 s"


def test_an_exact_local_time_is_converted_with_their_zone():
    due = W.resolve(in_seconds=None, at="2026-11-14 20:30", zone="America/Vancouver", now=NOW)
    assert due == datetime(2026, 11, 15, 3, 30, tzinfo=timezone.utc)
    assert W.describe(due, "America/Vancouver") == "2026-11-14 20:30:00 America/Vancouver"
    # Today's time only, and a time with its own offset (no zone needed).
    assert W.resolve(in_seconds=None, at="20:45", zone="America/Vancouver", now=NOW) == datetime(
        2026, 11, 15, 3, 45, tzinfo=timezone.utc)
    assert W.resolve(in_seconds=None, at="2026-11-15T10:00:00+05:30", zone=None, now=NOW) == datetime(
        2026, 11, 15, 4, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("kwargs, words", [
    ({"in_seconds": None, "at": "2026-11-14 20:30", "zone": None}, "time zone isn't known"),
    ({"in_seconds": None, "at": "2026-11-14 20:30", "zone": "Not/AZone"}, "time zone isn't known"),
    ({"in_seconds": None, "at": "20:00", "zone": "America/Vancouver"}, "that time today has passed"),
    ({"in_seconds": None, "at": "2026-11-14 19:00", "zone": "America/Vancouver"}, "that time has passed"),
    ({"in_seconds": 10, "at": "2026-11-14 21:00", "zone": "America/Vancouver"}, "exactly one"),
    ({"in_seconds": None, "at": None, "zone": "America/Vancouver"}, "exactly one"),
    ({"in_seconds": 0, "at": None, "zone": "America/Vancouver"}, "at least 1"),
    ({"in_seconds": 61 * 86400, "at": None, "zone": "America/Vancouver"}, "too far ahead"),
    ({"in_seconds": None, "at": "next tuesday", "zone": "America/Vancouver"}, "isn't a time I can read"),
])
def test_refusals_say_why(kwargs, words):
    with pytest.raises(ValueError, match=words):
        W.resolve(now=NOW, **kwargs)


def test_clock_changes_are_refused_rather_than_guessed():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="happens twice"):  # PDT -> PST: 01:30 occurs twice
        W.resolve(in_seconds=None, at="2026-11-01 01:30", zone="America/Los_Angeles", now=now)
    later = datetime(2027, 2, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="doesn't exist"):  # PST -> PDT: 02:30 never happens
        W.resolve(in_seconds=None, at="2027-03-14 02:30", zone="America/Los_Angeles", now=later)
    # With the offset given, the ambiguous time is exact.
    assert W.resolve(in_seconds=None, at="2026-11-01T01:30:00-08:00", zone="America/Los_Angeles",
                     now=now) == datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)


def test_unknown_zone_renders_as_utc_never_the_host_zone():
    assert W.describe(NOW, None) == "2026-11-15 03:10:00 UTC"
