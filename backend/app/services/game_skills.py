"""Moves she taught herself in a game, kept as skills (stage H2).

When an attempt to learn a move works (``services/game_practice``), its program
is saved here per user and game, so it outlives the session: live, she learned
to pillar up and a minute later, in a new session, said she couldn't, and
relearned it from scratch. Learning the same move again replaces the program.

Her hands (``skill_driver``) see each enabled move as one more skill,
``learned_<words>(times)``: the saved program, run ``times`` times in a row
through the pack's sandbox (stopping at the first try that doesn't work), so a
one-block pillar move climbs five blocks with ``times=5``. Usable at once (the
user's choice, 2026-10-17); the Learning page lists them, and the user can
switch one off or delete it. Columbina's game summary lists them too, so she
plans with them instead of offering to learn them again.

They look after themselves (2026-10-18):
- **A move that keeps missing switches itself off**: ``MISS_LIMIT`` misses in a row
  (it didn't work, its program broke, or it ran out of time; being hurt or stopped
  isn't the move's fault and doesn't count), with the reason kept for the Learning
  page and her summary. Learning it again, or the user switching it back on, starts
  the count over.
- **A learned program is tidied once** (``practice.tidy``, after the lesson ends): the
  program that worked was written for that moment, often with fixed coordinates, ids
  and leftover logging, so a tidy copy looks things up instead. It hasn't been run, so
  the one she learned stays in ``original_code``: if the tidied copy misses before it
  ever works, the move goes back to the original (that miss isn't counted against it).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DesktopSession, GameLearnedSkill, GameSkillAttempt

PREFIX = "learned_"
NAME_WORDS = 4
MAX_TIMES = 10
DEFAULT_SECONDS = 120
MAX_SECONDS = 300
MISS_LIMIT = 3  # misses in a row that switch a move off
WORKED = "done"
MISSED = {"failed", "timeout", "error"}  # the move's own fault; hurt/stopped/lost aren't
_STOPWORDS = {
    "a", "an", "the", "by", "and", "or", "to", "of", "with", "in", "on", "at", "for", "from", "into", "onto",
    "it", "its", "my", "me", "myself", "your", "yourself", "you", "i", "one", "some", "then", "so", "that",
    "how", "up", "down",
}


def name_for(task: str) -> str:
    """learned_<up to four words of the task>: "climb by jumping and placing a block beneath
    yourself" -> learned_climb_jumping_placing_block. Same words, same move."""
    words = [w for w in re.findall(r"[a-z0-9]+", task.lower()) if w not in _STOPWORDS]
    return (PREFIX + "_".join(words[:NAME_WORDS] or ["move"]))[:64]


def is_learned(skill: str | None) -> bool:
    return bool(skill) and skill.startswith(PREFIX)


def save_learned(db: Session, attempt: GameSkillAttempt) -> GameLearnedSkill | None:
    """Keep the program of an attempt that worked. The caller commits."""
    if attempt.status != "learned" or not attempt.code:
        return None
    name = name_for(attempt.task)
    row = db.scalar(select(GameLearnedSkill).where(
        GameLearnedSkill.user_id == attempt.user_id, GameLearnedSkill.game == attempt.game,
        GameLearnedSkill.name == name))
    if row is None:
        row = GameLearnedSkill(user_id=attempt.user_id, game=attempt.game, name=name, task=attempt.task,
                               code=attempt.code, attempt_id=attempt.id)
        db.add(row)
    else:  # learned again: the newer program, switched back on, untidied, its count started over
        row.task, row.code, row.attempt_id, row.enabled = attempt.task, attempt.code, attempt.id, True
        row.misses_in_row, row.off_reason = 0, None
        row.original_code, row.tidy_note, row.tidy_proven = None, None, False
    db.flush()
    return row


