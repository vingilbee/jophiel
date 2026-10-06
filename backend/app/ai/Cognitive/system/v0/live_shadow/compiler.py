"""Deterministic compilation of a capture into one canonical pipeline request.

The compiler consumes only the immutable capture and the previous terminal
shadow record of the same conversation.  It projects captured trusted facts and
accepted canonical defaults; it never infers structured inputs from fixtures,
design cases, prose, the live response, or later database state.  Anything a
required input would need that the capture does not hold becomes a typed skip.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..cognitive_pipeline_v0.models import (
    PIPELINE_VERSIONS_BY_SCHEMA,
    CognitivePipelineRequest,
    CognitivePipelineRuntimeSnapshot,
    CognitivePipelineTrace,
    PipelineDialogueContext,
)
from ..contracts.dialogue_memory import DIALOGUE_MEMORY_PROJECTION_VERSION, turn_context_digest
from ..dialogue_memory_v0.errors import DialogueMemoryError
from ..dialogue_memory_v0.projector import (
    action_projection,
    interpretation_dialogue_context,
    perception_working_context,
    verify_turn_context,
)
from ..contracts.goals import GoalSnapshot, GoalTurnRollover, GoalVersions, stale_turn_scoped_goals
from ..contracts.collaboration import CollaborationTurnRecord, WorkspaceTurnInputs
from ..contracts.requirement_resolution import RequirementResolutionInputs
from ..contracts.workspace import WorkspaceAuthorityMode, WorkspaceSnapshot
from ..contracts.safety import ApplicationSafetyDecision
from ..dynamic_state.initialization import DynamicStateSnapshotFactory
from ..dynamic_state.models import StateSnapshot
from ..dynamic_state.policy import POLICY_VERSION as STATE_POLICY_VERSION
from ..interpretation_v0.ontology import PREDICATES, PREDICATE_REGISTRY_VERSION
from ..perception_v0.models import AttachmentFact, PerceptionMessage
from .models import (
    CAPABILITY_MAP_VERSION,
    CAPTURE_SCHEMA_VERSION_V03,
    COMPILER_VERSION,
    CognitiveShadowCapture,
    SegmentResetReason,
    SkipReason,
    TerminalStatus,
    segment_id_for,
)

SUPPORTED_LANGUAGE_TAG = "en"


class CompileError(ValueError):
    """A deterministic, non-retryable compilation failure with a stable code."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class PredecessorRecord:
    """The terminal record of the previous captured turn of the conversation.

    ``pipeline_trace`` is present exactly when ``status`` is completed, and has
    already been validated through the canonical trace contract by the loader.
    """

    terminal_id: str
    turn_id: str
    user_ref: str
    conversation_ref: str
    focus_message_id: str
    segment_id: str
    status: TerminalStatus
    pipeline_trace: CognitivePipelineTrace | None


@dataclass(frozen=True)
class ShadowLineage:
    segment_id: str
    predecessor_trace_id: str | None
    reset_reason: SegmentResetReason | None
    missing_predecessor_refs: tuple[str, ...]
    previous_state: StateSnapshot | None
    previous_goal_snapshot: GoalSnapshot | None
    # Runner x.y.3: the predecessor's canonical workspace and exactly what it
    # proposed.  Present only when that stage recorded something, so a
    # conversation without collaborative work compiles exactly as before.
    previous_workspace: WorkspaceSnapshot | None = None
    previous_collaboration_record: CollaborationTurnRecord | None = None

    @property
    def continuous(self) -> bool:
        return self.reset_reason is None


@dataclass(frozen=True)
class CompileSkip:
    reason: SkipReason


def pipeline_schema_for(capture: CognitiveShadowCapture) -> int:
    """The canonical pipeline schema a capture compiles to (v0.3 captures carry dialogue memory)."""

    return 3 if capture.schema_version == CAPTURE_SCHEMA_VERSION_V03 else 2


