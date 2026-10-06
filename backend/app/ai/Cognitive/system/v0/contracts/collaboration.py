"""Collaborative Content Production V0 contracts (``collaborative-content-contract-v0.1``).

Collaborative content production is constructive reasoning between
requirement resolution and Action Selection: for a ready conversational
deliverable it proposes an artifact patch (create/revise) or presentation
content (explain, evaluate, describe, compare, summarize), plus inquiry
opportunities.  It is neither final wording (Realization) nor external tool
execution, and nothing it proposes is delivered until a validated delivered
outcome says so.

Contracts only.  Everything a proposal holds is typed: content items carry a
closed role, content class, claim kind, and exact source refs; inquiries carry
a closed purpose, expected use, criticality, and timing.  No final
user-facing prose belongs here.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .interpretation import FrozenModel
from .workspace import (
    MAX_LABEL_CHARACTERS, ArtifactOperation, ArtifactPatch, ArtifactRevision, ArtifactRevisionStatus, ArtifactType,
    ContentClass, DeliveredResponseOutcome, InquiryCriticality, InquiryExpectedUse, InquiryPurpose,
    InquirySensitivity, InquiryTargetKind, InquiryTiming, MODEL_ASSIGNABLE_CLASSES, ReconciliationState,
    WorkspaceAuthorityMode, WorkspaceSnapshot, bounded_text, envelope_digest, sha256_of,
)

COLLABORATION_CONTRACT_VERSION = "collaborative-content-contract-v0.1"
COLLABORATION_COMPILER_VERSION = "collaborative-content-compiler-v0.1"
# Recorded per candidate in the provider provenance; selects the tolerant
# bookkeeping rules in ``CollaborationCompilerV0.compile``.
COLLABORATION_COMPILER_VERSION_V0_2 = "collaborative-content-compiler-v0.2"
COLLABORATION_PROTOCOL_VERSION = "collaborative-content-model-protocol-v0.1"
INQUIRY_POLICY_VERSION = "inquiry-timing-policy-v0.1"
WORKSPACE_RESOLUTION_VERSION = "workspace-resolution-v0.1"
COLLABORATION_PROJECTION_VERSION = "collaboration-action-projection-v0.1"
# The Realization content producer id for projected artifact and inquiry content.
COLLABORATION_CONTENT_PRODUCER = "collaboration-content-projection-v0.1"
MAX_STATEMENT_CHARACTERS = 240


def _sorted_unique(values: tuple[str, ...], label: str) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must not be blank")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be unique and in canonical order")


# ---------------------------------------------------------------------------
# Content items
# ---------------------------------------------------------------------------

class ContentItemRole(StrEnum):
    """What one proposed content item does in the delivered contribution."""

    CHANGE = "change"                  # describes a change the patch makes (create/revise)
    EXPLANATION = "explanation"        # why or how the artifact works or fits
    EVALUATION = "evaluation"          # an assessment of the artifact
    DESCRIPTION = "description"        # the artifact presented in a requested style
    COMPARISON = "comparison"          # weighs alternatives
    SUMMARY = "summary"                # condenses the artifact or the agreed points
    ALTERNATIVE = "alternative"        # an optional alternative the user may choose
    SAFETY_GUIDANCE = "safety_guidance"  # cautious guidance tied to an attributed constraint


class ClaimKind(StrEnum):
    """The kind of claim an item makes.  Restricted kinds need trusted sources the model never has."""

    ARTIFACT_STRUCTURE = "artifact_structure"   # what the shared artifact contains
    CONSTRAINT_FIT = "constraint_fit"           # how it meets a stated requirement
    CULINARY_SUGGESTION = "culinary_suggestion"
    GENERAL_PRACTICE = "general_practice"       # ordinary, non-specific practical guidance
    PREFERENCE_FIT = "preference_fit"
    NUTRIENT_AMOUNT = "nutrient_amount"
    CURRENT_PRICE = "current_price"
    LEGAL_CONCLUSION = "legal_conclusion"
    MEDICAL_ADVICE = "medical_advice"
    TOOL_OUTCOME = "tool_outcome"
    EXTERNAL_STATE_CHANGE = "external_state_change"


# Claims that keep their trusted-source requirement (Section 8.2): a
# collaborative-content proposal can never supply one.
RESTRICTED_CLAIM_KINDS = frozenset({
    ClaimKind.NUTRIENT_AMOUNT, ClaimKind.CURRENT_PRICE, ClaimKind.LEGAL_CONCLUSION, ClaimKind.MEDICAL_ADVICE,
    ClaimKind.TOOL_OUTCOME, ClaimKind.EXTERNAL_STATE_CHANGE,
})
# The roles each operation may produce.
ROLES_BY_OPERATION: Mapping[ArtifactOperation, frozenset[ContentItemRole]] = {
    ArtifactOperation.CREATE: frozenset({ContentItemRole.CHANGE, ContentItemRole.EXPLANATION,
                                         ContentItemRole.ALTERNATIVE, ContentItemRole.SAFETY_GUIDANCE}),
    ArtifactOperation.REVISE: frozenset({ContentItemRole.CHANGE, ContentItemRole.EXPLANATION,
                                         ContentItemRole.ALTERNATIVE, ContentItemRole.SAFETY_GUIDANCE}),
    ArtifactOperation.EXPLAIN: frozenset({ContentItemRole.EXPLANATION}),
    ArtifactOperation.EVALUATE: frozenset({ContentItemRole.EVALUATION, ContentItemRole.ALTERNATIVE,
                                           ContentItemRole.SAFETY_GUIDANCE}),
    ArtifactOperation.DESCRIBE: frozenset({ContentItemRole.DESCRIPTION}),
    ArtifactOperation.COMPARE: frozenset({ContentItemRole.COMPARISON, ContentItemRole.ALTERNATIVE}),
    ArtifactOperation.SUMMARIZE: frozenset({ContentItemRole.SUMMARY}),
}
# Roles whose item may carry causal or evaluative language in Realization.
EXPLANATORY_ROLES = frozenset({ContentItemRole.EXPLANATION, ContentItemRole.EVALUATION,
                               ContentItemRole.COMPARISON, ContentItemRole.SAFETY_GUIDANCE})
# Roles worded as a suggestion (advice-like language is licensed inside them).
SUGGESTING_ROLES = frozenset({ContentItemRole.ALTERNATIVE, ContentItemRole.SAFETY_GUIDANCE,
                              ContentItemRole.EVALUATION})


class CollaborativeContentItem(FrozenModel):
    """One proposed meaning.  ``anchors`` are the exact surface terms wording must keep."""

    item_id: str = Field(min_length=1)
    role: ContentItemRole
    statement: str = Field(min_length=1)
    anchors: tuple[str, ...] = Field(min_length=1, max_length=6)
    content_class: ContentClass
    claim_kind: ClaimKind
    subject_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = Field(min_length=1)
    # Integer values the statement carries (each must come from typed artifact content).
    numbers: tuple[int, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> CollaborativeContentItem:
        bounded_text(self.statement, "content statement", MAX_STATEMENT_CHARACTERS)
        for anchor in self.anchors:
            bounded_text(anchor, "content anchor", MAX_LABEL_CHARACTERS)
            if anchor.casefold() not in self.statement.casefold():
                raise ValueError("every anchor occurs in its statement")
        _sorted_unique(self.subject_refs, "content subject refs")
        _sorted_unique(self.source_refs, "content source refs")
        if self.content_class not in MODEL_ASSIGNABLE_CLASSES and self.content_class != ContentClass.ARTIFACT_CONTENT:
            raise ValueError("a proposed item's content class is model-assignable or carried artifact content")
        if self.claim_kind in RESTRICTED_CLAIM_KINDS:
            raise ValueError("a restricted claim kind needs a trusted source a proposal never has")
        if len(self.numbers) != len(set(self.numbers)):
            raise ValueError("item numbers must be unique")
        return self


# ---------------------------------------------------------------------------
# Inquiry opportunities (Section 11)
# ---------------------------------------------------------------------------

class InquiryDisposition(StrEnum):
    SELECTED = "selected"
    SUPPRESSED = "suppressed"


class InquirySuppressionReason(StrEnum):
    UNRELATED_TARGET = "unrelated_target"
    SENSITIVITY_EXCEEDS_VALUE = "sensitivity_exceeds_value"
    ALREADY_ANSWERED = "already_answered"
    RECENTLY_ASKED = "recently_asked"
    BELOW_CURIOSITY_THRESHOLD = "below_curiosity_threshold"
    QUESTION_BUDGET_EXHAUSTED = "question_budget_exhausted"
    UNLIKELY_TO_CHANGE_BEHAVIOR = "unlikely_to_change_behavior"


class InquiryOpportunity(FrozenModel):
    """A typed question Columbina could ask, with its purpose and expected use."""

    inquiry_id: str = Field(min_length=1)
    parent_goal_ref: str = Field(min_length=1)
    originating_frame_ref: str = Field(min_length=1)
    artifact_ref: str | None = None
    target_kind: InquiryTargetKind
    target_ref: str = Field(min_length=1)
    question_kind: Literal["open_set", "yes_no", "choice"]
    purpose: InquiryPurpose
    expected_use: InquiryExpectedUse
    criticality: InquiryCriticality
    timing: InquiryTiming
    sensitivity: InquirySensitivity
    repeat_policy: Literal["once_per_target"] = "once_per_target"
    expires_after_user_turns: int = Field(default=2, ge=1, le=6)
    asked_about: str = Field(min_length=1)
    answer_options: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = Field(min_length=1)
    # Deterministic policy outcome (Section 11.2-11.3).
    disposition: InquiryDisposition
    suppression_reason: InquirySuppressionReason | None = None
    score: str = Field(pattern=r"^-?\d+\.\d{6}$")
    policy_rule_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> InquiryOpportunity:
        bounded_text(self.asked_about, "inquiry anchor", MAX_LABEL_CHARACTERS)
        _sorted_unique(self.source_refs, "inquiry source refs")
        if len(self.answer_options) != len(set(self.answer_options)):
            raise ValueError("inquiry answer options must be unique")
        if (self.question_kind == "choice") != (len(self.answer_options) >= 2):
            raise ValueError("a choice question, and only it, offers at least two options")
        if (self.disposition == InquiryDisposition.SUPPRESSED) != (self.suppression_reason is not None):
            raise ValueError("a suppressed inquiry, and only it, names its suppression reason")
        if self.criticality == InquiryCriticality.BLOCKING:
            raise ValueError("blocking prerequisites come from requirement resolution, never a proposal")
        if self.timing == InquiryTiming.BEFORE_PRIMARY:
            raise ValueError("a non-blocking inquiry never precedes the primary contribution")
        return self


# ---------------------------------------------------------------------------
# Proposals, failures, and the per-goal outcome
# ---------------------------------------------------------------------------

class CollaborationFailureCode(StrEnum):
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_CONFIGURATION_INCOMPATIBLE = "provider_configuration_incompatible"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_TRANSPORT = "provider_transport"
    PROVIDER_REFUSED = "provider_refused"
    PROVIDER_OUTPUT_INVALID = "provider_output_invalid"
    SCHEMA_INVALID = "schema_invalid"
    OPERATION_MISMATCH = "operation_mismatch"
    ARTIFACT_TYPE_MISMATCH = "artifact_type_mismatch"
    UNEXPECTED_PATCH = "unexpected_patch"
    MISSING_PATCH = "missing_patch"
    MISSING_CONTENT = "missing_content"
    FOREIGN_SOURCE_REF = "foreign_source_ref"
    FOREIGN_SUBJECT_REF = "foreign_subject_ref"
    UNSUPPORTED_CONTENT_CLASS = "unsupported_content_class"
    UNSUPPORTED_VERIFIED_CLAIM = "unsupported_verified_claim"
    FABRICATED_OUTCOME = "fabricated_outcome"
    ROLE_NOT_ALLOWED = "role_not_allowed"
    USER_CONSTRAINT_IGNORED = "user_constraint_ignored"
    UNSUPPORTED_NUMBER = "unsupported_number"
    PATCH_REJECTED = "patch_rejected"
    INQUIRY_INVALID = "inquiry_invalid"
    SENSITIVE_CONTENT_UNAUTHORIZED = "sensitive_content_unauthorized"
    CONTENT_INVALID = "content_invalid"


RETRYABLE_COLLABORATION_FAILURES = frozenset({
    CollaborationFailureCode.PROVIDER_TIMEOUT, CollaborationFailureCode.PROVIDER_TRANSPORT,
})


class CollaborationProviderProvenance(FrozenModel):
    """Every behavior-affecting option of one model-backed proposal call (never a replay oracle)."""

    provider: str = Field(min_length=1)
    provider_adapter: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_family: str = Field(min_length=1)
    endpoint: Literal["chat.completions", "offline_double"]
    capability_manifest_version: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    schema_name: str = Field(min_length=1)
    temperature: str = Field(min_length=1)
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    candidate_count: Literal[1] = 1
    structured_output: Literal["json_schema_strict"] = "json_schema_strict"
    tools: Literal["none"] = "none"
    streaming: Literal[False] = False
    provider_storage: Literal["disabled"] = "disabled"
    sdk_retries: Literal[0] = 0
    replayable: Literal[False] = False
    # The capability manifest's reasoning control (e.g. ``none`` for models
    # that accept a custom temperature only without reasoning).  Omitted when
    # not sent, so earlier recorded provenance serializes unchanged.
    reasoning_effort: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unsent_reasoning(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("reasoning_effort") is None:
            data.pop("reasoning_effort", None)
        return data


class CollaborationProposal(FrozenModel):
    """The validated, compiled proposal for one deliverable goal (Section 9.3)."""

    content_proposal_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    frame_ref: str = Field(min_length=1)
    operation: ArtifactOperation
    artifact_type: ArtifactType
    artifact_ref: str | None = None
    target_revision_ref: str | None = None
    target_dialogue_refs: tuple[str, ...] = ()
    patch: ArtifactPatch | None = None
    proposed_revision: ArtifactRevision | None = None
    content_items: tuple[CollaborativeContentItem, ...] = ()
    inquiry_opportunities: tuple[InquiryOpportunity, ...] = ()
    source_refs: tuple[str, ...] = Field(min_length=1)
    epistemic_classes: tuple[ContentClass, ...] = ()
    uncertainty_item_refs: tuple[str, ...] = ()
    candidate_digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> CollaborationProposal:
        _sorted_unique(self.source_refs, "proposal source refs")
        _sorted_unique(self.target_dialogue_refs, "proposal dialogue targets")
        revising = self.operation in {ArtifactOperation.CREATE, ArtifactOperation.REVISE}
        if revising != (self.patch is not None) or revising != (self.proposed_revision is not None):
            raise ValueError("a create or revise proposal, and only it, carries a patch and proposed revision")
        if self.proposed_revision is not None:
            if self.proposed_revision.status != ArtifactRevisionStatus.PROPOSED:
                raise ValueError("a proposal's revision is proposed, never delivered")
            if self.proposed_revision.artifact_id != self.artifact_ref:
                raise ValueError("the proposed revision belongs to a different artifact")
            # A revision of delivered dialogue content (no workspace artifact
            # yet) lifts it into a new artifact: a create patch.
            lifted = self.operation == ArtifactOperation.REVISE and bool(self.target_dialogue_refs)
            expected = ArtifactOperation.CREATE if lifted else self.operation
            if self.patch.artifact_ref != self.artifact_ref or self.patch.operation.value != expected.value:
                raise ValueError("the patch differs from the proposal it implements")
        if not revising and (self.target_revision_ref is None) == (not self.target_dialogue_refs):
            raise ValueError("a presentation proposal targets exactly a revision or delivered dialogue content")
        if self.operation == ArtifactOperation.CREATE and (self.target_revision_ref or self.target_dialogue_refs):
            raise ValueError("a create proposal has no existing target")
        if not self.content_items:
            raise ValueError("a proposal carries at least one content item")
        ids = tuple(item.item_id for item in self.content_items)
        if len(ids) != len(set(ids)):
            raise ValueError("content item ids must be unique")
        allowed = ROLES_BY_OPERATION[self.operation]
        if any(item.role not in allowed for item in self.content_items):
            raise ValueError("a content item's role is not produced by this operation")
        if self.epistemic_classes != tuple(sorted({item.content_class for item in self.content_items})):
            raise ValueError("the proposal's epistemic classes summarize its items")
        if not set(self.uncertainty_item_refs) <= set(ids):
            raise ValueError("uncertainty refs name proposal items")
        inquiry_ids = tuple(item.inquiry_id for item in self.inquiry_opportunities)
        if len(inquiry_ids) != len(set(inquiry_ids)):
            raise ValueError("inquiry ids must be unique")
        if any(item.parent_goal_ref != self.goal_ref for item in self.inquiry_opportunities):
            raise ValueError("an inquiry belongs to its proposal's goal")
        return self

    @property
    def selected_inquiries(self) -> tuple[InquiryOpportunity, ...]:
        return tuple(item for item in self.inquiry_opportunities if item.disposition == InquiryDisposition.SELECTED)


class PendingRequirementProposal(FrozenModel):
    """A blocking requirement a clarification of this turn asks for (Section 10.3).

    It becomes a carried ``pending_requirement`` record only if a validated
    delivered outcome names it; its answer then resumes the parent request.
    """

    pending_id: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    frame_ref: str = Field(min_length=1)
    resolution_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    argument_name: str = Field(min_length=1)
    operation: ArtifactOperation
    artifact_type: ArtifactType
    asked_about: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> PendingRequirementProposal:
        bounded_text(self.asked_about, "pending requirement anchor", MAX_LABEL_CHARACTERS)
        _sorted_unique(self.source_refs, "pending requirement source refs")
        return self


class CollaborationTurnRecord(FrozenModel):
    """Everything one turn proposed that a later delivery could make real.

    Carried, unchanged, into the next turn's workspace inputs so a delivered
    outcome can be reconciled against exactly what was proposed.
    """

    turn_ref: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    proposals: tuple[CollaborationProposal, ...] = ()
    pending_requirements: tuple[PendingRequirementProposal, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> CollaborationTurnRecord:
        if any(item.cognitive_turn_id != self.turn_ref for item in self.proposals):
            raise ValueError("a turn record holds only its own turn's proposals")
        ids = [item.content_proposal_id for item in self.proposals] + [item.pending_id for item in self.pending_requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal and pending-requirement ids are unique")
        return self


class CollaborationFailure(FrozenModel):
    goal_ref: str = Field(min_length=1)
    stage: Literal["provider", "compiler"]
    code: CollaborationFailureCode
    retryable: bool
    subject: str = ""

    @model_validator(mode="after")
    def valid(self) -> CollaborationFailure:
        if self.retryable != (self.code in RETRYABLE_COLLABORATION_FAILURES):
            raise ValueError("only transient provider failures are retryable")
        return self


class CollaborationSkipReason(StrEnum):
    GOAL_BLOCKED = "goal_blocked"
    ANSWER_DECLINED = "answer_declined"


class CollaborationGoalOutcome(FrozenModel):
    """Exactly one outcome per deliverable goal of the turn."""

    goal_ref: str = Field(min_length=1)
    status: Literal["proposed", "failed", "skipped"]
    proposal: CollaborationProposal | None = None
    failure: CollaborationFailure | None = None
    skip_reason: CollaborationSkipReason | None = None
    # The raw structured candidate the provider returned (for replay through
    # the recorded-candidate double; never re-requested from a model).
    candidate: Mapping[str, Any] | None = None
    provenance: CollaborationProviderProvenance | None = None

    @model_validator(mode="after")
    def valid(self) -> CollaborationGoalOutcome:
        expected = {
            "proposed": (True, False, False), "failed": (False, True, False), "skipped": (False, False, True),
        }[self.status]
        if (self.proposal is not None, self.failure is not None, self.skip_reason is not None) != expected:
            raise ValueError("a goal outcome carries exactly its status's record")
        if self.proposal is not None and self.proposal.goal_ref != self.goal_ref:
            raise ValueError("the proposal belongs to a different goal")
        if self.failure is not None and self.failure.goal_ref != self.goal_ref:
            raise ValueError("the failure belongs to a different goal")
        if self.proposal is not None and (self.candidate is None or self.proposal.candidate_digest != sha256_of(
            dict(self.candidate),
        )):
            raise ValueError("a proposal records the exact candidate it was compiled from")
        if self.skip_reason is not None and (self.candidate is not None or self.provenance is not None):
            raise ValueError("a skipped goal made no provider call")
        return self


# ---------------------------------------------------------------------------
# Workspace resolution (Section 3: distinguishable from content production)
# ---------------------------------------------------------------------------

class WorkspaceTargetStatus(StrEnum):
    NOT_REQUIRED = "not_required"            # create: no existing target
    RESOLVED_ARTIFACT = "resolved_artifact"
    RESOLVED_DIALOGUE = "resolved_dialogue"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class WorkspaceTargetBinding(FrozenModel):
    """The exact target of one deliverable goal (or the typed reason there is none)."""

    goal_ref: str = Field(min_length=1)
    frame_ref: str = Field(min_length=1)
    operation: ArtifactOperation
    artifact_type: ArtifactType
    status: WorkspaceTargetStatus
    artifact_ref: str | None = None
    base_revision_ref: str | None = None
    dialogue_refs: tuple[str, ...] = ()
    candidate_artifact_refs: tuple[str, ...] = ()
    inquiry_ref: str | None = None
    rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> WorkspaceTargetBinding:
        _sorted_unique(self.dialogue_refs, "target dialogue refs")
        _sorted_unique(self.candidate_artifact_refs, "candidate artifact refs")
        resolved_artifact = self.status == WorkspaceTargetStatus.RESOLVED_ARTIFACT
        if resolved_artifact != (self.artifact_ref is not None and self.base_revision_ref is not None):
            raise ValueError("an artifact target, and only it, names the artifact and its base revision")
        if (self.status == WorkspaceTargetStatus.RESOLVED_DIALOGUE) != bool(self.dialogue_refs):
            raise ValueError("a dialogue target, and only it, names its delivered dialogue items")
        if (self.status == WorkspaceTargetStatus.AMBIGUOUS) != (len(self.candidate_artifact_refs) >= 2):
            raise ValueError("an ambiguous target, and only it, keeps its competing candidates")
        return self


class ReconciliationRecord(FrozenModel):
    """How one prior proposal of this conversation was reconciled at this turn boundary."""

    proposal_ref: str = Field(min_length=1)
    goal_ref: str = Field(min_length=1)
    proposed_turn_ref: str = Field(min_length=1)
    state: ReconciliationState
    outcome_ref: str | None = None
    revision_ref: str | None = None
    inquiry_refs: tuple[str, ...] = ()
    rule_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid(self) -> ReconciliationRecord:
        _sorted_unique(self.inquiry_refs, "reconciled inquiry refs")
        delivered = self.state in {ReconciliationState.DELIVERED_AND_SATISFIED,
                                   ReconciliationState.DELIVERED_WAITING_FOR_ANSWER}
        if delivered and self.outcome_ref is None:
            raise ValueError("a delivered reconciliation cites its outcome")
        if self.state == ReconciliationState.SHADOW_ONLY_NOT_DELIVERED and self.outcome_ref is not None:
            raise ValueError("a shadow-only proposal was never delivered")
        return self


class InquiryAnswerBinding(FrozenModel):
    """An answer frame bound to the exact open inquiry it answers (typed linkage only)."""

    inquiry_ref: str = Field(min_length=1)
    frame_ref: str = Field(min_length=1)
    answer_kind: Literal["option", "free_text", "declined"]
    answer_option: str | None = None
    goal_ref: str | None = None


class WorkspaceResolution(FrozenModel):
    """The workspace stage of one turn: reconciliation, the canonical snapshot, and targets."""

    resolution_version: Literal["workspace-resolution-v0.1"] = WORKSPACE_RESOLUTION_VERSION
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    input_snapshot_ref: str = Field(min_length=1)
    snapshot: WorkspaceSnapshot
    reconciliations: tuple[ReconciliationRecord, ...] = ()
    targets: tuple[WorkspaceTargetBinding, ...] = ()
    answer_bindings: tuple[InquiryAnswerBinding, ...] = ()
    digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> WorkspaceResolution:
        if self.snapshot.conversation_ref != self.conversation_ref:
            raise ValueError("the workspace resolution belongs to a different conversation")
        goals = tuple(item.goal_ref for item in self.targets)
        if goals != tuple(sorted(set(goals))):
            raise ValueError("one target binding per goal, in canonical order")
        inquiries = tuple(item.inquiry_ref for item in self.answer_bindings)
        if inquiries != tuple(sorted(set(inquiries))):
            raise ValueError("one answer binding per inquiry, in canonical order")
        if self.digest != envelope_digest(self.model_dump(mode="json")):
            raise ValueError("the workspace resolution digest does not match its contents")
        return self

    @classmethod
    def sealed(cls, **fields: Any) -> WorkspaceResolution:
        draft = cls.model_construct(**{**fields, "digest": "0" * 64})
        return cls(**{**fields, "digest": envelope_digest(draft.model_dump(mode="json"))})

    def target(self, goal_ref: str) -> WorkspaceTargetBinding | None:
        return next((item for item in self.targets if item.goal_ref == goal_ref), None)


class WorkspaceTurnInputs(FrozenModel):
    """The trusted workspace facts one pipeline request carries.

    ``previous_snapshot`` is the canonical workspace at the end of the prior
    turn; ``predecessor_record`` is exactly what that turn proposed;
    ``delivered_outcomes`` are the validated deliveries observed since.  In
    observational shadow there are never any deliveries of shadow proposals.
    """

    schema_version: Literal[1] = 1
    authority_mode: WorkspaceAuthorityMode
    previous_snapshot: WorkspaceSnapshot | None = None
    predecessor_record: CollaborationTurnRecord | None = None
    delivered_outcomes: tuple[DeliveredResponseOutcome, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> WorkspaceTurnInputs:
        if self.previous_snapshot is not None and self.previous_snapshot.authority_mode != self.authority_mode:
            raise ValueError("the previous workspace was kept under a different authority mode")
        if self.authority_mode == WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW and self.delivered_outcomes:
            raise ValueError("an observational shadow never delivers a shadow proposal")
        ids = tuple(item.outcome_id for item in self.delivered_outcomes)
        if len(ids) != len(set(ids)):
            raise ValueError("delivered outcome ids must be unique")
        if self.predecessor_record is not None and self.previous_snapshot is not None and (
            self.predecessor_record.conversation_ref != self.previous_snapshot.conversation_ref
        ):
            raise ValueError("the predecessor record belongs to a different conversation")
        return self


class CollaborationActionBinding(FrozenModel):
    """Which Action index objects one deliverable goal's outcome was projected into."""

    goal_ref: str = Field(min_length=1)
    status: Literal["proposed", "failed", "skipped"]
    proposal_ref: str | None = None
    object_refs: tuple[str, ...] = ()
    content_refs: tuple[str, ...] = ()
    # A failed proposal is a typed blocker: the conversational request a
    # truthful decline must target.  Empty otherwise.
    blocker_refs: tuple[str, ...] = ()
    failure_code: CollaborationFailureCode | None = None

    @model_validator(mode="after")
    def valid(self) -> CollaborationActionBinding:
        for label, values in (("object refs", self.object_refs), ("content refs", self.content_refs),
                              ("blocker refs", self.blocker_refs)):
            _sorted_unique(values, label)
        if (self.status == "proposed") != (self.proposal_ref is not None):
            raise ValueError("a proposed binding, and only it, names its proposal")
        if (self.status == "failed") != (self.failure_code is not None):
            raise ValueError("a failed binding, and only it, names its failure")
        if self.blocker_refs and self.status != "failed":
            raise ValueError("only a failed proposal is a blocker")
        return self


