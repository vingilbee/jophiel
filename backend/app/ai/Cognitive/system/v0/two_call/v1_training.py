"""Candidate cognitive training records (``cognitive-training-example-v1``).

A pure projection of one terminal shadow record produced by protocol
``cognitive-two-call-v1``.  It never exports, persists, retains, or promotes
anything: every review status starts ``unreviewed`` and contract validity is
recorded separately from cognitive or behavioral quality (handoff §14).

The projection removes prompt text, provider handles, canonical/persistence
IDs, retries, latency spans, diagnostics and Realization prose.  Cross-record
references are deterministic local refs (``frame:0``, ``goal:1``) defined by
order within this example.
"""
from __future__ import annotations

from typing import Any

from .v1 import focus_tokens, span_bounds
from .v1_contracts import (
    APPRAISAL_FIELDS, ARGUMENT_KIND_REGISTRY_VERSION, CONFLICT_REGISTRY_VERSION, EPISTEMIC_POLICY_VERSION,
    POLICY_COMPILER_VERSION, POLICY_MODE_ROUTER_VERSION, PROTOCOL_V1, SELECTION_FACTOR_REGISTRY_VERSION,
    SPAN_ADDRESSING_VERSION, SUPPORT_POLICY_VERSION, UNDERSTANDING_COMPILER_VERSION, UNKNOWN_REGISTRY_VERSION,
    CompiledPolicy, UnderstandingProposalV1,
)

TRAINING_SCHEMA_VERSION = 'cognitive-training-example-v1'
PROJECTOR_VERSION = 'cognitive-training-projector-v1'
REVIEW_UNREVIEWED = 'unreviewed'


class _Local:
    """Canonical ID -> deterministic local ref, by kind and first appearance."""

    def __init__(self) -> None:
        self.refs: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def add(self, canonical: str, kind: str) -> str:
        if canonical not in self.refs:
            index = self.counts.get(kind, 0)
            self.counts[kind] = index + 1
            self.refs[canonical] = f'{kind}:{index}'
        return self.refs[canonical]

    def get(self, canonical: str) -> str | None:
        return self.refs.get(canonical)


def _plain(value: Any) -> Any:
    if hasattr(value, 'value') and not isinstance(value, (str, int, float, bool)):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def failure_category(code: str | None) -> str | None:
    """Typed negative class; schema, reference, cognitive and policy failures stay apart."""
    if code is None:
        return None
    if 'proposal_shape' in code or 'output_invalid' in code:
        return 'schema'
    if 'understanding_compilation' in code or 'policy_compilation' in code or 'policy_input' in code:
        return 'reference'
    if 'behavior_proposal_ineligible' in code or 'behavior_plan_disagreement' in code:
        return 'policy'
    if any(token in code for token in ('timeout', 'transport', 'configuration', 'refused')):
        return 'provider'
    return 'cognitive'


