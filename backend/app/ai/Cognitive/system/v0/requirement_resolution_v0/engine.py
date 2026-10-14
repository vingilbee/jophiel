"""Pure, deterministic requirement resolution (``requirement-resolution-engine-v0.1``).

For every active, current-turn user or shared request goal, the engine
recovers the exact semantic frame the goal was formed from, declares that
frame's prerequisites from the closed manifest, and resolves each one from
typed snapshots only:

- arguments and referents from the frame, its typed unknowns, and -- for a
  pronoun or deictic referent -- the authorized Dialogue Working Memory cue
  whose span lies inside the frame's own evidence;
- trusted content through ``producers_v0`` (the one producer protocol);
- capability through the registered capability map;
- authorization and consent from captured trusted runtime records;
- safety from the closed application safety decision.

It makes no model call, parses no text, reads no clock, and has no I/O.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from ..contracts.actions import ActionRuntimeContext, missing_safety_decision_ref
from ..contracts.goals import GoalOwner, GoalRecord, GoalSnapshot, GoalStatus
from ..contracts.interpretation import (
    FrozenModel, HypothesisDisposition, InterpretationEnvelope, InterpretationEvidenceKind, SemanticFrame,
)
from ..contracts.realization import ArgumentValueType
from ..contracts.requirement_resolution import (
    CONTENT_STATUS_BY_PRODUCER, REQUIREMENT_RESOLUTION_ENGINE_VERSION, ContentProducerStatus, GoalReadiness,
    RequirementContentProvenance, RequirementKind as K, RequirementReason as R, RequirementResolution,
    RequirementResolutionInputs, RequirementResolutionSnapshot, RequirementResolutionStatus as S,
)
from ..contracts.safety import SafetyRestrictionReason, safety_restriction
from ..goal_v0.formation_policy import FORMATION_BY_DEFINITION, frame_eligible, frame_target_refs
from ..interpretation_v0.models import InterpretationDialogueContext
from ..producers_v0 import (
    QUERY_MANIFEST_VERSION, ApplicationIdentityProducer, KnowledgeGapRecord, ProducerReason, ProducerRecord,
    ProducerRegistry, ProducerResult, QueryKind, SemanticQuery, TrustedProducer, captured_runtime_producer,
    knowledge_gap_for,
)
from ..contracts.collaboration import WorkspaceResolution
from ..contracts.requirement_resolution import RequirementCriticality
from .manifest import (
    INQUIRY_ANSWER_GOAL, MANIFEST_VERSION, REFERENT_CUE_KINDS, REQUEST_GOAL_DEFINITIONS, TIER_CONSENT,
    TIER_EXECUTION, TIER_SAFETY,
    UTC_ZONE, PrerequisiteDeclaration, capability_for_predicate, capability_ref, compile_content_query,
    declarations_for,
)

ENGINE_VERSION = REQUIREMENT_RESOLUTION_ENGINE_VERSION
# Runtime values that grant an authorization or consent record (the same
# closed set the Action hard gate accepts).
GRANTED = frozenset({"authorized", "granted", "confirmed", "allowed", "not_required"})
# The typed identity used when no user was captured: user-owned records can
# then never be admitted (fail closed).
UNIDENTIFIED_USER = "requirement-resolution:unidentified-user"
RUNTIME_DATE_PREDICATE = "runtime.current_date"

_PRODUCER_REASONS: Mapping[ProducerReason, R] = {
    ProducerReason.NO_MATCHING_RECORD: R.PRODUCER_NO_MATCHING_RECORD,
    ProducerReason.STALE_OBSERVATION: R.PRODUCER_STALE_OBSERVATION,
    ProducerReason.QUERY_UNSUPPORTED: R.PRODUCER_QUERY_UNSUPPORTED,
    ProducerReason.REQUIRES_REVIEW: R.PRODUCER_REQUIRES_REVIEW,
    ProducerReason.PRODUCER_NOT_INSTALLED: R.PRODUCER_NOT_INSTALLED,
    ProducerReason.MISSING_QUERY_ARGUMENT: R.PRODUCER_MISSING_QUERY_ARGUMENT,
    ProducerReason.NOT_AUTHORIZED: R.PRODUCER_NOT_AUTHORIZED,
    ProducerReason.CONFLICTING_VALUES: R.PRODUCER_CONFLICTING_VALUES,
}
if set(_PRODUCER_REASONS) != set(ProducerReason):
    raise RuntimeError("every producer reason maps to exactly one requirement reason")

_SAFETY_REASONS: Mapping[SafetyRestrictionReason, tuple[S, R]] = {
    SafetyRestrictionReason.ALLOWED: (S.SATISFIED, R.SAFETY_ALLOWED),
    SafetyRestrictionReason.BLOCKS_COMMITMENTS: (S.PROHIBITED, R.SAFETY_BLOCKS_COMMITMENTS),
    SafetyRestrictionReason.BLOCKS_RESPONSE: (S.PROHIBITED, R.SAFETY_BLOCKS_RESPONSE),
    SafetyRestrictionReason.DECISION_MISSING: (S.UNAVAILABLE, R.SAFETY_DECISION_MISSING),
    SafetyRestrictionReason.DECISION_UNKNOWN: (S.UNAVAILABLE, R.SAFETY_DECISION_UNKNOWN),
    SafetyRestrictionReason.DECISION_UNAVAILABLE: (S.UNAVAILABLE, R.SAFETY_DECISION_UNAVAILABLE),
    SafetyRestrictionReason.POLICY_VERSION_UNSUPPORTED: (S.UNAVAILABLE, R.SAFETY_POLICY_VERSION_UNSUPPORTED),
}
if set(_SAFETY_REASONS) != set(SafetyRestrictionReason):
    raise RuntimeError("every safety restriction reason maps to exactly one requirement outcome")

_RESOLVERS: Mapping[K, str] = {
    K.REQUIRED_ARGUMENT: "requirement-resolver.required-argument",
    K.DIALOGUE_REFERENT: "requirement-resolver.dialogue-referent",
    K.TRUSTED_CONTENT: "requirement-resolver.trusted-content",
    K.CAPABILITY: "requirement-resolver.capability",
    K.AUTHORIZATION: "requirement-resolver.authorization-consent",
    K.CONSENT: "requirement-resolver.authorization-consent",
    K.SAFETY: "requirement-resolver.safety",
    K.TOOL_OUTCOME: "requirement-resolver.tool-outcome",
    K.ARTIFACT_TARGET: "requirement-resolver.workspace-target",
    K.REQUESTED_CHANGE: "requirement-resolver.requested-change",
    K.REQUESTED_OUTCOME: "requirement-resolver.requested-outcome",
    K.INQUIRY_LINK: "requirement-resolver.inquiry-link",
    K.DOMAIN_CONTEXT: "requirement-resolver.domain-context",
}


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def _sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


class RequirementResolutionError(ValueError):
    """The resolution inputs are internally inconsistent; nothing is resolved."""


class RequirementResolutionRequest(FrozenModel):
    """Typed snapshots only: never raw text, never a model, never mutable state."""

    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    interpretation: InterpretationEnvelope
    goal_snapshot: GoalSnapshot
    runtime: ActionRuntimeContext
    dialogue_context: InterpretationDialogueContext | None = None
    # Grounded refs Dialogue Working Memory projected into Action this turn.
    dialogue_action_refs: tuple[str, ...] = ()
    inputs: RequirementResolutionInputs | None = None
    # Engine v0.2: the conversational workspace stage of this turn (exact
    # targets of deliverable goals and bound inquiry answers).
    workspace: WorkspaceResolution | None = None

    @model_validator(mode="after")
    def valid(self) -> RequirementResolutionRequest:
        interpretation, goals = self.interpretation, self.goal_snapshot
        if self.workspace is not None and (
            self.workspace.cognitive_turn_id != self.cognitive_turn_id
            or self.workspace.conversation_ref != self.conversation_ref
        ):
            raise ValueError("the workspace resolution belongs to a different turn or conversation")
        if not (self.cognitive_turn_id == interpretation.cognitive_turn_id == goals.cognitive_turn_id):
            raise ValueError("requirement inputs belong to different cognitive turns")
        if not (self.conversation_ref == interpretation.conversation_ref == goals.conversation_ref):
            raise ValueError("requirement inputs belong to different conversations")
        if goals.interpretation_trace_ref != interpretation.interpretation_trace_id:
            raise ValueError("the goal snapshot cites a different interpretation")
        if not (self.runtime.runtime_snapshot_ref == goals.runtime_snapshot_ref == interpretation.runtime_snapshot_id):
            raise ValueError("runtime lineage differs across requirement inputs")
        if self.dialogue_context is not None and (
            self.dialogue_context.conversation_ref != self.conversation_ref
            or self.dialogue_context.focus_message_ref != interpretation.focus_message_id
        ):
            raise ValueError("the dialogue context belongs to a different conversation or focus")
        return self

    def runtime_refs(self) -> tuple[str, ...]:
        runtime = self.runtime
        refs = [runtime.runtime_snapshot_ref, runtime.capability_snapshot_ref, *runtime.policy_records,
                runtime.safety_basis.decision_ref]
        if runtime.safety_decision is not None:
            refs.append(runtime.safety_decision.producer_ref)
        return _sorted(refs)

    def dialogue_refs(self) -> tuple[str, ...]:
        return dialogue_references(self.dialogue_context, self.dialogue_action_refs)

    def workspace_refs(self) -> tuple[str, ...]:
        return workspace_references(self.workspace)


def workspace_references(workspace: WorkspaceResolution | None) -> tuple[str, ...]:
    """Every workspace identity a resolution of this turn may cite (never content)."""

    if workspace is None:
        return ()
    snapshot = workspace.snapshot
    refs = {workspace.input_snapshot_ref, snapshot.snapshot_id}
    refs.update(item.artifact_id for item in snapshot.heads)
    refs.update(item.revision_id for item in snapshot.revisions)
    refs.update(item.inquiry_id for item in snapshot.inquiries)
    for target in workspace.targets:
        refs.update(ref for ref in (target.artifact_ref, target.base_revision_ref, target.inquiry_ref) if ref)
        refs.update(target.dialogue_refs)
        refs.update(target.candidate_artifact_refs)
    return _sorted(refs)


def dialogue_references(context: InterpretationDialogueContext | None, action_refs: Iterable[str]) -> tuple[str, ...]:
    """Every authorized dialogue reference of one turn a resolution may cite."""

    if context is None:
        return _sorted(action_refs)
    return _sorted((*action_refs, *(item.item_ref for item in context.items),
                    *(item.cue_ref for item in context.referents)))


@dataclass(frozen=True)
class ContentRecord:
    """The producer objects behind one trusted-content resolution (projection input)."""

    resolution_ref: str
    query: SemanticQuery
    result: ProducerResult
    gap: KnowledgeGapRecord | None


@dataclass(frozen=True)
class RequirementResolutionOutcome:
    snapshot: RequirementResolutionSnapshot
    content_records: tuple[ContentRecord, ...]


ProducerFactory = Callable[[RequirementResolutionInputs | None, datetime | None], tuple[TrustedProducer, ...]]


def runtime_date_record(turn_time: datetime, time_zone: str | None) -> ProducerRecord:
    """The one captured runtime fact V0 installs: the date at the turn boundary.

    Observed exactly at the captured turn time, in the captured user zone or,
    when none was captured, explicitly in UTC.  Never a model-generated date.
    """

    zone = time_zone or UTC_ZONE
    local = turn_time.astimezone(ZoneInfo(zone)).date().isoformat()
    stamp = turn_time.isoformat()
    return ProducerRecord(
        record_id=f"runtime-current-date:{zone}:{stamp}", query_kind=QueryKind.CURRENT_DATE.value, arguments={},
        predicate=RUNTIME_DATE_PREDICATE, value=local, value_type=ArgumentValueType.DATE, value_anchor=local,
        owner="runtime", authorized=True, admission_state="captured", revision=f"captured:{stamp}",
        observed_at=stamp, max_age_seconds=0,
    )


def default_producers(inputs: RequirementResolutionInputs | None, turn_time: datetime | None) -> tuple[TrustedProducer, ...]:
    """Reviewed application identity, plus captured runtime facts when a turn time was captured."""

    producers: list[TrustedProducer] = [ApplicationIdentityProducer()]
    if turn_time is not None:
        producers.append(captured_runtime_producer(
            (runtime_date_record(turn_time, inputs.time_zone if inputs else None),), turn_time=turn_time,
        ))
    return tuple(producers)


class RequirementResolutionEngine:
    """Resolves every declared prerequisite of the current request goals."""

    version = ENGINE_VERSION

    def __init__(self, *, producer_factory: ProducerFactory = default_producers,
                 allow_review_classes: bool = False, preserve_accepted_uncertainty: bool = False) -> None:
        # ``allow_review_classes`` exists only for offline evaluation of
        # producer classes that still require privacy and authorization review;
        # the live-shadow pipeline never sets it.
        self.producer_factory = producer_factory
        self.allow_review_classes = allow_review_classes
        # Enabled by the versioned two-call adapter. Historical captures keep
        # their cue fallback; a new accepted null is not overturned by that
        # fallback after the model has already considered the dialogue.
        self.preserve_accepted_uncertainty = preserve_accepted_uncertainty

    # -- entry point -----------------------------------------------------------
    def resolve(self, request: RequirementResolutionRequest) -> RequirementResolutionOutcome:
        interpretation = request.interpretation
        inputs = request.inputs
        turn_time = datetime.fromisoformat(inputs.turn_time) if inputs is not None and inputs.turn_time else None
        registry = ProducerRegistry(self.producer_factory(inputs, turn_time),
                                    allow_review_classes=self.allow_review_classes)
        goals = sorted(
            (record for record in request.goal_snapshot.records if self._is_request_goal(record, request)),
            key=lambda record: record.goal_id,
        )
        resolutions: list[RequirementResolution] = []
        records: list[ContentRecord] = []
        provenance: list[RequirementContentProvenance] = []
        for goal in goals:
            frames = goal_frames(goal, interpretation, task_ref=request.runtime.task_ref)
            if not frames:
                raise RequirementResolutionError("a request goal could not be traced to its semantic frame")
            for frame in frames:
                for resolution, record in self._resolve_frame(request, registry, goal, frame):
                    resolutions.append(resolution)
                    if record is not None:
                        records.append(record)
                        provenance.append(_provenance(record))
        resolutions = _apply_blocking(resolutions)
        resolutions.sort(key=lambda item: item.sort_key)
        readiness = tuple(_readiness(goal.goal_id, resolutions) for goal in goals)
        snapshot = RequirementResolutionSnapshot.sealed(
            snapshot_id=f"requirement-snapshot-{_digest(request.cognitive_turn_id, request.goal_snapshot.goal_snapshot_id)}",
            engine_version=ENGINE_VERSION, manifest_version=MANIFEST_VERSION,
            producer_manifest_version=QUERY_MANIFEST_VERSION, cognitive_turn_id=request.cognitive_turn_id,
            conversation_ref=request.conversation_ref, interpretation_trace_ref=interpretation.interpretation_trace_id,
            goal_snapshot_ref=request.goal_snapshot.goal_snapshot_id,
            runtime_snapshot_ref=request.runtime.runtime_snapshot_ref,
            goal_refs=tuple(goal.goal_id for goal in goals), resolutions=tuple(resolutions),
            content_queries=tuple(sorted(provenance, key=lambda item: item.resolution_ref)),
            goal_readiness=readiness,
        )
        return RequirementResolutionOutcome(snapshot, tuple(sorted(records, key=lambda item: item.resolution_ref)))

    @staticmethod
    def _is_request_goal(record: GoalRecord, request: RequirementResolutionRequest) -> bool:
        return (
            record.status == GoalStatus.ACTIVE
            and record.created_turn_ref == request.cognitive_turn_id
            and record.owner in {GoalOwner.USER, GoalOwner.SHARED}
            and record.definition_ref.definition_key in REQUEST_GOAL_DEFINITIONS
        )

    # -- one frame -----------------------------------------------------------------
    def _resolve_frame(self, request, registry, goal, frame):
        key = goal.definition_ref.definition_key
        target = request.workspace.target(goal.goal_id) if request.workspace is not None else None
        if key == INQUIRY_ANSWER_GOAL:
            operation = target.operation.value if target is not None else None
            artifact_type = target.artifact_type.value if target is not None else None
        else:
            operation, artifact_type = frame.arguments.get("operation"), frame.arguments.get("artifact_type")
        declarations = list(declarations_for(key, frame.predicate, operation=operation, artifact_type=artifact_type))
        out: list[tuple[RequirementResolution, ContentRecord | None]] = []
        missing_referent = False
        referent_items: tuple[str, ...] = ()
        referent_anchor: str | None = None
        index = 0
        while index < len(declarations):
            declaration = declarations[index]
            index += 1
            if declaration.kind in {K.REQUIRED_ARGUMENT, K.DIALOGUE_REFERENT}:
                resolution, items, anchor = self._argument(request, goal, frame, declaration)
                if declaration.kind == K.DIALOGUE_REFERENT:
                    missing_referent |= resolution.status != S.SATISFIED
                    referent_items, referent_anchor = items or referent_items, anchor or referent_anchor
                out.append((resolution, None))
            elif declaration.kind == K.CAPABILITY:
                resolution, entry = self._capability(request, goal, frame, declaration)
                out.append((resolution, None))
                if entry is not None:
                    # A registered capability declares what executing it needs.
                    if entry.requires_authorization:
                        declarations.append(PrerequisiteDeclaration(K.AUTHORIZATION, "authorization.capability", TIER_EXECUTION))
                    if entry.requires_consent:
                        declarations.append(PrerequisiteDeclaration(K.CONSENT, "consent.capability", TIER_CONSENT))
            elif declaration.kind == K.SAFETY:
                out.append((self._safety(request, goal, frame, declaration), None))
            elif declaration.kind in {K.AUTHORIZATION, K.CONSENT}:
                out.append((self._grant(request, goal, frame, declaration), None))
            elif declaration.kind in WORKSPACE_KINDS:
                out.append((self._workspace(request, goal, frame, declaration, target), None))
            elif declaration.kind == K.TRUSTED_CONTENT:
                if missing_referent:
                    # Content depends on the referent: it is not declared until
                    # the user (or the dialogue) supplies it.
                    continue
                out.append(self._content(request, registry, goal, frame, declaration, referent_items, referent_anchor))
            else:  # pragma: no cover - the manifest declares no other kind in V0
                raise RequirementResolutionError("the manifest declared an unsupported prerequisite kind")
        return out

    # -- conversational deliverables (engine v0.2) ------------------------------------
    def _workspace(self, request, goal, frame, declaration, target):
        """Workspace target, requested change, outcome certainty, inquiry link, and domain context.

        Read only from typed records: the frame's closed arguments, co-turn
        requirement frames, and this turn's workspace resolution.
        """

        kind = declaration.kind
        record = lambda status, reason, **refs: self._record(  # noqa: E731
            request, goal, frame, declaration, status, reason, subject=(frame.frame_id,),
            evidence=frame.evidence_refs, **refs)
        if kind == K.REQUESTED_OUTCOME:
            if frame.arguments.get("outcome_certainty") == "ambiguous_external_effect":
                return record(S.MISSING_USER_INPUT, R.OUTCOME_AMBIGUOUS,
                              values=(f"{frame.frame_id}#outcome_certainty",))
            return record(S.SATISFIED, R.OUTCOME_CLEAR)
        if kind == K.REQUESTED_CHANGE:
            stated = requested_change_refs(frame, request.interpretation)
            if stated:
                return record(S.SATISFIED, R.CHANGE_STATED, values=stated)
            return record(S.MISSING_USER_INPUT, R.CHANGE_UNSTATED)
        if kind == K.INQUIRY_LINK:
            if target is not None and target.inquiry_ref and target.rule_id != "workspace-target.inquiry-not-open.v1":
                return record(S.SATISFIED, R.INQUIRY_OPEN, values=(target.inquiry_ref,))
            return record(S.UNAVAILABLE, R.INQUIRY_NOT_OPEN)
        if kind == K.ARTIFACT_TARGET:
            if target is None:
                return record(S.MISSING_USER_INPUT, R.ARTIFACT_TARGET_NOT_FOUND)
            status = target.status.value
            if status == "resolved_artifact":
                return record(S.SATISFIED, R.ARTIFACT_TARGET_RESOLVED,
                              values=(target.artifact_ref, target.base_revision_ref))
            if status == "resolved_dialogue":
                return record(S.SATISFIED, R.DIALOGUE_TARGET_RESOLVED, values=target.dialogue_refs)
            if status == "ambiguous":
                return record(S.MISSING_USER_INPUT, R.ARTIFACT_TARGET_AMBIGUOUS, values=target.candidate_artifact_refs)
            return record(S.MISSING_USER_INPUT, R.ARTIFACT_TARGET_NOT_FOUND)
        if kind == K.DOMAIN_CONTEXT:
            base = None
            if target is not None and target.base_revision_ref and request.workspace is not None:
                base = request.workspace.snapshot.revision(target.base_revision_ref)
            if base is not None and domain_context_known(base):
                return record(S.SATISFIED, R.CONTEXT_KNOWN, values=(base.revision_id,))
            return record(S.MISSING_USER_INPUT, R.CONTEXT_UNKNOWN)
        raise RequirementResolutionError("unsupported workspace prerequisite")  # pragma: no cover

    def _record(self, request, goal, frame, declaration, status, reason, *, argument=None, subject=(),
                values=(), evidence=(), policy=(), capability=()) -> RequirementResolution:
        return RequirementResolution(
            resolution_id=f"requirement-resolution-{_digest(request.cognitive_turn_id, goal.goal_id, frame.frame_id, declaration.key)}",
            cognitive_turn_id=request.cognitive_turn_id, conversation_ref=request.conversation_ref,
            goal_ref=goal.goal_id, goal_requirement_ref=goal.requirements[0].requirement_id if goal.requirements else None,
            frame_ref=frame.frame_id, kind=declaration.kind, key=declaration.key, argument_name=argument,
            status=status, blocking=False,
            tier=TIER_SAFETY if status == S.PROHIBITED else declaration.tier, reason_code=reason,
            subject_refs=_sorted(subject), value_refs=_sorted(values), evidence_refs=_sorted(evidence),
            policy_refs=_sorted(policy), capability_refs=_sorted(capability),
            resolver_id=_RESOLVERS[declaration.kind], resolver_version=ENGINE_VERSION,
            criticality=RequirementCriticality(declaration.criticality),
        )

    # -- argument and referent -------------------------------------------------------
    def _argument(self, request, goal, frame, declaration):
        name = declaration.argument
        argument_ref = f"{frame.frame_id}#{name}"
        interpretation = request.interpretation
        unknowns = tuple(
            item.unknown_id for item in interpretation.unknowns
            if item.key == name and set(item.evidence_refs) & set(frame.evidence_refs)
        )
        value = frame.arguments.get(name)
        present = value is not None and name not in frame.unresolved_arguments
        referent = declaration.kind == K.DIALOGUE_REFERENT
        if present:
            reason = R.REFERENT_STATED if referent else R.ARGUMENT_PRESENT
            return self._record(request, goal, frame, declaration, S.SATISFIED, reason, argument=name,
                                subject=(frame.frame_id,), values=(argument_ref,),
                                evidence=frame.evidence_refs), (), None
        if not referent:
            return self._record(request, goal, frame, declaration, S.MISSING_USER_INPUT, R.ARGUMENT_UNRESOLVED,
                                argument=name, subject=(frame.frame_id, argument_ref, *unknowns),
                                evidence=frame.evidence_refs), (), None
        cues = linked_referent_cues(frame, interpretation, request.dialogue_context)
        dialogue_unknowns = tuple(
            f"dialogue:{cue.cue_ref}" for cue in cues if f"dialogue:{cue.cue_ref}" in set(request.dialogue_action_refs)
        )
        if (not self.preserve_accepted_uncertainty
                and len(cues) == 1 and cues[0].resolution == "resolved"):
            cue = cues[0]
            return self._record(request, goal, frame, declaration, S.SATISFIED, R.REFERENT_RESOLVED_FROM_DIALOGUE,
                                argument=name, subject=(frame.frame_id, argument_ref, *unknowns, cue.cue_ref),
                                values=cue.resolved_refs, evidence=frame.evidence_refs), \
                tuple(sorted(cue.resolved_refs)), cue.surface_anchor
        reason = R.REFERENT_AMBIGUOUS if len(cues) == 1 and cues[0].resolution == "ambiguous" else R.REFERENT_UNRESOLVED
        return self._record(request, goal, frame, declaration, S.MISSING_USER_INPUT, reason, argument=name,
                            subject=(frame.frame_id, argument_ref, *unknowns, *(cue.cue_ref for cue in cues),
                                     *dialogue_unknowns),
                            values=tuple(ref for cue in cues for ref in cue.candidate_refs),
                            evidence=frame.evidence_refs), (), None

    # -- capability, authorization, consent, safety --------------------------------------
    def _capability(self, request, goal, frame, declaration):
        entry = capability_for_predicate(frame.predicate)
        runtime = request.runtime
        ref = capability_ref(entry, frame.predicate)
        if entry is None:
            status, reason = S.UNAVAILABLE, R.CAPABILITY_UNREGISTERED
        elif entry.capability_key in runtime.available_capabilities:
            status, reason = S.SATISFIED, R.CAPABILITY_AVAILABLE
        else:
            status, reason = S.UNAVAILABLE, R.CAPABILITY_UNAVAILABLE
        resolution = self._record(request, goal, frame, declaration, status, reason, subject=(frame.frame_id,),
                                  evidence=(runtime.capability_snapshot_ref,), capability=(ref,))
        return resolution, entry

    def _grant(self, request, goal, frame, declaration):
        runtime = request.runtime
        entry = capability_for_predicate(frame.predicate)
        capability = (capability_ref(entry, frame.predicate),)
        if declaration.kind == K.AUTHORIZATION:
            granted = (runtime.authorization or "").lower() in GRANTED
            status, reason = (S.SATISFIED, R.AUTHORIZATION_GRANTED) if granted else (S.UNAUTHORIZED, R.AUTHORIZATION_MISSING)
        else:
            granted = (runtime.consent or "").lower() in GRANTED
            status, reason = (S.SATISFIED, R.CONSENT_GRANTED) if granted else (S.MISSING_USER_INPUT, R.CONSENT_MISSING)
        return self._record(request, goal, frame, declaration, status, reason, subject=(frame.frame_id,),
                            evidence=(runtime.runtime_snapshot_ref,), capability=capability)

    def _safety(self, request, goal, frame, declaration):
        runtime = request.runtime
        _, restriction_reason = safety_restriction(runtime.safety_decision)
        status, reason = _SAFETY_REASONS[restriction_reason]
        decision = runtime.safety_decision
        decision_ref = decision.decision_id if decision is not None else missing_safety_decision_ref(runtime.runtime_snapshot_ref)
        policy = (decision_ref, *((decision.producer_ref,) if decision is not None else ()))
        if status == S.PROHIBITED:
            policy = (*policy, *runtime.policy_records)
        return self._record(request, goal, frame, declaration, status, reason, subject=(frame.frame_id,),
                            evidence=(decision_ref,), policy=policy)

    # -- trusted content -----------------------------------------------------------------
    def _content(self, request, registry, goal, frame, declaration, referent_items, referent_anchor):
        inputs = request.inputs
        compiled = compile_content_query(
            frame.predicate, frame.arguments, time_zone=inputs.time_zone if inputs else None,
            referent_item_refs=referent_items, referent_anchor=referent_anchor,
        )
        query = SemanticQuery(
            query_id=f"requirement-query:{_digest(request.cognitive_turn_id, goal.goal_id, frame.frame_id)}",
            cognitive_turn_id=request.cognitive_turn_id,
            user_ref=(inputs.user_ref if inputs is not None and inputs.user_ref else UNIDENTIFIED_USER),
            query_kind=compiled.query_kind, subject_ref=frame.frame_id, subject_anchor=compiled.subject_anchor,
            arguments=dict(compiled.arguments), evidence_refs=frame.evidence_refs,
        )
        result = self._content_result(request, registry, frame, query)
        gap = knowledge_gap_for(query, result)
        status = CONTENT_STATUS_BY_PRODUCER[ContentProducerStatus(result.status.value)]
        reason = R.PRODUCER_KNOWN if result.reason is None else _PRODUCER_REASONS[result.reason]
        evidence = (query.query_id, result.result_id, *(fact.fact_id for fact in result.facts),
                    *((gap.gap_id,) if gap is not None else ()), *frame.evidence_refs)
        resolution = self._record(request, goal, frame, declaration, status, reason, subject=(frame.frame_id,),
                                  values=referent_items, evidence=evidence)
        return resolution, ContentRecord(resolution.resolution_id, query, result, gap)

    def _content_result(self, request, registry, frame, query):
        return registry.resolve(query)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def goal_frames(goal: GoalRecord, interpretation: InterpretationEnvelope, *, task_ref: str | None) -> tuple[SemanticFrame, ...]:
    """The exact frames a semantic-act goal was formed from (the inverse of formation).

    A frame qualifies when a retained act hypothesis of the goal's formation
    entry cites it (or cites no frame at all), its act family is eligible,
    and re-binding the entry against it reproduces the goal's requirement
    targets exactly.
    """

    entry = FORMATION_BY_DEFINITION[goal.definition_ref.definition_key]
    retained = (HypothesisDisposition.LEADING, HypothesisDisposition.RETAINED_ALTERNATIVE)
    acts = [item for item in interpretation.hypotheses
            if item.hypothesis_key == entry.act_hypothesis_key and item.disposition in retained]
    cited = set()
    for act in acts:
        cited.update(act.semantic_frame_refs or (frame.frame_id for frame in interpretation.semantic_frames))
    targets = {(target.role, target.ref) for requirement in goal.requirements for target in requirement.target_refs}
    out = []
    for frame in interpretation.semantic_frames:
        if frame.frame_id not in cited or not frame_eligible(entry, frame):
            continue
        bound = set(frame_target_refs(entry, frame, conversation_ref=interpretation.conversation_ref, task_ref=task_ref))
        if bound == targets:
            out.append(frame)
    return tuple(sorted(out, key=lambda item: item.frame_id))


def frame_focus_spans(frame: SemanticFrame, interpretation: InterpretationEnvelope) -> tuple[tuple[int, int], ...]:
    evidence = {item.evidence_id: item for item in interpretation.evidence}
    spans = []
    for ref in frame.evidence_refs:
        item = evidence.get(ref)
        if (item is not None and item.source_kind == InterpretationEvidenceKind.MESSAGE_SPAN and item.span is not None
                and item.span.message_id == interpretation.focus_message_id):
            spans.append((item.span.start, item.span.end))
    return tuple(sorted(set(spans)))


def linked_referent_cues(frame: SemanticFrame, interpretation: InterpretationEnvelope,
                         context: InterpretationDialogueContext | None):
    """Referent cues of the authorized dialogue context inside the frame's own evidence spans."""

    if context is None:
        return ()
    spans = frame_focus_spans(frame, interpretation)
    return tuple(sorted(
        (cue for cue in context.referents if cue.cue_kind in REFERENT_CUE_KINDS
         and any(start <= cue.start and cue.end <= end for start, end in spans)),
        key=lambda cue: (cue.start, cue.end, cue.cue_ref),
    ))


