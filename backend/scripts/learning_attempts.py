"""What she wrote while teaching herself moves in a game (stage H1): each attempt's task,
every try's code, what the program did, what code counted, and the critic's verdict.

    uv run python scripts/learning_attempts.py                    # the latest 5 attempts, newest first
    uv run python scripts/learning_attempts.py --limit 20 --brief  # one line each
    uv run python scripts/learning_attempts.py --id 3f2a           # one attempt (id prefix), in full
    uv run python scripts/learning_attempts.py --user me@example.com

Read-only. Wipe them with ``scripts/reset_memory.py --scope game``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import String, cast, select  # noqa: E402

from app.db.models import GameSkillAttempt, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def _when(attempt: GameSkillAttempt) -> str:
    return attempt.created_at.astimezone().strftime("%Y-%m-%d %H:%M")


def _line(attempt: GameSkillAttempt) -> str:
    tries = len(attempt.tries or [])
    return (f"{str(attempt.id)[:8]}  {_when(attempt)}  {attempt.status:<8} {tries} {'try ' if tries == 1 else 'tries'}"
            f"  {attempt.task}" + (f"  — {attempt.result}" if attempt.result else ""))


def _full(attempt: GameSkillAttempt) -> str:
    out = [f"=== {_line(attempt)}", f"    origin: {attempt.origin} ({'she offered, the user said yes' if attempt.origin == 'offer' else 'the user asked'})"]
    for t in attempt.tries or []:
        out.append(f"\n--- try {t.get('n')} ---")
        if t.get("approach"):
            out.append(f"approach: {t['approach']}")
        if t.get("code"):
            out.append("code:")
            out += [f"    {line}" for line in t["code"].splitlines()]
        program = t.get("program")
        if program:
            out.append(f"program: {program.get('status')}" + (f" ({program['reason']})" if program.get("reason") else "")
                       + (f", {program['seconds']} s" if program.get("seconds") is not None else ""))
            for c in program.get("calls") or []:
                out.append(f"    {c.get('skill')}({c.get('params')}) -> {c.get('status')}"
                           + (f": {c['reason']}" if c.get("reason") else ""))
            actions = program.get("actions")
            if actions:
                counts = ", ".join(f"{name} x{n}" for name, n in (actions.get("by") or {}).items())
                out.append(f"    single actions: {actions.get('total')} ({counts}); the last:")
                for a in actions.get("last") or []:
                    out.append(f"      act.{a.get('action')}({', '.join(map(str, a.get('args') or []))}) -> {a.get('status')}"
                               + (f": {a['reason']}" if a.get("reason") else ""))
            if program.get("log"):
                out.append("log: " + " | ".join(program["log"]))
            if program.get("value") is not None:
                out.append(f"returned: {program['value']}")
        if t.get("facts"):
            out.append(f"counted: {t['facts']}")
        verdict = t.get("verdict") or {}
        out.append(f"verdict: {'WORKED' if verdict.get('success') else 'missed'}: {verdict.get('reason')}")
    if attempt.code:
        out.append("\nThe program that worked is the last try's code.")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", help="only this user's attempts (email)")
    parser.add_argument("--id", help="one attempt, by id prefix")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--brief", action="store_true", help="one line per attempt")
    args = parser.parse_args()
    with SessionLocal() as db:
        query = select(GameSkillAttempt).order_by(GameSkillAttempt.created_at.desc())
        if args.user:
            user = db.scalar(select(User).where(User.email == args.user))
            if user is None:
                print(f"no user {args.user}", file=sys.stderr)
                return 1
            query = query.where(GameSkillAttempt.user_id == user.id)
        if args.id:
            query = query.where(cast(GameSkillAttempt.id, String).startswith(args.id.lower()))
        attempts = list(db.scalars(query.limit(1 if args.id else args.limit)))
    if not attempts:
        print("No attempts yet.")
        return 0
    for attempt in attempts:
        print(_line(attempt) if args.brief else _full(attempt) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