def runtime_snapshot_id_for(turn_id: str) -> str:
    return f"cognitive-shadow-runtime:{COMPILER_VERSION}:{turn_id}"


def capability_snapshot_id_for(turn_id: str, mapping_version: str) -> str:
    return f"cognitive-shadow-capability:{COMPILER_VERSION}:{mapping_version}:{turn_id}"


def bootstrap_turn_id_for(turn_id: str) -> str:
    """The distinct cognitive-turn id that owns a segment's initial state."""

    return f"cognitive-shadow-bootstrap:{COMPILER_VERSION}:{turn_id}"


def trace_ref(terminal_id: str) -> str:
    return f"cognitive-shadow-trace:{terminal_id}"


def _message_ref(message_id: str) -> str:
    return f"chat-message:{message_id}"


def turn_rollover_id_for(turn_id: str, closed_turn_id: str) -> str:
    return f"cognitive-shadow-turn-rollover:{COMPILER_VERSION}:{closed_turn_id}:{turn_id}"


def goal_turn_rollover_for(
    capture: CognitiveShadowCapture, previous_goal: GoalSnapshot
) -> GoalTurnRollover | None:
    """The trusted rollover proof for a continuous segment turn.

    Built only from captured server-owned facts: the predecessor goal snapshot
    (which ``resolve_lineage`` accepted only as the completed trace of the
    immediately preceding user message) and this turn's trusted response
    obligation.  A new trusted response obligation for a later user message of
    the same conversation is the proof that the prior response episode ended.
    Nothing is inferred from message text.  ``None`` when there is nothing to
    close, so such requests serialize exactly as before.
    """

    if not stale_turn_scoped_goals(previous_goal, capture.turn_id):
        return None
    return GoalTurnRollover(
        rollover_id=turn_rollover_id_for(capture.turn_id, previous_goal.cognitive_turn_id),
        proof_kind="successor_response_turn",
        producer=f"{COMPILER_VERSION}:successor-response-obligation",
        conversation_ref=capture.conversation_ref,
        closed_turn_ref=previous_goal.cognitive_turn_id,
        closed_goal_snapshot_ref=previous_goal.goal_snapshot_id,
        closing_turn_ref=capture.turn_id,
        closing_response_obligation_ref=capture.response_obligation.evidence_ref,
    )


