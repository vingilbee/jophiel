"""Close her open "promises" that only copy a fact ("Remember that Sam is allergic to peanuts").

What the user tells her is kept as facts; a promise is for something still to do. New ones are refused
(``memory_manager.fact_copy``); this cancels the ones made before, with the reason recorded. The facts stay.

    uv run python scripts/close_fact_copy_promises.py                         # list them (nothing changes)
    uv run python scripts/close_fact_copy_promises.py --user me@example.com   # one user
    uv run python scripts/close_fact_copy_promises.py --apply                 # cancel them
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import User, WorkingItem  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import memory_manager  # noqa: E402

REASON = "only kept a fact the user told you (kept in memory on its own), not something still to do"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", action="append", metavar="EMAIL", help="a user's email (repeatable); default all")
    parser.add_argument("--apply", action="store_true", help="cancel them (default: only list)")
    args = parser.parse_args()
    with SessionLocal() as db:
        query = select(WorkingItem, User.email).join(User, User.id == WorkingItem.user_id).where(
            WorkingItem.kind == "commitment", WorkingItem.owner == "columbina", WorkingItem.closed_at.is_(None))
        if args.user:
            query = query.where(User.email.in_(args.user))
        found = [(entry, email) for entry, email in db.execute(query.order_by(WorkingItem.created_at))
                 if memory_manager.fact_copy(entry.text)]
        for entry, email in found:
            print(f"{email}  [{memory_manager.ref(entry)}]  {entry.text}")
        print(f"{len(found)} fact-copy promise{'s' if len(found) != 1 else ''}"
              + ("" if args.apply else " (dry run: --apply to cancel)"))
        if not args.apply:
            return 0
        for entry, _ in found:
            memory_manager.resolve(db, entry.user_id, entry.id, status="cancelled", reason=REASON)
        db.commit()
        print(f"cancelled {len(found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
