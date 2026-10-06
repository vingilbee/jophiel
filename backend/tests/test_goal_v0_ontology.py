from app.ai.Cognitive.system.v0.goal_v0.ontology import all_definitions, definition_by_key

def test_closed_registry_has_thirteen_definitions():
    assert len(all_definitions()) == 13
    assert definition_by_key("interaction.answer_information_request").definition_version == "v0.1-draft"


def test_request_handler_is_non_committing_and_turn_scoped():
    handler = definition_by_key("interaction.handle_action_request")
    [template] = handler.requirement_templates
    assert (handler.domain.value, handler.mode.value, [item.value for item in handler.allowed_scopes]) == (
        "interaction", "achieve", ["turn"],
    )
    # Its terminal condition is that the request was handled, never that the
    # external effect occurred (that stays the committing task goal).
    assert (template.requirement_key, template.satisfaction_policy_key) == (
        "action_request_handled", "goal-satisfaction.action-request-handled.v1",
    )
    task = definition_by_key("task.complete_authorized_request").requirement_templates[0]
    assert task.satisfaction_policy_key == "goal-satisfaction.authorized-task-result.v1"


def test_conversational_deliverable_shares_no_capability_requirement_with_the_handler():
    deliverable = definition_by_key("interaction.fulfill_conversational_deliverable")
    [template] = deliverable.requirement_templates
    assert (deliverable.domain.value, deliverable.mode.value, [item.value for item in deliverable.allowed_scopes]) == (
        "interaction", "achieve", ["turn"],
    )
    assert template.requirement_key == "conversational_deliverable_delivered"
    assert template.satisfaction_policy_key == "goal-satisfaction.conversational-deliverable-delivered.v1"
    assert set(template.allowed_parameter_keys) == {"operation", "artifact_type"}
    handler = definition_by_key("interaction.handle_action_request").requirement_templates[0]
    assert template.satisfaction_policy_key != handler.satisfaction_policy_key
    answer = definition_by_key("interaction.incorporate_inquiry_answer").requirement_templates[0]
    assert (answer.required_target_roles, answer.allowed_parameter_keys) == (("answer_ref",), ("inquiry_ref",))