def resolve_lineage(
    capture: CognitiveShadowCapture, predecessor: PredecessorRecord | None
) -> ShadowLineage:
    """Decide segment continuity from captured facts and the predecessor only.

    A turn continues its predecessor's segment only when the predecessor is the
    completed shadow trace of the immediately preceding user message and was
    produced under the same lineage versions.  Anything else is a visible
    segment reset; the chain is never presented as continuous across a gap.
    """

    def reset(reason: SegmentResetReason, missing: tuple[str, ...]) -> ShadowLineage:
        return ShadowLineage(
            segment_id=segment_id_for(capture.turn_id), predecessor_trace_id=None,
            reset_reason=reason, missing_predecessor_refs=missing,
            previous_state=None, previous_goal_snapshot=None,
        )

    previous_message = capture.previous_user_message_id
    if predecessor is None:
        if previous_message is None:
            return reset(SegmentResetReason.INITIAL_CONVERSATION, ())
        # Earlier user turns were never captured (sampling, a disabled period,
        # or a capture failure): their state was never observed.
        return reset(SegmentResetReason.PREDECESSOR_GAP, (_message_ref(previous_message),))

    if predecessor.user_ref != capture.user_ref or predecessor.conversation_ref != capture.conversation_ref:
        raise CompileError(
            "predecessor_ownership_mismatch",
            "The shadow predecessor belongs to a different user or conversation.",
        )
    if predecessor.turn_id == capture.turn_id:
        raise CompileError("predecessor_is_same_turn", "A turn cannot be its own shadow predecessor.")

    if predecessor.status != TerminalStatus.COMPLETED or predecessor.pipeline_trace is None:
        return reset(SegmentResetReason.PREDECESSOR_GAP, (trace_ref(predecessor.terminal_id),))
    if previous_message is None or predecessor.focus_message_id != previous_message:
        missing = (_message_ref(previous_message),) if previous_message is not None else (
            trace_ref(predecessor.terminal_id),
        )
        return reset(SegmentResetReason.PREDECESSOR_GAP, missing)

    trace = predecessor.pipeline_trace
    state = trace.cognitive_turn.dynamic_state_update.snapshot
    goal = trace.goal_snapshot
    turn_input = trace.cognitive_turn.input_snapshot
    if (
        trace.cognitive_turn_id != predecessor.turn_id
        or trace.conversation_ref != capture.conversation_ref
        or trace.focus_message_id != predecessor.focus_message_id
        or state.conversation_ref != capture.conversation_ref
        or state.cognitive_turn_id != predecessor.turn_id
        or goal.conversation_ref != capture.conversation_ref
        or goal.cognitive_turn_id != predecessor.turn_id
    ):
        raise CompileError(
            "predecessor_lineage_mismatch",
            "The shadow predecessor's state or goal lineage differs from its record.",
        )

    personality = capture.personality
    core = capture.core_memory
    compatible = (
        (trace.versions.pipeline_schema, trace.versions.pipeline_runner)
        == PIPELINE_VERSIONS_BY_SCHEMA[pipeline_schema_for(capture)]
        and state.policy_version == STATE_POLICY_VERSION
        and goal.versions == GoalVersions()
        and personality is not None
        and core is not None
        and state.personality_mapper_version == personality.state_personality.mapper_version
        and turn_input.personality == personality.state_personality
        and turn_input.core_memory_ref == core.core_memory_ref
    )
    if not compatible:
        return reset(SegmentResetReason.LINEAGE_VERSION_CHANGE, (trace_ref(predecessor.terminal_id),))

    collaboration = getattr(trace, "collaboration", None)
    workspace = record = None
    if collaboration is not None:
        snapshot, turn_record = collaboration.workspace.snapshot, collaboration.turn_record
        if snapshot.authority_mode != WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW:
            raise CompileError(
                "predecessor_workspace_authority",
                "The shadow predecessor's workspace was not kept in observational mode.",
            )
        if (snapshot.heads or snapshot.inquiries or turn_record.proposals or turn_record.pending_requirements
                or collaboration.workspace.reconciliations):
            workspace, record = snapshot, turn_record
    return ShadowLineage(
        segment_id=predecessor.segment_id, predecessor_trace_id=predecessor.terminal_id,
        reset_reason=None, missing_predecessor_refs=(),
        previous_state=state, previous_goal_snapshot=goal,
        previous_workspace=workspace, previous_collaboration_record=record,
    )


