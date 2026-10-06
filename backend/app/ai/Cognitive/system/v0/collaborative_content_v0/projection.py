"""Project collaboration outcomes into Action inputs and closed Realization content.

``collaboration-action-projection-v0.1``.  Deterministic and pure; it adds
no special case to the Action scorer:

- a **proposed** deliverable becomes one ARTIFACT_REVISION object (the
  operation it performs, its validated content items as PROPOSITION basis
  objects) plus one INQUIRY object per *selected* inquiry, each with OPTION
  objects for a choice question; its Realization content is one
  ``ArtifactPresentation`` per item and one ``InquiryPrompt`` per inquiry;
- a **failed** deliverable becomes a conversational REQUEST that can only be
  truthfully declined, with a typed reason ("the requested content was not
  produced") -- never an acknowledgement and never content from nowhere;
- a **skipped** (blocked) deliverable projects nothing: requirement
  resolution already projected its exact blocker.

It also records the turn's ``CollaborationTurnRecord`` -- every proposal and
every pending requirement a clarification of this turn asks for -- so a
later delivered outcome can be reconciled against exactly what was proposed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..contracts.actions import ActionGroundedObject, ActionRuntimeContext, ActionTargetType as T
from ..contracts.collaboration import (
    COLLABORATION_CONTENT_PRODUCER, EXPLANATORY_ROLES, SUGGESTING_ROLES, CollaborationActionBinding,
    CollaborationActionHandoff, CollaborationGoalOutcome, CollaborationProposal, CollaborationTurnRecord,
    ContentItemRole, PendingRequirementProposal, WorkspaceResolution,
)
from ..contracts.goals import GoalSnapshot
from ..contracts.interpretation import InterpretationEnvelope
from ..contracts.realization import (
    ArgumentValueType, ArtifactPresentation, ContentAttribution, ContentEpistemicStatus, ContentSourceKind,
    DialogueReference, DialogueReferenceKind, GroundedProposition, InquiryPrompt, Polarity, PropositionArgument,
    PropositionTerm, RealizationContentItem, ReferenceForm, SensitivityClass,
)
from ..contracts.requirement_resolution import (
    RequirementActionHandoff, RequirementKind as RK, RequirementResolutionStatus as RS,
)
from ..contracts.workspace import ArtifactRevision, ContentClass, MealPlanPayload

PROJECTION_VERSION = "collaboration-action-projection-v0.1"
CONTENT_PRODUCER = COLLABORATION_CONTENT_PRODUCER
REQUEST_ANCHOR = "your request"
# Blocking prerequisites whose clarification is carried as a pending requirement.
PENDING_KINDS = frozenset({RK.REQUIRED_ARGUMENT, RK.ARTIFACT_TARGET, RK.REQUESTED_CHANGE, RK.REQUESTED_OUTCOME})
_PENDING_ARGUMENTS = {RK.ARTIFACT_TARGET: "target_ref", RK.REQUESTED_CHANGE: "requested_change",
                      RK.REQUESTED_OUTCOME: "requested_outcome"}


def _digest(*parts: str, length: int = 24) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:length]


def artifact_object_id(proposal: CollaborationProposal) -> str:
    return f"collaboration-artifact:{_digest(proposal.content_proposal_id)}"


def content_object_id(item_id: str) -> str:
    return f"collaboration-content:{item_id.rsplit(':', 1)[-1]}"


def option_object_id(inquiry_id: str, option: str) -> str:
    return f"{inquiry_id}#option.{option}"


def request_object_id(goal_ref: str) -> str:
    return f"collaboration-request:{_digest(goal_ref)}"


def pending_id_for(goal_ref: str, resolution_ref: str) -> str:
    return f"workspace-pending:{_digest(goal_ref, resolution_ref)}"


def _units_by_value(revision: ArtifactRevision | None) -> dict[int, set[str]]:
    """Which registered units each typed artifact integer may carry in wording."""

    out: dict[int, set[str]] = {}
    if revision is None or not isinstance(revision.payload, MealPlanPayload):
        return out
    payload = revision.payload
    if payload.servings is not None:
        out.setdefault(payload.servings.value, set()).add("serving")
    if payload.time_limit_minutes is not None:
        out.setdefault(payload.time_limit_minutes.value, set()).add("minute")
    for dish in payload.dishes:
        if dish.prep_time_minutes is not None:
            out.setdefault(dish.prep_time_minutes, set()).add("minute")
    return out


@dataclass(frozen=True)
class CollaborationProjection:
    grounded_objects: tuple[ActionGroundedObject, ...]
    realization_content: tuple[RealizationContentItem, ...]
    handoff: CollaborationActionHandoff
    turn_record: CollaborationTurnRecord


class CollaborationProjectorV0:
    version = PROJECTION_VERSION

    def project(
        self, outcomes: tuple[CollaborationGoalOutcome, ...], *, interpretation: InterpretationEnvelope,
        goal_snapshot: GoalSnapshot, workspace: WorkspaceResolution, runtime: ActionRuntimeContext,
        requirement_handoff: RequirementActionHandoff | None,
        existing_objects: tuple[ActionGroundedObject, ...] = (),
    ) -> CollaborationProjection:
        turn = interpretation.cognitive_turn_id
        taken = {item.object_id for item in existing_objects}
        frames = {frame.frame_id: frame for frame in interpretation.semantic_frames}
        objects: dict[str, ActionGroundedObject] = {}
        content: dict[str, RealizationContentItem] = {}
        bindings: list[CollaborationActionBinding] = []

        def put(item: ActionGroundedObject) -> None:
            if item.object_id in taken or item.object_id in objects:
                raise ValueError("a projected collaboration object collides with another action object")
            objects[item.object_id] = item

        for outcome in sorted(outcomes, key=lambda item: item.goal_ref):
            if outcome.status == "proposed":
                refs, content_refs = self._proposal(outcome.proposal, turn, workspace, put, content)
                bindings.append(CollaborationActionBinding(
                    goal_ref=outcome.goal_ref, status="proposed", proposal_ref=outcome.proposal.content_proposal_id,
                    object_refs=tuple(sorted(refs)), content_refs=tuple(sorted(content_refs)),
                ))
            elif outcome.status == "failed":
                frame = frames[outcome.proposal.frame_ref] if outcome.proposal else self._frame(
                    outcome.goal_ref, goal_snapshot, frames)
                refs, content_refs, blocker = self._failure(outcome.goal_ref, frame, turn, runtime, put, content)
                bindings.append(CollaborationActionBinding(
                    goal_ref=outcome.goal_ref, status="failed", object_refs=tuple(sorted(refs)),
                    content_refs=tuple(sorted(content_refs)), blocker_refs=(blocker,),
                    failure_code=outcome.failure.code,
                ))
            else:
                bindings.append(CollaborationActionBinding(goal_ref=outcome.goal_ref, status="skipped"))
        record = CollaborationTurnRecord(
            turn_ref=turn, conversation_ref=interpretation.conversation_ref,
            proposals=tuple(sorted((item.proposal for item in outcomes if item.status == "proposed"),
                                   key=lambda item: item.content_proposal_id)),
            pending_requirements=self._pending(goal_snapshot, workspace, requirement_handoff, frames),
        )
        return CollaborationProjection(
            grounded_objects=tuple(objects[key] for key in sorted(objects)),
            realization_content=tuple(content[key] for key in sorted(content)),
            handoff=CollaborationActionHandoff(cognitive_turn_id=turn, bindings=tuple(bindings)),
            turn_record=record,
        )

    @staticmethod
    def _frame(goal_ref, goal_snapshot, frames):
        record = next(item for item in goal_snapshot.records if item.goal_id == goal_ref)
        ref = next(target.ref for requirement in record.requirements for target in requirement.target_refs
                   if target.ref in frames)
        return frames[ref]

    # -- a proposed deliverable --------------------------------------------------------
    def _proposal(self, proposal: CollaborationProposal, turn: str, workspace: WorkspaceResolution, put, content):
        artifact_id = artifact_object_id(proposal)
        frame_ref = proposal.frame_ref
        revision = proposal.proposed_revision or (
            workspace.snapshot.revision(proposal.target_revision_ref) if proposal.target_revision_ref else None
        )
        units = _units_by_value(revision)
        revision_ref = revision.revision_id if revision is not None else None
        basis: list[str] = []
        content_refs: list[str] = []
        for item in proposal.content_items:
            object_id = content_object_id(item.item_id)
            hedged = item.content_class in {ContentClass.UNCERTAIN_SUGGESTION, ContentClass.REASONED_INFERENCE}
            user = item.content_class in {ContentClass.USER_SUPPLIED_CONSTRAINT, ContentClass.ATTRIBUTED_USER_CLAIM}
            put(ActionGroundedObject(
                object_id=object_id, object_type=T.PROPOSITION, cognitive_turn_id=turn, source_refs=(frame_ref,),
                about_refs=(frame_ref, proposal.goal_ref), asserted_by="user" if user else "columbina",
                epistemic_status="tentative" if hedged or item.content_class == ContentClass.GENERATED_PROPOSAL
                else "supported",
            ))
            basis.append(object_id)
            attributed = item.content_class == ContentClass.ATTRIBUTED_USER_CLAIM
            pairs = sorted({(number, unit) for number in item.numbers
                            for unit in (sorted(units.get(number, ())) or [None])},
                           key=lambda pair: (pair[0], pair[1] or ""))
            arguments = tuple(
                PropositionArgument(role=f"value.{index}", value_type=ArgumentValueType.INTEGER, value=str(number),
                                    unit=unit)
                for index, (number, unit) in enumerate(pairs)
            )
            content_id = f"collaboration-presentation:{item.item_id.rsplit(':', 1)[-1]}"
            content[content_id] = ArtifactPresentation(
                content_id=content_id, cognitive_turn_id=turn, source_kind=ContentSourceKind.COLLABORATIVE_PROPOSAL,
                source_refs=(proposal.content_proposal_id,), represents_refs=(artifact_id, object_id),
                authorized_for_realization=True,
                sensitivity_class=SensitivityClass.PERSONAL if user else SensitivityClass.ORDINARY,
                epistemic_status=(ContentEpistemicStatus.ATTRIBUTED if attributed else ContentEpistemicStatus.TENTATIVE
                                  if hedged else ContentEpistemicStatus.OBSERVED),
                attribution=ContentAttribution.USER if attributed else ContentAttribution.COLUMBINA,
                surface_anchors=item.anchors, artifact_ref=proposal.artifact_ref or "conversation",
                revision_ref=revision_ref or "delivered-dialogue", operation=proposal.operation.value,
                role=item.role.value, content_class=item.content_class.value, meaning=item.statement,
                arguments=arguments, requires_hedge=hedged, licenses_causal=item.role in EXPLANATORY_ROLES,
                licenses_suggestion=item.role in SUGGESTING_ROLES,
                reports_change=item.role == ContentItemRole.CHANGE and proposal.operation.value in {"create", "revise"},
            )
            content_refs.append(content_id)
        put(ActionGroundedObject(
            object_id=artifact_id, object_type=T.ARTIFACT_REVISION, cognitive_turn_id=turn, revision_ref=revision_ref,
            source_refs=(frame_ref,), about_refs=(frame_ref, proposal.goal_ref), asserted_by="columbina",
            owner="shared", basis_refs=tuple(basis), artifact_operation=proposal.operation.value,
        ))
        refs = [artifact_id, *basis]
        for inquiry in proposal.selected_inquiries:
            options = tuple(option_object_id(inquiry.inquiry_id, option) for option in inquiry.answer_options)
            for option, object_id in zip(inquiry.answer_options, options):
                put(ActionGroundedObject(
                    object_id=object_id, object_type=T.OPTION, cognitive_turn_id=turn, source_refs=(artifact_id,),
                    about_refs=(frame_ref,), asserted_by="columbina",
                ))
            put(ActionGroundedObject(
                object_id=inquiry.inquiry_id, object_type=T.INQUIRY, cognitive_turn_id=turn,
                source_refs=(artifact_id,), about_refs=(frame_ref, proposal.goal_ref), asserted_by="columbina",
                owner="columbina", pursues_ref=artifact_id, option_refs=options,
                inquiry_question_form=inquiry.question_kind, inquiry_timing=inquiry.timing.value,
                inquiry_criticality=inquiry.criticality.value,
            ))
            content_id = f"collaboration-inquiry:{_digest(inquiry.inquiry_id)}"
            content[content_id] = InquiryPrompt(
                content_id=content_id, cognitive_turn_id=turn, source_kind=ContentSourceKind.COLLABORATIVE_PROPOSAL,
                source_refs=(proposal.content_proposal_id,), represents_refs=(inquiry.inquiry_id,),
                authorized_for_realization=True, epistemic_status=ContentEpistemicStatus.UNRESOLVED,
                attribution=ContentAttribution.COLUMBINA, surface_anchors=(inquiry.asked_about,),
                inquiry_ref=inquiry.inquiry_id, purpose=inquiry.purpose.value, timing=inquiry.timing.value,
                question_form=inquiry.question_kind,
                option_anchors=tuple(option.replace("_", " ") for option in inquiry.answer_options),
                sensitivity_class=SensitivityClass.PERSONAL if inquiry.sensitivity.value != "ordinary"
                else SensitivityClass.ORDINARY,
            )
            refs.extend((inquiry.inquiry_id, *options))
            content_refs.append(content_id)
        return refs, content_refs

    # -- a failed deliverable -----------------------------------------------------------
    @staticmethod
    def _failure(goal_ref, frame, turn, runtime, put, content):
        request_id = request_object_id(goal_ref)
        reason_id = f"{request_id}#reason"
        put(ActionGroundedObject(
            object_id=reason_id, object_type=T.EVIDENCE_RECORD, cognitive_turn_id=turn,
            source_refs=(runtime.runtime_snapshot_ref,), about_refs=(frame.frame_id,), asserted_by="runtime",
            epistemic_status="supported",
        ))
        put(ActionGroundedObject(
            object_id=request_id, object_type=T.REQUEST, cognitive_turn_id=turn, source_refs=(frame.frame_id,),
            about_refs=(frame.frame_id, goal_ref), focus_contribution=True, asserted_by="user", owner="user",
            requested_by_focus=True, fulfillment_mode="conversational", acceptance_status="unavailable",
            decline_reason_refs=(reason_id,),
        ))
        reason_content = f"collaboration-reason:{_digest(goal_ref)}"
        request_content = f"collaboration-request-ref:{_digest(goal_ref)}"
        content[reason_content] = GroundedProposition(
            content_id=reason_content, cognitive_turn_id=turn, source_kind=ContentSourceKind.TRUSTED_RUNTIME_OBSERVATION,
            source_refs=(runtime.runtime_snapshot_ref,), represents_refs=(reason_id,), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.GROUNDED, attribution=ContentAttribution.RUNTIME,
            subject=PropositionTerm(ref=request_id, surface_anchor=REQUEST_ANCHOR),
            predicate="runtime.requested_content_produced", polarity=Polarity.NEGATIVE,
        )
        content[request_content] = DialogueReference(
            content_id=request_content, cognitive_turn_id=turn, source_kind=ContentSourceKind.CURRENT_USER_STATEMENT,
            source_refs=(frame.frame_id,), represents_refs=(request_id,), authorized_for_realization=True,
            epistemic_status=ContentEpistemicStatus.OBSERVED, attribution=ContentAttribution.USER,
            surface_anchors=(REQUEST_ANCHOR,), reference_kind=DialogueReferenceKind.REQUEST, target_ref=request_id,
            reference_form=ReferenceForm.ANCHORED,
        )
        return (request_id, reason_id), (reason_content, request_content), request_id

    # -- pending requirements -------------------------------------------------------------
    @staticmethod
    def _pending(goal_snapshot, workspace: WorkspaceResolution, handoff: RequirementActionHandoff | None, frames,
                 ) -> tuple[PendingRequirementProposal, ...]:
        if handoff is None:
            return ()
        from ..requirement_resolution_v0.manifest import COLLABORATION_GOAL_DEFINITIONS, elicitation_anchor

        definitions = {record.goal_id: record.definition_ref.definition_key for record in goal_snapshot.records}
        out = []
        for resolution in handoff.snapshot.resolutions:
            if (definitions.get(resolution.goal_ref) not in COLLABORATION_GOAL_DEFINITIONS
                    or not resolution.blocking or resolution.status != RS.MISSING_USER_INPUT
                    or resolution.kind not in PENDING_KINDS):
                continue
            binding = handoff.binding(resolution.resolution_id)
            target = workspace.target(resolution.goal_ref)
            if binding is None or not binding.target_refs or target is None:
                continue
            argument = resolution.argument_name or _PENDING_ARGUMENTS[resolution.kind]
            out.append(PendingRequirementProposal(
                pending_id=pending_id_for(resolution.goal_ref, resolution.resolution_id), goal_ref=resolution.goal_ref,
                frame_ref=resolution.frame_ref, resolution_ref=resolution.resolution_id,
                target_ref=binding.target_refs[0], argument_name=argument, operation=target.operation,
                artifact_type=target.artifact_type, asked_about=elicitation_anchor(argument),
                source_refs=(resolution.frame_ref,),
            ))
        return tuple(sorted(out, key=lambda item: item.pending_id))


__all__ = [
    "CONTENT_PRODUCER", "CollaborationProjection", "CollaborationProjectorV0", "PROJECTION_VERSION",
    "artifact_object_id", "content_object_id", "option_object_id", "pending_id_for", "request_object_id",
]
