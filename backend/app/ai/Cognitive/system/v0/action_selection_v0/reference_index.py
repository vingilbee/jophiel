"""One deterministic, typed reference index built from the exact request.

The index copies structural facts, never semantic prose: types come from the
canonical object an entry was built from, not from identifier spelling.  Every
grounded-object, descriptor, and prior-record reference must resolve here.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts.actions import (
    ActionGroundedObject,
    ActionSelectionRequest,
    ObjectStatus,
)
from ..contracts.actions import (
    ActionTargetType as T,
)
from ..contracts.actions import (
    ReferenceSourceKind as S,
)
from ..contracts.goals import GoalStatus
from ..contracts.interpretation import (
    HypothesisDimension,
    HypothesisDisposition,
    HypothesisSupport,
)
from .models import IndexEntry, frozen_mapping

_GROUNDED_REFERENCE_FIELDS = (
    "source_refs", "about_refs", "basis_refs", "contradicted_by_refs", "replacement_refs",
    "explanation_basis_refs", "example_refs", "answer_content_refs", "decline_reason_refs",
    "requires_consent_refs", "step_refs", "option_refs", "candidate_answer_refs", "risk_basis_refs",
)
_DESCRIPTOR_REFERENCE_FIELDS = (
    "competing_candidate_refs", "candidate_answer_refs", "tentative_value_refs",
    "explanation_basis_refs", "about_refs",
)


def argument_ref(frame_id: str, argument: str) -> str:
    return f"{frame_id}#{argument}"


class ActionReferenceIndex:
    def __init__(self, request: ActionSelectionRequest) -> None:
        self.request = request
        self.turn = request.cognitive_turn_id
        self.focus_message_id = request.interpretation.focus_message_id
        self._entries: dict[tuple[str, str], IndexEntry] = {}
        self._refs: dict[str, S] = {}
        # Resolvable references that are not current (e.g. rejected hypotheses):
        # citable as lineage, never as evidence.
        self._non_current: set[str] = set()
        self._build()

    # -- registration -------------------------------------------------------
    def _ref(self, ref: str, kind: S) -> None:
        if not ref or not ref.strip():
            raise ValueError("index references must not be blank")
        previous = self._refs.get(ref)
        if previous is not None and previous != kind:
            raise ValueError("one reference cannot belong to two source kinds")
        self._refs[ref] = kind

    def _add(self, entry: IndexEntry) -> None:
        if entry.key in self._entries:
            raise ValueError("duplicate typed reference in the action index")
        self._entries[entry.key] = entry
        self._ref(entry.ref, entry.source_kind)

    def _replace(self, entry: IndexEntry) -> None:
        self._entries[entry.key] = entry

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        request = self.request
        interpretation = request.interpretation
        turn = self.turn
        self._ref(interpretation.interpretation_trace_id, S.INTERPRETATION)
        for message_id in interpretation.input_message_ids:
            self._add(IndexEntry(T.MESSAGE, message_id, S.INTERPRETATION, turn,
                                 focus_contribution=message_id == self.focus_message_id,
                                 attrs=frozen_mapping({"is_focus": message_id == self.focus_message_id})))
        for item in interpretation.evidence:
            self._ref(item.evidence_id, S.INTERPRETATION)
        for item in interpretation.scenarios:
            self._ref(item.scenario_id, S.INTERPRETATION)
        for item in interpretation.out_of_scope_records:
            self._ref(item.out_of_scope_id, S.INTERPRETATION)
        for frame in interpretation.semantic_frames:
            self._add(IndexEntry(T.SEMANTIC_FRAME, frame.frame_id, S.INTERPRETATION, turn,
                                 source_refs=frame.evidence_refs,
                                 attrs=frozen_mapping({"polarity": frame.polarity, "unresolved": frame.unresolved_arguments})))
            for name in frame.unresolved_arguments:
                self._add(IndexEntry(T.SEMANTIC_ARGUMENT, argument_ref(frame.frame_id, name), S.INTERPRETATION, turn,
                                     about_refs=(frame.frame_id,), source_refs=frame.evidence_refs,
                                     attrs=frozen_mapping({"unresolved": True, "uncertainty_refs": (argument_ref(frame.frame_id, name),)})))
        for hypothesis in interpretation.hypotheses:
            if hypothesis.disposition == HypothesisDisposition.REJECTED:
                self._ref(hypothesis.hypothesis_id, S.INTERPRETATION)
                self._non_current.add(hypothesis.hypothesis_id)
                continue
            attrs = {
                "hypothesis_key": hypothesis.hypothesis_key, "dimension": hypothesis.dimension.value,
                "support": hypothesis.support.value, "disposition": hypothesis.disposition.value,
                "asserted_by": "columbina",
            }
            self._add(IndexEntry(T.INTERPRETATION_HYPOTHESIS, hypothesis.hypothesis_id, S.INTERPRETATION, turn,
                                 about_refs=hypothesis.semantic_frame_refs, source_refs=hypothesis.supporting_evidence_refs,
                                 attrs=frozen_mapping(attrs)))
            retained = hypothesis.disposition in {HypothesisDisposition.LEADING, HypothesisDisposition.RETAINED_ALTERNATIVE}
            if hypothesis.dimension == HypothesisDimension.COMMUNICATIVE_ACT and retained and hypothesis.support != HypothesisSupport.UNSUPPORTED:
                self._entries[(T.USER_ACT.value, hypothesis.hypothesis_id)] = IndexEntry(
                    T.USER_ACT, hypothesis.hypothesis_id, S.INTERPRETATION, turn,
                    about_refs=hypothesis.semantic_frame_refs, source_refs=hypothesis.supporting_evidence_refs,
                    focus_contribution=True, attrs=frozen_mapping({**attrs, "act_key": hypothesis.hypothesis_key}),
                )
        for unknown in interpretation.unknowns:
            self._add(IndexEntry(T.UNKNOWN, unknown.unknown_id, S.INTERPRETATION, turn, source_refs=unknown.evidence_refs,
                                 attrs=frozen_mapping({"owner": unknown.owner, "unresolved": True,
                                                       "uncertainty_refs": (unknown.unknown_id,)})))
        for contradiction in interpretation.contradictions:
            self._add(IndexEntry(T.CONTRADICTION, contradiction.contradiction_id, S.INTERPRETATION, turn,
                                 source_refs=contradiction.evidence_refs,
                                 attrs=frozen_mapping({"status": contradiction.status})))
        goals = request.goal_snapshot
        self._ref(goals.goal_snapshot_id, S.GOAL)
        focal = set(goals.focal_goal_refs)
        for record in goals.records:
            for requirement in record.requirements:
                self._ref(requirement.requirement_id, S.GOAL)
            if record.status != GoalStatus.ACTIVE:
                self._ref(record.goal_id, S.GOAL)
                continue
            self._add(IndexEntry(T.GOAL, record.goal_id, S.GOAL, turn,
                                 about_refs=tuple(target.ref for req in record.requirements for target in req.target_refs),
                                 attrs=frozen_mapping({"priority_band": record.priority_band.value,
                                                       "definition_key": record.definition_ref.definition_key,
                                                       "focal": record.goal_id in focal})))
        for assessment in goals.requirement_assessments:
            self._ref(assessment.assessment_id, S.GOAL)
        appraisal = request.appraisal
        self._ref(appraisal.appraisal_trace_id, S.APPRAISAL)
        for frame in appraisal.frames:
            self._ref(frame.frame_id, S.APPRAISAL)
            for dimension in frame.dimensions:
                self._add(IndexEntry(T.APPRAISAL_FIELD, f"{frame.frame_id}#{dimension.dimension_key}", S.APPRAISAL, turn,
                                     about_refs=(frame.subject.focal_goal_ref,),
                                     attrs=frozen_mapping({"dimension_key": dimension.dimension_key,
                                                           "status": dimension.status.value, "value": dimension.value})))
        state = request.dynamic_state
        self._ref(state.state_snapshot_id, S.DYNAMIC_STATE)
        for value in state.values:
            ref = f"{state.state_snapshot_id}#{value.state_key.value}#{value.scope_type.value}#{value.scope_ref}"
            self._add(IndexEntry(T.DYNAMIC_STATE_FIELD, ref, S.DYNAMIC_STATE, turn, about_refs=(value.scope_ref,),
                                 attrs=frozen_mapping({"state_key": value.state_key.value, "status": value.status.value,
                                                       "band": value.band.value if value.band else None})))
        for item in state.attention:
            self._ref(self.attention_ref(item.target_type.value, item.target_ref), S.DYNAMIC_STATE)
        if state.derived_controls is not None:
            for name in type(state.derived_controls).model_fields:
                self._ref(self.control_ref(name), S.DYNAMIC_STATE)
        runtime = request.runtime
        for ref in (runtime.runtime_snapshot_ref, runtime.capability_snapshot_ref, runtime.response_obligation_ref,
                    runtime.task_ref, runtime.last_tool_event_ref):
            if ref is not None:
                self._ref(ref, S.RUNTIME)
        for record in runtime.policy_records:
            self._ref(record, S.RUNTIME)
        # The application safety decision (or the record of its absence) is a
        # trusted runtime fact every gate result cites, as is its producer.
        self._ref(self.safety_decision_ref, S.RUNTIME)
        if runtime.safety_decision is not None:
            self._ref(runtime.safety_decision.producer_ref, S.RUNTIME)
        for name in runtime.available_capabilities:
            self._add(IndexEntry(T.CAPABILITY, self.capability_ref(name), S.RUNTIME, turn,
                                 attrs=frozen_mapping({"capability": name, "available": True})))
        self._add(IndexEntry(T.DIALOGUE_SCOPE, request.conversation_ref, S.RUNTIME, turn,
                             attrs=frozen_mapping({"conversation_status": runtime.conversation_status})))
        for obj in request.grounded_objects:
            self._add(self._grounded(obj))
        for record in request.prior_action_records:
            self._add(IndexEntry(T.PRIOR_ACTION, record.record_id, S.PRIOR_ACTION, record.cognitive_turn_ref,
                                 status=record.status, authorized=record.authorized, about_refs=record.about_refs,
                                 attrs=frozen_mapping({"action_key": record.definition_ref.action_key,
                                                       "definition_version": record.definition_ref.definition_version,
                                                       "target_keys": tuple(item.sort_key for item in record.target_refs),
                                                       "failure_kind": record.failure_kind,
                                                       "correction_status": record.correction_status,
                                                       "asserted_by": "columbina"})))
        for record in request.prior_realization_records:
            self._add(IndexEntry(T.PRIOR_REALIZATION, record.record_id, S.PRIOR_REALIZATION, record.cognitive_turn_ref,
                                 status=record.status, authorized=record.authorized,
                                 about_refs=(*record.about_refs, *record.asserted_refs),
                                 source_refs=tuple(ref for ref in (record.prior_action_ref, record.message_ref) if ref),
                                 attrs=frozen_mapping({"fidelity": record.fidelity, "asserted_refs": record.asserted_refs,
                                                       "correction_status": record.correction_status,
                                                       "failure_kind": "realization" if record.fidelity == "unfaithful" else None,
                                                       "asserted_by": "columbina"})))
        for outcome in request.observed_outcomes:
            self._add(IndexEntry(T.OBSERVED_OUTCOME, outcome.outcome_id, S.OBSERVED_OUTCOME, turn,
                                 authorized=outcome.authorized, about_refs=(outcome.subject_ref, *outcome.about_refs),
                                 source_refs=outcome.evidence_refs, focus_contribution=outcome.focus_contribution,
                                 attrs=frozen_mapping({"dimension": outcome.dimension, "assessment": outcome.assessment,
                                                       "subject_ref": outcome.subject_ref,
                                                       "outcome_assessment": outcome.assessment})))
        for descriptor in request.target_descriptors:
            self._describe(descriptor)
        self._validate_references()

    def _grounded(self, obj: ActionGroundedObject) -> IndexEntry:
        data = obj.model_dump(mode="python")
        for name in ("object_id", "object_type", "revision_ref", "status", "authorized", "cognitive_turn_id",
                     "source_refs", "about_refs", "focus_contribution"):
            data.pop(name)
        # A projected user-owned unknown is its own uncertainty (contract v0.4).
        data["uncertainty_refs"] = (obj.object_id,) if obj.unresolved else ()
        return IndexEntry(obj.object_type, obj.object_id, S.GROUNDED_OBJECT, obj.cognitive_turn_id,
                          revision_ref=obj.revision_ref, status=obj.status, authorized=obj.authorized,
                          about_refs=obj.about_refs, source_refs=obj.source_refs,
                          focus_contribution=obj.focus_contribution, attrs=frozen_mapping(data))

    def _describe(self, descriptor) -> None:
        entry = self._entries.get((descriptor.target_type.value, descriptor.ref))
        if entry is None:
            raise ValueError("target descriptor does not describe an indexed object")
        attrs = dict(entry.attrs)
        for name in ("owner", "unknown_kind", "represents_ref", "scale_min", "scale_max"):
            value = getattr(descriptor, name)
            if value is not None:
                attrs[name] = value
        for name in ("competing_candidate_refs", "candidate_answer_refs", "tentative_value_refs", "explanation_basis_refs"):
            value = getattr(descriptor, name)
            if value:
                attrs[name] = tuple(value)
        uncertainty = tuple(dict.fromkeys((*attrs.get("uncertainty_refs", ()), *(
            (descriptor.represents_ref,) if descriptor.represents_ref else ()))))
        attrs["uncertainty_refs"] = uncertainty
        self._replace(IndexEntry(entry.target_type, entry.ref, entry.source_kind, entry.cognitive_turn_ref,
                                 revision_ref=entry.revision_ref, status=entry.status, authorized=entry.authorized,
                                 about_refs=tuple(dict.fromkeys((*entry.about_refs, *descriptor.about_refs))),
                                 source_refs=entry.source_refs, focus_contribution=entry.focus_contribution,
                                 attrs=frozen_mapping(attrs)))

    def _validate_references(self) -> None:
        request = self.request
        for obj in request.grounded_objects:
            for name in _GROUNDED_REFERENCE_FIELDS:
                self._require(getattr(obj, name))
            if obj.pursues_ref is not None:
                self._require((obj.pursues_ref,))
            if obj.safety_policy_refs and not set(obj.safety_policy_refs) <= set(request.runtime.policy_records):
                raise ValueError("grounded safety policy refs must be trusted runtime policy records")
        for descriptor in request.target_descriptors:
            for name in _DESCRIPTOR_REFERENCE_FIELDS:
                self._require(getattr(descriptor, name))
            if descriptor.represents_ref is not None:
                self._require((descriptor.represents_ref,))
        messages = set(request.interpretation.input_message_ids)
        for record in request.prior_realization_records:
            if record.message_ref not in messages:
                raise ValueError("prior realization cites a message outside the bounded input")
            self._require(record.about_refs); self._require(record.asserted_refs)
            if record.prior_action_ref is not None and record.prior_action_ref not in {item.record_id for item in request.prior_action_records}:
                raise ValueError("prior realization cites an unknown prior action")
        prior_ids = {item.record_id for item in (*request.prior_action_records, *request.prior_realization_records)}
        grounded_ids = {item.object_id for item in request.grounded_objects}
        for record in request.prior_action_records:
            self._require(record.about_refs)
        for outcome in request.observed_outcomes:
            if outcome.subject_ref not in prior_ids | grounded_ids:
                raise ValueError("observed outcome must cite a prior record or grounded object")
            self._require(outcome.evidence_refs); self._require(outcome.about_refs)
        handoff = request.requirement_handoff
        if handoff is not None:
            # Every object a resolution was projected into, and every blocker a
            # move must target, is an exact typed index reference.
            for binding in handoff.bindings:
                self._require(binding.target_refs); self._require(binding.object_refs)
                self._require(binding.descriptor_refs)
                if not set(binding.object_refs) <= grounded_ids:
                    raise ValueError("a requirement binding names an object the request does not carry")

    def _require(self, refs: Iterable[str]) -> None:
        for ref in refs:
            if ref not in self._refs:
                raise ValueError("action reference does not resolve in the typed index")

    # -- queries ------------------------------------------------------------
    @staticmethod
    def attention_ref(target_type: str, target_ref: str) -> str:
        return f"attention#{target_type}#{target_ref}"

    def control_ref(self, name: str) -> str:
        return f"{self.request.dynamic_state.state_snapshot_id}#control#{name}"

    @property
    def safety_decision_ref(self) -> str:
        return self.request.runtime.safety_basis.decision_ref

    def capability_ref(self, name: str) -> str:
        return f"{self.request.runtime.capability_snapshot_ref}#{name}"

    def entry(self, target_type: T, ref: str) -> IndexEntry | None:
        return self._entries.get((target_type.value, ref))

    def entries_of(self, *target_types: T) -> tuple[IndexEntry, ...]:
        wanted = {item.value for item in target_types}
        return tuple(self._entries[key] for key in sorted(self._entries) if key[0] in wanted)

    def entries_for_ref(self, ref: str) -> tuple[IndexEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries) if key[1] == ref)

    def resolves(self, ref: str) -> bool:
        return ref in self._refs

    def source_kind(self, ref: str) -> S:
        return self._refs[ref]

    def is_non_current_ref(self, ref: str) -> bool:
        return ref in self._non_current

    @property
    def entries(self) -> tuple[IndexEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def related_refs(self, ref: str) -> frozenset[str]:
        """The object itself plus one explicit hop of about/source lineage."""

        related = {ref}
        for entry in self.entries_for_ref(ref):
            related.update(entry.about_refs); related.update(entry.source_refs)
        return frozenset(related)

    def relates(self, ref: str, requirement_target: str) -> bool:
        if ref == requirement_target:
            return True
        if requirement_target in self.related_refs(ref):
            return True
        return ref in self.related_refs(requirement_target)

    def uptake_members(self) -> frozenset[str]:
        """Objects that constitute the focus contribution (not merely what it mentions)."""

        members = {entry.ref for entry in self.entries if entry.focus_contribution and entry.current}
        members.discard(self.focus_message_id)
        return frozenset(members)

    def is_current_for_turn(self, entry: IndexEntry) -> bool:
        if not entry.current:
            return False
        if entry.source_kind in {S.PRIOR_ACTION, S.PRIOR_REALIZATION}:
            return entry.cognitive_turn_ref != self.turn
        return entry.cognitive_turn_ref == self.turn


def build_index(request: ActionSelectionRequest) -> ActionReferenceIndex:
    return ActionReferenceIndex(request)


__all__ = ["ActionReferenceIndex", "argument_ref", "build_index", "ObjectStatus"]
