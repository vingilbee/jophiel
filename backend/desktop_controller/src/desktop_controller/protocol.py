"""Wire contracts: the driver's decision (plan §7.2) and step output (§7.2.1).

These models only check *shape*. Whether a decision is allowed for the active
game, context and frame is decided by ``validate.validate_decision``, which needs
the profile. Names here are profile names (``forward``), never physical keys.

Every field is optional (``{}`` releases everything), with defaults filled in by the
validator from the profile, so a typical decision stays around 40 tokens.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

Name = Annotated[str, Field(min_length=1, max_length=32)]
Ms = Annotated[int, Field(ge=0, le=60_000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CropPoint(_Strict):
    """A point inside a named crop, in that crop's own pixels."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    crop: Name = Field(alias="in")
    xy: tuple[int, int]


# A plain [x, y] is pixels in the latest full frame.
Point = Union[tuple[int, int], CropPoint]


class TapEvent(_Strict):
    type: Literal["tap"]
    at_ms: Ms = 0
    button: Name
    hold_ms: Ms | None = None
    point: Point | None = None


class ClickEvent(_Strict):
    type: Literal["click"]
    at_ms: Ms = 0
    button: Name
    point: Point


class DragEvent(_Strict):
    type: Literal["drag"]
    at_ms: Ms = 0
    button: Name
    path: list[Point] = Field(min_length=2)
    duration_ms: Ms = 300


class MoveEvent(_Strict):
    type: Literal["move"]
    at_ms: Ms = 0
    point: Point
    duration_ms: Ms = 0


class ScrollEvent(_Strict):
    type: Literal["scroll"]
    at_ms: Ms = 0
    amount: int
    point: Point | None = None


class MacroEvent(_Strict):
    type: Literal["macro"]
    at_ms: Ms = 0
    name: Name


class LookAtEvent(_Strict):
    type: Literal["look_at"]
    at_ms: Ms = 0
    point: Point
    duration_ms: Ms = 150


class TurnEvent(_Strict):
    """Turn the view by an exact amount, then stop (relative degrees).

    Unlike a look axis (a rate that keeps turning until the decision ends or
    is replaced), the total turn doesn't depend on when the next decision
    arrives. The speed is capped by the look axes' ``max`` (deg/s), so a large
    turn takes proportionally longer; ``duration_ms`` can only slow it down.
    """

    type: Literal["turn"]
    at_ms: Ms = 0
    yaw: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)] = 0.0  # + right, - left
    pitch: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)] = 0.0  # + down, - up
    duration_ms: Ms | None = None


Event = Annotated[
    Union[TapEvent, ClickEvent, DragEvent, MoveEvent, ScrollEvent, MacroEvent, LookAtEvent, TurnEvent],
    Field(discriminator="type"),
]

EVENT_KINDS: frozenset[str] = frozenset(
    {"tap", "click", "drag", "move", "scroll", "macro", "look_at", "turn"}
)


class ChangedCondition(_Strict):
    region: Name
    condition: Literal["changed"]
    threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.3


class StaticCondition(_Strict):
    region: Name
    condition: Literal["static"]
    for_ms: Annotated[int, Field(ge=50, le=10_000)] = 400


UntilCondition = Annotated[
    Union[ChangedCondition, StaticCondition], Field(discriminator="condition")
]


class Until(_Strict):
    any: list[UntilCondition] = Field(min_length=1)


class Decision(_Strict):
    """The complete desired input state. Anything not listed is released.

    Every field is optional: ``{}`` is the valid "release everything" decision.
    """

    intent: Annotated[str, Field(max_length=60)] = ""
    hold: list[Name] = Field(default_factory=list)
    axes: dict[Name, float] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    until: Until | None = None
    valid_for_ms: Ms | None = None
    wait_ms: Ms | None = None


class StepStatus(StrEnum):
    WORKING = "working"
    STEP_DONE = "step_done"
    STUCK = "stuck"
    NEEDS_HUMAN = "needs_human"


class Fact(_Strict):
    key: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")]
    value: Annotated[str, Field(max_length=80)]
    source: Annotated[str, Field(max_length=32)] | None = None


class ReportEntry(_Strict):
    kind: Literal["notable", "danger", "progress"]
    text: Annotated[str, Field(min_length=1, max_length=160)]


class Answer(_Strict):
    question_id: Annotated[str, Field(min_length=1, max_length=64)]
    text: Annotated[str, Field(max_length=200)]
    confidence: Literal["seen", "remembered", "unknown"]


class StepOutput(_Strict):
    """What the driver returns each step. ``decision`` comes first on the wire.

    Omitted fields mean "unchanged": no status means still working, no step_id
    means the same step, no notes means keep the previous notes. Everything except
    ``decision``, ``status`` and ``step_id`` is untrusted game observation.
    """

    decision: Decision
    step_id: Annotated[str, Field(max_length=16)] | None = None
    status: StepStatus | None = None
    notes: Annotated[str, Field(max_length=200)] | None = None
    facts: list[Fact] = Field(default_factory=list, max_length=12)
    report: list[ReportEntry] = Field(default_factory=list, max_length=4)
    answers: list[Answer] = Field(default_factory=list, max_length=4)


class RejectionCode(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    UNKNOWN_BUTTON = "unknown_button"
    UNKNOWN_AXIS = "unknown_axis"
    UNKNOWN_REGION = "unknown_region"
    UNKNOWN_CROP = "unknown_crop"
    UNKNOWN_MACRO = "unknown_macro"
    CONTEXT_MISMATCH = "context_mismatch"
    POINT_OUTSIDE_VIEWPORT = "point_outside_viewport"
    LIMIT_EXCEEDED = "limit_exceeded"
    CONFLICTING_INPUT = "conflicting_input"
    BLOCKED_COMBINATION = "blocked_combination"
    LOOK_AT_UNCALIBRATED = "look_at_uncalibrated"
    STALE_FRAME = "stale_frame"
    STALE_PLAN_REVISION = "stale_plan_revision"
    STALE_GENERATION = "stale_generation"
    NO_LEASE = "no_lease"
    DUPLICATE_CONFLICT = "action_duplicate_conflict"


class Rejection(_Strict):
    code: RejectionCode
    field: str
    reason: str

    def render(self) -> str:
        """The line the driver sees in its next action report (plan §7.9)."""
        return f"last decision REJECTED: {self.code}: {self.reason} ({self.field})"
