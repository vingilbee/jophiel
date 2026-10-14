"""Opt-in real-model regression against exact recorded closed-loop inputs.

No DB writes or visible responses. Each turn is independent, with its recorded
predecessor (not a newly generated closed-loop trajectory). Output must be new.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='gpt-6-luna')
    parser.add_argument('--case', choices=['exception', 'preference', 'revision'])
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; choose a new file')
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    os.environ['DATABASE_URL'] = 'postgresql+psycopg://localhost:1/no_database'
    os.environ['REDIS_URL'] = 'redis://localhost:1/0'
    from dotenv import dotenv_values
    key = os.environ.get('OPENAI_API_KEY') or dotenv_values(backend / '.env').get('OPENAI_API_KEY')
    if not key:
        parser.error('OPENAI_API_KEY unavailable')
    from app.ai.Cognitive.system.v0.live_shadow.models import CognitiveShadowCapture, CognitiveShadowTerminalRecord
    from app.ai.Cognitive.system.v0.live_shadow.compiler import PredecessorRecord
    from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, build_pipeline, observe
    from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
    from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
    from app.ai.Cognitive.system.v0.two_call.orchestrator import install
    from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
    from app.ai.Cognitive.system.v0.realization_provider import OpenAIRealizationModel
    from app.ai.Cognitive.system.v0.realization_v0.engine import RealizationEngineV0
    from openai import OpenAI
    results = []
    cases = [('standard-preference-r3', 3), ('opposing-preference-r1', 8), ('standard-revision-r1', 8)]
    if args.case:
        cases = [cases[['exception', 'preference', 'revision'].index(args.case)]]
    # Exclusive creation prevents overwriting the original evaluation artifacts.
    with args.output.open('x') as output:
        for name, turn in cases:
            with gzip.open(args.artifacts / (name + '.json.gz'), 'rt') as source:
                old = json.load(source)
            row = old['turns'][turn - 1]
            prior = old['turns'][turn - 2]
            capture = CognitiveShadowCapture.model_validate(row['capture'])
            terminal = CognitiveShadowTerminalRecord.model_validate(prior['terminal'])
            previous = PredecessorRecord(terminal_id=terminal.terminal_id, turn_id=terminal.turn_id,
                user_ref=terminal.user_ref, conversation_ref=terminal.conversation_ref,
                focus_message_id=prior['capture']['focus_message_id'], segment_id=terminal.segment_id,
                status=terminal.status, pipeline_trace=terminal.pipeline_trace)
            boundary = []
            client = OpenAI(api_key=key, timeout=60, max_retries=0)
            def create(**kwargs):
                entry = {'request': kwargs}
                boundary.append(entry)
                response = client.chat.completions.create(**kwargs)
                entry['response'] = response.model_dump(mode='json')
                return response
            observed_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            model = OpenAITwoCallModel(model_id=args.model, api_key=key, timeout_seconds=60,
                max_output_tokens=6000, client=observed_client)
            pipeline = install(build_pipeline(capture.turn_id), model, protocol='cognitive-two-call-v1')
            record = observe(capture, previous, ObservationContext(job_id=capture.capture_id,
                attempt=1, capture_digest=payload_digest(capture), started_at='2026-10-12T00:00:00+00:00'),
                pipeline_factory=lambda _: pipeline)
            engine = RealizationEngineV0(OpenAIRealizationModel(model_id=args.model, api_key=key,
                timeout_seconds=60, client=observed_client), max_attempts=2)
            realized = realize_record(record, engine=engine, configured_maximum_characters=1200,
                                      dialogue_context=capture.dialogue.turn_context)
            trace = realized.realization_trace
            result = dict(case=f'{name}:T{turn}', status=record.status.value,
                realization_status=realized.realization_status.value,
                reply=trace.final_candidate.text if trace and trace.final_candidate else None,
                terminal=record.model_dump(mode='json'), realized=realized.model_dump(mode='json'), boundary=boundary)
            results.append(result)
            print(json.dumps({k: result[k] for k in ('case', 'status', 'realization_status', 'reply')}), flush=True)
            output.seek(0)
            json.dump(results, output, ensure_ascii=False, indent=2)
            output.truncate()
            output.flush()


if __name__ == '__main__':
    main()
