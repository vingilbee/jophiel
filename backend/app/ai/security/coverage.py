"""What she could see of a channel over a window, and why not the rest (pure).

Every moment of a window gets exactly one answer, in this order of precedence:

1. ``expired``: before retention: whatever was collected then is gone, so nothing can be said either way;
2. ``off``: the owner had monitoring switched off: never collected, on purpose;
3. ``covered``: every record written then was read (continuity checked by record number);
4. ``gap`` / ``reset``: records from then were overwritten, or the log was cleared, before they were read;
5. ``unavailable``: the channel couldn't be read (the reason says why);
6. ``unverified``: no report covers it (the collector was offline, or hasn't reported up to now).

Only ``covered`` time supports "nothing matched": zero matches anywhere else is not evidence of absence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

Interval = tuple[datetime, datetime]
PRECEDENCE = ("expired", "off", "covered", "gap", "reset", "unavailable")
NOT_SEEN = ("expired", "off", "gap", "reset", "unavailable", "unverified")


@dataclass(frozen=True)
class Row:
    """One stored coverage interval (``end`` None: still open, e.g. monitoring switched off now)."""

    channel: str
    status: str
    start: datetime
    end: datetime | None
    reason: str | None = None


@dataclass(frozen=True)
class Piece:
    status: str
    start: datetime
    end: datetime
    reason: str | None = None


@dataclass
class ChannelView:
    channel: str
    pieces: list[Piece] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every moment of the window was seen."""
        return bool(self.pieces) and all(p.status == "covered" for p in self.pieces)

    def seen(self) -> list[Interval]:
        return [(p.start, p.end) for p in self.pieces if p.status == "covered"]

    def not_seen(self) -> list[Piece]:
        return [p for p in self.pieces if p.status != "covered"]

    def seen_seconds(self) -> float:
        return sum((b - a).total_seconds() for a, b in self.seen())

    def covers(self, moment: datetime) -> bool:
        return any(a <= moment <= b for a, b in self.seen())


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted(intervals):
        if end < start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def subtract(base: list[Interval], cuts: list[Interval]) -> list[Interval]:
    out = merge(base)
    for cut_start, cut_end in merge(cuts):
        kept: list[Interval] = []
        for start, end in out:
            if cut_end <= start or cut_start >= end:
                kept.append((start, end))
                continue
            if start < cut_start:
                kept.append((start, cut_start))
            if cut_end < end:
                kept.append((cut_end, end))
        out = kept
    return out


def clip(intervals: list[Interval], window: Interval) -> list[Interval]:
    lo, hi = window
    return [(max(a, lo), min(b, hi)) for a, b in intervals if min(b, hi) > max(a, lo)]


def view(channel: str, rows: list[Row], window: Interval, *, now: datetime, horizon: datetime) -> ChannelView:
    """The window, piece by piece, for one channel. ``rows``: this channel's coverage rows plus the monitoring
    switch's (channel ``*``, status ``off``). ``horizon``: the oldest moment retention still keeps."""
    start, end = window
    end = min(end, now)
    result = ChannelView(channel)
    if end <= start:
        return result
    left = [(start, end)]
    claims: list[tuple[str, list[tuple[Interval, str | None]]]] = []
    claims.append(("expired", [((start, horizon), "older than what is kept")] if horizon > start else []))
    for status in PRECEDENCE[1:]:
        claims.append((status, [((r.start, r.end or now), r.reason) for r in rows
                                if r.status == status and (r.channel == channel or (status == "off" and r.channel == "*"))]))
    pieces: list[Piece] = []
    for status, spans in claims:
        for (a, b), reason in spans:
            for piece_start, piece_end in clip(subtract([(a, b)], subtract([(a, b)], left)), window):
                pieces.append(Piece(status, piece_start, min(piece_end, end), reason))
            left = subtract(left, [(a, b)])
    for a, b in left:
        pieces.append(Piece("unverified", a, b, "no report from the collector covers it (it was offline, or "
                                                 "hasn't reported this far yet)"))
    result.pieces = _joined(sorted((p for p in pieces if p.end > p.start), key=lambda p: (p.start, p.end)))
    return result


def _joined(pieces: list[Piece]) -> list[Piece]:
    out: list[Piece] = []
    for piece in pieces:
        if out and out[-1].status == piece.status and out[-1].reason == piece.reason and piece.start <= out[-1].end:
            out[-1] = Piece(piece.status, out[-1].start, max(out[-1].end, piece.end), piece.reason)
        else:
            out.append(piece)
    return out


def tidy(pieces: list[Piece], *, at_least: timedelta = timedelta(seconds=2)) -> list[Piece]:
    """For reading: slivers shorter than ``at_least`` between two seen pieces (read timing) are left out of
    what's listed as not seen; anything longer is kept."""
    return [p for p in pieces if p.status == "covered" or p.end - p.start >= at_least]
