"""Delivered-outcome reconciliation (``workspace-reconciliation-v0.1``).

Only a validated delivered-response outcome can advance a workspace head or
open an inquiry, and only in an authority mode that permits it:

- ``observational_shadow``: shadow proposals are trace-only.  Every prior
  proposal reconciles as ``shadow_only_not_delivered``; an outcome citing one
  is an integrity failure, never a delivery.
- ``simulated_authority``: offline evaluation only.  An explicitly marked
  simulated delivery of a reviewed shadow output advances the head exactly as
  a real delivery would, in an isolated simulated conversation.
- ``live_authority``: reserved for a separately approved rollout; not approved
  here, so it fails closed.

Head advancement is compare-and-set: a delivered revision whose base is no
longer the head reconciles as ``artifact_conflict`` and never overwrites it.
Pure: no I/O, clock, or model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..contracts.collaboration import (
    CollaborationProposal, CollaborationTurnRecord, PendingRequirementProposal, ReconciliationRecord,
)
from ..contracts.workspace import (
    ArtifactHead, ArtifactRevisionStatus, DeliveredResponseOutcome, DeliveryChannel, DeliveryStatus,
    InquiryCriticality, InquiryExpectedUse, InquiryPurpose, InquiryTargetKind, OpenInquiryRecord,
    ReconciliationState as R, WorkspaceAuthorityMode as Mode, WorkspaceSnapshot,
)
from .errors import WorkspaceError, WorkspaceErrorCode as E

RECONCILIATION_VERSION = "workspace-reconciliation-v0.1"
PENDING_REQUIREMENT_EXPIRY_TURNS = 1


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


@dataclass(frozen=True)
class ReconciliationResult:
    heads: tuple[ArtifactHead, ...]
    revisions: tuple
    inquiries: tuple[OpenInquiryRecord, ...]
    records: tuple[ReconciliationRecord, ...]


def _proposal_refs(proposal: CollaborationProposal) -> set[str]:
    refs = {proposal.content_proposal_id, *(item.inquiry_id for item in proposal.selected_inquiries)}
    if proposal.proposed_revision is not None:
        refs.add(proposal.proposed_revision.revision_id)
    return refs


def _cited(outcome: DeliveredResponseOutcome) -> set[str]:
    return {*outcome.artifact_revision_refs, *outcome.inquiry_refs, *outcome.delivered_content_refs}


def reconcile(
    snapshot: WorkspaceSnapshot, *, record: CollaborationTurnRecord | None,
    outcomes: tuple[DeliveredResponseOutcome, ...], turn_ref: str,
) -> ReconciliationResult:
    """Reconcile the predecessor turn's proposals against the observed deliveries."""

    mode = snapshot.authority_mode
    if mode == Mode.LIVE_AUTHORITY:
        raise WorkspaceError(E.AUTHORITY_MODE_NOT_APPROVED)
    proposals = record.proposals if record is not None else ()
    pending = record.pending_requirements if record is not None else ()
    known = {ref for item in proposals for ref in _proposal_refs(item)} | {item.pending_id for item in pending}
    for outcome in outcomes:
        if outcome.conversation_ref != snapshot.conversation_ref:
            raise WorkspaceError(E.OUTCOME_LINEAGE_MISMATCH, outcome.outcome_id)
        if mode == Mode.OBSERVATIONAL_SHADOW:
            raise WorkspaceError(E.SHADOW_DELIVERY_IN_OBSERVATIONAL_MODE, outcome.outcome_id)
        if outcome.delivery_channel != DeliveryChannel.SIMULATED:
            # Simulated authority accepts only marked simulated deliveries.
            raise WorkspaceError(E.AUTHORITY_MODE_NOT_APPROVED, outcome.outcome_id)
        if record is None or outcome.turn_ref != record.turn_ref:
            raise WorkspaceError(E.OUTCOME_LINEAGE_MISMATCH, outcome.outcome_id)
        if not _cited(outcome) <= known:
            raise WorkspaceError(E.OUTCOME_CITES_UNKNOWN_PROPOSAL, outcome.outcome_id)

    heads = {item.artifact_id: item for item in snapshot.heads}
    revisions = {item.revision_id: item for item in snapshot.revisions}
    inquiries = {item.inquiry_id: item for item in snapshot.inquiries}
    records: list[ReconciliationRecord] = []

    def outcome_for(refs: set[str]) -> DeliveredResponseOutcome | None:
        matching = [item for item in outcomes if _cited(item) & refs]
        if len(matching) > 1:
            raise WorkspaceError(E.OUTCOME_LINEAGE_MISMATCH, sorted(refs)[0])
        return matching[0] if matching else None

    for proposal in sorted(proposals, key=lambda item: item.content_proposal_id):
        refs = _proposal_refs(proposal)
        base = dict(proposal_ref=proposal.content_proposal_id, goal_ref=proposal.goal_ref,
                    proposed_turn_ref=proposal.cognitive_turn_id)
        if mode == Mode.OBSERVATIONAL_SHADOW:
            records.append(ReconciliationRecord(
                **base, state=R.SHADOW_ONLY_NOT_DELIVERED, rule_id="reconcile.observational-shadow-trace-only.v1",
            ))
            continue
        outcome = outcome_for(refs)
        if outcome is None:
            records.append(ReconciliationRecord(**base, state=R.ABANDONED, rule_id="reconcile.not-delivered.v1"))
            continue
        if outcome.delivery_status == DeliveryStatus.FAILED:
            records.append(ReconciliationRecord(**base, state=R.DELIVERY_FAILED, outcome_ref=outcome.outcome_id,
                                                rule_id="reconcile.delivery-failed.v1"))
            continue
        revision_ref = None
        revision = proposal.proposed_revision
        if revision is not None:
            if revision.revision_id not in outcome.artifact_revision_refs:
                records.append(ReconciliationRecord(**base, state=R.CONTENT_REJECTED, outcome_ref=outcome.outcome_id,
                                                    rule_id="reconcile.revision-not-delivered.v1"))
                continue
            head = heads.get(revision.artifact_id)
            expected_base = head.head_revision_ref if head is not None else None
            if revision.parent_revision_ref != expected_base:
                # Compare-and-set: the base moved, so this delivery never overwrites the head.
                records.append(ReconciliationRecord(**base, state=R.ARTIFACT_CONFLICT, outcome_ref=outcome.outcome_id,
                                                    revision_ref=revision.revision_id,
                                                    rule_id="reconcile.compare-and-set-conflict.v1"))
                continue
            if head is not None:
                previous = revisions[head.head_revision_ref]
                revisions[previous.revision_id] = previous.with_status(
                    ArtifactRevisionStatus.SUPERSEDED, delivered_outcome_ref=previous.delivered_outcome_ref,
                )
            delivered = revision.with_status(ArtifactRevisionStatus.DELIVERED, delivered_outcome_ref=outcome.outcome_id)
            revisions[delivered.revision_id] = delivered
            heads[delivered.artifact_id] = ArtifactHead(
                artifact_id=delivered.artifact_id, artifact_type=delivered.artifact_type,
                head_revision_ref=delivered.revision_id, revision_number=delivered.revision_number,
            )
            revision_ref = delivered.revision_id
        opened = []
        for inquiry in proposal.selected_inquiries:
            if inquiry.inquiry_id not in outcome.inquiry_refs:
                continue
            inquiries[inquiry.inquiry_id] = OpenInquiryRecord(
                inquiry_id=inquiry.inquiry_id, record_kind="optional_inquiry",
                conversation_ref=snapshot.conversation_ref, parent_goal_ref=inquiry.parent_goal_ref,
                originating_frame_ref=inquiry.originating_frame_ref,
                artifact_ref=inquiry.artifact_ref if inquiry.artifact_ref in heads else None,
                target_kind=inquiry.target_kind, target_ref=inquiry.target_ref, purpose=inquiry.purpose,
                expected_use=inquiry.expected_use, criticality=inquiry.criticality, blocking=False,
                asked_about=inquiry.asked_about, answer_options=inquiry.answer_options,
                sensitivity=inquiry.sensitivity, elicitation_move_ref=outcome.selected_action_ref,
                elicitation_delivery_ref=outcome.outcome_id, created_turn_ref=proposal.cognitive_turn_id,
                expires_after_user_turns=inquiry.expires_after_user_turns,
            )
            opened.append(inquiry.inquiry_id)
        records.append(ReconciliationRecord(
            **base, state=R.DELIVERED_WAITING_FOR_ANSWER if opened else R.DELIVERED_AND_SATISFIED,
            outcome_ref=outcome.outcome_id, revision_ref=revision_ref, inquiry_refs=tuple(sorted(opened)),
            rule_id="reconcile.delivered.v1",
        ))

    for item in sorted(pending, key=lambda value: value.pending_id):
        _reconcile_pending(item, mode=mode, outcomes=outcomes, outcome_for=outcome_for, snapshot=snapshot,
                           record=record, inquiries=inquiries, records=records, heads=heads)

    return ReconciliationResult(
        heads=tuple(heads[key] for key in sorted(heads)),
        revisions=tuple(sorted(revisions.values(), key=lambda item: (item.artifact_id, item.revision_number))),
        inquiries=tuple(inquiries[key] for key in sorted(inquiries)),
        records=tuple(sorted(records, key=lambda item: item.proposal_ref)),
    )


