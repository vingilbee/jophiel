"""Ordered deterministic Interpretation V0 execution engine."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from ..contracts.interpretation import (
    HYPOTHESIS_KEYS_BY_DIMENSION,
    BiasAdjustment,
    Contradiction,
    HypothesisDimension,
    HypothesisDisposition,
    HypothesisSupport,
    InterpretationEnvelope,
    InterpretationEvidence,
    InterpretationHypothesis,
    InterpretationScenario,
    InterpretationVersions,
    OutOfScopeRecord,
    SemanticFrame,
    UnknownInput,
)
from ..perception_v0.evidence import stable_id
from ..perception_v0.manifest import get_manifest
from ..perception_v0.models import CueLifecycleStatus, CueState
from .bias import BIAS_POLICY_VERSION, BiasPolicy
from .evidence import (
    EVIDENCE_VALIDATOR_VERSION,
    EvidenceRegistryBuilder,
    validate_evidence_registry,
)
from .models import (
    InterpretationExecutionError,
    InterpretationFailurePhase,
    InterpretationRequest,
)
from .ontology import (
    ACTIVE_PERCEPTION_FEATURES,
    FEATURE_MANIFEST_VERSION,
    HYPOTHESIS_MANIFEST,
    ONTOLOGY_VERSION,
    PREDICATE_REGISTRY_VERSION,
    validate_hypothesis_manifest,
    validate_predicate_manifest,
    validate_semantic_frames,
)
from .rules import HYPOTHESIS_RULES_VERSION, HypothesisCandidate, HypothesisRuleEngine
from .scenarios import SCENARIO_BUILDER_VERSION, ScenarioBuilder
from .semantic_grounding import (
    SEMANTIC_GROUNDER_VERSION,
    SemanticGrounder,
    validate_runtime_predicate_registry,
)

INTERPRETATION_SCHEMA_VERSION = "interpretation-envelope-v0.1"


class InterpretationEngine:
    def __init__(
        self,
        *,
        request_validator: Callable[[InterpretationRequest], None] | None = None,
        evidence_builder: EvidenceRegistryBuilder | None = None,
        grounder: SemanticGrounder | None = None,
        rule_engine: HypothesisRuleEngine | None = None,
        bias_policy: BiasPolicy | None = None,
        scenario_builder: ScenarioBuilder | None = None,
        envelope_factory: Callable[..., InterpretationEnvelope] = InterpretationEnvelope,
    ) -> None:
        self.request_validator = request_validator or self._validate_request
        self.evidence_builder = evidence_builder or EvidenceRegistryBuilder()
        self.grounder = grounder or SemanticGrounder()
        self.rule_engine = rule_engine or HypothesisRuleEngine()
        self.bias_policy = bias_policy or BiasPolicy()
        self.scenario_builder = scenario_builder or ScenarioBuilder()
        self.envelope_factory = envelope_factory

    def interpret(self, request: InterpretationRequest) -> InterpretationEnvelope:
        turn_id = getattr(request, "cognitive_turn_id", "unknown-turn")
        try:
            self.request_validator(request)
        except Exception as exc:
            self._raise(turn_id, "validation", "Interpretation input validation failed.", exc)
        try:
            evidence = self.evidence_builder.build(request)
            validate_evidence_registry(evidence, request)
        except Exception as exc:
            self._raise(turn_id, "evidence", "Interpretation evidence could not be validated.", exc)
        try:
            grounded = self.grounder.ground(request, evidence)
            validate_semantic_frames(grounded.frames)
        except Exception as exc:
            self._raise(turn_id, "semantic_grounding", "Semantic grounding could not complete.", exc)
        try:
            candidates = self.rule_engine.evaluate(
                request, evidence, grounded.frames, grounded.unknowns,
                tuple(item.contradiction_id for item in grounded.contradictions),
            )
            candidates = self._consolidate_candidates(candidates)
        except Exception as exc:
            self._raise(turn_id, "hypothesis_rules", "Interpretation hypotheses could not be evaluated.", exc)
        try:
            adjusted = self.bias_policy.apply(candidates, request.biases)
            hypotheses = self._materialize_hypotheses(request, adjusted)
        except Exception as exc:
            self._raise(turn_id, "bias_adjustment", "Interpretation bias adjustment failed.", exc)
        try:
            scenarios = self.scenario_builder.build(
                cognitive_turn_id=request.cognitive_turn_id,
                perception_event_id=request.perception.perception_event_id,
                frames=grounded.frames,
                hypotheses=hypotheses,
                unknowns=grounded.unknowns,
                contradiction_ids=tuple(item.contradiction_id for item in grounded.contradictions),
            )
        except Exception as exc:
            self._raise(turn_id, "scenario_building", "Interpretation scenarios could not be built.", exc)
        try:
            versions = InterpretationVersions(
                schema=INTERPRETATION_SCHEMA_VERSION,
                ontology=request.ontology_version,
                feature_manifest=request.feature_manifest_version,
                semantic_grounder=SEMANTIC_GROUNDER_VERSION,
                hypothesis_rules=HYPOTHESIS_RULES_VERSION,
                scenario_builder=SCENARIO_BUILDER_VERSION,
                bias_policy=BIAS_POLICY_VERSION,
                evidence_validator=EVIDENCE_VALIDATOR_VERSION,
                perception_schema=request.perception.versions.schema_id,
                perception_cue_manifest=request.perception.versions.cue_manifest,
                perception_detectors=request.perception.versions.deterministic_detectors,
            )
            trace_id = stable_id(
                "interpretation", request.cognitive_turn_id,
                request.perception.perception_event_id,
                request.conversation_ref,
                request.biases.model_dump(mode="json"),
                versions.model_dump(mode="json"),
            )
            envelope = self.envelope_factory(
                interpretation_trace_id=trace_id,
                cognitive_turn_id=request.cognitive_turn_id,
                conversation_ref=request.conversation_ref,
                perception_event_id=request.perception.perception_event_id,
                input_message_ids=request.perception.input_message_ids,
                focus_message_id=request.perception.focus_message_id,
                runtime_snapshot_id=request.perception.runtime_snapshot_id,
                evidence=evidence,
                semantic_frames=grounded.frames,
                hypotheses=hypotheses,
                scenarios=scenarios,
                unknowns=grounded.unknowns,
                contradictions=grounded.contradictions,
                out_of_scope_records=grounded.out_of_scope_records,
                context_truncated=request.perception.context_truncated,
                versions=versions,
            )
            # Revalidate even if an injected factory returned an already-built
            # model instance via model_construct.
            envelope = InterpretationEnvelope.model_validate(
                envelope.model_dump(mode="python") if isinstance(envelope, InterpretationEnvelope) else envelope
            )
            self._validate_completed_envelope(
                envelope=envelope,
                request=request,
                evidence=evidence,
                frames=grounded.frames,
                hypotheses=hypotheses,
                scenarios=scenarios,
                unknowns=grounded.unknowns,
                contradictions=grounded.contradictions,
                out_of_scope_records=grounded.out_of_scope_records,
                versions=versions,
            )
        except Exception as exc:
            self._raise(turn_id, "envelope_validation", "Interpretation output validation failed.", exc)
        return envelope

    @staticmethod
    def _validate_completed_envelope(
        *,
        envelope: InterpretationEnvelope,
        request: InterpretationRequest,
        evidence: tuple[InterpretationEvidence, ...],
        frames: tuple[SemanticFrame, ...],
        hypotheses: tuple[InterpretationHypothesis, ...],
        scenarios: tuple[InterpretationScenario, ...],
        unknowns: tuple[UnknownInput, ...],
        contradictions: tuple[Contradiction, ...],
        out_of_scope_records: tuple[OutOfScopeRecord, ...],
        versions: InterpretationVersions,
    ) -> None:
        expected_ownership = (
            request.cognitive_turn_id,
            request.conversation_ref,
            request.perception.perception_event_id,
            request.perception.input_message_ids,
            request.perception.focus_message_id,
            request.perception.runtime_snapshot_id,
            request.perception.context_truncated,
        )
        actual_ownership = (
            envelope.cognitive_turn_id,
            envelope.conversation_ref,
            envelope.perception_event_id,
            envelope.input_message_ids,
            envelope.focus_message_id,
            envelope.runtime_snapshot_id,
            envelope.context_truncated,
        )
        if actual_ownership != expected_ownership:
            raise ValueError("completed interpretation ownership differs from its request")
        validate_evidence_registry(envelope.evidence, request)
        expected_outputs = (
            evidence, frames, hypotheses, scenarios, unknowns, contradictions,
            out_of_scope_records, versions,
        )
        actual_outputs = (
            envelope.evidence, envelope.semantic_frames, envelope.hypotheses,
            envelope.scenarios, envelope.unknowns, envelope.contradictions,
            envelope.out_of_scope_records, envelope.versions,
        )
        if actual_outputs != expected_outputs:
            raise ValueError("completed interpretation differs from validated stage outputs")

    @staticmethod
    def _validate_request(request: InterpretationRequest) -> None:
        # Check authorization before serializing or otherwise inspecting record content.
        if any(not memory.authorized for memory in request.memories):
            raise ValueError("unauthorized memories cannot enter interpretation")
        if request.relationship is not None and not request.relationship.authorized:
            raise ValueError("an unauthorized relationship record cannot enter interpretation")
        # Revalidate constructed/injected model instances at the trust boundary.
        InterpretationRequest.model_validate(request.model_dump(mode="python"))
        validate_hypothesis_manifest()
        validate_predicate_manifest()
        validate_runtime_predicate_registry(request)
        if request.ontology_version != ONTOLOGY_VERSION:
            raise ValueError("unsupported interpretation ontology version")
        if request.feature_manifest_version != FEATURE_MANIFEST_VERSION:
            raise ValueError("unsupported interpretation feature manifest version")
        manifest = get_manifest(request.perception.versions.cue_manifest)
        active = tuple(
            item.cue_key for item in manifest
            if item.lifecycle_status == CueLifecycleStatus.V0_ACTIVE
        )
        supplied = tuple(item.cue_key for item in request.perception.cue_activations)
        if supplied != active or active != ACTIVE_PERCEPTION_FEATURES:
            raise ValueError("perception handoff does not satisfy the Interpretation V0 feature manifest")
        for activation in request.perception.cue_activations:
            if activation.lifecycle_status != CueLifecycleStatus.V0_ACTIVE:
                raise ValueError("non-active perception cue entered Interpretation V0")
        by_key = {item.cue_key: item for item in request.perception.cue_activations}
        attachment_state = by_key["runtime.attachment_present"].state
        if bool(request.runtime.attachments) != (attachment_state == CueState.PRESENT):
            raise ValueError("interpretation runtime attachments conflict with perception")
        tool = request.runtime.last_tool_event
        tool_state = by_key["runtime.tool_failure"].state
        recognized_failure = bool(
            tool and tool.trusted_runtime and tool.authorized
            and tool.status.casefold() in {"failed", "failure", "error", "timed_out", "timeout", "cancelled"}
        )
        if (tool_state == CueState.PRESENT) != recognized_failure:
            raise ValueError("interpretation runtime tool event conflicts with perception")

    @staticmethod
    def _consolidate_candidates(
        candidates: tuple[HypothesisCandidate, ...],
    ) -> tuple[HypothesisCandidate, ...]:
        """Merge rule contributions into one authoritative record per ontology key."""

        support_rank = {value: index for index, value in enumerate(HypothesisSupport)}
        disposition_rank = {
            HypothesisDisposition.REJECTED: 0,
            HypothesisDisposition.UNKNOWN: 1,
            HypothesisDisposition.RETAINED_ALTERNATIVE: 2,
            HypothesisDisposition.LEADING: 3,
        }

        def unique(refs: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(dict.fromkeys(refs))

        grouped: dict[str, list[HypothesisCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.hypothesis_key, []).append(candidate)
        merged: list[HypothesisCandidate] = []
        for key, contributions in grouped.items():
            dimensions = {item.dimension for item in contributions}
            if len(dimensions) != 1:
                raise ValueError(f"hypothesis {key} was emitted in multiple dimensions")
            supported = [
                item for item in contributions
                if item.support != HypothesisSupport.UNSUPPORTED
            ]
            decisive = supported or contributions
            strongest = max(
                decisive,
                key=lambda item: (
                    support_rank[item.support], disposition_rank[item.disposition]
                ),
            )
            base_support = max(
                (item.base_support for item in decisive), key=support_rank.__getitem__
            )
            support = max(
                (item.support for item in decisive), key=support_rank.__getitem__
            )
            disposition = max(
                (item.disposition for item in decisive), key=disposition_rank.__getitem__
            )
            merged.append(HypothesisCandidate(
                hypothesis_key=key,
                dimension=strongest.dimension,
                base_support=base_support,
                support=support,
                disposition=disposition,
                semantic_frame_refs=unique(tuple(
                    ref for item in decisive for ref in item.semantic_frame_refs
                )),
                supporting_evidence_refs=unique(tuple(
                    ref for item in decisive for ref in item.supporting_evidence_refs
                )),
                contradicting_evidence_refs=unique(tuple(
                    ref for item in contributions for ref in item.contradicting_evidence_refs
                )),
                unknown_refs=unique(tuple(
                    ref for item in decisive for ref in item.unknown_refs
                )),
                contradiction_refs=unique(tuple(
                    ref for item in contributions for ref in item.contradiction_refs
                )),
                rule_ids=unique(tuple(
                    rule for item in contributions for rule in item.rule_ids
                )),
                ambiguity_gate=max(item.ambiguity_gate for item in decisive),
            ))
        return tuple(merged)

    @staticmethod
    def _materialize_hypotheses(
        request: InterpretationRequest,
        candidates: tuple[HypothesisCandidate, ...],
    ) -> tuple[InterpretationHypothesis, ...]:
        ontology_order = {
            key: index
            for index, definition in enumerate(HYPOTHESIS_MANIFEST)
            for key in (definition.hypothesis_key,)
        }
        support_rank = {value: index for index, value in enumerate(HypothesisSupport)}
        materialized = []
        for item in candidates:
            disposition = item.disposition
            if item.support == HypothesisSupport.UNSUPPORTED and disposition in {
                HypothesisDisposition.LEADING, HypothesisDisposition.RETAINED_ALTERNATIVE,
            }:
                disposition = HypothesisDisposition.REJECTED
            hypothesis_id = stable_id(
                "interpretation-hypothesis", request.cognitive_turn_id,
                request.perception.perception_event_id, item.hypothesis_key,
                item.semantic_frame_refs, item.rule_ids,
            )
            materialized.append(InterpretationHypothesis(
                hypothesis_id=hypothesis_id,
                hypothesis_key=item.hypothesis_key,
                definition_version=1,
                dimension=item.dimension,
                base_support=item.base_support,
                support=item.support,
                disposition=disposition,
                semantic_frame_refs=item.semantic_frame_refs,
                supporting_evidence_refs=item.supporting_evidence_refs,
                contradicting_evidence_refs=item.contradicting_evidence_refs,
                unknown_refs=item.unknown_refs,
                contradiction_refs=item.contradiction_refs,
                rule_ids=item.rule_ids,
                bias_adjustments=tuple(
                    adjustment for adjustment in item.bias_adjustments
                    if isinstance(adjustment, BiasAdjustment)
                ),
            ))
        dimension_order = {dimension: index for index, dimension in enumerate(HypothesisDimension)}
        return tuple(sorted(
            materialized,
            key=lambda item: (
                dimension_order[item.dimension], -support_rank[item.support],
                ontology_order[item.hypothesis_key], item.hypothesis_id,
            ),
        ))

    @staticmethod
    def _raise(
        turn_id: str,
        phase: InterpretationFailurePhase,
        safe_message: str,
        cause: Exception,
    ) -> NoReturn:
        raise InterpretationExecutionError(
            cognitive_turn_id=turn_id,
            failed_phase=phase,
            safe_message=safe_message,
        ) from cause


def interpret(request: InterpretationRequest) -> InterpretationEnvelope:
    return InterpretationEngine().interpret(request)
