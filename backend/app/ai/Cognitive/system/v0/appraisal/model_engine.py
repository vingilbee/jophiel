"""Model proposals compiled into the existing deterministic state spine."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .context import project_context
from .engine import AppraisalEngine
from .rules import RuleContext
from .models import AppraisalDimension, AppraisalRequest, AppraisalResult
from .model_contracts import (
    AppraisalModelError, AppraisalModelOutput, AppraisalModelPayload, AppraisalModelTrace,
    CognitiveModelContext, COMPILER_VERSION, DIMENSIONS, SCALES,
)
from ..latency import SpanRecorder, LatencySpan

PROMPT_VERSION = 'appraisal-prompt-v0.1'
PROMPT = (Path(__file__).with_name('prompt_v0_1.txt').read_text()
          + '\nDimension definitions: ' + json.dumps(DIMENSIONS, sort_keys=True)
          + '\nAllowed scales: ' + json.dumps(SCALES, sort_keys=True))
PROMPT_HASH = hashlib.sha256(PROMPT.encode()).hexdigest()


def canonical(value):
    return json.dumps(value.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def payload_for(request: AppraisalRequest, context: CognitiveModelContext) -> AppraisalModelPayload:
    frames = {f.frame_id: f for f in request.interpretation.semantic_frames}
    pairs = tuple({'scenario': scenario.model_dump(mode='json'), 'goal': goal.model_dump(mode='json'),
                   'events': tuple(frames[ref].model_dump(mode='json') for ref in scenario.semantic_frame_refs),
                   'unknowns': tuple(u.model_dump(mode='json') for u in request.interpretation.unknowns
                                     if u.unknown_id in scenario.unknown_refs),
                   'contradictions': tuple(c.model_dump(mode='json') for c in request.interpretation.contradictions
                                          if c.contradiction_id in scenario.contradiction_refs)}
                  for scenario in request.interpretation.scenarios if scenario.disposition in {'leading', 'retained_alternative'}
                  for goal in request.active_goals if goal.status == 'active')
    evidence_refs = {ref for pair in pairs for frame in pair['events'] for ref in frame['evidence_refs']}
    payload = AppraisalModelPayload(
        focus_message_ref=request.interpretation.focus_message_id, context=context, pairs=pairs,
        evidence=tuple(e.model_dump(mode='json') for e in request.interpretation.evidence if e.evidence_id in evidence_refs),
        runtime=request.runtime.model_dump(mode='json', exclude={'diagnostics', 'last_tool_event', 'provided_parameters'}) |
                {'provided_parameter_keys': tuple(request.runtime.provided_parameters)},
        values=tuple(v.model_dump(mode='json') for v in request.values),
        supplemental_inputs=tuple(v.model_dump(mode='json') for v in request.supplemental_inputs),
    )
    if len(pairs) > 8 or len(canonical(payload)) > 48000:
        raise AppraisalModelError('proposal_rejected')
    return payload


def proposal_reference_scope(request, payload, scenario, goal):
    """Canonical per-pair evidence scope, also used by proposal adapters."""
    events = tuple(f for f in request.interpretation.semantic_frames
                   if f.frame_id in scenario.semantic_frame_refs)
    scoped = RuleContext(request, scenario, goal, events)
    sent_refs = {e['evidence_id'] for e in payload.evidence}
    allowed = ({ref for event in events for ref in event.evidence_refs if ref in sent_refs}
               | set(scenario.semantic_frame_refs) | {scenario.scenario_id, goal.goal_id,
                 request.runtime.capability_snapshot.snapshot_id}
               | {v.value_id for v in request.values}
               | set(scenario.unknown_refs) | set(scenario.contradiction_refs)
               | set(scoped.supplemental_ids))
    if request.relationship:
        allowed.add(request.relationship.relationship_snapshot_id)
    allowed |= {item['ref'] for item in payload.context.dialogue_context}
    allowed |= {t.source_snapshot_ref for t in payload.context.traits if t.source_snapshot_ref}
    if payload.context.dynamic_state_snapshot_ref:
        allowed.add(payload.context.dynamic_state_snapshot_ref)
    return frozenset(allowed)


def compile_proposal(request, payload, proposal, provenance) -> AppraisalResult:
    """Validate pair coverage, scales, uncertainty and scoped evidence before state sees it."""
    try:
        if payload_for(request, payload.context) != payload:
            raise ValueError("payload differs from authoritative request")
        proposal = AppraisalModelOutput.model_validate(proposal)
        expected = {(s.scenario_id, g.goal_id): (s, g)
                    for s in request.interpretation.scenarios if s.disposition in {'leading', 'retained_alternative'}
                    for g in request.active_goals if g.status == 'active'}
        keys = [(f.scenario_ref, f.goal_ref) for f in proposal.frames]
        if len(keys) != len(set(keys)) or set(keys) != set(expected):
            raise ValueError('pair coverage')
        baseline = AppraisalEngine()
        compiled = {}
        for frame in proposal.frames:
            scenario, goal = expected[(frame.scenario_ref, frame.goal_ref)]
            if {d.dimension_key for d in frame.dimensions} != set(DIMENSIONS):
                raise ValueError('dimension coverage')
            allowed = proposal_reference_scope(request, payload, scenario, goal)
            dims = []
            for d in frame.dimensions:
                if not (set(d.evidence_refs) | set(d.conflict_refs)) <= allowed:
                    raise ValueError('foreign evidence')
                if ((d.status == 'known' and d.assessment_basis not in {'observed', 'inferred'})
                    or (d.status == 'unknown' and d.assessment_basis != 'insufficient')
                    or (d.status == 'not_applicable' and d.assessment_basis != 'not_applicable')):
                    raise ValueError('inconsistent epistemic basis')
                if d.status == 'known' and (
                    not set(d.evidence_refs) & set(scenario.semantic_frame_refs)
                    or goal.goal_id not in d.evidence_refs or d.uncertainty == 'high'
                ):
                    raise ValueError('unsupported assessment')
                dims.append(AppraisalDimension(
                    dimension_key=d.dimension_key, status=d.status, value_type=DIMENSIONS[d.dimension_key][0],
                    value=d.value, evidence_refs=d.evidence_refs,
                    derivation_strength={'low': 'strong', 'moderate': 'moderate', 'high': 'weak'}[d.uncertainty],
                    rule_id=f'{COMPILER_VERSION}:{d.dimension_key}',
                    required_input_refs=(scenario.scenario_id, goal.goal_id),
                    missing_input_keys=d.missing_input_keys, conflict_refs=d.conflict_refs,
                ))
            # Reuse deterministic identity/revision/subject construction, without running its appraisal rules.
            compiled[(scenario.scenario_id, goal.goal_id)] = baseline._frame(request, scenario, goal, tuple(dims))
        frames = tuple(compiled[key] for key in expected)
        trace = AppraisalModelTrace(prompt_version=PROMPT_VERSION, prompt_hash=PROMPT_HASH,
            payload_digest=hashlib.sha256(canonical(payload).encode()).hexdigest(), provenance=provenance,
            payload=payload, proposal=proposal)
        result = AppraisalResult(
            appraisal_trace_id='appraisal-model-' + hashlib.sha256(
                (request.cognitive_turn_id + canonical(proposal)).encode()).hexdigest()[:20],
            cognitive_turn_id=request.cognitive_turn_id,
            interpretation_trace_id=request.interpretation.interpretation_trace_id,
            frames=frames, aggregate=baseline._aggregate(request, frames), model_trace=trace,
            versions={'schema': 'appraisal-schema-v1', 'dimension_registry': 'appraisal-dimensions-v1',
                      'scenario_builder': request.interpretation.versions.scenario_builder,
                      'rules': COMPILER_VERSION, 'protocol': payload.protocol_version,
                      'context': payload.context.schema_version, 'prompt': PROMPT_VERSION},
        )
        baseline._validate_result(request, result)
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise AppraisalModelError('proposal_rejected') from exc


class ModelAppraisalEngine:
    def __init__(self, model=None, *, unavailable_code='configuration_unavailable', clock=time.monotonic):
        self.model = model
        self.unavailable_code = unavailable_code
        self.clock = clock

    def appraise_turn(self, request, turn):
        return self.appraise(request, context=turn.appraisal_model_context or project_context(turn))

    def appraise(self, request, *, context=None):
        recorder = SpanRecorder(self.clock)
        try:
            with recorder.measure('payload_compile'):
                payload = payload_for(request, context or CognitiveModelContext())
            if not payload.pairs:
                return compile_proposal(request, payload, AppraisalModelOutput(frames=()), {'provider': 'skipped'}), tuple(recorder.spans)
            if self.model is None:
                raise AppraisalModelError(self.unavailable_code)
            with recorder.measure('provider_attempt', attempt_number=1,
                                  skipped=isinstance(self.model, FrozenAppraisalModel)):
                try:
                    proposal = self.model.generate(payload)
                except AppraisalModelError:
                    raise
                except Exception as exc:
                    raise AppraisalModelError('output_invalid') from exc
            usage = getattr(self.model, 'usage', {})
            if usage:
                recorder.spans[-1] = LatencySpan.model_validate(recorder.spans[-1].model_dump(mode='python') |
                    {key: value for key, value in usage.items()
                     if key in {'input_tokens', 'output_tokens', 'cached_input_tokens'}})
            with recorder.measure('response_compile'):
                result = compile_proposal(request, payload, proposal, self.model.provenance)
            return result, tuple(recorder.spans)
        except AppraisalModelError as exc:
            exc.spans = tuple(recorder.spans)
            raise


class FrozenAppraisalModel:
    """Replay the downstream spine from a captured proposal; never call a provider."""
    def __init__(self, trace: AppraisalModelTrace):
        self.trace = trace
        self.provenance = trace.provenance

    def generate(self, payload):
        if hashlib.sha256(canonical(payload).encode()).hexdigest() != self.trace.payload_digest:
            raise AppraisalModelError('proposal_rejected')
        if self.trace.prompt_hash != PROMPT_HASH or self.trace.compiler_version != COMPILER_VERSION:
            raise AppraisalModelError('configuration_incompatible')
        return self.trace.proposal
