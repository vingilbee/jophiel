"""What changed in her game since a moment: counted by code, no model.

A reply, a thought or a background turn is written from one picture of her game,
and writing takes seconds while the game doesn't wait. Seen live (2026-10-17): she
crafted 32 planks in 0.3 s, then a hard thought that had started before it told her
she had "4 logs and 17 planks" and was paused (she wasn't); a lesson finished 2 s
before she said she hadn't learned it yet.

``capture`` keeps the facts a sentence most often gets wrong: playing or paused, the
goal and step, what she carries, her health and height, a lesson. ``changes`` says in
plain words what's different now. Callers decide what to do with it: her tool results
carry it, a thought written before it isn't said or is marked out of date, and a
reply it contradicts gets a short correction (services/game_correction.py).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DesktopEnvironment, DesktopSession, GameSkillAttempt

# How many inventory items one change line names (a long list is noise).
MAX_ITEMS = 6
# Height changes smaller than this are walking about, not news.
HEIGHT_BLOCKS = 4
# Health points (of 20) lost before it counts as getting hurt.
HURT_POINTS = 2

# The kinds of change ``changes`` reports, for callers that only care about some.
STATE, PLAN, STEP, LESSON, INVENTORY, HEALTH, HEIGHT = (
    "state", "plan", "step", "lesson", "inventory", "health", "height")
# The game moving on by itself: not her goal or steps, which a turn may have changed itself.
WORLD = frozenset({STATE, LESSON, INVENTORY, HEALTH, HEIGHT})


@dataclass(frozen=True)
class Moment:
    at: float
    session_id: uuid.UUID
    state: str
    reason_code: str | None
    plan_revision: int
    goal: str | None
    step_id: str | None
    steps: tuple[tuple[str, str, str], ...]  # (id, state, do)
    inventory: dict[str, int] | None  # None: no game state (not her own player, or it was unreachable)
    health: float | None  # points of 20
    y: float | None
    lesson: tuple[str, str, str] | None  # (attempt id, task, status) of the session's latest lesson


def _inventory(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state.get("inventory") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            counts[item["name"]] = counts.get(item["name"], 0) + int(item.get("count") or 0)
    return counts


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _lesson(db: Session | None, session_id: uuid.UUID) -> tuple[str, str, str] | None:
    if db is None:
        return None
    attempt = db.scalar(select(GameSkillAttempt).where(GameSkillAttempt.session_id == session_id)
                        .order_by(GameSkillAttempt.created_at.desc()).limit(1))
    return (str(attempt.id), attempt.task, attempt.status) if attempt is not None else None


def capture(db: Session | None, session, state: dict | None, *, at: float | None = None) -> Moment:
    """The moment from a session row and the pack's state (None when there is none).
    ``db`` may be None (no lesson then)."""
    plan = getattr(session, "plan", None) or {}
    steps = tuple((s.get("id", ""), s.get("state", ""), s.get("do", "")) for s in plan.get("steps") or [])
    step_id = getattr(session, "current_step_id", None) or next(
        (sid for sid, st, _ in steps if st in ("active", "pending")), None)
    state = state if isinstance(state, dict) else None
    position = (state or {}).get("position") or {}
    return Moment(
        at=time.time() if at is None else at, session_id=session.id, state=session.state,
        reason_code=getattr(session, "reason_code", None), plan_revision=getattr(session, "plan_revision", 0),
        goal=plan.get("goal"), step_id=step_id, steps=steps,
        inventory=_inventory(state) if state is not None else None,
        health=_number((state or {}).get("health")), y=_number(position.get("y")),
        lesson=_lesson(db, session.id),
    )


def now(db: Session, session_id: uuid.UUID) -> Moment | None:
    """The moment as it is now (asks her own player for its state). None: no such session."""
    session = db.get(DesktopSession, session_id)
    if session is None:
        return None
    db.refresh(session)
    state = None
    env = db.get(DesktopEnvironment, session.environment_id)
    from app.services import game_pack

    if env is not None and game_pack.is_pack(env) and env.status == "online":
        from app.ai.desktop.pack_client import PackClient, PackError

        try:
            state = PackClient.from_settings().state()
        except PackError:
            state = None
    return capture(db, session, state)


def _do(moment: Moment, step_id: str | None) -> str:
    return next((do for sid, _, do in moment.steps if sid == step_id), step_id or "")


def _step_state(moment: Moment, step_id: str | None) -> str | None:
    return next((st for sid, st, _ in moment.steps if sid == step_id), None)


def _items(before: dict[str, int], after: dict[str, int]) -> str | None:
    changed = [(name, before.get(name, 0), after.get(name, 0)) for name in sorted(set(before) | set(after))
               if before.get(name, 0) != after.get(name, 0)]
    if not changed:
        return None
    changed.sort(key=lambda c: -abs(c[2] - c[1]))
    text = ", ".join(f"{name} {was} -> {now}" for name, was, now in changed[:MAX_ITEMS])
    return text + (f" (and {len(changed) - MAX_ITEMS} more)" if len(changed) > MAX_ITEMS else "")


def changes(before: Moment | None, after: Moment | None,
            kinds: frozenset[str] | None = None) -> list[str]:
    """What's different in ``after``, one plain line per change, said to her ("you ..."). Empty
    when nothing she'd say is affected changed, or either moment is missing. ``kinds``: only these."""
    if before is None or after is None or before.session_id != after.session_id:
        return []
    found: list[tuple[str, str]] = []
    if after.state != before.state:
        from app.services.desktop_game import REASONS

        why = REASONS.get(after.reason_code or "", after.reason_code or "")
        if after.state == "running":
            found.append((STATE, f"you're playing again (you were {before.state})"))
        elif after.state == "paused":
            found.append((STATE, "you're paused now" + (f" ({why})" if why else "")))
        else:
            found.append((STATE, f"your game is now {after.state}" + (f" ({why})" if why else "")))
    if after.plan_revision != before.plan_revision:
        if after.goal != before.goal:
            found.append((PLAN, f'your goal changed to "{after.goal}"' if after.goal else "your goal was cleared"))
        else:
            found.append((PLAN, "your plan's steps changed"))
    elif after.step_id != before.step_id and before.step_id is not None:
        ended = _step_state(after, before.step_id) or "over"
        line = f'the step "{_do(before, before.step_id)}" is {ended}'
        line += f'; now on "{_do(after, after.step_id)}"' if after.step_id else "; no steps left"
        found.append((STEP, line))
    if after.lesson is not None and after.lesson != before.lesson:
        _, task, status = after.lesson
        new = before.lesson is None or before.lesson[0] != after.lesson[0]
        if status == "learned":
            from app.services.game_skills import name_for

            found.append((LESSON, f'you learned how to "{task}": your hands have it now as {name_for(task)}'))
        elif status == "failed":
            found.append((LESSON, f'you couldn\'t work out how to "{task}"'))
        elif status == "stopped":
            found.append((LESSON, f'you stopped learning "{task}"'))
        elif new:
            found.append((LESSON, f'you started learning "{task}"'))
    if before.inventory is not None and after.inventory is not None:
        items = _items(before.inventory, after.inventory)
        if items:
            found.append((INVENTORY, f"what you carry: {items}"))
    if before.health is not None and after.health is not None and before.health - after.health >= HURT_POINTS:
        found.append((HEALTH, f"you got hurt: {after.health / 2:g} of 10 hearts now (was {before.health / 2:g})"))
    if before.y is not None and after.y is not None and abs(after.y - before.y) >= HEIGHT_BLOCKS:
        found.append((HEIGHT, f"you went {'up' if after.y > before.y else 'down'} {abs(after.y - before.y):.0f} "
                              f"blocks (height {before.y:.0f} -> {after.y:.0f})"))
    return [text for kind, text in found if kinds is None or kind in kinds]


def since(db: Session, before: Moment | None, kinds: frozenset[str] | None = None) -> list[str]:
    """``changes`` from ``before`` to now; never fails (no lines on any error)."""
    if before is None:
        return []
    try:
        return changes(before, now(db, before.session_id), kinds)
    except Exception:  # noqa: BLE001 - a check that can't run just reports nothing
        db.rollback()
        return []
