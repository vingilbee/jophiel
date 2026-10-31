"""The two conversational decisions around background thinking, and what cognition can decide today
(docs/SHARED_STATE_STEP4_SPEC.md, "Mouth and result cognition"). Pure.

What the agent decides is cognition's: whether to acknowledge, answer a part, or stay silent while she thinks,
and whether a finished thought is said, asked, deferred or kept. Both decisions go through these interfaces,
and only cognition answers them. The coordinator never picks a social behaviour itself; when cognition can't
decide, the result says so (``supported=False``, with the missing capability), and the coordinator applies a
labelled fallback, never a hidden one.

Who decides (2026-10-28):

- While she thinks (acknowledge / partial / silent / respond): Student 2, live, decides it with the request: its
  ``communication`` is the immediate action (``shared_state.prepare``). Nothing else is asked.
- A finished thought (respond / ask / defer / silent): Student 2 reads it as [T1], "just finished", with the
  thinking behind it (trigger ``thought_result``; ``work_outcome`` when the work ended without a conclusion) and
  decides whether and how to bring it up (``cognition_decisions.student_result``). ``defer`` has no store yet, so
  it is silence.
- When Student 2 can't decide (server down, unreadable or invalid answer), the decision is unsupported, said as
  such: a request's reply falls back to a labelled ``respond``, and a finished thought stays private.

Student 1 has no input or output for either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PendingAction = Literal["acknowledge", "partial", "silent", "respond"]
ResultAction = Literal["respond", "ask", "defer", "silent"]

PENDING_GAP = ("Student 1/2 have no input for 'a thought of hers is pending' and no output for "
               "acknowledge / partial / silent (Student 2's 20 move groups have no silence or hold option).")
RESULT_GAP = ("No result decision: Student 2 wasn't asked, and Student 1 has no input slot for a finished thought "
              "or output for respond / ask / defer / silent about it.")


@dataclass(frozen=True)
class PendingWork:
    """What the mouth may know about work in progress. ``status`` is real: queued is not thinking."""

    work_id: str
    kind: str  # recall / mind
    status: str  # queued / waiting_for_memory / running / ...
    subject: str  # what it's about, in words from the request (never a conclusion)
    unresolved: str  # what part of the request waits on it


@dataclass(frozen=True)
class PendingInput:
    request_id: str
    trigger_ref: str
    interpretation: dict | None  # Student 1's reading of the message
    pending: tuple[PendingWork, ...]
    channel: str
    voice: bool


@dataclass(frozen=True)
class PendingDecision:
    action: PendingAction | None
    origin: str  # student_2 / unsupported / policy_fallback / stub (tests only)
    supported: bool
    missing_capability: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ThoughtResult:
    work_id: str
    kind: str | None  # hard.py's conclusion kind: decision / guess / question / intention / feeling; None: none
    conclusion: str | None
    question: str | None
    uncertain: bool  # a guess, or a thought that rests on failed or missing evidence
    evidence_refs: tuple[str, ...] = ()
    evidence_status: str | None = None  # the recall dependency's outcome, when there was one
    failed: str | None = None  # the work failed or expired: its error code


@dataclass(frozen=True)
class ResultInput:
    request_id: str
    trigger_ref: str
    result: ThoughtResult
    interpretation: dict | None  # the trigger message's reading
    newer_user_messages: int  # since the trigger: the conversation moved on
    immediate_decision: str | None  # what the immediate phase did (respond / silent / ...)
    channel: str
    phase: str = "thought_result"
    user_id: str | None = None
    activity_id: str | None = None
    conversation_id: str | None = None
    input_versions: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResultDecision:
    action: ResultAction | None
    origin: str
    supported: bool
    missing_capability: str | None = None
    reason: str | None = None
    extra: dict = field(default_factory=dict)
