"""Pair baseline/trial appraisal on identical canonical turn snapshots.

Input JSONL: {"request": CognitiveTurnRequest, "model_trace": optional frozen trace}.
Output is content-free; frozen traces retain sensitive evidence and remain local.
Live evaluation requires --live and an explicit model in settings or --model.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from app.ai.Cognitive.system.v0.appraisal.context import project_context
from app.ai.Cognitive.system.v0.appraisal.engine import AppraisalEngine
from app.ai.Cognitive.system.v0.appraisal.models import AppraisalRequest
from app.ai.Cognitive.system.v0.appraisal.model_contracts import AppraisalModelError, AppraisalModelTrace
from app.ai.Cognitive.system.v0.appraisal.model_engine import FrozenAppraisalModel, ModelAppraisalEngine
from app.ai.Cognitive.system.v0.cognitive_turn import CognitiveTurnRequest


def percentiles(values):
    if not values:
        return {'sample_size': 0, 'p50_ms': None, 'p95_ms': None, 'p99_ms': None}
    ordered = sorted(values)
    return {'sample_size': len(values), **{f'p{p}_ms': ordered[math.ceil(len(ordered) * p / 100) - 1]
                                          for p in (50, 95, 99)}}


def compare(items, *, live_model=None):
    records, baseline_times, trial_times, provider_times, actual_calls = [], [], [], [], 0
    for index, item in enumerate(items):
        turn = CognitiveTurnRequest.model_validate(item['request'])
        request = AppraisalRequest(cognitive_turn_id=turn.cognitive_turn_id, interpretation=turn.interpretation,
            active_goals=turn.active_goals, runtime=turn.runtime, memories=turn.memories, values=turn.values,
            relationship=turn.relationship, supplemental_inputs=turn.supplemental_inputs)
        context = turn.appraisal_model_context or project_context(turn)
        started = time.monotonic()
        baseline = AppraisalEngine().appraise(request)
        baseline_times.append((time.monotonic() - started) * 1000)
        baseline_known = sum(d.status == 'known' for f in baseline.frames for d in f.dimensions)
        model = live_model or FrozenAppraisalModel(AppraisalModelTrace.model_validate(item['model_trace']))
        started = time.monotonic()
        try:
            trial, spans = ModelAppraisalEngine(model).appraise(request, context=context)
        except AppraisalModelError as exc:
            if live_model is not None:
                attempts = [s for s in exc.spans if s.component == 'provider_attempt']
                actual_calls += len(attempts)
                provider_times.extend(s.elapsed_ms for s in attempts)
            records.append({'case_index': index, 'status': 'failed', 'code': exc.code,
                            'baseline_known_dimensions': baseline_known})
            continue
        trial_times.append((time.monotonic() - started) * 1000)
        if live_model is not None:
            attempts = [s for s in spans if s.component == 'provider_attempt']
            actual_calls += len(attempts)
            provider_times.extend(s.elapsed_ms for s in attempts)
        records.append({'case_index': index, 'status': 'compiled',
            'baseline_known_dimensions': baseline_known,
            'trial_known_dimensions': sum(d.status == 'known' for f in trial.frames for d in f.dimensions),
            'trial_unknown_dimensions': sum(d.status == 'unknown' for f in trial.frames for d in f.dimensions),
            'evidence_reference_validation': 'passed',
            'appropriateness_review': 'pending', 'frozen_model_trace': trial.model_trace.model_dump(mode='json')})
    # Return replay material separately; reports never expose context or summaries.
    frozen = [{'request': item['request'], 'model_trace': record['frozen_model_trace']}
              for item, record in zip(items, records) if record['status'] == 'compiled']
    for record in records:
        record.pop('frozen_model_trace', None)
    return {'schema_version': 'appraisal-comparison-v0.1',
        'mode': 'live_provider' if live_model is not None else 'frozen_output_replay',
        'measurement_scope': 'appraisal_only', 'input_sample_size': len(items),
        'latency': {'deterministic': percentiles(baseline_times), 'trial': percentiles(trial_times),
                    'provider': percentiles(provider_times)},
        'live_model_call_count': actual_calls,
        'provider_sdk_retries': 0, 'worker_retries': 'not_measured_by_this_runner',
        'behavioral_gate': 'pending_independent_review', 'records': records}, frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--frozen', action='store_true')
    parser.add_argument('--model')
    parser.add_argument('--frozen-output', type=Path)
    args = parser.parse_args()
    model = None
    if args.live:
        from app.core.config import get_settings
        from app.ai.Cognitive.system.v0.appraisal_provider import OpenAIAppraisalModel
        settings = get_settings()
        try:
            model = OpenAIAppraisalModel(model_id=args.model or settings.cognitive_shadow_appraisal_model,
                api_key=settings.openai_api_key, timeout_seconds=settings.cognitive_shadow_appraisal_timeout_seconds)
        except AppraisalModelError as exc:
            parser.error(f'appraisal configuration: {exc.code}')
    items = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    report, frozen = compare(items, live_model=model)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    if args.frozen_output:
        args.frozen_output.write_text(''.join(json.dumps(item) + '\n' for item in frozen))
    print(json.dumps({key: value for key, value in report.items() if key != 'records'}, indent=2))


if __name__ == '__main__':
    main()
