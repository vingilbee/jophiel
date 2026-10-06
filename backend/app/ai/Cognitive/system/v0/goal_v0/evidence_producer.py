"""Pure producer of trusted Goal reconciliation evidence (SH-EVAL-014).

Goal lifecycle reconciliation already consumes typed ``GoalEvidence``, but
nothing produced it from ordinary conversation processing, so an active goal
could never reach a justified terminal state.  This module turns already
authorized, typed event projections into ``GoalEvidence`` for exact target
goals and requirements of a predecessor ``GoalSnapshot``.

Boundaries:

- It is pure.  Callers (the runtime, a worker, a reviewer) own database and
  provider access and hand over only the typed projections below.
- It never chooses a target.  Every event names its exact goal and
  requirement, and the producer rejects (raises) any event whose target is
  unknown, terminal, of another conversation or turn, or not authorized for
  that event class by the authority table.
- Generated text, selected actions, plans, sentiment, gratitude, continued
  conversation, silence, and Dynamic State have no event class and so can
  never produce evidence.  In particular no event lets an assistant claim
  satisfy a task goal: task goals accept only finalized tool results.
- Output is sorted by evidence id, so it is independent of event order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter, model_validator

from ..contracts.goals import (
    GOAL_EVIDENCE_POLICY_VERSION,
    GoalEvidence,
    GoalEvidenceProvenance,
    GoalRecord,
    GoalSnapshot,
    GoalStatus,
    GoalUpdateTrigger,
    RESPONSE_OBLIGATION_DEFINITION_KEY,
)
from ..contracts.interpretation import FrozenModel

PRODUCER_ID = "goal-v0-evidence-producer"


class GoalOutcomeEventKind(StrEnum):
    RESPONSE_DELIVERED = "response_delivered"
    USER_CONFIRMATION = "user_confirmation"
    USER_WITHDRAWAL = "user_withdrawal"
    GOAL_REPLACEMENT = "goal_replacement"
    DEFERRAL = "deferral"
    RESUMPTION = "resumption"
    TOOL_RESULT = "tool_result"
    ARGUMENT_GROUNDED = "argument_grounded"
    CONTRADICTION_RESOLVED = "contradiction_resolved"
    VERIFIED_IMPOSSIBILITY = "verified_impossibility"
    SCOPE_CLOSURE = "scope_closure"
    # Evidence policy v0.3: a validated delivered artifact operation.
    ARTIFACT_DELIVERED = "artifact_delivered"


class _Event(FrozenModel):
    """Fields every trusted event carries.

    ``producer`` names the trusted component that observed the event (the
    runtime, the tool executor, the delivery channel); ``source_ref`` is that
    component's own durable record.  ``occurred_turn_ref`` is the cognitive
    turn in which the event was observed.
    """

    event_id: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    occurred_turn_ref: str = Field(min_length=1)
    target_goal_ref: str = Field(min_length=1)
    target_requirement_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def _nonblank(self):
        for name in ("event_id", "producer", "source_ref", "conversation_ref", "occurred_turn_ref",
                     "target_goal_ref", "target_requirement_ref"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be blank")
        return self


class ResponseDeliveredEvent(_Event):
    """A non-empty response was actually delivered for ``delivered_turn_ref``.

    ``delivery_ref`` is the channel's delivery record (not the generated
    text).  ``addressed_target_ref`` is the requirement target the response
    addressed (topic, proposition, dialogue scope, failure).  For a goal whose
    policy needs validated content, ``validated_content_ref`` names the trusted
    validation of that content; without it delivery is only observed.
    """

    kind: Literal[GoalOutcomeEventKind.RESPONSE_DELIVERED] = GoalOutcomeEventKind.RESPONSE_DELIVERED
    delivered_turn_ref: str = Field(min_length=1)
    delivery_ref: str = Field(min_length=1)
    response_non_empty: Literal[True] = True
    addressed_target_ref: str | None = None
    validated_content_ref: str | None = None


class ArtifactDeliveredEvent(_Event):
    """A delivered response performed the exact requested artifact operation.

    Produced only from a validated delivered-response outcome
    (``contracts.workspace.DeliveredResponseOutcome``): ``delivery_ref`` is
    that outcome, ``operation`` the operation actually delivered,
    ``artifact_revision_ref`` the delivered revision (create/revise) or the
    exact revision presented (explain, evaluate, describe, compare,
    summarize), and ``validated_content_ref`` the validated realization.  A
    proposed revision, a selected move, or a shadow candidate has no event.
    """

    kind: Literal[GoalOutcomeEventKind.ARTIFACT_DELIVERED] = GoalOutcomeEventKind.ARTIFACT_DELIVERED
    delivered_turn_ref: str = Field(min_length=1)
    delivery_ref: str = Field(min_length=1)
    addressed_target_ref: str = Field(min_length=1)
    operation: Literal["create", "revise", "explain", "evaluate", "describe", "compare", "summarize"]
    artifact_revision_ref: str | None = None
    validated_content_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent(self):
        if self.operation in {"create", "revise"} and self.artifact_revision_ref is None:
            raise ValueError("a delivered create or revise names the delivered revision")
        return self


class UserConfirmationEvent(_Event):
    """The user explicitly confirmed the exact target (e.g. a repaired proposition)."""

    kind: Literal[GoalOutcomeEventKind.USER_CONFIRMATION] = GoalOutcomeEventKind.USER_CONFIRMATION
    confirmed_target_ref: str = Field(min_length=1)
    user_message_ref: str = Field(min_length=1)


class UserWithdrawalEvent(_Event):
    kind: Literal[GoalOutcomeEventKind.USER_WITHDRAWAL] = GoalOutcomeEventKind.USER_WITHDRAWAL
    user_message_ref: str = Field(min_length=1)


class GoalReplacementEvent(_Event):
    """Typed evidence that ``replacement_goal_ref`` replaces the target goal."""

    kind: Literal[GoalOutcomeEventKind.GOAL_REPLACEMENT] = GoalOutcomeEventKind.GOAL_REPLACEMENT
    replacement_goal_ref: str = Field(min_length=1)
    user_message_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self):
        if self.replacement_goal_ref == self.target_goal_ref:
            raise ValueError("a goal cannot replace itself")
        return self


class DeferralEvent(_Event):
    """A temporary blocker: the user deferred it, or a prerequisite is missing."""

    kind: Literal[GoalOutcomeEventKind.DEFERRAL] = GoalOutcomeEventKind.DEFERRAL
    cause: Literal["user_deferral", "missing_temporary_prerequisite"]


class ResumptionEvent(_Event):
    kind: Literal[GoalOutcomeEventKind.RESUMPTION] = GoalOutcomeEventKind.RESUMPTION
    resolved_blocker_ref: str = Field(min_length=1)


class ToolResultEvent(_Event):
    """A tool/runtime result for the exact requested (or prevented) effect."""

    kind: Literal[GoalOutcomeEventKind.TOOL_RESULT] = GoalOutcomeEventKind.TOOL_RESULT
    outcome_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "unavailable"]
    final: bool
    authorized: bool
    effect_ref: str = Field(min_length=1)
    alternate_route_available: bool | None = None
    verified_impossible: bool = False

    @model_validator(mode="after")
    def _coherent(self):
        if self.alternate_route_available is not None and self.status != "failed":
            raise ValueError("an alternate route belongs to a failed result")
        if self.verified_impossible and (self.status == "succeeded" or self.alternate_route_available):
            raise ValueError("impossibility requires a failed result with no alternate route")
        return self


class ArgumentGroundedEvent(_Event):
    kind: Literal[GoalOutcomeEventKind.ARGUMENT_GROUNDED] = GoalOutcomeEventKind.ARGUMENT_GROUNDED
    argument_ref: str = Field(min_length=1)
    grounding_ref: str = Field(min_length=1)


class ContradictionResolvedEvent(_Event):
    kind: Literal[GoalOutcomeEventKind.CONTRADICTION_RESOLVED] = GoalOutcomeEventKind.CONTRADICTION_RESOLVED
    contradiction_ref: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)


class VerifiedImpossibilityEvent(_Event):
    """A trusted capability/prerequisite change proves the goal impossible."""

    kind: Literal[GoalOutcomeEventKind.VERIFIED_IMPOSSIBILITY] = GoalOutcomeEventKind.VERIFIED_IMPOSSIBILITY
    capability_ref: str = Field(min_length=1)
    alternate_route_available: Literal[False] = False


class ScopeClosureEvent(_Event):
    """The goal's task or conversation scope closed.  Closure is never success.

    The one exception is typed: for an avoid-mode (prevent/stop) goal,
    ``prevention_monitoring_adequate`` is the runtime's monitoring verdict that
    the action stayed prevented through the closed scope.  ``True`` satisfies
    the goal; ``False`` leaves it open and it expires with its scope.
    """

    kind: Literal[GoalOutcomeEventKind.SCOPE_CLOSURE] = GoalOutcomeEventKind.SCOPE_CLOSURE
    closed_scope_ref: str = Field(min_length=1)
    prevention_monitoring_adequate: bool | None = None


GoalOutcomeEvent = Annotated[
    Union[
        ResponseDeliveredEvent, UserConfirmationEvent, UserWithdrawalEvent, GoalReplacementEvent,
        DeferralEvent, ResumptionEvent, ToolResultEvent, ArgumentGroundedEvent,
        ContradictionResolvedEvent, VerifiedImpossibilityEvent, ScopeClosureEvent, ArtifactDeliveredEvent,
    ],
    Field(discriminator="kind"),
]
GOAL_OUTCOME_EVENT_ADAPTER = TypeAdapter(GoalOutcomeEvent)

K = GoalOutcomeEventKind
_LIFECYCLE_EVENTS = frozenset({
    K.USER_WITHDRAWAL, K.GOAL_REPLACEMENT, K.DEFERRAL, K.RESUMPTION, K.VERIFIED_IMPOSSIBILITY, K.SCOPE_CLOSURE,
})

# Authority table.  ``satisfies``: event classes that can produce final
# satisfying evidence for the definition.  ``observes``: event classes that
# are recorded against the requirement but can never satisfy it.  Lifecycle
# events (withdrawal, replacement, deferral, resumption, impossibility,
# closure) are governed separately below.  Any other pairing is rejected.
EVIDENCE_AUTHORITY: Mapping[str, Mapping[str, frozenset[GoalOutcomeEventKind]]] = {
    RESPONSE_OBLIGATION_DEFINITION_KEY: {
        "satisfies": frozenset({K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    "interaction.answer_information_request": {
        # Delivery plus validated grounded answer content for the exact topic.
        "satisfies": frozenset({K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    "interaction.acknowledge_relevant_information": {
        "satisfies": frozenset({K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    "interaction.respond_to_social_contact": {
        "satisfies": frozenset({K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    "interaction.explain_unavailable_or_failed_operation": {
        "satisfies": frozenset({K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    # Evidence policy v0.2: the request handler is met by a validated response
    # that handled the exact request (never by a planned action).
    "interaction.handle_action_request": {
        "satisfies": frozenset({K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    # Evidence policy v0.3: only a delivered artifact operation of the exact
    # requested kind satisfies a deliverable; an ordinary delivered response
    # (e.g. a clarification or a truthful decline) is observed, never success.
    "interaction.fulfill_conversational_deliverable": {
        "satisfies": frozenset({K.ARTIFACT_DELIVERED}), "observes": frozenset({K.RESPONSE_DELIVERED}),
    },
    # An answer is incorporated by the delivered revision that used it, or by
    # a validated response that acknowledged a declined question.
    "interaction.incorporate_inquiry_answer": {
        "satisfies": frozenset({K.ARTIFACT_DELIVERED, K.RESPONSE_DELIVERED}), "observes": frozenset(),
    },
    # The registered policy requires shared confirmation: a delivered repair
    # is observed (incorporated, awaiting the user), only confirmation satisfies.
    "interaction.repair_shared_understanding": {
        "satisfies": frozenset({K.USER_CONFIRMATION}), "observes": frozenset({K.RESPONSE_DELIVERED}),
    },
    "epistemic.resolve_required_unknown": {
        "satisfies": frozenset({K.ARGUMENT_GROUNDED}), "observes": frozenset(),
    },
    "epistemic.resolve_material_contradiction": {
        "satisfies": frozenset({K.CONTRADICTION_RESOLVED}), "observes": frozenset(),
    },
    "task.complete_authorized_request": {
        "satisfies": frozenset({K.TOOL_RESULT}), "observes": frozenset(),
    },
    "task.prevent_or_stop_requested_action": {
        "satisfies": frozenset({K.TOOL_RESULT}), "observes": frozenset(),
    },
}
# Definitions whose satisfaction needs validated content, not delivery alone.
_CONTENT_VALIDATED = frozenset({
    "interaction.answer_information_request", "interaction.acknowledge_relevant_information",
    "interaction.respond_to_social_contact", "interaction.explain_unavailable_or_failed_operation",
    "interaction.handle_action_request", "interaction.incorporate_inquiry_answer",
})
# The requirement target role a delivered response / confirmation must name.
_ADDRESSED_ROLE: Mapping[str, str] = {
    RESPONSE_OBLIGATION_DEFINITION_KEY: "turn_ref",
    "interaction.answer_information_request": "topic_ref",
    "interaction.acknowledge_relevant_information": "proposition_ref",
    "interaction.respond_to_social_contact": "dialogue_scope_ref",
    "interaction.explain_unavailable_or_failed_operation": "failure_ref",
    "interaction.repair_shared_understanding": "proposition_ref",
    "interaction.handle_action_request": "request_ref",
    "interaction.fulfill_conversational_deliverable": "request_ref",
    "interaction.incorporate_inquiry_answer": "answer_ref",
}
_EFFECT_PARAMETER: Mapping[str, str] = {
    "task.complete_authorized_request": "requested_effect",
    "task.prevent_or_stop_requested_action": "prevented_effect",
}
# Which goals a user may withdraw or replace: the ones the user asked for.
_USER_REQUESTED = frozenset({
    "interaction.answer_information_request", "interaction.acknowledge_relevant_information",
    "interaction.repair_shared_understanding", "interaction.respond_to_social_contact",
    "task.complete_authorized_request", "task.prevent_or_stop_requested_action",
    "interaction.handle_action_request", "interaction.fulfill_conversational_deliverable",
    "interaction.incorporate_inquiry_answer",
})
# The avoid-mode satisfaction policy that trusted closure monitoring can meet.
AVOIDANCE_MONITORING_POLICY = "goal-satisfaction.requested-action-stopped.v1"
# Evidence class each satisfying/observing event is recorded as.
SATISFYING_EVIDENCE_KINDS: Mapping[str, frozenset[str]] = {
    "goal-satisfaction.response-delivered.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.supported-answer.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.relevant-acknowledgement.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.shared-repair-confirmed.v1": frozenset({"explicit_user_confirmation"}),
    "goal-satisfaction.social-contact-answered.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.required-argument-grounded.v1": frozenset({"semantic_grounding", "reviewed_outcome"}),
    "goal-satisfaction.material-contradiction-resolved.v1": frozenset({"reviewed_outcome"}),
    "goal-satisfaction.authorized-task-result.v1": frozenset({"finalized_tool_result", "reviewed_outcome"}),
    "goal-satisfaction.requested-action-stopped.v1": frozenset({"finalized_tool_result", "reviewed_outcome"}),
    "goal-satisfaction.operation-failure-explained.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.action-request-handled.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.conversational-deliverable-delivered.v1": frozenset({"realization_validation"}),
    "goal-satisfaction.inquiry-answer-incorporated.v1": frozenset({"realization_validation"}),
}

_TRIGGERS: Mapping[GoalOutcomeEventKind, GoalUpdateTrigger] = {
    K.RESPONSE_DELIVERED: GoalUpdateTrigger.REALIZATION_OR_OUTCOME,
    K.ARTIFACT_DELIVERED: GoalUpdateTrigger.REALIZATION_OR_OUTCOME,
    K.USER_CONFIRMATION: GoalUpdateTrigger.REALIZATION_OR_OUTCOME,
    K.TOOL_RESULT: GoalUpdateTrigger.REALIZATION_OR_OUTCOME,
    K.ARGUMENT_GROUNDED: GoalUpdateTrigger.REALIZATION_OR_OUTCOME,
    K.CONTRADICTION_RESOLVED: GoalUpdateTrigger.REALIZATION_OR_OUTCOME,
    K.USER_WITHDRAWAL: GoalUpdateTrigger.EXPLICIT_CORRECTION,
    K.GOAL_REPLACEMENT: GoalUpdateTrigger.EXPLICIT_CORRECTION,
    K.RESUMPTION: GoalUpdateTrigger.TRUSTED_RUNTIME_CHANGE,
    K.VERIFIED_IMPOSSIBILITY: GoalUpdateTrigger.TRUSTED_RUNTIME_CHANGE,
    K.SCOPE_CLOSURE: GoalUpdateTrigger.TRUSTED_SCOPE_CLOSURE,
}


class GoalEvidenceProductionError(ValueError):
    """An event could not be turned into evidence; nothing is produced."""


def trigger_for(events: Iterable[GoalOutcomeEvent]) -> GoalUpdateTrigger:
    """The single lifecycle trigger a standalone evidence revision uses."""

    triggers = set()
    for event in events:
        if event.kind == K.DEFERRAL:
            triggers.add(
                GoalUpdateTrigger.EXPLICIT_CORRECTION if event.cause == "user_deferral"
                else GoalUpdateTrigger.TRUSTED_RUNTIME_CHANGE
            )
        else:
            triggers.add(_TRIGGERS[event.kind])
    if len(triggers) != 1:
        raise GoalEvidenceProductionError("one evidence revision carries events of exactly one trigger class")
    return triggers.pop()


def _target(record: GoalRecord, requirement_ref: str, role: str) -> str | None:
    requirement = next(item for item in record.requirements if item.requirement_id == requirement_ref)
    return next((item.ref for item in requirement.target_refs if item.role == role), None)


def _parameter(record: GoalRecord, requirement_ref: str, name: str):
    requirement = next(item for item in record.requirements if item.requirement_id == requirement_ref)
    return requirement.parameters.get(name)


def _fail(message: str):
    raise GoalEvidenceProductionError(message)


def _provenance(event, **facts) -> GoalEvidenceProvenance:
    return GoalEvidenceProvenance(producer=f"{PRODUCER_ID}:{event.producer}", review_state="trusted_projection", **facts)


def _evidence(event, record, *, kind: str, finality: str, outcome_id: str | None, **facts) -> GoalEvidence:
    return GoalEvidence(
        evidence_id=f"goal-evidence:{GOAL_EVIDENCE_POLICY_VERSION}:{event.kind.value}:{event.event_id}:{record.goal_id}",
        evidence_kind=kind, source_type=event.kind.value, source_ref=event.source_ref,
        episode_or_turn_ref=event.occurred_turn_ref, target_goal_ref=record.goal_id,
        target_requirement_ref=event.target_requirement_ref, provenance=_provenance(event, **facts),
        finality=finality, outcome_id=outcome_id,
    )


def _produce_one(event, record: GoalRecord, snapshot: GoalSnapshot) -> GoalEvidence:
    key = record.definition_ref.definition_key
    authority = EVIDENCE_AUTHORITY[key]
    if event.kind in _LIFECYCLE_EVENTS:
        return _lifecycle(event, record, key)
    satisfies = event.kind in authority["satisfies"]
    observes = event.kind in authority["observes"]
    if not (satisfies or observes):
        _fail(f"{event.kind.value} evidence is not authorized for {key}")

    if event.kind == K.RESPONSE_DELIVERED:
        # Delivery belongs to the response episode that just ended: the
        # predecessor snapshot's turn, and a goal that episode was focused on.
        if event.delivered_turn_ref != snapshot.cognitive_turn_id:
            _fail("a delivered response must belong to the predecessor's response episode")
        focal = set(snapshot.focal_goal_refs)
        if key == RESPONSE_OBLIGATION_DEFINITION_KEY:
            if record.scope_ref != event.delivered_turn_ref:
                _fail("a delivered response satisfies only its own turn's response obligation")
        elif record.goal_id not in focal:
            _fail("a delivered response can only address a goal its episode was focused on")
        role = _ADDRESSED_ROLE[key]
        expected = _target(record, event.target_requirement_ref, role)
        addressed = event.addressed_target_ref if key != RESPONSE_OBLIGATION_DEFINITION_KEY else event.delivered_turn_ref
        if addressed is None or addressed != expected:
            _fail("the delivered response addressed a different requirement target")
        satisfying = satisfies and (key not in _CONTENT_VALIDATED or bool(event.validated_content_ref))
        return _evidence(
            event, record, kind="realization_validation", finality="final", outcome_id=event.delivery_ref,
            non_satisfying_observation=not satisfying,
        )
    if event.kind == K.ARTIFACT_DELIVERED:
        if event.delivered_turn_ref != snapshot.cognitive_turn_id:
            _fail("a delivered artifact must belong to the predecessor's response episode")
        if record.goal_id not in set(snapshot.focal_goal_refs):
            _fail("a delivered artifact can only address a goal its episode was focused on")
        expected = _target(record, event.target_requirement_ref, _ADDRESSED_ROLE[key])
        if event.addressed_target_ref != expected:
            _fail("the delivered artifact addressed a different requirement target")
        requested = _parameter(record, event.target_requirement_ref, "operation")
        # A deliverable is satisfied only by the operation it asked for; the
        # answer goal is satisfied by the revision that incorporated it.
        exact = requested is None or requested == event.operation
        return _evidence(
            event, record, kind="realization_validation", finality="final", outcome_id=event.delivery_ref,
            non_satisfying_observation=not exact,
        )
    if event.kind == K.USER_CONFIRMATION:
        expected = _target(record, event.target_requirement_ref, _ADDRESSED_ROLE[key])
        if event.confirmed_target_ref != expected:
            _fail("the confirmation names a different requirement target")
        return _evidence(event, record, kind="explicit_user_confirmation", finality="final",
                         outcome_id=event.user_message_ref)
    if event.kind == K.TOOL_RESULT:
        expected = _parameter(record, event.target_requirement_ref, _EFFECT_PARAMETER[key])
        exact = event.effect_ref == expected
        succeeded = event.status == "succeeded"
        facts = {}
        if event.status != "succeeded":
            facts["outcome_status"] = event.status
            if event.status == "failed" and event.alternate_route_available is not None:
                facts["alternate_route_available"] = event.alternate_route_available
            if event.verified_impossible and event.final and event.authorized and exact:
                # Only a final, authorized result for the exact effect can
                # prove impossibility (lifecycle ``fail``).
                facts["verified_impossible"] = True
        # A result for another effect, an unauthorized result, or a non-success
        # is recorded against the requirement and never satisfies it.
        return _evidence(
            event, record, kind="finalized_tool_result", finality="final" if event.final else "non_final",
            outcome_id=event.outcome_id,
            non_satisfying_observation=not (exact and event.authorized and succeeded),
            **facts,
        )
    if event.kind == K.ARGUMENT_GROUNDED:
        if event.argument_ref != _target(record, event.target_requirement_ref, "argument_ref"):
            _fail("the grounding names a different argument")
        return _evidence(event, record, kind="semantic_grounding", finality="final", outcome_id=event.grounding_ref)
    if event.kind == K.CONTRADICTION_RESOLVED:
        if event.contradiction_ref != _target(record, event.target_requirement_ref, "contradiction_ref"):
            _fail("the resolution names a different contradiction")
        return _evidence(event, record, kind="reviewed_outcome", finality="final", outcome_id=event.outcome_id)
    _fail("unsupported goal outcome event")  # pragma: no cover - closed union


def _lifecycle(event, record: GoalRecord, key: str) -> GoalEvidence:
    if event.kind in {K.USER_WITHDRAWAL, K.GOAL_REPLACEMENT} and key not in _USER_REQUESTED:
        _fail("only a user-requested goal can be withdrawn or replaced by the user")
    if event.kind == K.USER_WITHDRAWAL:
        return _evidence(event, record, kind="explicit_user_confirmation", finality="final", outcome_id=None,
                         withdraws_goal_ref=record.goal_id)
    if event.kind == K.GOAL_REPLACEMENT:
        return _evidence(event, record, kind="explicit_user_confirmation", finality="final", outcome_id=None,
                         replacement_goal_ref=event.replacement_goal_ref)
    if event.kind == K.DEFERRAL:
        if record.status != GoalStatus.ACTIVE:
            _fail("only an active goal can be suspended")
        rule = (
            "goal-lifecycle.suspend-explicit-deferral.v1" if event.cause == "user_deferral"
            else "goal-lifecycle.suspend-missing-temporary-prerequisite.v1"
        )
        kind = "explicit_user_confirmation" if event.cause == "user_deferral" else "trusted_runtime_fact"
        return _evidence(event, record, kind=kind, finality="final", outcome_id=None,
                         suspend_goal_ref=record.goal_id, suspend_rule_id=rule)
    if event.kind == K.RESUMPTION:
        if record.status != GoalStatus.SUSPENDED:
            _fail("only a suspended goal can resume")
        return _evidence(event, record, kind="trusted_runtime_fact", finality="final", outcome_id=None,
                         resume_goal_ref=record.goal_id)
    if event.kind == K.VERIFIED_IMPOSSIBILITY:
        return _evidence(event, record, kind="trusted_runtime_fact", finality="final", outcome_id=None,
                         verified_impossible=True)
    if event.kind == K.SCOPE_CLOSURE:
        if event.closed_scope_ref != record.scope_ref:
            _fail("the closed scope is not the goal's scope")
        facts = {}
        if event.prevention_monitoring_adequate is not None:
            if key != "task.prevent_or_stop_requested_action":
                _fail("prevention monitoring belongs only to an avoid-mode goal")
            facts["monitoring_adequate"] = event.prevention_monitoring_adequate
        return _evidence(event, record, kind="trusted_runtime_fact", finality="final", outcome_id=None, **facts)
    _fail("unsupported lifecycle event")  # pragma: no cover


def produce_goal_evidence(
    snapshot: GoalSnapshot, events: Iterable[GoalOutcomeEvent | Mapping],
) -> tuple[GoalEvidence, ...]:
    """Evidence for ``events`` against ``snapshot``; raises if any event is invalid.

    All-or-nothing: one invalid event produces nothing, so a caller can never
    reconcile a partial, silently filtered batch.
    """

    parsed = tuple(
        GOAL_OUTCOME_EVENT_ADAPTER.validate_python(item) if isinstance(item, Mapping) else item
        for item in events
    )
    ids = tuple(item.event_id for item in parsed)
    if len(ids) != len(set(ids)):
        _fail("goal outcome event ids must be unique")
    records = {item.goal_id: item for item in snapshot.records}
    produced = []
    for event in parsed:
        if event.conversation_ref != snapshot.conversation_ref:
            _fail("goal outcome event belongs to another conversation")
        record = records.get(event.target_goal_ref)
        if record is None:
            _fail("goal outcome event targets an unknown goal")
        if record.status not in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED}:
            _fail("goal outcome event targets a goal that is not active or suspended")
        if not any(item.requirement_id == event.target_requirement_ref for item in record.requirements):
            _fail("goal outcome event targets a requirement of another goal")
        produced.append(_produce_one(event, record, snapshot))
    evidence_ids = tuple(item.evidence_id for item in produced)
    if len(evidence_ids) != len(set(evidence_ids)):
        _fail("goal outcome events produced duplicate evidence")
    return tuple(sorted(produced, key=lambda item: item.evidence_id))


__all__ = [
    "AVOIDANCE_MONITORING_POLICY", "ArgumentGroundedEvent", "ArtifactDeliveredEvent", "ContradictionResolvedEvent", "DeferralEvent", "EVIDENCE_AUTHORITY",
    "GOAL_OUTCOME_EVENT_ADAPTER", "GoalEvidenceProductionError", "GoalOutcomeEvent", "GoalOutcomeEventKind",
    "GoalReplacementEvent", "ResponseDeliveredEvent", "ResumptionEvent", "SATISFYING_EVIDENCE_KINDS",
    "ScopeClosureEvent", "ToolResultEvent", "UserConfirmationEvent", "UserWithdrawalEvent",
    "VerifiedImpossibilityEvent", "produce_goal_evidence", "trigger_for",
]
