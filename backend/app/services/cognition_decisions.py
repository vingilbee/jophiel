"""Cognition's two decisions around background thinking, backed by the trained student (contracts and the
capability table: ``app/ai/state/decisions.py``).

- Pending (while she thinks): Student 2 decides it together with the request (``shared_state.prepare`` passes
  its choice to ``coordinator.decide_immediate``), so this provider is only asked when it didn't, and answers
  ``supported=False``.
- Result (a finished thought, or work that ended without one): Student 2 reads the situation as it is now, with
  the thought as [T1] (``student_two.gather_result``), and decides whether and how to bring it up. Its work part
  is carried out where an owner exists, and its decision reaches the follow-up's mouth as the brief. When it
  can't decide (server down, unreadable or invalid answer), the result is unsupported, said as such, and the
  thought stays private: never a guess.

Tests replace ``PROVIDERS`` with scripted decisions to drive every branch; production has no other provider
(no teacher or extra model).
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from app.ai.state import student_two as student_contract
from app.ai.state.decisions import PENDING_GAP, PendingDecision, PendingInput, ResultDecision, ResultInput
from app.db.models import CoordinatorWork
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Student 2's communication, as the result phase acts on it: anything that speaks goes out as one follow-up.
_RESULT_ACTION = {"respond": "respond", "acknowledge": "respond", "partial": "respond", "ask": "ask",
                  "silent": "silent", "defer": "defer"}


def student_pending(inp: PendingInput) -> PendingDecision:
    """Only asked when Student 2 didn't decide with the request: unsupported."""
    return PendingDecision(None, "unsupported", False, missing_capability=PENDING_GAP)


def student_result(inp: ResultInput) -> ResultDecision:
    """Student 2 on a finished thought: say it (respond / ask), keep it (silent), or come back to it (defer,
    which has no store yet: silence)."""
    from app.services import student_two

    with SessionLocal() as db:
        work = db.get(CoordinatorWork, uuid.UUID(inp.result.work_id))
        if work is None:
            return ResultDecision(None, "student_2", False, missing_capability="the work item is gone")
        live = student_two.gather_result(db, work)
    outcome = student_two.decide(live)
    if outcome.ok:
        with SessionLocal() as db:
            student_two.apply(db, outcome, user_id=work.user_id, message_id=None, routed=False,
                              conversation_id=work.conversation_id)
    student_two.log(outcome, user_id=work.user_id, conversation_id=work.conversation_id, trigger=live.trigger)
    if not outcome.ok:
        why = outcome.error or "; ".join(outcome.problems)[:200] or outcome.status
        return ResultDecision(None, "student_2", False, missing_capability=f"Student 2 {outcome.status}: {why}"[:300],
                              extra={"decision_id": outcome.decision_id})
    d = outcome.decision
    action = _RESULT_ACTION[d["communication"]]
    brief = (student_contract.brief(d, outcome.situation, effect=outcome.effect)
             if action in ("respond", "ask") else None)
    return ResultDecision(action, "student_2", True, reason=d["intention"].strip()[:200] or None,
                          extra={"decision_id": outcome.decision_id, "communication": d["communication"],
                                 "work": d["work"], "effect": outcome.effect, "brief": brief})


PROVIDERS: dict[str, Callable] = {"pending": student_pending, "result": student_result}


def decide_pending(inp: PendingInput) -> PendingDecision:
    try:
        return PROVIDERS["pending"](inp)
    except Exception as exc:  # noqa: BLE001 - a failed decision is an unsupported one, said as such
        return PendingDecision(None, "error", False, reason=f"{type(exc).__name__}: {exc}"[:200])


def decide_result(inp: ResultInput) -> ResultDecision:
    try:
        return PROVIDERS["result"](inp)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cognition_decisions: result decision failed")
        return ResultDecision(None, "error", False, reason=f"{type(exc).__name__}: {exc}"[:200])
