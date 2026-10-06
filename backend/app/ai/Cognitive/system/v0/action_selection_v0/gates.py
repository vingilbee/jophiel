"""The small deterministic six-category hard gate.

The gate answers only whether a grounded candidate is structurally and
systemically permissible.  It never encodes social appropriateness, ordering,
repetition, humor timing, verbosity, or utility.  Every category is evaluated
in fixed order and every applicable violation is retained.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..contracts.actions import (
    ACTION_HARD_GATE_VERSION, COMMITMENT_EFFECT_KEYS, PRIOR_RECORD_SOURCE_KINDS, ActionDefinition,
    ActionGateCategory as C, ActionGateResult, ActionGateStatus, ActionGateViolation, ActionLifecycle,
    ActionTargetType as T, BindingMode, GroundedActionCandidate, ObjectStatus, ParameterValueType,
    RealizationConstraint, evidence_specification_violations,
)
from .models import IndexEntry
from .reference_index import ActionReferenceIndex
from .registry import ActionRegistry

GATE_VERSION = ACTION_HARD_GATE_VERSION
GRANTED = frozenset({"authorized", "granted", "confirmed", "allowed", "not_required"})

COMMITMENT_EFFECTS = COMMITMENT_EFFECT_KEYS
COMPLIANCE_EFFECTS = frozenset({
    "coordination.request_acceptance", "coordination.suggestion_acceptance", "coordination.promise",
})
ASSERTION_EFFECTS = frozenset({
    "information.answer", "information.inform", "information.explanation", "information.elaboration",
    "information.summary", "information.example", "information.justification", "information.risk_disclosure",
    "stance.agreement", "feedback.understanding_expression", "feedback.conversational_validation",
})
AFFECT_ASSERTING_EFFECTS = frozenset({"social_support.comfort", "social_support.sympathy"})
AFFECT_CONSTRAINED_EFFECTS = frozenset({
    "feedback.reflection", "social_support.experience_validation", "elicitation.experience",
    "social_support.comfort", "social_support.sympathy",
})
GUARANTEE_CONSTRAINED_EFFECTS = frozenset({
    "social_support.reassurance", "coordination.offer", "choice_guidance.advice", "coordination.promise",
    "social_support.encouragement",
})
NON_ENDORSING_EFFECTS = frozenset({
    "feedback.receipt", "social_support.experience_validation", "feedback.conversational_validation",
})
COMPLETION_CONSTRAINED_EFFECTS = frozenset({
    "coordination.request_acceptance", "coordination.next_step", "coordination.offer", "coordination.plan_proposal",
})


def _violation(category: C, code: str, subject: str, evidence: tuple[str, ...] = ()) -> ActionGateViolation:
    return ActionGateViolation(category=category, code=code, subject_ref=subject, evidence_refs=tuple(dict.fromkeys(evidence)))


def _constraint(code: str, subject: str, evidence: tuple[str, ...] = ()) -> RealizationConstraint:
    return RealizationConstraint(code=code, subject_ref=subject, evidence_refs=tuple(dict.fromkeys(evidence)))


class HardGate:
    def __init__(self, registry: ActionRegistry) -> None:
        self.registry = registry

    def evaluate(self, candidate: GroundedActionCandidate, index: ActionReferenceIndex) -> ActionGateResult:
        runtime = index.request.runtime
        definition = self.registry.get(candidate.definition_ref)
        violations: list[ActionGateViolation] = []
        constraints: list[RealizationConstraint] = []
        entries = [index.entry(item.target_type, item.ref) for item in candidate.target_refs]
        primary = [entry for item, entry in zip(candidate.target_refs, entries) if item.role in {"primary", "member", "scope"} and entry is not None]
        effect = definition.effect.effect_key if definition else None

        # 1. registry and lifecycle -------------------------------------------------
        if definition is None:
            violations.append(_violation(C.REGISTRY_LIFECYCLE, "unknown_definition_or_version", candidate.definition_ref.identity))
        else:
            if definition.lifecycle in {ActionLifecycle.CANDIDATE, ActionLifecycle.REVIEWED}:
                violations.append(_violation(C.REGISTRY_LIFECYCLE, f"lifecycle_{definition.lifecycle.value}_not_selectable", candidate.definition_ref.identity))
            elif definition.lifecycle in {ActionLifecycle.DEPRECATED, ActionLifecycle.RETIRED}:
                violations.append(_violation(C.REGISTRY_LIFECYCLE, f"lifecycle_{definition.lifecycle.value}_cannot_enter_plans", candidate.definition_ref.identity))
        if candidate.registry_version != self.registry.version:
            violations.append(_violation(C.REGISTRY_LIFECYCLE, "registry_version_mismatch", candidate.definition_ref.identity))

        # 2. schema and reference integrity -----------------------------------------
        for item, entry in zip(candidate.target_refs, entries):
            if entry is None:
                violations.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "target_does_not_resolve", item.ref))
                continue
            if entry.revision_ref != item.revision_ref:
                violations.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "target_revision_mismatch", item.ref))
            if not entry.authorized:
                violations.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "target_not_authorized", item.ref))
            if entry.status.value != "current":
                violations.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, f"target_{entry.status.value}", item.ref))
            elif not index.is_current_for_turn(entry):
                violations.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "target_outside_turn_ownership", item.ref))
        if definition is not None:
            violations.extend(self._schema(definition, candidate, index))
            violations.extend(self._evidence(definition, candidate, index))
        else:
            violations.extend(item for _, item in self._evidence_citations(candidate, index))

        # 3. truth and evidence -----------------------------------------------------
        if effect is not None:
            for entry in primary:
                status = entry.attr("epistemic_status")
                if effect in ASSERTION_EFFECTS and status in {"known_false", "contradicted"}:
                    violations.append(_violation(C.TRUTH_EVIDENCE, "asserts_contradicted_content", entry.ref, entry.attr("contradicted_by_refs", ()) or ()))
                if effect in ASSERTION_EFFECTS and status == "unsupported":
                    violations.append(_violation(C.TRUTH_EVIDENCE, "asserts_unsupported_content", entry.ref))
                if effect in COMPLIANCE_EFFECTS and entry.attr("requires_false_assertion"):
                    violations.append(_violation(C.TRUTH_EVIDENCE, "commits_to_false_assertion", entry.ref, entry.attr("decline_reason_refs", ()) or ()))
                if effect in COMMITMENT_EFFECTS and entry.attr("guarantees_external_outcome"):
                    violations.append(_violation(C.TRUTH_EVIDENCE, "unsupported_outcome_guarantee", entry.ref))
                if effect in AFFECT_ASSERTING_EFFECTS and entry.attr("expression") != "explicit":
                    violations.append(_violation(C.TRUTH_EVIDENCE, "asserts_hidden_affect", entry.ref))
                if effect == "feedback.understanding_expression" and status not in {None, "supported"}:
                    violations.append(_violation(C.TRUTH_EVIDENCE, "confirms_unsupported_interpretation", entry.ref, entry.attr("contradicted_by_refs", ()) or ()))
            constraints.extend(self._constraints(effect, candidate, primary))

        # 4. capability -------------------------------------------------------------
        available = set(runtime.available_capabilities)
        if effect in COMMITMENT_EFFECTS:
            for entry in primary:
                missing = [name for name in entry.attr("required_capabilities", ()) or () if name not in available]
                for name in missing:
                    violations.append(_violation(C.CAPABILITY, "capability_unavailable", entry.ref, (runtime.capability_snapshot_ref,)))
                if effect in COMPLIANCE_EFFECTS and entry.attr("acceptance_status") == "unavailable" and not missing:
                    violations.append(_violation(C.CAPABILITY, "capability_unavailable", entry.ref, (runtime.capability_snapshot_ref,)))

        # 5. authorization, consent, and privacy -------------------------------------
        if effect in COMMITMENT_EFFECTS or effect in ASSERTION_EFFECTS:
            for entry in primary:
                if effect in COMMITMENT_EFFECTS and entry.attr("requires_authorization") and (runtime.authorization or "").lower() not in GRANTED:
                    violations.append(_violation(C.AUTHORIZATION_CONSENT_PRIVACY, "authorization_missing", entry.ref, (runtime.runtime_snapshot_ref,)))
                if effect in COMMITMENT_EFFECTS and entry.attr("acceptance_status") == "unauthorized":
                    violations.append(_violation(C.AUTHORIZATION_CONSENT_PRIVACY, "authorization_denied", entry.ref, (runtime.runtime_snapshot_ref,)))
                if entry.attr("requires_consent_refs") and (runtime.consent or "").lower() not in GRANTED:
                    violations.append(_violation(C.AUTHORIZATION_CONSENT_PRIVACY, "consent_absent", entry.ref, tuple(entry.attr("requires_consent_refs"))))
                if entry.attr("discloses_private_data") and (runtime.external_export_control is not False):
                    violations.append(_violation(C.AUTHORIZATION_CONSENT_PRIVACY, "private_disclosure_not_permitted", entry.ref, (runtime.runtime_snapshot_ref,)))

        # 6. application safety -----------------------------------------------------
        # The closed, versioned decision is consumed exactly as the contract maps
        # it; nothing computed after this gate (features, score, state,
        # personality, goals, prior outcomes) can reach a blocked candidate.
        safety = runtime.safety_basis
        safety_code = safety.violation_code(effect)
        if safety_code is not None:
            violations.append(_violation(C.APPLICATION_SAFETY, safety_code, candidate.definition_ref.identity, (safety.decision_ref,)))
        if effect in COMPLIANCE_EFFECTS or effect in ASSERTION_EFFECTS:
            for entry in primary:
                for policy in entry.attr("safety_policy_refs", ()) or ():
                    violations.append(_violation(C.APPLICATION_SAFETY, "application_policy_prohibits", entry.ref, (policy,)))

        order = {category: index_ for index_, category in enumerate(C)}
        violations.sort(key=lambda item: order[item.category])
        lifecycle = definition.lifecycle if definition else None
        if violations:
            status = ActionGateStatus.BLOCKED
        elif lifecycle == ActionLifecycle.APPROVED:
            status = ActionGateStatus.PASSED
        else:
            status = ActionGateStatus.SHADOW_ONLY
        unique_constraints = {(item.code, item.subject_ref): item for item in constraints}
        return ActionGateResult(
            candidate_ref=candidate.candidate_id, status=status, violations=tuple(violations),
            realization_constraints=tuple(unique_constraints[key] for key in sorted(unique_constraints)),
            gate_version=GATE_VERSION, definition_version=definition.definition_ref.definition_version if definition else None,
            definition_lifecycle=lifecycle, registry_version=self.registry.version, safety=safety,
        )

    # -- schema -----------------------------------------------------------------
    def _schema(self, definition: ActionDefinition, candidate: GroundedActionCandidate, index: ActionReferenceIndex) -> list[ActionGateViolation]:
        out: list[ActionGateViolation] = []
        effect = definition.effect
        roles = effect.target_roles
        for item in candidate.target_refs:
            if item.role not in roles or item.target_type not in roles[item.role]:
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "target_type_not_accepted", item.ref))
        principal_roles = {"primary", "member", "scope", "source", "destination"}
        count = sum(1 for item in candidate.target_refs if item.role in principal_roles)
        schema = definition.target_schema
        if effect.binding_mode in {BindingMode.PRIMARY_TARGET, BindingMode.PRIMARY_WITH_SUPPORT}:
            if sum(1 for item in candidate.target_refs if item.role == "primary") != 1:
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "primary_target_cardinality", candidate.candidate_id))
        elif effect.binding_mode == BindingMode.DIALOGUE_SCOPE:
            if sum(1 for item in candidate.target_refs if item.role == "scope") != 1:
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "dialogue_scope_cardinality", candidate.candidate_id))
        elif count < max(1, schema.minimum_targets) or (schema.maximum_targets is not None and count > schema.maximum_targets):
            out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "target_cardinality", candidate.candidate_id))
        out.extend(self._parameters(definition, candidate.parameters, index, candidate.candidate_id))
        return out

    # -- evidence ---------------------------------------------------------------
    @staticmethod
    def _evidence_citations(candidate: GroundedActionCandidate, index: ActionReferenceIndex) -> list[tuple[tuple[str, str, str], ActionGateViolation]]:
        """Per-citation integrity: resolution, typing, authorization, freshness, turn boundary."""

        out: list[tuple[tuple[str, str, str], ActionGateViolation]] = []

        seen: set[tuple[tuple[str, str, str], str]] = set()

        def reject(evidence, code: str) -> None:
            if (evidence.sort_key, code) not in seen:
                seen.add((evidence.sort_key, code))
                out.append((evidence.sort_key, _violation(C.SCHEMA_REFERENCE_INTEGRITY, code, evidence.ref)))

        for evidence in candidate.supporting_evidence_refs:
            if not index.resolves(evidence.ref):
                reject(evidence, "evidence_does_not_resolve")
                continue
            if index.source_kind(evidence.ref) != evidence.source_kind:
                reject(evidence, "evidence_source_kind_mismatch")
                continue
            if index.is_non_current_ref(evidence.ref):
                reject(evidence, "evidence_not_current")
            for entry in index.entries_for_ref(evidence.ref):
                if not entry.authorized:
                    reject(evidence, "evidence_not_authorized")
                if entry.status == ObjectStatus.SUPERSEDED:
                    reject(evidence, "evidence_superseded")
                elif entry.status == ObjectStatus.DELETED:
                    reject(evidence, "evidence_deleted")
                prior = entry.source_kind in PRIOR_RECORD_SOURCE_KINDS
                if prior == (entry.cognitive_turn_ref == index.turn):
                    reject(evidence, "evidence_outside_turn_boundary")
        return out

    def _evidence(self, definition: ActionDefinition, candidate: GroundedActionCandidate, index: ActionReferenceIndex) -> list[ActionGateViolation]:
        citations = self._evidence_citations(candidate, index)
        rejected = frozenset(key for key, _ in citations)
        out = [item for _, item in citations]
        for code, subject in evidence_specification_violations(
            definition.evidence_specification, candidate.supporting_evidence_refs, candidate.candidate_id, rejected,
        ):
            out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, code, subject))
        return out

    def _parameters(self, definition: ActionDefinition, parameters: Mapping[str, Any], index: ActionReferenceIndex, subject: str) -> list[ActionGateViolation]:
        out: list[ActionGateViolation] = []
        rules = {item.parameter_key: item for item in definition.parameter_schema}
        for key in parameters:
            if key not in rules:
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "unknown_parameter", f"{subject}#{key}"))
        for key, rule in rules.items():
            present = key in parameters
            required = rule.required or (rule.required_when is not None and all(parameters.get(k) == v for k, v in rule.required_when.items()))
            if required and not present:
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "missing_required_parameter", f"{subject}#{key}"))
                continue
            if not present:
                continue
            value = parameters[key]
            if rule.value_type == ParameterValueType.ENUM and value not in rule.allowed_values:
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "parameter_value_not_allowed", f"{subject}#{key}"))
            elif rule.value_type == ParameterValueType.NUMBER and (type(value) not in {int, float} or not math.isfinite(value)):
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "parameter_not_finite_number", f"{subject}#{key}"))
            elif rule.value_type in {ParameterValueType.REFERENCE, ParameterValueType.REFERENCE_ARRAY}:
                values = [value] if rule.value_type == ParameterValueType.REFERENCE else value
                if not isinstance(values, (list, tuple)) or not values or any(not isinstance(item, str) for item in values):
                    out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "reference_parameter_malformed", f"{subject}#{key}"))
                    continue
                for ref in values:
                    typed = [entry for entry in index.entries_for_ref(ref) if entry.target_type in rule.reference_target_types]
                    if not typed:
                        out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "reference_parameter_unresolved", ref))
        if parameters.get("question_form") == "choice" and len(parameters.get("candidate_answer_refs", ())) < 2:
            out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "choice_requires_two_candidate_answers", subject))
        if parameters.get("question_form") == "scale":
            low, high = parameters.get("scale_min"), parameters.get("scale_max")
            if not (type(low) in {int, float} and type(high) in {int, float} and math.isfinite(low) and math.isfinite(high) and low < high):
                out.append(_violation(C.SCHEMA_REFERENCE_INTEGRITY, "scale_requires_ordered_finite_bounds", subject))
        return out

    # -- realization constraints -------------------------------------------------
    @staticmethod
    def _constraints(effect: str, candidate: GroundedActionCandidate, primary: list[IndexEntry]) -> list[RealizationConstraint]:
        out: list[RealizationConstraint] = []
        for entry in primary:
            tentative = entry.attr("epistemic_status") in {"tentative", "unknown"} or entry.attr("support") in {"weak", "moderate"}
            if tentative or effect in {"information.uncertainty_qualification", "elicitation.understanding_check", "feedback.paraphrase"}:
                out.append(_constraint("preserve_uncertainty", entry.ref))
            if effect in AFFECT_CONSTRAINED_EFFECTS:
                out.append(_constraint("forbid_hidden_affect_assertion", entry.ref))
            if effect in GUARANTEE_CONSTRAINED_EFFECTS:
                out.append(_constraint("forbid_unsupported_guarantee", entry.ref))
            if effect in NON_ENDORSING_EFFECTS:
                out.append(_constraint("forbid_factual_endorsement", entry.ref))
            if effect in COMPLETION_CONSTRAINED_EFFECTS:
                out.append(_constraint("forbid_completion_claim", entry.ref))
            if effect.startswith("social_play."):
                out.append(_constraint("preserve_factual_frame_boundary", entry.ref))
            if entry.target_type in {T.PRIOR_REALIZATION, T.PRIOR_ACTION} and entry.attr("correction_status") == "explicitly_corrected":
                out.append(_constraint("forbid_reasserting_corrected_content", entry.ref))
        return out


def is_live_selectable(result: ActionGateResult) -> bool:
    """A live boundary accepts only approved definitions that passed every gate."""

    return result.eligible_live


__all__ = ["GATE_VERSION", "HardGate", "is_live_selectable"]
