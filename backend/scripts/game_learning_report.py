"""Is she learning? Per-session play stats, split by whether lessons were on, and
what exploring did (challenges she set herself, lessons, model calls).

    uv run python scripts/game_experience_report.py                  # all game sessions
    uv run python scripts/game_experience_report.py --user me@x.com --last 20

Lesson delivery comes from shared MemoryLessonUse records. Use LESSONS_SUPPLY_ENABLED
for a controlled comparison, and reset derived test learning with --scope consolidation
between attempts. Delivery is separate from evidence that a lesson changed behavior.
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
from app.services import game_challenges, game_experience  # noqa: E402

COLUMNS = ("minutes", "outcomes", "success_rate", "repeated_failures", "deaths", "steps_done", "stuck")
EXPLORE = ("picked", "taken", "done", "given_up", "replaced", "lessons", "self_lessons", "learned", "tries",
           "model_calls")


def _avg(rows: list[dict], key: str) -> str:
    values = [r[key] for r in rows if r[key] is not None]
    return f"{mean(values):.2f}" if values else "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", metavar="EMAIL")
    parser.add_argument("--last", type=int, default=50)
    args = parser.parse_args()
    with SessionLocal() as db:
        query = select(DesktopSession).where(DesktopSession.profile_id.in_(game_experience.GAME_BY_PROFILE))
        if args.user:
            user = db.scalar(select(User).where(User.email == args.user))
            if user is None:
                print(f"No such user: {args.user}", file=sys.stderr)
                return 1
            query = query.where(DesktopSession.owner_user_id == user.id)
        sessions = list(db.scalars(query.order_by(DesktopSession.started_at.desc()).limit(args.last)))
        rows = [game_experience.session_stats(db, s) for s in reversed(sessions)]
        explored = [(s.started_at, game_challenges.session_stats(db, s)) for s in reversed(sessions)]
    if not rows:
        print("No game sessions yet.")
        return 0
    print(f"{'started':16} " + " ".join(f"{c:>17}" for c in COLUMNS))
    for r in rows:
        print(f"{r['started_at']:%Y-%m-%d %H:%M} "
              + " ".join(f"{'-' if r[c] is None else r[c]:>17}" for c in COLUMNS))
    print()
    print(f"{'all':12} n={len(rows):<3} " + "  ".join(f"{c}={_avg(rows, c)}" for c in COLUMNS))
    explored = [(at, r) for at, r in explored if r["picked"] or r["lessons"]]
    if explored:
        print("\nExploring (challenges she set herself; model_calls = picks + 2 per lesson try + 1 per tidy):")
        print(f"{'started':16} " + " ".join(f"{c:>12}" for c in EXPLORE))
        for at, r in explored:
            print(f"{at:%Y-%m-%d %H:%M} " + " ".join(f"{r[c]:>12}" for c in EXPLORE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