def compile_request(
    capture: CognitiveShadowCapture, lineage: ShadowLineage
) -> CognitivePipelineRequest | CompileSkip:
    """Return one validated canonical request, or the typed reason it cannot exist."""

    if capture.language.tag != SUPPORTED_LANGUAGE_TAG:
        return CompileSkip(SkipReason.UNSUPPORTED_LANGUAGE)
    if capture.capabilities.mapping_version != CAPABILITY_MAP_VERSION:
        return CompileSkip(SkipReason.MISSING_REQUIRED_RUNTIME_FACT)
    if any(not memory.authorized for memory in capture.memories) or (
        capture.relationship is not None and not capture.relationship.authorized
    ):
        return CompileSkip(SkipReason.UNAUTHORIZED_INPUT)
    personality = capture.personality
    if personality is None or capture.core_memory is None:
        return CompileSkip(SkipReason.MISSING_PERSONALITY_PROJECTION)

    turn_id = capture.turn_id
    snapshot_id = runtime_snapshot_id_for(turn_id)
    captured_safety = capture.safety_decision
    safety = ApplicationSafetyDecision(
        schema_version=captured_safety.schema_version,
        decision_id=f"{snapshot_id}:safety-decision",
        policy_version=captured_safety.policy_version,
        disposition=captured_safety.disposition,
        provenance=captured_safety.provenance,
        producer_ref=captured_safety.producer_ref,
        runtime_snapshot_ref=snapshot_id,
    )
    attachments = tuple(
        AttachmentFact(
            # Namespaced by store so image and asset ids can never collide.
            attachment_id=f"{item.store}:{item.attachment_id}",
            filename=None,
            trusted_runtime_provenance=f"{item.provenance}:{item.store}",
            evidence_reference=f"{_message_ref(capture.focus_message_id)}:{item.store}:{item.attachment_id}",
        )
        for item in capture.attachments
    )
    runtime = CognitivePipelineRuntimeSnapshot(
        snapshot_id=snapshot_id,
        predicate_registry_version=PREDICATE_REGISTRY_VERSION,
        known_predicates=PREDICATES,
        known_entities=(),
        attachments_available=capture.attachments_available,
        attachments=attachments,
        available_capabilities=capture.capabilities.capability_keys,
        capability_snapshot_id=capability_snapshot_id_for(turn_id, capture.capabilities.mapping_version),
        task_contract=None,
        authorization=capture.authorization,
        consent=capture.consent,
        safety_decision=safety,
        external_export_control=capture.external_export_control,
        policy_records=(),
        last_tool_event=None,
        conversation_mode=None,
        response_obligation=capture.response_obligation.obligation,
        response_obligation_ref=capture.response_obligation.evidence_ref,
        conversation_status=capture.conversation_status,
    )

    dialogue = _dialogue_inputs(capture)

    if lineage.continuous:
        if lineage.previous_state is None:
            raise CompileError("lineage_state_missing", "A continuous segment turn has no predecessor state.")
        previous_state = lineage.previous_state
        previous_goal = lineage.previous_goal_snapshot
        if previous_goal is None:
            raise CompileError("lineage_goal_missing", "A continuous segment turn has no predecessor goal snapshot.")
        rollover = goal_turn_rollover_for(capture, previous_goal)
    else:
        if lineage.segment_id != segment_id_for(turn_id):
            raise CompileError("lineage_segment_mismatch", "A reset segment must be named by its first turn.")
        previous_state = DynamicStateSnapshotFactory().create_initial(
            conversation_ref=capture.conversation_ref,
            cognitive_turn_id=bootstrap_turn_id_for(turn_id),
            personality=personality.state_personality,
        )
        previous_goal = None
        rollover = None

    try:
        return CognitivePipelineRequest(
            cognitive_turn_id=turn_id,
            conversation_ref=capture.conversation_ref,
            messages=tuple(
                PerceptionMessage(message_id=message.message_id, role=message.role, content=message.content)
                for message in capture.messages
            ),
            focus_message_id=capture.focus_message_id,
            language_tag=capture.language.tag,
            context_truncated=capture.context_truncated,
            runtime=runtime,
            memories=capture.memories,
            relationship=capture.relationship,
            interpretation_biases=personality.interpretation_biases,
            previous_goal_snapshot=previous_goal,
            goal_turn_rollover=rollover,
            previous_state=previous_state,
            state_personality=personality.state_personality,
            core_memory_ref=capture.core_memory.core_memory_ref,
            memory_retrieval_policy_version=capture.memory_retrieval_policy_version,
            action_personality=personality.action_personality,
            requirement_inputs=requirement_inputs_for(capture),
            workspace_inputs=workspace_inputs_for(lineage),
            **dialogue,
        )
    except ValueError as exc:
        raise CompileError(
            "compiled_request_invalid", "The compiled request failed the canonical pipeline contract."
        ) from exc


