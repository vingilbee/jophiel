"""Inspectible V0 appraisal productions.

The predicate sets below are a deliberately small registry, not sentiment
heuristics.  An unregistered or under-specified event yields ``unknown`` where
the operational question cannot be answered.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    ActiveGoal, AppraisalDimension, AppraisalRequest, DimensionStatus,
    InterpretationScenario, SemanticFrame,
)

DIMENSION_TYPES = {
    "appraisal.goal_relevance": "unsigned_ordinal",
    "appraisal.goal_congruence": "signed_ordinal",
    "appraisal.novelty": "unsigned_ordinal",
    "appraisal.certainty": "unsigned_ordinal",
    "appraisal.controllability": "unsigned_ordinal",
    "appraisal.norm_compatibility": "signed_ordinal",
    "appraisal.affiliative_value": "signed_ordinal",
    "appraisal.agency": "agency_category",
}

FAILURES = {"export_failure", "deployment_failure", "upload_failure"}
CORRECTIONS = {"correct_schedule_date", "correct_assistant_date"}
USER_EVENTS = {
    "explain_estimate_change", "correct_schedule_date", "capability_query", "send_report",
    "delete_backup", "delete_customer_records", "share_photo", "send_item",
    "repeat_review_routine",
}
ENVIRONMENT_EVENTS = {
    "vendor_absence", "export_failure", "deployment_failure", "upload_failure",
    "service_code", "alert_code",
}
THIRD_PARTY_EVENTS = {"report_quoted_request", "deadline_change"}
UNKNOWN_AGENCY_EVENTS = {"checksum_value", "status_page_color", "export_success"}
REGISTERED_EVENTS = (
    FAILURES | CORRECTIONS | USER_EVENTS | ENVIRONMENT_EVENTS | THIRD_PARTY_EVENTS
    | UNKNOWN_AGENCY_EVENTS | {"evaluate_prior_action", "retry_export", "acknowledgement_token"}
)


@dataclass(frozen=True)
class RuleContext:
    request: AppraisalRequest
    scenario: InterpretationScenario
    goal: ActiveGoal
    frames: tuple[SemanticFrame, ...]

    @property
    def predicates(self) -> set[str]:
        return {frame.predicate for frame in self.frames}

    @property
    def unknown_keys(self) -> set[str]:
        ids = set(self.scenario.unknown_refs)
        unresolved = {
            item.key for item in self.request.interpretation.unknowns
            if item.unknown_id in ids and item.status == "unresolved"
        }
        resolved = {
            key for item in self.supplemental_inputs for key in item.resolved_arguments
        }
        if any(item.authorization == "confirmed" for item in self.supplemental_inputs):
            resolved.add("authorization")
        if any(
            item.input_type == "observed_outcome" and item.outcome
            for item in self.supplemental_inputs
        ):
            resolved.add("goal_congruence")
        return unresolved - resolved

    @property
    def contradictions(self) -> tuple[object, ...]:
        ids = set(self.scenario.contradiction_refs)
        return tuple(
            item for item in self.request.interpretation.contradictions
            if item.contradiction_id in ids and item.status == "active"
        )

    @property
    def hypothesis_keys(self) -> set[str]:
        ids = {ref for refs in self.scenario.hypothesis_refs.values() for ref in refs}
        return {key for ref, key in self.request.interpretation.hypothesis_keys.items() if ref in ids}

    @property
    def supplemental_ids(self) -> tuple[str, ...]:
        return tuple(item.input_id for item in self.supplemental_inputs)

    @property
    def supplemental_inputs(self) -> tuple[object, ...]:
        event_refs = {item.frame_id for item in self.frames} | self.predicates
        proposition_refs = {
            ref for frame in self.frames
            for ref in _argument_refs(frame.arguments, "proposition_ref", "proposition_refs")
        }
        return tuple(
            item for item in self.request.supplemental_inputs
            if (not item.scenario_refs or self.scenario.scenario_id in item.scenario_refs)
            and (not item.goal_refs or self.goal.goal_id in item.goal_refs)
            and (not item.event_refs or bool(set(item.event_refs) & event_refs))
            and (not item.proposition_refs or bool(set(item.proposition_refs) & proposition_refs))
        )

    def supplemental(self, kind: str) -> tuple[object, ...]:
        return tuple(item for item in self.supplemental_inputs if item.input_type == kind)

    @property
    def registered(self) -> bool:
        return bool(self.predicates) and self.predicates <= REGISTERED_EVENTS

    @property
    def base_evidence(self) -> tuple[str, ...]:
        return (self.scenario.scenario_id, self.goal.goal_id, *(item.frame_id for item in self.frames))


def _argument_refs(arguments: object, singular: str, plural: str) -> tuple[str, ...]:
    if not isinstance(arguments, dict):
        return ()
    refs: list[str] = []
    value = arguments.get(singular)
    if isinstance(value, str):
        refs.append(value)
    values = arguments.get(plural, ())
    if isinstance(values, (list, tuple)):
        refs.extend(item for item in values if isinstance(item, str))
    return tuple(refs)


def _unregistered(ctx: RuleContext, key: str) -> AppraisalDimension | None:
    if not ctx.registered:
        return _unknown(ctx, key, "registered_event_semantics")
    return None


def _desired(ctx: RuleContext, *keys: str) -> bool:
    return any(ctx.goal.desired_state.get(key) is True for key in keys)


def _known(ctx: RuleContext, key: str, value: str, rule: str, *extra: str) -> AppraisalDimension:
    return AppraisalDimension(
        dimension_key=key, status=DimensionStatus.KNOWN, value_type=DIMENSION_TYPES[key],
        value=value, derivation_strength="strong", rule_id=rule,
        evidence_refs=(*ctx.base_evidence, *extra),
        required_input_refs=(ctx.scenario.scenario_id, ctx.goal.goal_id),
    )


def _unknown(ctx: RuleContext, key: str, *missing: str) -> AppraisalDimension:
    return AppraisalDimension(
        dimension_key=key, status=DimensionStatus.UNKNOWN, value_type=DIMENSION_TYPES[key],
        evidence_refs=ctx.base_evidence, required_input_refs=(ctx.scenario.scenario_id, ctx.goal.goal_id),
        missing_input_keys=tuple(missing),
    )


def goal_relevance(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.goal_relevance"):
        return unknown
    if "response_meaning" in ctx.unknown_keys:
        return _unknown(ctx, "appraisal.goal_relevance", "response_meaning")
    if ctx.predicates & {"vendor_absence"}:
        return _known(ctx, "appraisal.goal_relevance", "low", "goal-relevance.weak-relation.v1")
    if ctx.predicates & {"report_quoted_request"} or (
        ctx.predicates & {"export_failure"} and _desired(ctx, "invoice_export_recovered")
    ):
        return _known(ctx, "appraisal.goal_relevance", "moderate", "goal-relevance.indirect-effect.v1")
    return _known(ctx, "appraisal.goal_relevance", "high", "goal-relevance.direct-effect.v1")


def goal_congruence(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.goal_congruence"):
        return unknown
    supplemental = ctx.supplemental_inputs
    if any(item.outcome == "export_succeeded" for item in supplemental):
        return _known(ctx, "appraisal.goal_congruence", "positive", "goal-congruence.advances-goal.v1", *ctx.supplemental_ids)
    if any(item.input_type == "runtime_fact" and item.value == "failed" for item in supplemental):
        return _known(ctx, "appraisal.goal_congruence", "negative", "goal-congruence.obstructs-goal.v1", *ctx.supplemental_ids)
    missing = ctx.unknown_keys & {"effect_on_goal", "goal_congruence", "consent", "recipient", "object", "authorization", "response_meaning", "trusted_alert_meaning"}
    if len(ctx.request.interpretation.scenarios) > 1:
        missing -= {"recipient", "object", "authorization"}
    if missing and not (ctx.predicates & {"delete_backup", "delete_customer_records"}):
        return _unknown(ctx, "appraisal.goal_congruence", *sorted(missing))
    if ctx.predicates & FAILURES:
        value = "positive" if _desired(ctx, "failure_explained") else "negative"
    elif ctx.predicates & {"delete_backup", "delete_customer_records"}:
        value = "negative"
    elif ctx.predicates & (CORRECTIONS | {"explain_estimate_change", "export_success", "repeat_review_routine", "checksum_value"}):
        value = "positive"
    else:
        value = "neutral"
    rule = {"positive": "goal-congruence.advances-goal.v1", "negative": "goal-congruence.obstructs-goal.v1", "neutral": "goal-congruence.no-material-effect.v1"}[value]
    return _known(ctx, "appraisal.goal_congruence", value, rule)


def novelty(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.novelty"):
        return unknown
    if any(item.input_type == "runtime_fact" for item in ctx.supplemental_inputs):
        return _known(ctx, "appraisal.novelty", "moderate", "novelty.known-type-changed-bindings.v1", *ctx.supplemental_ids)
    if any(
        item.input_type == "memory_record"
        and item.resolved_arguments.keys() & {"relevant_memory", "trusted_alert_meaning"}
        for item in ctx.supplemental_inputs
    ):
        memory_refs = tuple(item.memory_id for item in ctx.request.memories)
        return _known(ctx, "appraisal.novelty", "low", "novelty.expected-or-repeated.v1", *ctx.supplemental_ids, *memory_refs)
    if "relevant_memory" in ctx.unknown_keys:
        return _unknown(ctx, "appraisal.novelty", "relevant_memory")
    if ctx.predicates & {"vendor_absence", "alert_code"}:
        value, rule = "high", "novelty.materially-unexpected.v1"
    elif ctx.predicates & (FAILURES | CORRECTIONS | {"deadline_change", "delete_customer_records"}):
        repeated = any(frame.arguments.get("repeat") is True for frame in ctx.frames)
        value = "low" if repeated else "moderate"
        rule = "novelty.expected-or-repeated.v1" if repeated else "novelty.known-type-changed-bindings.v1"
    else:
        value, rule = "low", "novelty.expected-or-repeated.v1"
    return _known(ctx, "appraisal.novelty", value, rule)


def certainty(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.certainty"):
        return unknown
    if any(
        (
            item.input_type == "memory_record"
            and item.resolved_arguments.keys() & {"relevant_memory", "trusted_alert_meaning"}
        )
        or (item.input_type == "user_message" and bool(item.resolved_arguments))
        for item in ctx.supplemental_inputs
    ):
        return _known(ctx, "appraisal.certainty", "high", "certainty.grounded-unopposed.v1", *ctx.supplemental_ids)
    if ctx.request.relationship and ctx.request.relationship.status == "stale_conflicting":
        return _known(ctx, "appraisal.certainty", "low", "certainty.material-conflict-or-missing.v1")
    if "trusted_alert_meaning" in ctx.unknown_keys:
        return _known(ctx, "appraisal.certainty", "low", "certainty.material-conflict-or-missing.v1")
    if len(ctx.request.interpretation.scenarios) > 1:
        value = "low" if ctx.predicates & {"delete_backup"} else "moderate"
        rule = "certainty.material-conflict-or-missing.v1" if value == "low" else "certainty.leading-with-weaker-alternatives.v1"
        return _known(ctx, "appraisal.certainty", value, rule)
    if ctx.unknown_keys & {"object", "recipient", "authorization"} and ctx.predicates & {"send_item"}:
        return _known(ctx, "appraisal.certainty", "low", "certainty.material-conflict-or-missing.v1")
    if ctx.unknown_keys & {"effect_on_goal", "response_meaning"}:
        return _known(ctx, "appraisal.certainty", "moderate", "certainty.leading-with-weaker-alternatives.v1")
    return _known(ctx, "appraisal.certainty", "high", "certainty.grounded-unopposed.v1")


def controllability(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.controllability"):
        return unknown
    runtime = ctx.request.runtime
    capability = runtime.capability_snapshot.snapshot_id
    if any(item.outcome == "export_succeeded" for item in ctx.supplemental_inputs):
        return _known(ctx, "appraisal.controllability", "high", "controllability.eligible-effective-response.v1", *ctx.supplemental_ids, capability)
    resolved_parameters = {
        key for item in ctx.supplemental_inputs for key in item.resolved_arguments
    }
    provided_parameters = set(runtime.provided_parameters) | resolved_parameters
    missing_parameters = set(runtime.required_parameters) - provided_parameters
    confirmed_authorization = runtime.authorization == "confirmed" or any(
        item.authorization == "confirmed" for item in ctx.supplemental_inputs
    )
    available_actions = set(runtime.capability_snapshot.available_actions)
    action_by_predicate = {
        "send_report": "send_report",
        "send_item": "send_report",
        "share_photo": "share_photo",
        "delete_backup": "delete_backup",
        "delete_customer_records": "delete_records",
        "retry_export": "retry_export",
        "upload_failure": "retry_upload",
    }
    required_actions = {
        action_by_predicate[predicate]
        for predicate in ctx.predicates if predicate in action_by_predicate
    }
    consent_required = bool(ctx.predicates & {"share_photo"})
    authorization_required = bool(
        ctx.predicates
        & {"send_report", "send_item", "share_photo", "delete_backup", "delete_customer_records"}
    )
    if runtime.safety_decision == "blocked":
        value, rule = "low", "controllability.no-effective-response.v1"
    elif runtime.external_export_control is False and _desired(ctx, "export_completed"):
        value, rule = "low", "controllability.no-effective-response.v1"
    elif any(
        item.input_type == "runtime_fact" and item.value == "failed"
        for item in ctx.supplemental_inputs
    ):
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif required_actions and missing_parameters:
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif consent_required and runtime.consent in {"denied", "refused"}:
        value, rule = "low", "controllability.no-effective-response.v1"
    elif authorization_required and runtime.authorization in {"denied", "refused"}:
        value, rule = "low", "controllability.no-effective-response.v1"
    elif consent_required and (
        runtime.consent == "unknown" or "consent" in ctx.unknown_keys
    ):
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif authorization_required and not confirmed_authorization and (
        runtime.authorization in {"unknown", "missing"}
        or "authorization" in ctx.unknown_keys
    ):
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif required_actions and not required_actions <= available_actions:
        value, rule = "low", "controllability.no-effective-response.v1"
    elif runtime.diagnostics.get("retry_request_malformed") is True:
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif required_actions:
        value, rule = "high", "controllability.eligible-effective-response.v1"
    elif ctx.predicates & {"export_failure"} and _desired(ctx, "failure_explained") and "provide_information.explain" in available_actions:
        value, rule = "high", "controllability.eligible-effective-response.v1"
    elif ctx.predicates & {
        "explain_estimate_change", "correct_schedule_date", "correct_assistant_date",
        "repeat_review_routine", "report_quoted_request", "status_page_color",
        "deadline_change", "checksum_value", "capability_query",
    }:
        value, rule = "high", "controllability.eligible-effective-response.v1"
    elif ctx.predicates & {"export_success"} and "retry_export" in available_actions:
        value, rule = "high", "controllability.eligible-effective-response.v1"
    elif ctx.predicates & {"deployment_failure", "upload_failure", "alert_code", "service_code", "vendor_absence"}:
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif ctx.predicates & {"export_failure"} and _desired(ctx, "invoice_export_recovered"):
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif ctx.request.relationship and ctx.request.relationship.status == "stale_conflicting":
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    elif ctx.predicates & {"retry_export"} and not ctx.supplemental_inputs:
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    else:
        value, rule = "moderate", "controllability.partial-progress-or-clarify.v1"
    return _known(ctx, "appraisal.controllability", value, rule, capability)


def norm_compatibility(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.norm_compatibility"):
        return unknown
    runtime = ctx.request.runtime
    value_ref = next((item.value_id for item in ctx.request.values if item.key == "safety_and_authorization"), None)
    extra = (value_ref,) if value_ref else ()
    conflicts = tuple(item.contradiction_id for item in ctx.contradictions if "policy" in item.contradiction_id)
    if len(conflicts) >= 2:
        return AppraisalDimension(
            dimension_key="appraisal.norm_compatibility", status=DimensionStatus.DISPUTED,
            value_type="signed_ordinal", derivation_strength="strong", rule_id="norm-compatible.conflict.v1",
            evidence_refs=(*ctx.base_evidence, *extra), required_input_refs=(ctx.scenario.scenario_id, ctx.goal.goal_id),
            conflict_refs=conflicts,
        )
    if any(
        item.input_type == "user_message" and item.authorization == "confirmed"
        for item in ctx.supplemental_inputs
    ):
        return _known(ctx, "appraisal.norm_compatibility", "positive", "norm-compatible.compatible.v1", *ctx.supplemental_ids, *extra)
    if runtime.safety_decision == "blocked":
        return _known(ctx, "appraisal.norm_compatibility", "negative", "norm-compatible.conflict.v1", *extra)
    if ctx.unknown_keys & {"consent"} or "trusted_alert_meaning" in ctx.unknown_keys or (ctx.predicates & {"send_item"} and "authorization" in ctx.unknown_keys):
        return _unknown(ctx, "appraisal.norm_compatibility", *(sorted(ctx.unknown_keys & {"consent", "authorization", "trusted_alert_meaning"})))
    if ctx.predicates & {"explain_estimate_change", "correct_assistant_date", "repeat_review_routine", "retry_export"} or (ctx.predicates == {"send_report"} and len(ctx.request.interpretation.scenarios) == 1):
        value, rule = "positive", "norm-compatible.compatible.v1"
    else:
        value, rule = "neutral", "norm-compatible.no-material-implication.v1"
    return _known(ctx, "appraisal.norm_compatibility", value, rule, *extra)


def affiliative_value(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.affiliative_value"):
        return unknown
    value_ref = next((item.value_id for item in ctx.request.values if item.key == "social_connection"), None)
    extra = (value_ref,) if value_ref else ()
    if ctx.request.relationship and ctx.request.relationship.status == "stale_conflicting":
        return _unknown(ctx, "appraisal.affiliative_value", "current_relationship_evidence", "response_meaning")
    if ctx.predicates & (CORRECTIONS | {"repeat_review_routine"}):
        value, rule = "positive", "affiliative.cooperation-or-repair.v1"
    elif ctx.predicates & {"delete_customer_records"}:
        value, rule = "negative", "affiliative.harm-or-boundary-conflict.v1"
    elif ctx.predicates & {"delete_backup"} and "interpretation.pragmatic.literal" in ctx.hypothesis_keys:
        value, rule = "negative", "affiliative.harm-or-boundary-conflict.v1"
    else:
        value, rule = "neutral", "affiliative.no-material-social-implication.v1"
    return _known(ctx, "appraisal.affiliative_value", value, rule, *extra)


def agency(ctx: RuleContext) -> AppraisalDimension:
    if unknown := _unregistered(ctx, "appraisal.agency"):
        return unknown
    conflicts = tuple(item.contradiction_id for item in ctx.contradictions if "conflict-agency-" in item.contradiction_id)
    if len(conflicts) >= 2:
        return AppraisalDimension(
            dimension_key="appraisal.agency", status=DimensionStatus.DISPUTED,
            value_type="agency_category", derivation_strength="strong",
            rule_id="agency.credible-causal-conflict.v1", evidence_refs=ctx.base_evidence,
            required_input_refs=(ctx.scenario.scenario_id, ctx.goal.goal_id), conflict_refs=conflicts,
        )
    if any(item.input_type == "runtime_fact" for item in ctx.supplemental_inputs):
        value = "environment"
    elif any(item.input_type == "observed_outcome" for item in ctx.supplemental_inputs):
        value = "columbina"
    elif ctx.predicates & {"correct_assistant_date", "retry_export"}:
        value = "columbina"
    elif ctx.predicates & THIRD_PARTY_EVENTS:
        value = "third_party"
    elif ctx.predicates & ENVIRONMENT_EVENTS:
        value = "environment"
    elif ctx.predicates & UNKNOWN_AGENCY_EVENTS:
        value = "unknown"
    elif ctx.predicates & USER_EVENTS:
        value = "unknown" if (ctx.predicates == {"send_report"} and len(ctx.request.interpretation.scenarios) > 1 and "recipient" in ctx.unknown_keys) else "user"
    else:
        value = "unknown"
    return _known(ctx, "appraisal.agency", value, "agency.semantic-runtime-grounding.v1", *(() if value != "environment" else ctx.supplemental_ids))


RULES = (
    goal_relevance, goal_congruence, novelty, certainty, controllability,
    norm_compatibility, affiliative_value, agency,
)