def _provenance(record: ContentRecord) -> RequirementContentProvenance:
    result = record.result
    return RequirementContentProvenance(
        resolution_ref=record.resolution_ref, query_id=record.query.query_id, query_kind=record.query.query_kind,
        result_ref=result.result_id, producer_status=ContentProducerStatus(result.status.value),
        producer_reason=result.reason.value if result.reason is not None else None,
        authority_class=result.authority_class.value if result.authority_class is not None else None,
        producer_id=result.producer_id, producer_version=result.producer_version,
        producer_manifest_version=result.manifest_version,
        fact_refs=_sorted(fact.fact_id for fact in result.facts),
        withheld_fact_refs=result.withheld_fact_refs, conflicting_fact_refs=result.conflicting_fact_refs,
        gap_ref=record.gap.gap_id if record.gap is not None else None,
    )


def _apply_blocking(resolutions: list[RequirementResolution]) -> list[RequirementResolution]:
    """Only each goal's lowest unsatisfied *blocking-criticality* tier blocks.

    Quality-improving and optional prerequisites are kept and traced but never
    make a goal unready (contract v0.2).
    """

    lowest: dict[str, int] = {}
    gating = [item for item in resolutions if item.criticality == RequirementCriticality.BLOCKING]
    for item in gating:
        if item.status != S.SATISFIED:
            lowest[item.goal_ref] = min(lowest.get(item.goal_ref, item.tier), item.tier)
    return [
        item.model_copy(update={"blocking": item.criticality == RequirementCriticality.BLOCKING
                                and item.status != S.SATISFIED and item.tier == lowest.get(item.goal_ref)})
        for item in resolutions
    ]