def _reconcile_pending(item: PendingRequirementProposal, *, mode, outcomes, outcome_for, snapshot, record, inquiries,
                       records, heads) -> None:
    base = dict(proposal_ref=item.pending_id, goal_ref=item.goal_ref, proposed_turn_ref=record.turn_ref)
    if mode == Mode.OBSERVATIONAL_SHADOW:
        records.append(ReconciliationRecord(**base, state=R.SHADOW_ONLY_NOT_DELIVERED,
                                            rule_id="reconcile.observational-shadow-trace-only.v1"))
        return
    outcome = outcome_for({item.pending_id})
    if outcome is None or outcome.delivery_status == DeliveryStatus.FAILED:
        state = R.ABANDONED if outcome is None else R.DELIVERY_FAILED
        records.append(ReconciliationRecord(**base, state=state, outcome_ref=outcome.outcome_id if outcome else None,
                                            rule_id="reconcile.pending-requirement-not-asked.v1"))
        return
    inquiries[item.pending_id] = OpenInquiryRecord(
        inquiry_id=item.pending_id, record_kind="pending_requirement", conversation_ref=snapshot.conversation_ref,
        parent_goal_ref=item.goal_ref, originating_frame_ref=item.frame_ref, artifact_ref=None,
        target_kind=InquiryTargetKind.REQUEST_ARGUMENT, target_ref=item.target_ref,
        purpose=InquiryPurpose.RESOLVE_REQUIRED_MEANING, expected_use=InquiryExpectedUse.COMPLETE_REQUEST,
        criticality=InquiryCriticality.BLOCKING, blocking=True, asked_about=item.asked_about,
        elicitation_move_ref=outcome.selected_action_ref, elicitation_delivery_ref=outcome.outcome_id,
        created_turn_ref=record.turn_ref, expires_after_user_turns=PENDING_REQUIREMENT_EXPIRY_TURNS,
        parent_operation=item.operation, parent_artifact_type=item.artifact_type,
    )
    records.append(ReconciliationRecord(**base, state=R.DELIVERED_WAITING_FOR_ANSWER, outcome_ref=outcome.outcome_id,
                                        inquiry_refs=(item.pending_id,), rule_id="reconcile.pending-requirement-asked.v1"))


__all__ = ["PENDING_REQUIREMENT_EXPIRY_TURNS", "RECONCILIATION_VERSION", "ReconciliationResult", "reconcile"]
