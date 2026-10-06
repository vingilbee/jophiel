"""Conversational workspace resolution (``workspace-resolution-v0.1``).

Runs after Goal and before Requirement Resolution.  It resolves *existing*
state only -- it never proposes content:

1. reconciles the predecessor turn's proposals against delivered outcomes
   (``reconciliation``), producing this turn's canonical snapshot;
2. ages, answers, or expires open inquiries: an answer binds only through an
   ``inquiry_answer`` frame that cites the exact supplied inquiry (typed
   linkage, never similarity over raw text);
3. binds each current-turn conversational-deliverable goal to its exact
   target: the unique active artifact revision of the requested type, or --
   when the workspace holds none -- the delivered dialogue the request is
   about.  Several candidates are ambiguous and are never guessed.

Pure: typed snapshots in, a sealed ``WorkspaceResolution`` out.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts.collaboration import (
    InquiryAnswerBinding, WorkspaceResolution, WorkspaceTargetBinding, WorkspaceTargetStatus as T, WorkspaceTurnInputs,
)
from ..contracts.goals import GoalSnapshot, GoalStatus
from ..contracts.interpretation import InterpretationEnvelope, SemanticFrame
from ..contracts.workspace import (
    PRESENTATION_OPERATIONS, ArtifactOperation, ArtifactType, OpenInquiryStatus, WorkspaceSnapshot, empty_workspace,
    workspace_snapshot_id,
)
from ..interpretation_v0.models import InterpretationDialogueContext
from .reconciliation import reconcile

RESOLUTION_VERSION = "workspace-resolution-v0.1"
DELIVERABLE_GOAL = "interaction.fulfill_conversational_deliverable"
ANSWER_GOAL = "interaction.incorporate_inquiry_answer"
WORKSPACE_GOALS = frozenset({DELIVERABLE_GOAL, ANSWER_GOAL})
# Artifact types that name an output form rather than a kind of work product.
FORM_ARTIFACT_TYPES = frozenset({
    ArtifactType.SUMMARY, ArtifactType.LIST, ArtifactType.COMPARISON, ArtifactType.OUTLINE, ArtifactType.OTHER,
})


def _frame_of(goal, frames: dict[str, SemanticFrame], role: str) -> SemanticFrame | None:
    for requirement in goal.requirements:
        for target in requirement.target_refs:
            if target.role == role and target.ref in frames:
                return frames[target.ref]
    return None


def _dialogue_targets(context: InterpretationDialogueContext | None) -> tuple[str, ...]:
    """Delivered, complete utterances of the authorized dialogue context (never shadow output)."""

    if context is None:
        return ()
    return tuple(sorted(
        item.item_ref for item in context.items
        if item.item_kind == "utterance" and item.completeness == "complete"
    ))


def _artifact_type(value) -> ArtifactType | None:
    try:
        return ArtifactType(value)
    except ValueError:
        return None


def _operation(value) -> ArtifactOperation | None:
    try:
        return ArtifactOperation(value)
    except ValueError:
        return None


def _age_inquiries(inquiries: Iterable, *, answers: dict[str, SemanticFrame], turn_ref: str, heads: set[str]):
    out = []
    for inquiry in inquiries:
        if inquiry.status != OpenInquiryStatus.OPEN:
            out.append(inquiry)
            continue
        frame = answers.get(inquiry.inquiry_id)
        if frame is not None:
            declined = frame.arguments.get("answer_kind") == "declined"
            out.append(inquiry.model_copy(update={
                "status": OpenInquiryStatus.DECLINED if declined else OpenInquiryStatus.ANSWERED,
                "resolution_ref": frame.frame_id,
            }))
            continue
        if inquiry.artifact_ref is not None and inquiry.artifact_ref not in heads:
            out.append(inquiry.model_copy(update={
                "status": OpenInquiryStatus.SUPERSEDED, "resolution_ref": f"superseded:{turn_ref}",
            }))
            continue
        elapsed = inquiry.user_turns_elapsed + 1
        if elapsed > inquiry.expires_after_user_turns:
            # Carry-forward policy: unanswered beyond its window, it expires.
            out.append(inquiry.model_copy(update={
                "status": OpenInquiryStatus.EXPIRED, "resolution_ref": f"expired:{turn_ref}",
            }))
        else:
            out.append(inquiry.model_copy(update={"user_turns_elapsed": elapsed}))
    return tuple(sorted(out, key=lambda item: item.inquiry_id))


def resolve_workspace(
    *, cognitive_turn_id: str, conversation_ref: str, inputs: WorkspaceTurnInputs,
    interpretation: InterpretationEnvelope, goal_snapshot: GoalSnapshot,
    dialogue_context: InterpretationDialogueContext | None,
) -> WorkspaceResolution:
    previous = inputs.previous_snapshot or empty_workspace(conversation_ref, inputs.authority_mode)
    if previous.conversation_ref != conversation_ref:
        raise ValueError("the previous workspace belongs to a different conversation")
    reconciled = reconcile(previous, record=inputs.predecessor_record, outcomes=inputs.delivered_outcomes,
                           turn_ref=cognitive_turn_id)
    frames = {frame.frame_id: frame for frame in interpretation.semantic_frames}
    answers: dict[str, SemanticFrame] = {}
    bindings: list[InquiryAnswerBinding] = []
    open_refs = {item.inquiry_id for item in reconciled.inquiries if item.status == OpenInquiryStatus.OPEN}
    answer_goals = {
        frame.frame_id: goal.goal_id
        for goal in goal_snapshot.records
        if goal.definition_ref.definition_key == ANSWER_GOAL and goal.created_turn_ref == cognitive_turn_id
        and goal.status == GoalStatus.ACTIVE
        for frame in (_frame_of(goal, frames, "answer_ref"),) if frame is not None
    }
    for frame in sorted(interpretation.semantic_frames, key=lambda item: item.frame_id):
        if frame.predicate != "inquiry_answer" or frame.arguments.get("embedding", "direct") != "direct":
            continue
        ref = frame.arguments.get("inquiry_ref")
        if ref not in open_refs or ref in answers:
            continue  # the parser validated it was supplied; one answer per inquiry
        answers[ref] = frame
        bindings.append(InquiryAnswerBinding(
            inquiry_ref=ref, frame_ref=frame.frame_id, answer_kind=frame.arguments["answer_kind"],
            answer_option=frame.arguments.get("answer_option"), goal_ref=answer_goals.get(frame.frame_id),
        ))
    heads = {item.artifact_id for item in reconciled.heads if item.status == "active"}
    inquiries = _age_inquiries(reconciled.inquiries, answers=answers, turn_ref=cognitive_turn_id, heads=heads)
    sequence = previous.sequence + 1
    snapshot = WorkspaceSnapshot.sealed(
        snapshot_id=workspace_snapshot_id(conversation_ref, sequence, previous.snapshot_id, cognitive_turn_id),
        conversation_ref=conversation_ref, authority_mode=previous.authority_mode, sequence=sequence,
        previous_snapshot_ref=previous.snapshot_id, turn_ref=cognitive_turn_id, heads=reconciled.heads,
        revisions=reconciled.revisions, inquiries=inquiries,
    )
    targets = []
    dialogue = _dialogue_targets(dialogue_context)
    previous_inquiries = {item.inquiry_id: item for item in reconciled.inquiries}
    for goal in sorted(goal_snapshot.records, key=lambda item: item.goal_id):
        key = goal.definition_ref.definition_key
        if key not in WORKSPACE_GOALS or goal.created_turn_ref != cognitive_turn_id or goal.status != GoalStatus.ACTIVE:
            continue
        if key == DELIVERABLE_GOAL:
            frame = _frame_of(goal, frames, "request_ref")
            if frame is None:
                continue
            targets.append(_deliverable_target(goal.goal_id, frame, snapshot, dialogue))
        else:
            frame = _frame_of(goal, frames, "answer_ref")
            if frame is None:
                continue
            targets.append(_answer_target(goal.goal_id, frame, previous_inquiries, snapshot))
    return WorkspaceResolution.sealed(
        cognitive_turn_id=cognitive_turn_id, conversation_ref=conversation_ref, input_snapshot_ref=previous.snapshot_id,
        snapshot=snapshot, reconciliations=reconciled.records, targets=tuple(targets),
        answer_bindings=tuple(sorted(bindings, key=lambda item: item.inquiry_ref)),
    )


def _deliverable_target(goal_ref: str, frame: SemanticFrame, snapshot: WorkspaceSnapshot,
                        dialogue: tuple[str, ...]) -> WorkspaceTargetBinding:
    operation = _operation(frame.arguments.get("operation"))
    artifact_type = _artifact_type(frame.arguments.get("artifact_type"))
    base = dict(goal_ref=goal_ref, frame_ref=frame.frame_id)
    if operation is None or artifact_type is None:
        # An unresolved operation or type is a user-input requirement, resolved downstream.
        return WorkspaceTargetBinding(**base, operation=operation or ArtifactOperation.CREATE,
                                      artifact_type=artifact_type or ArtifactType.OTHER, status=T.NOT_FOUND,
                                      rule_id="workspace-target.unresolved-frame.v1")
    base.update(operation=operation, artifact_type=artifact_type)
    if operation == ArtifactOperation.CREATE:
        return WorkspaceTargetBinding(**base, status=T.NOT_REQUIRED, rule_id="workspace-target.create.v1")
    candidates = [head for head in snapshot.active_heads if head.artifact_type == artifact_type]
    rule = "workspace-target.unique-active-artifact.v1"
    if not candidates and operation in PRESENTATION_OPERATIONS and artifact_type in FORM_ARTIFACT_TYPES:
        # A summary, list, or comparison *of* the shared work names its output
        # form, not its target: the target is the active artifact, if unique.
        candidates = list(snapshot.active_heads)
        rule = "workspace-target.unique-active-artifact-any-type.v1"
    if len(candidates) == 1:
        head = candidates[0]
        return WorkspaceTargetBinding(**base, status=T.RESOLVED_ARTIFACT, artifact_ref=head.artifact_id,
                                      base_revision_ref=head.head_revision_ref, rule_id=rule)
    if len(candidates) > 1:
        return WorkspaceTargetBinding(**base, status=T.AMBIGUOUS,
                                      candidate_artifact_refs=tuple(sorted(item.artifact_id for item in candidates)),
                                      rule_id="workspace-target.ambiguous-artifacts.v1")
    if dialogue:
        # No workspace artifact: the request is about what was delivered in
        # the conversation itself (quote/reference only, never a fact).
        return WorkspaceTargetBinding(**base, status=T.RESOLVED_DIALOGUE, dialogue_refs=dialogue,
                                      rule_id="workspace-target.delivered-dialogue.v1")
    return WorkspaceTargetBinding(**base, status=T.NOT_FOUND, rule_id="workspace-target.not-found.v1")


def _answer_target(goal_ref: str, frame: SemanticFrame, inquiries: dict, snapshot: WorkspaceSnapshot,
                   ) -> WorkspaceTargetBinding:
    inquiry = inquiries.get(frame.arguments.get("inquiry_ref"))
    if inquiry is None or inquiry.status != OpenInquiryStatus.OPEN:
        return WorkspaceTargetBinding(goal_ref=goal_ref, frame_ref=frame.frame_id, operation=ArtifactOperation.REVISE,
                                      artifact_type=ArtifactType.OTHER, status=T.NOT_FOUND,
                                      inquiry_ref=frame.arguments.get("inquiry_ref"),
                                      rule_id="workspace-target.inquiry-not-open.v1")
    head = next((item for item in snapshot.active_heads if item.artifact_id == inquiry.artifact_ref), None)
    if inquiry.record_kind == "pending_requirement":
        # The answer resumes the parent request with its recorded operation.
        operation, artifact_type = inquiry.parent_operation, inquiry.parent_artifact_type
        if head is None and operation != ArtifactOperation.CREATE:
            candidates = [item for item in snapshot.active_heads if item.artifact_type == artifact_type]
            head = candidates[0] if len(candidates) == 1 else None
    else:
        operation = ArtifactOperation.REVISE
        artifact_type = head.artifact_type if head is not None else ArtifactType.OTHER
    base = dict(goal_ref=goal_ref, frame_ref=frame.frame_id, operation=operation, artifact_type=artifact_type,
                inquiry_ref=inquiry.inquiry_id)
    if operation == ArtifactOperation.CREATE:
        return WorkspaceTargetBinding(**base, status=T.NOT_REQUIRED, rule_id="workspace-target.answer-resumes-create.v1")
    if head is None:
        return WorkspaceTargetBinding(**base, status=T.NOT_FOUND, rule_id="workspace-target.answer-artifact-missing.v1")
    return WorkspaceTargetBinding(**base, status=T.RESOLVED_ARTIFACT, artifact_ref=head.artifact_id,
                                  base_revision_ref=head.head_revision_ref,
                                  rule_id="workspace-target.answer-parent-artifact.v1")


__all__ = ["ANSWER_GOAL", "DELIVERABLE_GOAL", "RESOLUTION_VERSION", "WORKSPACE_GOALS", "resolve_workspace"]
