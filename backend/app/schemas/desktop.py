"""Request/response schemas for the desktop agent (plan §17-18).

Text fields that end up in a model prompt (goal, steps, constraints) are
bounded here, so the service never has to re-check lengths. None of them is
ever a permission: authorization lives on the environment record.
"""
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SessionState = Literal["starting", "running", "paused", "manual", "disconnected", "ended", "failed"]
ControlOwner = Literal["none", "gameplay", "manual"]
CommentaryMode = Literal["quiet", "occasional", "commentary"]
StepState = Literal["pending", "active", "done", "skipped", "abandoned"]
# columbina_mind: her planner revising the plan she's on (app/ai/mind/plan.py); a new goal it sets
# is columbina_user_turn (the user asked for it) or columbina_planning_turn (she picked it).
PlanSource = Literal["columbina_planning_turn", "columbina_user_turn", "columbina_mind", "user_api"]
CaptureGate = Literal["allowed", "blocked", "manual_auth"]
CaptureScope = Literal["desktop", "game_window"]

ProfileId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
# A rule her hands keep for the goal: whole, never cut (a cut rule was copied forward cut, seen live 2026-11-05).
RuleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PlanText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- profiles and environments ---------------------------------------------


class ProfileOut(BaseModel):
    id: str
    revision: int
    display_name: str
    # Decision points are in this frame's pixels (plan §7.8).
    observation_size: tuple[int, int]


class EnvironmentCreate(_In):
    """Stage A registration by the owner; stage B replaces it with pairing."""

    display_name: PlanText = Field(max_length=64)
    guest_os: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] | None = None
    allowed_profile_ids: list[ProfileId] = Field(min_length=1, max_length=16)


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    hosting_kind: str
    guest_os: str | None
    allowed_profile_ids: list[str]
    policy_revision: int
    status: Literal["offline", "online"]
    last_heartbeat_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    paired_at: datetime | None
    controller_version: str | None
    capture_gate: CaptureGate
    capture_scope: CaptureScope
    # The VM has acknowledged the current capture setting (vs. only requested).
    capture_confirmed: bool
    # Why there is or isn't a picture, as last reported by the VM.
    target_state: str | None
    # The game's input mode during a session ("playing", "menu", "unknown").
    input_context: str | None
    # Security monitoring of the VM's event logs: the owner's own switch (apart from capture and input).
    security_monitoring: bool = False
    security_changed_at: datetime | None = None


class CaptureGateIn(_In):
    gate: CaptureGate | None = None
    scope: CaptureScope | None = None


class SecurityMonitoringIn(_In):
    enabled: bool


class SecurityChannelOut(BaseModel):
    channel: str
    # readable | unavailable | waiting (no report about it yet)
    status: str
    reason: str | None = None
    # Coverage so far ends here (the VM's clock).
    through: str | None = None


class SecurityStatusOut(BaseModel):
    monitoring: bool
    changed_at: datetime | None
    # online / offline / unknown: the source as observation last knew it.
    source: str
    last_report_at: str | None
    skew_seconds: float | None
    channels: list[SecurityChannelOut]


class PairingOut(BaseModel):
    """Shown once. The code is never retrievable again."""

    code: str
    expires_at: datetime
    # What the VM dials: `desktop-controller pair <relay_address> <code>`.
    relay_address: str
    # For an optional by-eye check against what the controller prints.
    relay_fingerprint: str


# --- plan ---------------------------------------------------------------------


class StepCheck(_In):
    """A step's done_when in a form code counts (app/ai/desktop/step_check.py)."""

    kind: Literal["has", "gained", "killed", "top_here", "peak", "near_player"]
    what: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    count: int | None = Field(default=None, ge=1, le=2304)


class PlanStepIn(_In):
    do: ShortText
    done_when: ShortText
    check: StepCheck | None = None


class PlanIn(_In):
    goal: PlanText
    steps: list[PlanStepIn] = Field(min_length=1, max_length=50)  # the configured cap is enforced by the service
    if_stuck: PlanText | None = None
    constraints: list[RuleText] = Field(default_factory=list, max_length=5)
    # The item or block the goal is about (a game id, e.g. iron_ore): her planner works out
    # what it takes to get it (services/game_planning).
    target: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,64}$")] | None = None


class PlanReplace(PlanIn):
    expected_plan_revision: int = Field(ge=0)
    source: PlanSource = "user_api"


class PlanAppend(_In):
    expected_plan_revision: int = Field(ge=1)
    steps: list[PlanStepIn] = Field(min_length=1, max_length=50)


class PlanStepOut(BaseModel):
    id: str
    do: str
    done_when: str
    state: StepState
    check: dict | None = None


class PlanOut(BaseModel):
    plan_revision: int
    goal: str
    steps: list[PlanStepOut]
    if_stuck: str | None
    constraints: list[str]
    source: PlanSource


# --- sessions -------------------------------------------------------------------


class SessionCreate(_In):
    environment_id: uuid.UUID
    profile_id: ProfileId
    conversation_id: uuid.UUID | None = None
    commentary_mode: CommentaryMode = "occasional"
    max_duration_seconds: int | None = Field(default=None, gt=0)
    plan: PlanIn | None = None
    # A retried start (e.g. a repeated tool call) with the same key returns the
    # same session instead of starting a second one.
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=64)] | None = None


class SessionCommand(_In):
    reason: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")] | None = None


class SessionOptions(_In):
    autonomy: Literal["continue", "stop"] | None = None
    commentary_mode: CommentaryMode | None = None
    explore: bool | None = None  # stage H3: she picks her own challenges after a goal


class SessionResume(_In):
    expected_state_revision: int = Field(ge=1)


class SessionOut(BaseModel):
    id: uuid.UUID
    environment_id: uuid.UUID
    conversation_id: uuid.UUID | None
    profile_id: str
    profile_revision: int
    state: SessionState
    state_revision: int
    reason_code: str | None
    control_owner: ControlOwner
    execution_generation: int
    plan_revision: int
    plan: PlanOut | None
    current_step_id: str | None
    driver_status: str | None
    commentary_mode: CommentaryMode
    autonomy: Literal["continue", "stop"] = "continue"
    explore: bool = True
    max_duration_seconds: int
    started_at: datetime
    ended_at: datetime | None
    # Pause/takeover/end ask the controller to release held input. Until the
    # controller acknowledges (stage B), release is only requested, never
    # confirmed: the UI must not claim the game is safely idle.
    release_pending: bool = False


class SessionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    state_revision: int
    payload: dict
    occurred_at: datetime


class DecisionIn(_In):
    """A hand-written decision (plan §7.2), for stage C testing."""

    decision: dict
    decision_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")] | None = None


class DecisionSent(BaseModel):
    decision_id: str