def workspace_inputs_for(lineage: ShadowLineage) -> WorkspaceTurnInputs | None:
    """Observational workspace inputs carried from the predecessor trace.

    Live shadow never delivers a shadow proposal: there are never delivered
    outcomes, so every carried proposal reconciles as
    ``shadow_only_not_delivered`` and no head advances.  ``None`` (an empty
    observational workspace) when the segment reset or nothing was recorded.
    """

    if not lineage.continuous or lineage.previous_collaboration_record is None:
        return None
    return WorkspaceTurnInputs(
        authority_mode=WorkspaceAuthorityMode.OBSERVATIONAL_SHADOW, previous_snapshot=lineage.previous_workspace,
        predecessor_record=lineage.previous_collaboration_record,
    )


def requirement_inputs_for(capture: CognitiveShadowCapture) -> RequirementResolutionInputs:
    """Captured turn-boundary facts for requirement resolution.

    The turn time is the server clock at capture and the user scope is the
    captured owner.  No trusted user time zone is captured, so none is
    invented: the runtime date is then reported explicitly in UTC.
    """

    return RequirementResolutionInputs(turn_time=capture.captured_at, time_zone=None, user_ref=capture.user_ref)


def _dialogue_inputs(capture: CognitiveShadowCapture) -> dict:
    """Schema-3 request fields recomputed from the captured dialogue turn context.

    Every projection is rebuilt from the immutable capture alone (never the
    mutable head or a cache).  An unavailable or inconsistent context is a
    hard, typed failure for this observation.
    """

    if capture.schema_version != CAPTURE_SCHEMA_VERSION_V03:
        return {"perception_working_context": None}
    dialogue = capture.dialogue
    if dialogue is None or dialogue.turn_context is None:
        code = dialogue.failure.code.value if dialogue is not None and dialogue.failure is not None else "unavailable"
        raise CompileError(f"dialogue_memory.{code}", "Dialogue working memory was unavailable for this turn.")
    context = dialogue.turn_context
    try:
        verify_turn_context(context, focus_content=capture.messages[-1].content)
        working = perception_working_context(context)
        interpretation = interpretation_dialogue_context(context)
        action = action_projection(context, cognitive_turn_id=capture.turn_id,
                                   window_message_ids=tuple(message.message_id for message in capture.messages))
    except DialogueMemoryError as exc:
        raise CompileError(f"dialogue_memory.{exc.code.value}", exc.failure.safe_message) from exc
    snapshot = context.prior_context_projection
    return {
        "schema_version": 3,
        "perception_working_context": working,
        "action_grounded_objects": action.grounded_objects,
        "action_target_descriptors": action.target_descriptors,
        "dialogue_context": PipelineDialogueContext(
            turn_context_ref=context.context_id, turn_context_digest=turn_context_digest(context),
            snapshot_ref=snapshot.snapshot_id, snapshot_digest=snapshot.semantic_digest,
            snapshot_revision=snapshot.revision, focus_message_ref=context.focus_message_ref,
            focus_conversation_sequence=context.focus_conversation_sequence,
            projection_version=DIALOGUE_MEMORY_PROJECTION_VERSION, interpretation_context=interpretation,
            action_object_refs=tuple(sorted(item.object_id for item in action.grounded_objects)),
            action_descriptor_refs=tuple(sorted(item.ref for item in action.target_descriptors)),
            outside_window_refs=action.outside_window_refs,
        ),
    }


__all__ = [
    "CompileError", "CompileSkip", "PredecessorRecord", "pipeline_schema_for", "SUPPORTED_LANGUAGE_TAG", "ShadowLineage",
    "bootstrap_turn_id_for", "capability_snapshot_id_for", "compile_request", "goal_turn_rollover_for",
    "requirement_inputs_for", "resolve_lineage", "runtime_snapshot_id_for", "trace_ref", "turn_rollover_id_for",
]
