"""Canonical immutable contracts shared after Interpretation V0.

This module contains contracts only.  It deliberately imports no engine from
interpretation, appraisal, dynamic state, or cognitive turn.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import Enum, StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer, model_validator,
)

from ..perception_v0.models import TextSpanEvidence


class FrozenDict(dict):
    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("frozen mapping cannot be modified")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen = FrozenDict()
        dict.update(frozen, {str(key): deep_freeze(item) for key, item in value.items()})
        return frozen
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(deep_freeze(item) for item in value)
    if value is None or type(value) in {bool, int, str} or isinstance(value, Enum):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, BaseModel):
        return value
    raise ValueError(f"value is not immutable JSON data: {type(value).__name__}")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_post_init(self, __context: Any) -> None:
        for name in type(self).model_fields:
            object.__setattr__(self, name, deep_freeze(getattr(self, name)))


class HypothesisDimension(StrEnum):
    COMMUNICATIVE_ACT = "communicative_act"
    SPEAKER_GOAL = "speaker_goal"
    MODALITY = "modality"
    PRAGMATIC_MEANING = "pragmatic_meaning"


class HypothesisSupport(StrEnum):
    UNSUPPORTED = "unsupported"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class HypothesisDisposition(StrEnum):
    LEADING = "leading"
    RETAINED_ALTERNATIVE = "retained_alternative"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


# Downstream appraisal historically used these names.  They are aliases, not
# duplicate enum definitions.
ScenarioSupport = HypothesisSupport
ScenarioDisposition = HypothesisDisposition


HYPOTHESIS_KEYS_BY_DIMENSION: Mapping[HypothesisDimension, tuple[str, ...]] = FrozenDict({
    HypothesisDimension.COMMUNICATIVE_ACT: (
        "interpretation.act.inform", "interpretation.act.request_information",
        "interpretation.act.request_action", "interpretation.act.correct",
        "interpretation.act.confirm", "interpretation.act.reject_or_decline",
        "interpretation.act.acknowledge", "interpretation.act.evaluate",
        "interpretation.act.greet", "interpretation.act.close",
    ),
    HypothesisDimension.SPEAKER_GOAL: (
        "interpretation.goal.transfer_information", "interpretation.goal.obtain_information",
        "interpretation.goal.cause_action", "interpretation.goal.prevent_or_stop_action",
        "interpretation.goal.repair_shared_understanding", "interpretation.goal.express_stance",
        "interpretation.goal.seek_acknowledgement",
        "interpretation.goal.maintain_social_connection", "interpretation.goal.unknown",
    ),
    HypothesisDimension.MODALITY: (
        "interpretation.modality.asserted", "interpretation.modality.tentative",
        "interpretation.modality.questioned", "interpretation.modality.requested",
        "interpretation.modality.hypothetical", "interpretation.modality.quoted",
    ),
    HypothesisDimension.PRAGMATIC_MEANING: (
        "interpretation.pragmatic.literal", "interpretation.pragmatic.indirect",
        "interpretation.pragmatic.tone_situation_mismatch", "interpretation.pragmatic.unknown",
    ),
})
HYPOTHESIS_DIMENSION_BY_KEY = FrozenDict({
    key: dimension for dimension, keys in HYPOTHESIS_KEYS_BY_DIMENSION.items() for key in keys
})


def _symmetric_conflicts(*groups: tuple[str, ...]) -> FrozenDict:
    collected: dict[str, set[str]] = {}
    for group in groups:
        for key in group:
            collected.setdefault(key, set()).update(item for item in group if item != key)
    return FrozenDict({key: tuple(sorted(values)) for key, values in collected.items()})


HYPOTHESIS_CONFLICTS: Mapping[str, tuple[str, ...]] = _symmetric_conflicts(
    (
        "interpretation.goal.cause_action",
        "interpretation.goal.prevent_or_stop_action",
    ),
    (
        "interpretation.pragmatic.literal",
        "interpretation.pragmatic.indirect",
        "interpretation.pragmatic.tone_situation_mismatch",
        "interpretation.pragmatic.unknown",
    ),
)

HYPOTHESIS_COMPATIBILITIES: Mapping[str, tuple[str, ...]] = _symmetric_conflicts(
    (
        "interpretation.act.request_information",
        "interpretation.goal.obtain_information",
        "interpretation.modality.questioned",
    ),
    (
        "interpretation.act.request_action",
        "interpretation.goal.cause_action",
        "interpretation.modality.requested",
        "interpretation.pragmatic.indirect",
    ),
    (
        "interpretation.act.greet",
        "interpretation.goal.maintain_social_connection",
    ),
    (
        "interpretation.act.close",
        "interpretation.goal.maintain_social_connection",
    ),
    (
        "interpretation.modality.hypothetical",
        "interpretation.modality.quoted",
    ),
)


class InterpretationEvidenceKind(StrEnum):
    PERCEPTION_CUE = "perception_cue"
    MESSAGE_SPAN = "message_span"
    MESSAGE_SEQUENCE = "message_sequence"
    RUNTIME_FACT = "runtime_fact"
    MEMORY_RECORD = "memory_record"
    RELATIONSHIP_RECORD = "relationship_record"
    SEMANTIC_FRAME = "semantic_frame"


class InterpretationEvidence(FrozenModel):
    evidence_id: str = Field(min_length=1)
    source_kind: InterpretationEvidenceKind
    source_ref: str = Field(min_length=1)
    message_id: str | None = None
    span: TextSpanEvidence | None = None
    message_ids: tuple[str, ...] = ()
    cue_key: str | None = None
    observation_refs: tuple[str, ...] = ()
    runtime_snapshot_ref: str | None = None
    json_pointer: str | None = None
    value: Any = None
    memory_ref: str | None = None
    semantic_frame_ref: str | None = None

    @model_validator(mode="after")
    def source_shape_matches_kind(self) -> InterpretationEvidence:
        if self.span is not None and self.message_id != self.span.message_id:
            raise ValueError("interpretation evidence span/message ids differ")
        required = {
            InterpretationEvidenceKind.PERCEPTION_CUE: self.cue_key,
            InterpretationEvidenceKind.MESSAGE_SPAN: self.span,
            InterpretationEvidenceKind.MESSAGE_SEQUENCE: self.message_ids,
            InterpretationEvidenceKind.RUNTIME_FACT: self.runtime_snapshot_ref,
            InterpretationEvidenceKind.MEMORY_RECORD: self.memory_ref,
            InterpretationEvidenceKind.SEMANTIC_FRAME: self.semantic_frame_ref,
        }
        if self.source_kind in required and not required[self.source_kind]:
            raise ValueError(f"{self.source_kind} evidence is missing its typed source reference")
        if self.source_kind == InterpretationEvidenceKind.RUNTIME_FACT and self.json_pointer is None:
            raise ValueError("runtime evidence requires a JSON Pointer")
        return self


class SemanticFrame(FrozenModel):
    frame_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    arguments: Mapping[str, Any] = Field(default_factory=dict)
    polarity: Literal["positive", "negative", "unknown"]
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    unresolved_arguments: tuple[str, ...] = ()
    grounding_rule_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def arguments_match_resolution(self) -> SemanticFrame:
        missing = set(self.unresolved_arguments)
        if len(missing) != len(self.unresolved_arguments):
            raise ValueError("semantic-frame unresolved arguments must be unique")
        if not missing <= set(self.arguments):
            raise ValueError("unresolved semantic arguments must exist as keys")
        if any(self.arguments[key] is not None for key in missing):
            raise ValueError("unresolved semantic arguments must have null values")
        return self

    @property
    def source_spans(self) -> tuple[str, ...]:
        """Temporary compatibility view; typed evidence remains authoritative."""

        return self.evidence_refs


class UnknownInput(FrozenModel):
    unknown_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    owner: Literal["user", "runtime", "system", "unknown"]
    status: Literal["unresolved"] = "unresolved"
    evidence_refs: tuple[str, ...] = ()


class Contradiction(FrozenModel):
    contradiction_id: str = Field(min_length=1)
    status: Literal["active", "resolved"]
    description: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    resolution_ref: str | None = None

    @model_validator(mode="after")
    def resolution_matches_status(self) -> Contradiction:
        if (self.status == "resolved") != (self.resolution_ref is not None):
            raise ValueError("only resolved contradictions have a resolution reference")
        return self


class OutOfScopeRecord(FrozenModel):
    out_of_scope_id: str = Field(min_length=1)
    surface_form: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    recoverable_by_user: bool


class BiasAdjustment(FrozenModel):
    bias_key: str = Field(min_length=1)
    direction: Literal["increase", "decrease", "unchanged"]
    ordinal_shift: Literal[-1, 0, 1]
    ambiguity_gate: float = Field(ge=0.0, le=1.0)
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def gate_controls_adjustment(self) -> BiasAdjustment:
        if not math.isfinite(self.ambiguity_gate):
            raise ValueError("ambiguity gate must be finite")
        if self.ambiguity_gate == 0 and self.ordinal_shift != 0:
            raise ValueError("a closed ambiguity gate cannot adjust support")
        expected_direction = {-1: "decrease", 0: "unchanged", 1: "increase"}[self.ordinal_shift]
        if self.direction != expected_direction:
            raise ValueError("bias direction and ordinal shift disagree")
        return self


class InterpretationHypothesis(FrozenModel):
    hypothesis_id: str = Field(min_length=1)
    hypothesis_key: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    dimension: HypothesisDimension
    base_support: HypothesisSupport
    support: HypothesisSupport
    disposition: HypothesisDisposition
    semantic_frame_refs: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...] = ()
    unknown_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = Field(min_length=1)
    bias_adjustments: tuple[BiasAdjustment, ...] = ()

    @model_validator(mode="after")
    def hypothesis_is_coherent(self) -> InterpretationHypothesis:
        expected = HYPOTHESIS_DIMENSION_BY_KEY.get(self.hypothesis_key)
        if expected is None or expected != self.dimension or self.definition_version != 1:
            raise ValueError("hypothesis key, dimension, or definition version is invalid")
        if self.support != HypothesisSupport.UNSUPPORTED and not self.supporting_evidence_refs:
            raise ValueError("supported hypotheses require supporting evidence")
        if self.support == HypothesisSupport.UNSUPPORTED and self.disposition in {
            HypothesisDisposition.LEADING, HypothesisDisposition.RETAINED_ALTERNATIVE,
        }:
            raise ValueError("unsupported hypotheses cannot be retained")
        if self.disposition == HypothesisDisposition.UNKNOWN and not self.unknown_refs:
            raise ValueError("unknown hypotheses require an unknown reference")
        levels = tuple(HypothesisSupport)
        if abs(levels.index(self.support) - levels.index(self.base_support)) > 1:
            raise ValueError("personality may shift support by at most one ordinal level")
        return self


class InterpretationScenario(FrozenModel):
    scenario_id: str = Field(min_length=1)
    support: ScenarioSupport
    disposition: ScenarioDisposition
    semantic_frame_refs: tuple[str, ...] = ()
    hypothesis_refs: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    supporting_evidence_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    unknown_refs: tuple[str, ...] = ()
    compatibility_rule_ids: tuple[str, ...] = Field(min_length=1)


class SemanticParserProvenance(FrozenModel):
    """Every behavior-affecting option of one model-backed semantic-parser call.

    Typed so a persisted trace identifies its exact request configuration
    without parsing ``InterpretationVersions.semantic_grounder``.  A sampled
    candidate is never a replay oracle, so ``replayable`` is always false.
    """

    provider: str = Field(min_length=1)
    provider_adapter: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_family: str = Field(min_length=1)
    endpoint: Literal["chat.completions"]
    capability_manifest_version: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    compiler_version: str = Field(min_length=1)
    predicate_registry_version: str = Field(min_length=1)
    temperature: str = Field(min_length=1)
    sampling_controls: Literal["temperature"] = "temperature"
    token_limit_parameter: Literal["max_completion_tokens", "max_tokens"]
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    candidate_count: Literal[1] = 1
    structured_output: Literal["json_schema_strict"] = "json_schema_strict"
    tools: Literal["none"] = "none"
    streaming: Literal[False] = False
    provider_storage: Literal["disabled"] = "disabled"
    sdk_retries: Literal[0] = 0
    replayable: Literal[False] = False
    reasoning_effort: Literal["none"] | None = None

    @model_serializer(mode="wrap")
    def omit_legacy_reasoning(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.reasoning_effort is None:
            data.pop("reasoning_effort", None)
        return data


class InterpretationVersions(FrozenModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )

    schema_id: str = Field(alias="schema", min_length=1)
    ontology: str = Field(min_length=1)
    feature_manifest: str = Field(min_length=1)
    semantic_grounder: str = Field(min_length=1)
    hypothesis_rules: str = Field(min_length=1)
    scenario_builder: str = Field(min_length=1)
    bias_policy: str = Field(min_length=1)
    evidence_validator: str = Field(min_length=1)
    perception_schema: str = Field(min_length=1)
    perception_cue_manifest: str = Field(min_length=1)
    perception_detectors: str = Field(min_length=1)
    # Present only for model-backed grounding; omitted from serialization when
    # absent so rule-grounded payloads and digests stay byte-identical.
    semantic_parser: SemanticParserProvenance | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_parser(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if isinstance(data, dict) and data.get("semantic_parser") is None:
            data.pop("semantic_parser", None)
        return data


_SHADOW_CUE_KEYS = frozenset({
    "conversation.topic_shift", "linguistic.positive_wording",
    "linguistic.negative_wording", "task.urgency",
})


class InterpretationEnvelope(FrozenModel):
    schema_version: Literal[1] = 1
    interpretation_trace_id: str = Field(min_length=1)
    cognitive_turn_id: str = Field(min_length=1)
    conversation_ref: str = Field(min_length=1)
    perception_event_id: str = Field(min_length=1)
    input_message_ids: tuple[str, ...] = Field(min_length=1)
    focus_message_id: str = Field(min_length=1)
    runtime_snapshot_id: str = Field(min_length=1)
    evidence: tuple[InterpretationEvidence, ...]
    semantic_frames: tuple[SemanticFrame, ...]
    hypotheses: tuple[InterpretationHypothesis, ...]
    scenarios: tuple[InterpretationScenario, ...] = Field(min_length=1)
    unknowns: tuple[UnknownInput, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    out_of_scope_records: tuple[OutOfScopeRecord, ...] = ()
    context_truncated: bool = False
    versions: InterpretationVersions

    @model_validator(mode="after")
    def references_resolve(self) -> InterpretationEnvelope:
        if len(self.input_message_ids) != len(set(self.input_message_ids)):
            raise ValueError("interpretation input message ids must be unique")
        if self.focus_message_id not in self.input_message_ids or self.input_message_ids[-1] != self.focus_message_id:
            raise ValueError("interpretation focus must be the final bounded input message")
        collections = {
            "evidence": tuple(item.evidence_id for item in self.evidence),
            "frame": tuple(item.frame_id for item in self.semantic_frames),
            "hypothesis": tuple(item.hypothesis_id for item in self.hypotheses),
            "scenario": tuple(item.scenario_id for item in self.scenarios),
            "unknown": tuple(item.unknown_id for item in self.unknowns),
            "contradiction": tuple(item.contradiction_id for item in self.contradictions),
            "out-of-scope": tuple(item.out_of_scope_id for item in self.out_of_scope_records),
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {label} ids in interpretation envelope")
        evidence_ids = set(collections["evidence"])
        frame_ids = set(collections["frame"])
        hypothesis_by_id = {item.hypothesis_id: item for item in self.hypotheses}
        hypothesis_keys = tuple(item.hypothesis_key for item in self.hypotheses)
        if len(hypothesis_keys) != len(set(hypothesis_keys)):
            raise ValueError("duplicate hypothesis keys in interpretation envelope")
        unknown_ids = set(collections["unknown"])
        contradiction_ids = set(collections["contradiction"])
        for item in self.evidence:
            if item.message_id is not None and item.message_id not in self.input_message_ids:
                raise ValueError("interpretation evidence cites a message outside the bounded input")
            if any(message_id not in self.input_message_ids for message_id in item.message_ids):
                raise ValueError("message-sequence evidence cites an unavailable message")
            if item.cue_key in _SHADOW_CUE_KEYS:
                raise ValueError("shadow perception cues cannot enter interpretation evidence")
            if item.semantic_frame_ref is not None and item.semantic_frame_ref not in frame_ids:
                raise ValueError("semantic-frame evidence reference does not resolve")
        for frame in self.semantic_frames:
            if not set(frame.evidence_refs) <= evidence_ids:
                raise ValueError("semantic frame evidence reference does not resolve")
        for hypothesis in self.hypotheses:
            if not set(hypothesis.semantic_frame_refs) <= frame_ids:
                raise ValueError("hypothesis semantic-frame reference does not resolve")
            for refs, available, label in (
                (hypothesis.supporting_evidence_refs, evidence_ids, "supporting evidence"),
                (hypothesis.contradicting_evidence_refs, evidence_ids, "contradicting evidence"),
                (hypothesis.unknown_refs, unknown_ids, "unknown"),
                (hypothesis.contradiction_refs, contradiction_ids, "contradiction"),
            ):
                if not set(refs) <= available:
                    raise ValueError(f"hypothesis {label} reference does not resolve")
        retained = 0
        leading = 0
        retained_hypotheses = {
            item.hypothesis_id: item
            for item in self.hypotheses
            if item.disposition == HypothesisDisposition.RETAINED_ALTERNATIVE
        }
        for scenario in self.scenarios:
            if not set(scenario.semantic_frame_refs) <= frame_ids:
                raise ValueError("scenario semantic-frame reference does not resolve")
            if not set(scenario.supporting_evidence_refs) <= evidence_ids:
                raise ValueError("scenario evidence reference does not resolve")
            if not set(scenario.unknown_refs) <= unknown_ids:
                raise ValueError("scenario unknown reference does not resolve")
            if not set(scenario.contradiction_refs) <= contradiction_ids:
                raise ValueError("scenario contradiction reference does not resolve")
            scenario_hypotheses: list[InterpretationHypothesis] = []
            for dimension, refs in scenario.hypothesis_refs.items():
                try:
                    expected_dimension = HypothesisDimension(dimension)
                except ValueError as exc:
                    raise ValueError(f"scenario uses an unknown hypothesis dimension: {dimension}") from exc
                for ref in refs:
                    hypothesis = hypothesis_by_id.get(ref)
                    if hypothesis is None or hypothesis.dimension != expected_dimension:
                        raise ValueError("scenario hypothesis reference has the wrong dimension")
                    if hypothesis.support == HypothesisSupport.UNSUPPORTED or hypothesis.disposition == HypothesisDisposition.REJECTED:
                        raise ValueError("scenario cannot retain unsupported/rejected hypotheses")
                    scenario_hypotheses.append(hypothesis)
            scenario_keys = {item.hypothesis_key for item in scenario_hypotheses}
            if any(
                scenario_keys & set(HYPOTHESIS_CONFLICTS.get(key, ()))
                for key in scenario_keys
            ):
                raise ValueError("scenario contains conflicting hypotheses")
            if scenario.disposition == ScenarioDisposition.LEADING and any(
                item.disposition != HypothesisDisposition.LEADING
                for item in scenario_hypotheses
            ):
                raise ValueError("leading scenario contains a non-leading hypothesis")
            if (
                scenario.disposition == ScenarioDisposition.RETAINED_ALTERNATIVE
                and scenario_hypotheses
                and not any(
                    item.disposition == HypothesisDisposition.RETAINED_ALTERNATIVE
                    for item in scenario_hypotheses
                )
                and "scenario.high-consequence-alternative-retention.v1"
                not in scenario.compatibility_rule_ids
            ):
                raise ValueError("alternative scenario does not contain an alternative hypothesis")
            if scenario.disposition in {ScenarioDisposition.LEADING, ScenarioDisposition.RETAINED_ALTERNATIVE}:
                retained += 1
                if scenario.support == ScenarioSupport.UNSUPPORTED:
                    raise ValueError("retained scenarios cannot be unsupported")
            if scenario.disposition == ScenarioDisposition.LEADING:
                leading += 1
            if scenario.disposition == ScenarioDisposition.RETAINED_ALTERNATIVE:
                included_alternatives = {
                    item.hypothesis_id for item in scenario_hypotheses
                    if item.disposition == HypothesisDisposition.RETAINED_ALTERNATIVE
                }
                required = set(included_alternatives)
                pending = list(included_alternatives)
                while pending:
                    current = retained_hypotheses[pending.pop()]
                    compatible_keys = set(
                        HYPOTHESIS_COMPATIBILITIES.get(current.hypothesis_key, ())
                    )
                    for candidate_id, candidate in retained_hypotheses.items():
                        if (
                            candidate_id not in required
                            and candidate.hypothesis_key in compatible_keys
                        ):
                            required.add(candidate_id)
                            pending.append(candidate_id)
                if included_alternatives != required:
                    raise ValueError(
                        "alternative scenario splits a compatible hypothesis group"
                    )
        if retained == 0 or leading != 1:
            raise ValueError("interpretation requires exactly one leading and at least one retained scenario")
        for item in (*self.unknowns, *self.contradictions, *self.out_of_scope_records):
            if not set(item.evidence_refs) <= evidence_ids:
                raise ValueError("interpretation record evidence reference does not resolve")
        return self

    @property
    def hypothesis_keys(self) -> Mapping[str, str]:
        return FrozenDict({item.hypothesis_id: item.hypothesis_key for item in self.hypotheses})

    @property
    def hypotheses_by_dimension(self) -> Mapping[str, tuple[InterpretationHypothesis, ...]]:
        return FrozenDict({
            dimension.value: tuple(item for item in self.hypotheses if item.dimension == dimension)
            for dimension in HypothesisDimension
        })

    @property
    def out_of_scope(self) -> bool:
        return bool(self.out_of_scope_records)
