"""Bounded, in-process shadow canary. Never sends or commits a chat response."""
import argparse
import json
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel, canonical, response_schema, prompt
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline, ObservationContext, observe
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0
# Hermetic capture creation contains a test persona; this is experimental
# model/contract evidence, not an authenticated production or persona eval.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from cognitive_shadow_support import make_capture

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--text',default='Hi')
    parser.add_argument('--output',default='/private/tmp/cognitive-two-call-canary.json')
    parser.add_argument('--capture-proposals', action='store_true', help='Save provider captures locally, including on failure, for offline diagnosis.')
    args=parser.parse_args()
    settings=get_settings()
    model=OpenAITwoCallModel(model_id=settings.cognitive_shadow_semantic_parser_model,
        api_key=settings.openai_api_key,timeout_seconds=settings.cognitive_shadow_semantic_parser_timeout_seconds,
        max_output_tokens=settings.cognitive_shadow_orchestration_max_output_tokens)
    capture=make_capture(contents=(('user',args.text),))
    pipeline=install(build_pipeline(capture.turn_id),model,protocol=settings.cognitive_shadow_two_call_protocol)
    started=time.perf_counter()
    record=observe(capture,None,ObservationContext(job_id=capture.capture_id,attempt=1,
        capture_digest=payload_digest(capture),started_at='2026-10-02T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
    # Earlier protocols word inside Call 2 (captured engine); v1 uses the
    # separately configured Realization provider.
    from app.services.cognitive_shadow import realization_engine
    engine=realization_engine(settings) if settings.cognitive_shadow_two_call_protocol=='cognitive-two-call-v1' else RealizationEngineV0(None)
    realization_started=time.perf_counter()
    record=realize_record(record,engine=engine,configured_maximum_characters=settings.cognitive_shadow_realization_max_characters)
    realization_ms=(time.perf_counter()-realization_started)*1000
    elapsed=(time.perf_counter()-started)*1000
    Path(args.output).write_text(record.model_dump_json(indent=2))
    if args.capture_proposals or record.status.value == 'failed':
        Path(args.output).with_suffix('.proposals.json').write_text(json.dumps(
            [call.model_dump(mode='json') for call in pipeline.two_call.captures], indent=2))
    spans=pipeline.two_call.recorder.spans
    sizes=[]
    for call in pipeline.two_call.captures:
        payload=call.model_dump(mode='json')['payload']
        sizes.append(dict(stage=call.stage,payload_chars=len(canonical(payload)),
            schema_chars=len(canonical(response_schema(call.stage,payload))),
            prompt_chars=len(prompt(call.stage,payload)),
            largest_components=sorted([dict(component=k,chars=len(canonical(v))) for k,v in payload.items()],
                                      key=lambda item:item['chars'],reverse=True)[:5]))
    print(json.dumps(dict(status=record.status.value,realization_status=record.realization_status.value,
        elapsed_ms=round(elapsed,2),provider_calls=sum(s.component=='provider_attempt' and s.status!='skipped' for s in spans),
        provider_work_ms=round(sum(s.elapsed_ms for s in spans if s.component=='provider_attempt'),2),
        payload_sizes=sizes,
        realization_ms=round(realization_ms,2),
        final_text=record.realization_trace.final_candidate.text if record.realization_trace is not None and record.realization_trace.final_candidate is not None else None,
        plan=[m.definition_ref.action_key for m in record.pipeline_trace.action_selection.plan.moves] if record.pipeline_trace is not None else None,
        failure_code=record.failure.code if record.failure else None,
        realization_failure=record.realization_failure.code if record.realization_failure else None,
        tokens=[dict(stage=s.stage,input=s.input_tokens,output=s.output_tokens) for s in spans if s.component=='provider_attempt'])))

if __name__=='__main__':main()