class CollaborationActionHandoff(FrozenModel):
    """The typed collaboration facts Action Selection consumes for one turn."""

    projection_version: Literal["collaboration-action-projection-v0.1"] = COLLABORATION_PROJECTION_VERSION
    cognitive_turn_id: str = Field(min_length=1)
    bindings: tuple[CollaborationActionBinding, ...] = ()

    @model_validator(mode="after")
    def valid(self) -> CollaborationActionHandoff:
        goals = tuple(item.goal_ref for item in self.bindings)
        if goals != tuple(sorted(set(goals))):
            raise ValueError("one binding per goal, in canonical order")
        return self


class CollaborationVersions(FrozenModel):
    contract: str = COLLABORATION_CONTRACT_VERSION
    compiler: str = COLLABORATION_COMPILER_VERSION
    protocol: str = COLLABORATION_PROTOCOL_VERSION
    inquiry_policy: str = INQUIRY_POLICY_VERSION
    workspace_resolution: str = WORKSPACE_RESOLUTION_VERSION
    projection: str = COLLABORATION_PROJECTION_VERSION
    content_producer: str = COLLABORATION_CONTENT_PRODUCER


__all__ = [name for name in globals() if not name.startswith("_") and name not in {
    "annotations", "Any", "Literal", "Mapping", "StrEnum", "Field", "model_validator", "FrozenModel",
}]
