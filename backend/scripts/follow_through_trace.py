"""One follow-through as a timestamped sequence: observation -> thought -> her mind's wake -> command -> the device's
answer -> fresh report -> outcome (docs/MIND_ARCHITECTURE.md, "Follow-through"). Read only.

    uv run python scripts/follow_through_trace.py                 # the latest item with a follow-through
    uv run python scripts/follow_through_trace.py --item 1a2b3c4d # one item, by the id she was shown
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import cast, select, String  # noqa: E402

from app.core.config import get_settings
from app.db.models import CoordinatorOutput, CoordinatorWork, FollowThroughAttempt, WorkingItem
from app.db.session import SessionLocal

BACKEND = Path(__file__).resolve().parents[1]


def _at(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--item", help="the item's id (or its first 8 characters)")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.item:
            item = db.scalar(select(WorkingItem).where(cast(WorkingItem.id, String).like(args.item.lower() + "%")))
        else:
            latest = db.scalar(select(CoordinatorWork).where(
                CoordinatorWork.input["wake"].astext == "follow_through").order_by(CoordinatorWork.created_at.desc()))
            item = db.get(WorkingItem, (latest.input.get("extra") or {}).get("item")) if latest else None
        if item is None:
            print("No follow-through found.")
            return
        ref = str(item.id)[:8]
        events: list[tuple[datetime, str, str]] = []
        path = Path(get_settings().mind_log_path)
        path = path if path.is_absolute() else BACKEND / path
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if ref not in line:
                    continue
                rec = json.loads(line)
                if (rec.get("item") or "").strip("[] ").startswith(ref):
                    events.append((_at(rec.get("thought_at") or rec["ts"]), "thought",
                                   f'{rec.get("noticed")!r}: "{rec.get("thought")}" -> act: "{rec.get("act")}" '
                                   f'({rec.get("act_outcome")})'))
        wakes = list(db.scalars(select(CoordinatorWork).where(
            (CoordinatorWork.input["extra"]["item"].astext == str(item.id))
            | (CoordinatorWork.input["follow_item"].astext == str(item.id))).order_by(CoordinatorWork.created_at)))
        for w in wakes:
            kind = "check" if w.kind == "wake" else "wake"
            what = (w.input or {}).get("about") if w.kind == "wake" else (w.result or {}).get("conclusion")
            events.append((_at(w.created_at), kind, f"[{str(w.id)[:8]}] {what} (status {w.status})"))
            for out in db.scalars(select(CoordinatorOutput).where(CoordinatorOutput.work_id == w.id)):
                events.append((_at(out.updated_at), "decision", f"[{str(w.id)[:8]}] {out.decision} / {out.status}"))
        for a in db.scalars(select(FollowThroughAttempt).where(FollowThroughAttempt.item_id == item.id)
                            .order_by(FollowThroughAttempt.issued_at)):
            events.append((_at(a.issued_at), "command", f"[{str(a.id)[:8]}] {a.action} {a.detail}"))
            if a.result:
                events.append((_at(a.issued_at), "device", f"[{str(a.id)[:8]}] answered: {a.result}"))
            if a.settled_at:
                events.append((_at(a.settled_at), "outcome", f"[{str(a.id)[:8]}] {a.status}: {a.settled_reason}"))
            elif a.status in ("sending", "pending"):
                events.append((_at(a.updated_at), "outcome", f"[{str(a.id)[:8]}] {a.status} ({a.checks} checks)"))
    print(f'Item [{ref}] ({item.kind}, {item.status}): "{item.text}"')
    for at, kind, text in sorted(events, key=lambda e: e[0]):
        print(f"{at:%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" UTC  {kind:<8} {text}")


if __name__ == "__main__":
    main()