WORKSPACE_KINDS = frozenset({K.ARTIFACT_TARGET, K.REQUESTED_CHANGE, K.REQUESTED_OUTCOME, K.INQUIRY_LINK,
                             K.DOMAIN_CONTEXT})
REQUIREMENT_PREDICATES = frozenset({"constraint_statement", "correction"})


def requested_change_refs(frame: SemanticFrame, interpretation: InterpretationEnvelope) -> tuple[str, ...]:
    """Typed evidence that a revise request says *what* to change (never text matching).

    The frame's own constraint list or presentation form, or a direct,
    positive co-turn requirement or correction frame.
    """

    refs = []
    constraints = frame.arguments.get("constraints")
    if isinstance(constraints, (list, tuple)) and any(isinstance(item, str) and item.strip() for item in constraints):
        refs.append(f"{frame.frame_id}#constraints")
    if isinstance(frame.arguments.get("requested_form"), str):
        refs.append(f"{frame.frame_id}#requested_form")
    for other in interpretation.semantic_frames:
        if (other.frame_id != frame.frame_id and other.predicate in REQUIREMENT_PREDICATES
                and other.arguments.get("embedding", "direct") == "direct"):
            refs.append(other.frame_id)
    return _sorted(refs)


def domain_context_known(revision) -> bool:
    """Whether the artifact already records the safety-relevant context of its domain."""

    payload = revision.payload
    if getattr(payload, "allergy_constraints", ()):
        return True
    dietary = getattr(payload, "dietary_constraints", ())
    return bool(dietary) and all(item.reason.value != "unspecified" for item in dietary)


def _readiness(goal_ref: str, resolutions: list[RequirementResolution]) -> GoalReadiness:
    blocking = tuple(sorted(item.resolution_id for item in resolutions if item.goal_ref == goal_ref and item.blocking))
    if not blocking:
        return GoalReadiness(goal_ref=goal_ref, readiness="ready")
    tier = min(item.tier for item in resolutions if item.resolution_id in set(blocking))
    return GoalReadiness(goal_ref=goal_ref, readiness="blocked", blocking_tier=tier, blocking_resolution_refs=blocking)


__all__ = [
    "ContentRecord", "ENGINE_VERSION", "GRANTED", "ProducerFactory", "RUNTIME_DATE_PREDICATE",
    "RequirementResolutionEngine", "RequirementResolutionError", "RequirementResolutionOutcome",
    "RequirementResolutionRequest", "UNIDENTIFIED_USER", "WORKSPACE_KINDS", "default_producers",
    "dialogue_references", "domain_context_known", "requested_change_refs", "workspace_references",
    "frame_focus_spans", "goal_frames",
    "linked_referent_cues", "runtime_date_record",
]