def save_tidied(db: Session, attempt_id: uuid.UUID, code: str, note: str) -> bool:
    """Use the tidied copy of the program an attempt learned, keeping the original. Only
    if the move still has that attempt's program as learned (relearned or tidied
    meanwhile: nothing). The caller commits."""
    row = db.scalar(select(GameLearnedSkill).where(GameLearnedSkill.attempt_id == attempt_id))
    if row is None or row.original_code is not None or not code.strip() or code.strip() == row.code.strip():
        return False
    row.original_code, row.code = row.code, code
    row.tidy_note, row.tidy_proven = note[:500] or "tidied", False
    db.flush()
    return True


def for_user(db: Session, user_id: uuid.UUID, game: str, *, enabled_only: bool = True) -> list[GameLearnedSkill]:
    query = select(GameLearnedSkill).where(GameLearnedSkill.user_id == user_id, GameLearnedSkill.game == game)
    if enabled_only:
        query = query.where(GameLearnedSkill.enabled.is_(True))
    return list(db.scalars(query.order_by(GameLearnedSkill.created_at)))


def for_session(db: Session, session_id: uuid.UUID) -> list[dict]:
    """Her hands' view: the enabled moves of the session's owner, as plain dicts."""
    from app.services.game_learning import game_for_profile

    session = db.get(DesktopSession, session_id)
    if session is None:
        return []
    game = game_for_profile(session.profile_id) or "minecraft"
    return [{"name": s.name, "task": s.task, "code": s.code, "uses": s.uses, "successes": s.successes}
            for s in for_user(db, session.owner_user_id, game)]


def manifest_entry(skill: dict) -> dict:
    """A learned move in the pack manifest's shape, so the hands' schema and prompt take it like any skill."""
    used = (f" Used {skill['uses']} time{'s' if skill['uses'] != 1 else ''}, worked {skill['successes']}."
            if skill.get("uses") else " Not used yet.")
    return {
        "name": skill["name"],
        "description": f'A move you taught yourself: "{skill["task"]}".{used}',
        "params": {
            "times": {"type": "integer", "min": 1, "max": MAX_TIMES, "default": 1,
                      "description": "how many times in a row (it stops at the first time it doesn't work)"},
            "time_limit": {"type": "number", "min": 1, "max": MAX_SECONDS, "default": DEFAULT_SECONDS,
                           "description": f"seconds before it gives up (default {DEFAULT_SECONDS})"},
        },
        "done_when": "it worked every time asked",
        "stops_when": "a time doesn't work, or you're hurt or stopped",
    }


def program(code: str, times: int) -> str:
    """The saved program, run ``times`` times in a row. A run that returns {done: false} stops it."""
    times = max(1, min(MAX_TIMES, int(times)))
    return (
        "const __move = async () => {\n" + code + "\n};\n"
        f"const __times = {times};\n"
        "let __last = null;\n"
        "for (let __i = 0; __i < __times; __i++) {\n"
        "  __last = await __move();\n"
        "  if (__last && typeof __last === 'object' && __last.done === false) {\n"
        "    return { done: false, times_done: __i, reason: __last.reason ?? __last.missing ?? null, last: __last };\n"
        "  }\n"
        "}\n"
        "return { done: true, times_done: __times, last: __last };\n"
    )


def outcome(record: dict, skill: str, params: dict) -> dict:
    """A finished program's record as a skill outcome {skill, params, status, reason, data, seconds}."""
    status = record.get("status")
    value = record.get("value") if isinstance(record.get("value"), dict) else {}
    reason = record.get("reason")
    if status == "done" and value.get("done") is False:
        status = "failed"
        reason = f"time {value.get('times_done', 0) + 1} didn't work" + (f": {value['reason']}" if value.get("reason") else "")
    elif status == "error":
        status, reason = "failed", f"the move's program broke: {reason}"
    data = {"times_done": value.get("times_done")} if value else {}
    return {"skill": skill, "params": params, "status": status, "reason": reason, "data": data,
            "seconds": record.get("seconds")}


