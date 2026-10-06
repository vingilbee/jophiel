"""Closed Goal V0 definition registry."""
from __future__ import annotations
from ..contracts.goals import *

_RAW = (
 ("interaction.discharge_response_obligation", "interaction", "achieve", ("system",), ("turn",), "moderate", (("response_obligation_discharged", ("turn_ref",), (), "goal-satisfaction.response-delivered.v1"),)),
 ("interaction.answer_information_request", "interaction", "achieve", ("user","shared"), ("turn","task"), "high", (("supported_answer_provided", ("topic_ref",), ("answer_scope",), "goal-satisfaction.supported-answer.v1"),)),
 ("interaction.acknowledge_relevant_information", "interaction", "achieve", ("user","shared"), ("turn","conversation"), "moderate", (("relevant_information_acknowledged", ("proposition_ref",), (), "goal-satisfaction.relevant-acknowledgement.v1"),)),
 ("interaction.repair_shared_understanding", "interaction", "achieve", ("shared",), ("turn","task"), "high", (("shared_misunderstanding_repaired", ("proposition_ref",), (), "goal-satisfaction.shared-repair-confirmed.v1"),)),
 ("interaction.respond_to_social_contact", "interaction", "achieve", ("shared",), ("turn","conversation"), "moderate", (("social_contact_answered", ("dialogue_scope_ref",), ("contact_kind",), "goal-satisfaction.social-contact-answered.v1"),)),
 ("epistemic.resolve_required_unknown", "epistemic", "achieve", ("columbina","shared"), ("turn","task"), "moderate", (("required_argument_resolved", ("parent_goal_ref","argument_ref"), ("argument_name",), "goal-satisfaction.required-argument-grounded.v1"),)),
 ("epistemic.resolve_material_contradiction", "epistemic", "achieve", ("columbina","shared"), ("turn","task"), "high", (("material_contradiction_resolved", ("contradiction_ref",), (), "goal-satisfaction.material-contradiction-resolved.v1"),)),
 ("task.complete_authorized_request", "task", "achieve", ("user","shared"), ("task",), "high", (("authorized_task_completed", ("task_ref",), ("requested_effect",), "goal-satisfaction.authorized-task-result.v1"),)),
 ("task.prevent_or_stop_requested_action", "task", "avoid", ("user","shared"), ("turn","task"), "high", (("requested_action_prevented_or_stopped", ("task_ref",), ("prevented_effect",), "goal-satisfaction.requested-action-stopped.v1"),)),
 ("interaction.explain_unavailable_or_failed_operation", "interaction", "achieve", ("user","shared"), ("turn","task"), "high", (("operation_failure_explained", ("operation_ref","failure_ref"), (), "goal-satisfaction.operation-failure-explained.v1"),)),
 # Ontology v0.2: the non-committing handler of an explicit action request.
 # Its terminal condition is that the request was appropriately handled
 # (fulfilled, clarified, or truthfully declined), never that the external
 # effect occurred -- that remains ``task.complete_authorized_request``.
 ("interaction.handle_action_request", "interaction", "achieve", ("user","shared"), ("turn",), "high", (("action_request_handled", ("request_ref",), ("requested_effect",), "goal-satisfaction.action-request-handled.v1"),)),
 # Ontology v0.3 (Collaborative Content and Inquiry V0): producing useful
 # content inside the conversation.  It shares no capability requirement with
 # the request handler, and only a validated delivered outcome of the exact
 # requested artifact operation satisfies it -- never a planned move or a
 # proposed revision.
 ("interaction.fulfill_conversational_deliverable", "interaction", "achieve", ("user","shared"), ("turn",), "high", (("conversational_deliverable_delivered", ("request_ref",), ("operation","artifact_type"), "goal-satisfaction.conversational-deliverable-delivered.v1"),)),
 # Ontology v0.3: incorporating the user's answer to an exact open inquiry
 # into the context that asked it.
 ("interaction.incorporate_inquiry_answer", "interaction", "achieve", ("user","shared"), ("turn",), "high", (("inquiry_answer_incorporated", ("answer_ref",), ("inquiry_ref",), "goal-satisfaction.inquiry-answer-incorporated.v1"),)),
)
_POLICIES = {x[3][0][3] for x in _RAW}

def _make(row):
    key, domain, mode, owners, scopes, priority, reqs = row
    return GoalDefinition(
        definition_key=key, definition_version="v0.1-draft", lifecycle="provisional_for_human_review",
        domain=GoalDomain(domain), mode=GoalMode(mode), allowed_owners=tuple(GoalOwner(x) for x in owners),
        allowed_scopes=tuple(GoalScope(x) for x in scopes), default_priority_band=GoalPriorityBand(priority),
        requirement_templates=tuple(GoalRequirementTemplate(requirement_key=k, required_target_roles=roles, allowed_parameter_keys=params, satisfaction_policy_key=policy) for k, roles, params, policy in reqs),
    )
_DEFINITIONS = tuple(_make(x) for x in _RAW)
_BY_KEY = {x.definition_key: x for x in _DEFINITIONS}
if len(_BY_KEY) != len(_DEFINITIONS): raise RuntimeError("duplicate goal definition")

def all_definitions() -> tuple[GoalDefinition, ...]: return _DEFINITIONS
def definition_by_key(key: str) -> GoalDefinition:
    try: return _BY_KEY[key]
    except KeyError as exc: raise ValueError("unknown goal definition") from exc
def definition(ref: GoalDefinitionRef) -> GoalDefinition:
    item = definition_by_key(ref.definition_key)
    if item.definition_version != ref.definition_version: raise ValueError("unsupported goal definition version")
    return item
def validate_requirement(defn: GoalDefinition, requirement: GoalRequirement) -> None:
    template = next((x for x in defn.requirement_templates if x.requirement_key == requirement.requirement_key), None)
    if template is None: raise ValueError("requirement is not registered for definition")
    if tuple(sorted(x.role for x in requirement.target_refs)) != tuple(sorted(template.required_target_roles)):
        raise ValueError("requirement target roles differ from definition")
    if set(requirement.parameters) != set(requirement.parameters) & set(template.allowed_parameter_keys):
        raise ValueError("requirement contains unregistered parameters")
    if requirement.satisfaction_policy_key != template.satisfaction_policy_key: raise ValueError("wrong satisfaction policy")
