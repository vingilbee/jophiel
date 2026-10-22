"""Cognition's two decisions around background thinking, backed by the trained student (contracts and the
capability table: ``app/ai/state/decisions.py``).

The student can't make either decision yet, so both providers answer ``supported=False`` with the missing
capability, and never guess. When the student gains these inputs and outputs, these functions are where it
plugs in, and nothing else in the coordinator changes. Tests replace ``PROVIDERS`` with scripted
decisions to drive every branch; production has no other provider (no teacher or extra model).
"""
from __future__ import annotations

from collections.abc import Callable

from app.ai.state.decisions import (
    PENDING_GAP, RESULT_GAP, PendingDecision, PendingInput, ResultDecision, ResultInput,
)


def student_pending(inp: PendingInput) -> PendingDecision:
    """Acknowledge / answer a part / stay silent while she thinks. Student 1 and 2 have no input or output for
    it: unsupported."""
    return PendingDecision(None, "unsupported", False, missing_capability=PENDING_GAP)


def student_result(inp: ResultInput) -> ResultDecision:
    """Say / ask / defer / keep a finished thought. The student reads only user messages: unsupported."""
    return ResultDecision(None, "unsupported", False, missing_capability=RESULT_GAP)


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
        return ResultDecision(None, "error", False, reason=f"{type(exc).__name__}: {exc}"[:200])
