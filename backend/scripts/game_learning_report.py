"""Is she learning? Per-session play stats, split by whether lessons were on.

    uv run python scripts/game_learning_report.py                  # all game sessions
    uv run python scripts/game_learning_report.py --user me@x.com --last 20

The experiment: run the same task on the test server several times with
GAME_LEARNING_APPLY_LESSONS=false, then several times with it true (reset in
between with scripts/reset_memory.py --scope game to start from nothing).
Learning shows as a higher success rate and fewer repeated failures, deaths
and stuck pauses with lessons on. Sessions record whether lessons were on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import DesktopSession, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import game_learning  # noqa: E402

COLUMNS = ("minutes", "outcomes", "success_rate", "repeated_failures", "deaths", "steps_done", "stuck")


def _avg(rows: list[dict], key: str) -> str:
    values = [r[key] for r in rows if r[key] is not None]
    return f"{mean(values):.2f}" if values else "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", metavar="EMAIL")
    parser.add_argument("--last", type=int, default=50)
    args = parser.parse_args()
    with SessionLocal() as db:
        query = select(DesktopSession).where(DesktopSession.profile_id.in_(game_learning.GAME_BY_PROFILE))
        if args.user:
            user = db.scalar(select(User).where(User.email == args.user))
            if user is None:
                print(f"No such user: {args.user}", file=sys.stderr)
                return 1
            query = query.where(DesktopSession.owner_user_id == user.id)
        sessions = list(db.scalars(query.order_by(DesktopSession.started_at.desc()).limit(args.last)))
        rows = [game_learning.session_stats(db, s) for s in reversed(sessions)]
    if not rows:
        print("No game sessions yet.")
        return 0
    print(f"{'started':16} {'lessons':>8} " + " ".join(f"{c:>17}" for c in COLUMNS))
    for r in rows:
        lessons = "-" if r["lessons_on"] is None else (f"on:{r['lessons']}" if r["lessons_on"] else "off")
        print(f"{r['started_at']:%Y-%m-%d %H:%M} {lessons:>8} "
              + " ".join(f"{'-' if r[c] is None else r[c]:>17}" for c in COLUMNS))
    print()
    for label, group in (("lessons on", [r for r in rows if r["lessons_on"]]),
                         ("lessons off", [r for r in rows if r["lessons_on"] is False])):
        if group:
            print(f"{label:12} n={len(group):<3} " + "  ".join(f"{c}={_avg(group, c)}" for c in COLUMNS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
