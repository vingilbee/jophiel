"""The explicit ``simulated_authority`` evaluation harness (Section 14.3, Gate 6).

Offline evaluation only.  It turns one completed pipeline trace -- the moves
Action actually selected -- into the inputs a *real* delivery would have
produced for the next turn, every one of them marked as simulated:

- ``simulated_delivery``: an immutable ``DeliveredResponseOutcome`` on the
  ``simulated`` channel, naming the harness, that cites exactly the
  revisions, inquiries, and pending requirements whose selected move was
  delivered (a proposal no move delivered is not cited and reconciles as
  ``abandoned``);
- ``delivery_events``: the ``ArtifactDeliveredEvent`` goal events that
  delivery supports (one per delivered deliverable or answer goal), for a
  separate outcome-time goal revision of the predecessor turn;
- ``next_workspace_inputs``: the next turn's ``WorkspaceTurnInputs`` (the
  canonical snapshot this turn ended with, exactly what it proposed, and the
  simulated delivery).

Never used by live shadow: ``reconcile`` rejects a simulated outcome in any
other authority mode, and live authority is not approved.  Pure: no clock,
database, model, or I/O; the caller supplies ``observed_at``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..collaborative_content_v0.projection import artifact_object_id
from ..contracts.collaboration import CollaborationTurnRecord, WorkspaceTurnInputs
from ..contracts.workspace import (
    DeliveredResponseOutcome, DeliveryChannel, DeliveryStatus, WorkspaceAuthorityMode,
)
from ..goal_v0.evidence_producer import ArtifactDeliveredEvent
from .resolution import ANSWER_GOAL, DELIVERABLE_GOAL

SIMULATION_HARNESS_VERSION = "workspace-simulated-authority-harness-v0.1"
DELIVER_ACTION = "collaborate.deliver_artifact"
INQUIRE_ACTION = "seek_information.inquire"
# Moves that ask the user for a missing blocking requirement.
CLARIFY_FAMILY = "seek_information"


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _targets_by_action(trace: Any) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for move in trace.action_selection.plan.moves:
        out.setdefault(move.definition_ref.action_key, set()).update(item.ref for item in move.target_refs)
    return out


def _record(trace: Any) -> CollaborationTurnRecord | None:
    collaboration = getattr(trace, "collaboration", None)
    return collaboration.turn_record if collaboration is not None else None


def simulated_delivery(
    trace: Any, *, simulation_ref: str, observed_at: str, realization_ref: str,
    delivery_status: DeliveryStatus = DeliveryStatus.DELIVERED,
) -> DeliveredResponseOutcome:
    """The marked simulated delivery of exactly what ``trace`` selected."""

    record = _record(trace)
    by_action = _targets_by_action(trace)
    delivered_objects = by_action.get(DELIVER_ACTION, set())
    inquired = by_action.get(INQUIRE_ACTION, set())
    clarified = {ref for key, refs in by_action.items() if key.startswith(f"{CLARIFY_FAMILY}.") for ref in refs}
    content: set[str] = set()
    revisions: set[str] = set()
    inquiries: set[str] = set()
    delivered = delivery_status == DeliveryStatus.DELIVERED
    if record is not None:
        # A failed delivery still names what it attempted (so it reconciles as
        # ``delivery_failed``), but it delivered no revision and asked nothing.
        for proposal in record.proposals:
            if artifact_object_id(proposal) not in delivered_objects:
                continue
            content.add(proposal.content_proposal_id)
            if not delivered:
                continue
            if proposal.proposed_revision is not None:
                revisions.add(proposal.proposed_revision.revision_id)
            inquiries.update(item.inquiry_id for item in proposal.selected_inquiries if item.inquiry_id in inquired)
        content.update(item.pending_id for item in record.pending_requirements if item.target_ref in clarified)
    plan = trace.action_selection.plan
    return DeliveredResponseOutcome(
        outcome_id=f"simulated-outcome:{_digest(simulation_ref, trace.cognitive_turn_id, realization_ref)}",
        conversation_ref=trace.conversation_ref, turn_ref=trace.cognitive_turn_id,
        selected_action_ref=plan.primary_move_ref or plan.plan_id, realization_ref=realization_ref,
        delivery_channel=DeliveryChannel.SIMULATED, delivered_content_refs=tuple(sorted(content)),
        artifact_revision_refs=tuple(sorted(revisions)), inquiry_refs=tuple(sorted(inquiries)),
        delivery_status=delivery_status, observed_at=observed_at, simulation_ref=simulation_ref,
    )


def delivery_events(trace: Any, outcome: DeliveredResponseOutcome) -> tuple[ArtifactDeliveredEvent, ...]:
    """Goal events a validated delivery supports: one per delivered collaboration goal.

    A deliverable goal is addressed at its ``request_ref`` target and an
    answer goal at its ``answer_ref``; the event names the delivered
    revision (create/revise) or the exact revision presented.
    """

    record = _record(trace)
    if record is None or outcome.delivery_status != DeliveryStatus.DELIVERED:
        return ()
    goals = {item.goal_id: item for item in trace.goal_snapshot.records}
    roles = {DELIVERABLE_GOAL: "request_ref", ANSWER_GOAL: "answer_ref"}
    events = []
    for proposal in record.proposals:
        if proposal.content_proposal_id not in outcome.delivered_content_refs:
            continue
        goal = goals.get(proposal.goal_ref)
        if goal is None or goal.definition_ref.definition_key not in roles:
            continue
        role = roles[goal.definition_ref.definition_key]
        [requirement] = goal.requirements
        addressed = next(item.ref for item in requirement.target_refs if item.role == role)
        revision = proposal.proposed_revision.revision_id if proposal.proposed_revision else proposal.target_revision_ref
        events.append(ArtifactDeliveredEvent(
            event_id=f"simulated-artifact-delivered:{_digest(outcome.outcome_id, proposal.content_proposal_id)}",
            producer=SIMULATION_HARNESS_VERSION, source_ref=outcome.outcome_id,
            conversation_ref=outcome.conversation_ref, occurred_turn_ref=outcome.turn_ref,
            target_goal_ref=goal.goal_id, target_requirement_ref=requirement.requirement_id,
            delivered_turn_ref=outcome.turn_ref, delivery_ref=outcome.outcome_id, addressed_target_ref=addressed,
            operation=proposal.operation.value, artifact_revision_ref=revision,
            validated_content_ref=outcome.realization_ref,
        ))
    return tuple(sorted(events, key=lambda item: item.event_id))


def next_workspace_inputs(trace: Any, outcomes: tuple[DeliveredResponseOutcome, ...]) -> WorkspaceTurnInputs:
    """The successor turn's simulated-authority workspace inputs."""

    collaboration = trace.collaboration
    snapshot = collaboration.workspace.snapshot
    if snapshot.authority_mode != WorkspaceAuthorityMode.SIMULATED_AUTHORITY:
        raise ValueError("only a simulated-authority conversation can be continued by the harness")
    if any(item.delivery_channel != DeliveryChannel.SIMULATED or item.simulation_ref is None for item in outcomes):
        raise ValueError("the harness injects only marked simulated deliveries")
    return WorkspaceTurnInputs(
        authority_mode=WorkspaceAuthorityMode.SIMULATED_AUTHORITY, previous_snapshot=snapshot,
        predecessor_record=collaboration.turn_record, delivered_outcomes=outcomes,
    )


__all__ = [
    "DELIVER_ACTION", "INQUIRE_ACTION", "SIMULATION_HARNESS_VERSION", "delivery_events", "next_workspace_inputs",
    "simulated_delivery",
]