def record_use(db: Session, session_id: uuid.UUID, name: str, status: str, reason: str | None = None) -> str | None:
    """Count one use of a move by how it ended. Returns what changed about the move for
    her hands to hear (switched off, back to the original program), or None."""
    from app.services.game_learning import game_for_profile

    session = db.get(DesktopSession, session_id)
    if session is None:
        return None
    row = db.scalar(select(GameLearnedSkill).where(
        GameLearnedSkill.user_id == session.owner_user_id,
        GameLearnedSkill.game == (game_for_profile(session.profile_id) or "minecraft"),
        GameLearnedSkill.name == name))
    if row is None or (status != WORKED and (status not in MISSED or reason == "died")):
        return None  # hurt, stopped, killed: not the move's doing
    row.uses += 1
    row.last_used_at = datetime.now(timezone.utc)
    note = None
    if status == WORKED:
        row.successes += 1
        row.misses_in_row = 0
        row.tidy_proven = True
    elif row.original_code is not None and not row.tidy_proven:
        row.code, row.original_code = row.original_code, None
        row.tidy_note = f"the tidied copy didn't work ({(reason or status)[:200]}), so it's back to the one she learned"
        note = "the tidied copy of this move didn't work, so it's back to the program you learned; try it again"
    else:
        row.misses_in_row += 1
        if row.misses_in_row >= MISS_LIMIT:
            row.enabled = False
            row.off_reason = f"didn't work {row.misses_in_row} times in a row (last: {(reason or status)[:200]})"
            note = f"that's {row.misses_in_row} misses in a row, so this move is switched off"
    db.commit()
    return note


def summary_lines(db: Session, user_id: uuid.UUID, game: str) -> list[str]:
    """For Columbina's game summary: the moves she has, so she plans with them, and the ones
    that switched themselves off."""
    skills = for_user(db, user_id, game)
    if not skills:
        return off_lines(db, user_id, game)
    lines = ["Moves you taught yourself (your hands have them as skills, any session; times = how many in a "
             "row). Put them in the plan; don't offer to learn them again:"]
    lines += [f'- {s.name}: "{s.task}"' + (f" (used {s.uses}, worked {s.successes})" if s.uses else "")
              for s in skills]
    return lines + off_lines(db, user_id, game)


def off_lines(db: Session, user_id: uuid.UUID, game: str) -> list[str]:
    """Moves that switched themselves off, so she doesn't plan with them or think she still has them."""
    rows = [r for r in for_user(db, user_id, game, enabled_only=False) if not r.enabled and r.off_reason]
    if not rows:
        return []
    return ["Moves of yours that stopped working and switched themselves off (don't plan with them; if one is "
            "needed, offer to learn it again):"] + [f'- "{r.task}": {r.off_reason}' for r in rows]


# --------------------------------------------------------------------------- #
# The Learning page
# --------------------------------------------------------------------------- #


def listing(db: Session, user_id: uuid.UUID) -> list[dict]:
    rows = db.scalars(select(GameLearnedSkill).where(GameLearnedSkill.user_id == user_id)
                      .order_by(GameLearnedSkill.created_at.desc()))
    return [{"id": str(r.id), "game": r.game, "name": r.name, "task": r.task, "code": r.code,
             "enabled": r.enabled, "uses": r.uses, "successes": r.successes,
             "misses_in_row": r.misses_in_row, "off_reason": r.off_reason, "original_code": r.original_code,
             "tidy_note": r.tidy_note, "tidy_proven": r.tidy_proven,
             "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
             "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()} for r in rows]


def _own(db: Session, user_id: uuid.UUID, skill_id: uuid.UUID) -> GameLearnedSkill | None:
    row = db.get(GameLearnedSkill, skill_id)
    return row if row is not None and row.user_id == user_id else None


def set_enabled(db: Session, user_id: uuid.UUID, skill_id: uuid.UUID, enabled: bool) -> bool:
    row = _own(db, user_id, skill_id)
    if row is None:
        return False
    row.enabled = enabled
    if enabled:  # the user gives it another go: its count starts over
        row.misses_in_row, row.off_reason = 0, None
    db.commit()
    return True


def delete(db: Session, user_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
    row = _own(db, user_id, skill_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
