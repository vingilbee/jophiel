"""Wipe Columbina's memory of users, to test memory and learning from a blank state.

    uv run python scripts/reset_memory.py --all                      # everyone, every scope
    uv run python scripts/reset_memory.py --user me@example.com      # one user
    uv run python scripts/reset_memory.py --user me@example.com --scope game
    uv run python scripts/reset_memory.py --all --dry-run            # counts only

Scopes: conversations (and everything built on them), memory (semantic,
episodic, learning, memory jobs), game (game event jobs, experiences, lessons,
proposals, reflection jobs). Game episodes are episodic memory: scope memory.
Never touches core_memory, accounts, integrations or calorie/weight logs.
Asks before deleting unless --yes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import memory_reset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--user", action="append", metavar="EMAIL", help="a user's email (repeatable)")
    who.add_argument("--all", action="store_true", help="every user")
    parser.add_argument("--scope", action="append", choices=sorted(memory_reset.SCOPES),
                        help="what to delete (repeatable; default: everything)")
    parser.add_argument("--dry-run", action="store_true", help="print counts, delete nothing")
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args()
    scopes = args.scope or list(memory_reset.SCOPES)

    with SessionLocal() as db:
        user_ids = None
        if args.user:
            users = list(db.scalars(select(User).where(User.email.in_(args.user))))
            missing = set(args.user) - {u.email for u in users}
            if missing:
                print(f"No such user: {', '.join(sorted(missing))}", file=sys.stderr)
                return 1
            user_ids = [u.id for u in users]
        counts = memory_reset.preview(db, user_ids=user_ids, scopes=scopes)
        target = "ALL users" if user_ids is None else ", ".join(args.user)
        print(f"Would delete for {target} (scopes: {', '.join(scopes)}):")
        for table, count in counts.items():
            print(f"  {table:28} {count}")
        if args.dry_run:
            return 0
        if not args.yes and input("Delete? Type 'yes': ").strip().lower() != "yes":
            print("Nothing deleted.")
            return 1
        memory_reset.reset(db, user_ids=user_ids, scopes=scopes)
        print("Deleted. core_memory and accounts were kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
