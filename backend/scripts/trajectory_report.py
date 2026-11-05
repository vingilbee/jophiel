"""What her trajectory costs, measured (docs/learning-system/POST-AUDIT-01-implementation.md, "Measurement").

    uv run python scripts/trajectory_report.py                 # the pack owner's trajectory, everything saved
    uv run python scripts/trajectory_report.py --since 24      # the last 24 hours only
    uv run python scripts/trajectory_report.py --email x@y.z   # another user's (their own records)
    uv run python scripts/trajectory_report.py --json

Read only. From the database: bytes per minute and hour of recorded time, the 30-day projection at that rate
(continuously connected, and at the share of time she actually was), compression, the pack's tick-handler and
encode time, streams with their holes and gaps. From the pack, if it's running: its buffer and counts. Exact
storage settings should follow these numbers; none is guaranteed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.models import CaptureGap, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import trajectory  # noqa: E402


def _mb(n: float | None) -> str:
    return "-" if n is None else f"{n / 1e6:,.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", help="whose trajectory (default: MINECRAFT_PACK_OWNER_EMAIL)")
    parser.add_argument("--since", type=float, help="only the last N hours")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()
    settings = get_settings()
    email = (args.email or settings.minecraft_pack_owner_email or "").strip().lower()
    if not email:
        print("no user: pass --email or set MINECRAFT_PACK_OWNER_EMAIL", file=sys.stderr)
        return 2
    since = datetime.now(timezone.utc) - timedelta(hours=args.since) if args.since else None
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(func.lower(User.email) == email))
        if user_id is None:
            print(f"no user with email {email}", file=sys.stderr)
            return 2
        measured = trajectory.measure(db, user_id, since=since)
        streams = trajectory.streams(db, user_id, since=since, limit=20)
        gaps = dict(db.execute(select(CaptureGap.reason, func.count()).where(
            CaptureGap.user_id == user_id, CaptureGap.label == "trajectory").group_by(CaptureGap.reason)).all())
    pack = None
    try:
        from app.ai.desktop.pack_client import PackClient
        pack = PackClient.from_settings()._call("GET", "/trajectory/stats", timeout=2.0)
    except Exception as exc:  # noqa: BLE001 - the pack may not be running
        pack = {"unreachable": str(exc)}
    report = {"measured": measured, "gaps": gaps, "streams": streams, "pack": pack}
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    days = settings.learning_capture_retention_days
    if not measured.get("chunks"):
        print("nothing recorded yet")
    else:
        m = measured
        print(f"recorded: {m['recorded_seconds'] / 3600:.2f} h of play over {m['span_seconds'] / 3600:.2f} h "
              f"({m['recorded_share']:.0%}), {m['ticks']:,} ticks, {m['events']:,} events, {m['chunks']:,} chunks")
        print(f"stored:   {_mb(m['bytes'])} ({_mb(m['raw_bytes'])} before gzip, x{m['compression']})")
        print(f"rate:     {m['bytes_per_minute'] / 1e3:,.1f} KB per minute, {_mb(m['bytes_per_hour'])} per hour of play")
        print(f"{days} days:  {_mb(m[f'projected_{days}_days_continuous_bytes'])} if connected the whole time, "
              f"{_mb(m[f'projected_{days}_days_at_observed_share_bytes'])} at the share observed")
        print(f"pack:     tick handler {m['tick_handler_ms_mean']} ms mean, {m['tick_handler_ms_max']} ms max; "
              f"encode {m['encode_ms_mean_per_chunk']} ms mean per chunk, {m['encode_ms_max_per_chunk']} ms max")
    print(f"gaps:     {gaps or 'none'}")
    for st in streams:
        holes = ", ".join(f"{h['from_seq']}-{h['to_seq']}" for h in st["missing"]) or "none"
        end = st["ended_at"] or ("end seen" if st["end_seen"] else "end not seen")
        print(f"stream {st['id'][:8]} #{st['index']} {st['started_at']} -> {end}: {st['chunks']} chunks, "
              f"{st['ticks']:,} ticks, {_mb(st['bytes'])}, holes: {holes}")
    if pack:
        print("pack now: " + json.dumps({k: v for k, v in pack.items() if k != "coverage"}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
