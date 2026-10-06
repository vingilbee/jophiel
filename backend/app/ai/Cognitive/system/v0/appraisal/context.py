"""Appraisal-specific projection; does not expand Action's mapper authority."""
from .model_contracts import CognitiveModelContext, ModelTrait

DEFINITIONS = {
    'curiosity': 'Attention to relevant uncertainty; no requirement to ask questions.',
    'warmth': 'Sensitivity to cooperation and repair; never assumed reciprocal attachment.',
    'playfulness': 'Openness to benign lightness; never disregard of a stated boundary.',
    'directness': 'Tolerance for clear disagreement; blunt correction is not automatically hostility.',
    'assertiveness': 'Willingness to disagree; no authoritative source in the current mapper.',
}


def project_context(turn) -> CognitiveModelContext:
    profile = turn.action_personality
    traits = tuple(ModelTrait(
        key=key, value=getattr(profile, key, None),
        source_kind='mapped' if getattr(profile, key, None) is not None else 'unavailable',
        source_snapshot_ref=profile.profile_id if getattr(profile, key, None) is not None else None,
        scale_definition_ref=('personality-runtime-mapping-v0.1:unit-interval'
                              if getattr(profile, key, None) is not None else 'unavailable'),
        behavioral_definition=definition,
    ) for key, definition in DEFINITIONS.items())
    relationship = turn.relationship
    return CognitiveModelContext(
        agent_identity_ref=turn.core_memory_ref, traits=traits,
        dynamic_state_snapshot_ref=turn.previous_state.state_snapshot_id,
        dynamic_state=tuple({k: v for k, v in item.model_dump(mode='json').items()
                             if k in {'state_key', 'scope_type', 'scope_ref', 'status', 'score', 'band'}}
                            for item in turn.previous_state.values if item.active),
        relationship_availability='available' if relationship else 'unavailable',
        relationship_snapshot_ref=relationship.relationship_snapshot_id if relationship else None,
        relationship=relationship.model_dump(mode='json') if relationship else None,
    )


def with_dialogue(context, messages, focus_message_ref):
    """Only the bounded, exposure-authorized pipeline message window enters appraisal."""
    selected = []
    omitted = []
    for message in messages:
        current = message.message_id == focus_message_ref
        if not current and (message not in messages[-8:] or len(message.content) > 2000):
            omitted.append(message.message_id)
            continue
        selected.append({'ref': message.message_id, 'speaker': message.role.value,
                         'text': message.content, 'current_turn': current,
                         'authority': 'current_user_task' if current and message.role.value == 'user' else 'context_only'})
    return CognitiveModelContext.model_validate(context.model_dump(mode='python') |
        {'dialogue_context': tuple(selected), 'omitted_dialogue_refs': tuple(omitted)})