def project_training_example(record) -> dict[str, Any]:
    """One candidate example.  Raises ``ValueError`` for a non-v1 record."""
    trace = record.pipeline_trace
    request = record.compiled_request
    orchestration = trace.orchestration if trace is not None else None
    if trace is not None and (orchestration is None or orchestration.protocol != PROTOCOL_V1):
        raise ValueError('training projection v1 accepts only cognitive-two-call-v1 traces')
    if request is None:
        raise ValueError('training projection requires the compiled request')
    local = _Local()

    # -- input -------------------------------------------------------------
    messages = []
    for message in request.messages:
        local.add(message.message_id, 'message')
        messages.append(dict(speaker=message.role.value, text=message.content,
                             focus=message.message_id == request.focus_message_id))
    goals_before = []
    if request.previous_goal_snapshot is not None:
        for goal in request.previous_goal_snapshot.records:
            if goal.status.value == 'active':
                goals_before.append(dict(ref=local.add(goal.goal_id, 'goal'), definition=goal.definition_ref.definition_key,
                                         priority=goal.priority_band.value, status=goal.status.value))
    personality = request.action_personality
    biases = request.interpretation_biases
    dialogue_entities = []
    if request.dialogue_context is not None:
        for item in request.dialogue_context.interpretation_context.items:
            if item.surface_anchor:
                dialogue_entities.append(dict(kind=item.item_kind, speaker=item.speaker, anchor=item.surface_anchor))
    example: dict[str, Any] = dict(
        schema_version=TRAINING_SCHEMA_VERSION,
        input=dict(
            perception=dict(cue_activations=[dict(cue_key=c.cue_key, state=c.state.value)
                                             for c in (trace.perception.cue_activations if trace is not None else ())]),
            authorized_context=dict(
                messages=messages, dialogue_entities=dialogue_entities,
                memories=[dict(text=m.content, epistemic_status=_plain(m.status)) for m in request.memories],
                context_truncated=request.context_truncated,
                capabilities=list(request.runtime.to_action_runtime().available_capabilities)),
            persona=dict(
                status=personality.status.value,
                traits={key: getattr(personality, key) for key in ('curiosity', 'warmth', 'playfulness', 'directness')},
                biases={key: getattr(biases, key) for key in (
                    'benign_prior', 'trust_prior', 'threat_sensitivity', 'confirmation_tendency',
                    'self_attribution_tendency', 'ambiguity_tolerance', 'social_affiliation_prior')}),
            relationship=dict(availability='available' if request.relationship is not None else 'unavailable',
                              status=request.relationship.status if request.relationship is not None else None),
            state_before=[dict(state_key=v.state_key.value, scope_type=v.scope_type.value,
                               band=_plain(v.band), score=v.score, status=v.status.value)
                          for v in request.previous_state.values if v.active],
            goals_before=goals_before,
        ),
    )

    failure = record.failure
    contract_valid = record.status.value == 'completed' and trace is not None
    example['evaluation'] = dict(
        contract_status='valid' if contract_valid else 'invalid',
        failure_category=None if contract_valid else failure_category(failure.code if failure else None),
        cognitive_review_status=REVIEW_UNREVIEWED, behavior_review_status=REVIEW_UNREVIEWED,
        personality_fidelity_status=REVIEW_UNREVIEWED, domain_label_status=REVIEW_UNREVIEWED,
        outcome_status='unavailable', promotion_status='not_promoted')
    example['provenance'] = dict(
        ontology_versions=dict(argument_kinds=(orchestration.calls[0].payload['ontology']['predicate_registry_version']
                                              if orchestration else ARGUMENT_KIND_REGISTRY_VERSION), unknowns=UNKNOWN_REGISTRY_VERSION,
                               conflicts=CONFLICT_REGISTRY_VERSION, selection_factors=SELECTION_FACTOR_REGISTRY_VERSION,
                               span_addressing=SPAN_ADDRESSING_VERSION,
                               action_registry=trace.action_selection.versions.registry if trace is not None else None),
        model_provenance=({key: orchestration.calls[0].provenance.get(key) for key in (
            'provider', 'model_id', 'reasoning_effort', 'temperature', 'protocol_version')} if orchestration else None),
        compiler_versions=dict(understanding=UNDERSTANDING_COMPILER_VERSION, policy=POLICY_COMPILER_VERSION,
                               support=SUPPORT_POLICY_VERSION, epistemic=EPISTEMIC_POLICY_VERSION,
                               policy_mode_router=POLICY_MODE_ROUTER_VERSION, projector=PROJECTOR_VERSION),
    )
    if not contract_valid:
        example['target'] = None
        return example

    # -- target: interpretation and appraisal ---------------------------------
    interpretation = trace.interpretation
    focus = next(m.content for m in request.messages if m.message_id == request.focus_message_id)
    tokens = focus_tokens(focus)
    proposal = UnderstandingProposalV1.model_validate(orchestration.calls[0].proposal)
    for frame in interpretation.semantic_frames:
        local.add(frame.frame_id, 'frame')
    for item in interpretation.hypotheses:
        local.add(item.hypothesis_id, 'hypothesis')
    events = []
    for event, frame in zip(proposal.events, orchestration.calls[0].compiled_proposal['interpretation']['frames']):
        arguments = {a['name']: _plain(a['value']) for a in frame['arguments']}
        from .continuity import dialogue_span
        entries = orchestration.calls[0].reference_table
        for name, value in event.arguments.items():
            if value.kind not in {'dialogue_span', 'dialogue_span_set'}:
                continue
            def source_span(span):
                start, end, text = dialogue_span(span, entries)
                return dict(message_ref=local.get(entries[span.message_ref]['canonical_ref']),
                            span=[start, end], text=text)
            arguments[name] = (source_span(value) if value.kind == 'dialogue_span'
                               else [source_span(span) for span in value.spans])
        events.append(dict(predicate=event.predicate_key, polarity=event.polarity,
                           arguments=arguments,
                           evidence_spans=[list(span_bounds(ref, tokens)) for ref in event.evidence_span_refs]))
    readings, impacts = [], []
    for index, event in enumerate(proposal.events):
        for reading in event.readings:
            dimensions = {name: getattr(reading.appraisal, name) for name in APPRAISAL_FIELDS}
            if reading.appraisal.uncertainty == 'high':  # compiled as unassessed
                dimensions = {k: v if v in {None, 'not_applicable'} else None for k, v in dimensions.items()}
            readings.append(dict(event_index=index, hypothesis_key=reading.hypothesis_key, uncertainty=reading.uncertainty,
                                 appraisal=dict(uncertainty=reading.appraisal.uncertainty, **dimensions)))
    before = {g['ref'] for g in goals_before}
    for impact in proposal.existing_goal_impacts:
        handle = impact.goal_ref  # ``goal:i`` indexes the supplied existing goals in order
        if handle not in before:
            raise ValueError('goal impact outside the projected goals')
        assessed = impact.uncertainty != 'high'
        impacts.append(dict(goal_ref=handle, event_index=impact.event_index, reading_index=impact.reading_index,
                            relevance=impact.relevance if assessed else None,
                            congruence=impact.congruence if assessed else None, uncertainty=impact.uncertainty))
    hypotheses = [dict(key=h.hypothesis_key, dimension=h.dimension.value, support=h.support.value,
                       disposition=h.disposition.value, frame_refs=[local.get(f) for f in h.semantic_frame_refs if local.get(f)])
                  for h in interpretation.hypotheses]

    # -- target: deterministic middle (derived labels) -------------------------
    appraisal = trace.cognitive_turn.appraisal
    for frame in appraisal.frames:
        local.add(frame.frame_id, 'appraisal')
    update = trace.cognitive_turn.dynamic_state_update
    before_state = {(v.state_key, v.scope_type, v.scope_ref): v for v in request.previous_state.values if v.active}
    transitions = []
    for value in update.snapshot.values:
        if not value.active:
            continue
        key = (value.state_key, value.scope_type, value.scope_ref)
        contributions = [c for c in update.contributions
                         if (c.state_key, c.scope_type, c.scope_ref) == key]
        prior = before_state.get(key)
        if not contributions:
            direction = 'no_contribution'
        elif any(c.direction.value == 'branch_dependent' for c in contributions):
            direction = 'branch_dependent'
        elif prior is None or prior.score is None or value.score is None:
            direction = 'unchanged' if all(c.direction.value == 'unchanged' for c in contributions) else contributions[0].direction.value
        else:
            direction = 'increase' if value.score > prior.score else ('decrease' if value.score < prior.score else 'unchanged')
        scope = local.get(value.scope_ref) or f'scope:{value.scope_type.value}'
        transitions.append(dict(state_key=value.state_key.value, scope_ref=scope,
                                before_band=_plain(prior.band) if prior is not None else None,
                                after_band=_plain(value.band), direction=direction,
                                source_refs=sorted({local.get(ref) for c in contributions for ref in c.source_appraisal_frame_refs
                                                    if local.get(ref)})))
    goals = trace.goal_snapshot
    records = {g.goal_id: g for g in goals.records}
    for goal in goals.records:
        local.add(goal.goal_id, 'goal')
    goal_transitions = []
    for item in goals.transitions:
        record_ = records.get(item.goal_id)
        if record_ is None:
            continue
        goal_transitions.append(dict(
            goal_ref=local.get(item.goal_id), goal_definition_key=record_.definition_ref.definition_key,
            transition=item.transition_kind.value, priority=record_.priority_band.value, status_after=item.to_status.value,
            target_refs=sorted({local.get(t.ref) for r in record_.requirements for t in r.target_refs if local.get(t.ref)}),
            evidence_refs=sorted({local.get(ref) for ref in (*item.source_refs, *item.evidence_refs) if local.get(ref)})))
    requirements = []
    requirement = trace.requirement_resolution
    if requirement is not None:
        for resolution in requirement.handoff.snapshot.resolutions:
            goal = records.get(resolution.goal_ref)
            requirements.append(dict(goal_ref=local.get(resolution.goal_ref),
                                     goal_definition_key=goal.definition_ref.definition_key if goal else None,
                                     kind=resolution.kind.value, status=resolution.status.value,
                                     blocking=resolution.blocking, reason=resolution.reason_code.value))

    # -- target: action plan -------------------------------------------------
    policy = CompiledPolicy.model_validate(orchestration.calls[1].compiled_proposal)
    plan = trace.action_selection.plan
    moves = []
    for move in plan.moves:
        targets = []
        for target in move.target_refs:
            ref = local.get(target.ref) or local.add(target.ref, 'object')
            targets.append(dict(role=target.role, target_type=target.target_type.value, ref=ref))
        moves.append(dict(action_key=move.definition_ref.action_key, targets=targets,
                          parameters=_plain(dict(move.parameters))))
    candidate_moves = {cid: index for index, cid in enumerate(policy.selected_candidate_ids)}
    factors = [dict(move_index=candidate_moves[item['candidate_id']], factor_keys=list(item['factor_keys']),
                    source_refs=sorted({local.get(ref) for ref in item['source_refs'] if local.get(ref)}))
               for item in policy.selection_factors if item['candidate_id'] in candidate_moves]
    example['target'] = dict(
        interpretation=dict(events=events, hypotheses=hypotheses,
                            unknowns=[dict(key=u.key, owner=u.owner) for u in interpretation.unknowns],
                            conflicts=[dict(key=c.description, status=c.status) for c in interpretation.contradictions]),
        appraisal=dict(readings=readings, existing_goal_impacts=impacts),
        state_transitions=transitions, goal_transitions=goal_transitions, requirements=requirements,
        action_plan=dict(policy_mode=policy.policy_mode, moves=moves, primary_index=policy.primary_index,
                         stop_after_selected=True, selection_factors=factors),
    )
    return example


__all__ = ['PROJECTOR_VERSION', 'TRAINING_SCHEMA_VERSION', 'failure_category', 'project_training_example']
