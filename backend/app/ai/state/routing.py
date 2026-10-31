"""Where a user message goes after cognition and assessment (docs/SHARED_STATE_REFACTOR_SPEC.md "Lifecycle A";
docs/SHARED_STATE_STEP4_SPEC.md).

Pure. Two independent processing needs, each ``not_required`` / ``required`` / ``uncertain``, or ``None``
(nobody assessed it):

- recall: must memory be looked up before the mouth? From Student 2 (``view.recall``) when it decided, else from
  cognition's ``missing_evidence`` (Student 1 never assesses it, so that's ``None``).
- thinking: must she think it through first?

| Route | When | Now |
|---|---|---|
| ``fast_path`` | cognition valid, recall and thinking both not required | mouth; eligibility ``confirmed`` |
| ``recall`` | recall required, thinking not required/unknown | memory search, then mouth |
| ``mind`` / ``recall_then_mind`` | thinking required | background work committed (recall first, then the mind), mouth told at once; it never waits for either |
| ``unassessed`` | otherwise: some need unknown or uncertain, nothing known required | mouth; limits said |

These are processing needs, not conversational decisions. A route that permits the mouth never certifies she
knows the answer, and asking what they meant or using a tool stays the mouth's choice on every route. Only
``fast_path`` is fast-path eligible. ``uncertain`` and an assessor failure are never read as "not required":
they keep the turn ``unassessed`` (a failure is also degraded). Uncertain recall never starts a search on its
own (no arbitrary query): the unresolved need goes to the mouth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .contracts import SharedView

Route = Literal["fast_path", "unassessed", "recall", "mind", "recall_then_mind"]
SUPPORTED: frozenset[str] = frozenset({"fast_path", "unassessed", "recall", "mind", "recall_then_mind"})

_FROM_COGNITION = {"missing": "required", "none_missing": "not_required", "not_assessed": None}


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    supported: bool  # wired in this step
    degraded: tuple[str, ...]  # what makes this turn's answer knowingly less than the route needs
    limits: tuple[str, ...]  # what this turn could not assess or provide, said to the mouth
    reasons: tuple[str, ...]
    recall: str | None = None  # the recall answer the route used (None: not assessed)
    thinking: str | None = None

    @property
    def eligibility(self) -> str:
        """``confirmed``: valid cognition, recall and thinking not required. ``unassessed``: nothing known
        required, something unknown. ``not_eligible``: recall or thinking is required."""
        return {"fast_path": "confirmed", "unassessed": "unassessed"}.get(self.route, "not_eligible")

    @property
    def outcome(self) -> str:
        """``degraded`` wins; otherwise the route."""
        return "degraded" if self.degraded else self.route

    @property
    def runs_recall(self) -> bool:
        """A search before the mouth. For ``recall_then_mind`` the search is background work the mind waits
        for: the mouth doesn't."""
        return self.route == "recall"

    @property
    def schedules_mind(self) -> bool:
        return self.route in ("mind", "recall_then_mind")


def _need(name: str, value: str | None, by: str, limits: list[str], reasons: list[str], unknown_why: str) -> None:
    if value is None:
        limits.append(f"{name}_need_not_assessed")
        reasons.append(f"{name} need not assessed: {unknown_why}")
    elif value == "uncertain":
        limits.append(f"{name}_need_uncertain")
        reasons.append(f"{name} need uncertain ({by})")
    else:
        reasons.append(f"{name}: {value} ({by})")


def route(view: SharedView) -> RouteDecision:
    cognition, memory = view.cognition, view.memory
    reasons: list[str] = []
    degraded: list[str] = []
    limits: list[str] = []

    cognition_ok = cognition.meta.status == "available" and cognition.interpretation is not None
    if not cognition_ok:
        degraded.append("cognition_unavailable")
        reasons.append(f"cognition {cognition.meta.status}: {cognition.meta.reason or 'no reading'}")
    if view.recall.meta.status == "unavailable":
        degraded.append("decision_unavailable")
        reasons.append(f"Student 2 unavailable: {view.recall.meta.reason}")

    if view.recall.choice is not None:
        recall, recall_by = view.recall.choice, view.recall.origin
    else:
        recall, recall_by = _FROM_COGNITION[cognition.missing_evidence.assessment], "cognition"
    thinking_view = view.thinking
    if thinking_view.choice is not None:
        thinking = thinking_view.choice
    else:
        thinking = {True: "required", False: "not_required", None: None}[thinking_view.required]

    _need("recall", recall, recall_by, limits, reasons,
          view.recall.meta.reason or cognition.missing_evidence.reason or "no signal")
    _need("thinking", thinking, thinking_view.decider, limits, reasons, thinking_view.reason or "no decider")

    needs_recall, needs_mind = recall == "required", thinking == "required"
    unknown = recall in (None, "uncertain") or thinking in (None, "uncertain")
    taken: Route
    if needs_mind:
        taken = "recall_then_mind" if needs_recall else "mind"
    elif needs_recall:
        taken = "recall"
    elif unknown or not cognition_ok:
        taken = "unassessed"
    else:
        taken = "fast_path"
    supported = taken in SUPPORTED
    if not supported:
        degraded.append(f"{taken}_not_wired")

    if cognition.interpretation is not None and cognition.interpretation.request == "outside_ability":
        # Cognition's reading, not a route: the mouth's own tools and specialists are how she acts. Named so
        # the mouth doesn't promise what no tool of hers can do.
        limits.append("request_outside_talking")
        reasons.append("Student 1: the request needs more than talking")

    if memory.meta.status == "unavailable":
        limits.append("memory_cache_unavailable")
        reasons.append(f"memory cache unavailable: {memory.meta.reason}")
    elif memory.gone:
        limits.append("memory_cache_changed")
        reasons.append(f"{len(memory.gone)} cached memory gone since the last answer")

    return RouteDecision(taken, supported, tuple(degraded), tuple(limits), tuple(reasons), recall, thinking)
