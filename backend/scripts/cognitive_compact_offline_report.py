"""Reproduce compact/legacy structural measurements with synthetic doubles only.

No OpenAI client, API key, database writes, or captured user conversation.
Character counts include canonical JSON and are not provider token counts.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_compact_two_call import CompactDouble,run_compact,assert_replay
from test_two_call_orchestration import observe_double,dinner_observe
from simulated_authority_support import DINNER
from app.ai.Cognitive.system.v0.two_call.provider import canonical,response_schema,prompt
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.realization_v0 import RealizationEngineV0


def main():
    rows=[]
    cases=(('legacy','social',lambda:observe_double()),
        ('legacy','artifact',lambda:dinner_observe()),
        ('compact','social',lambda:run_compact()),
        ('compact','artifact',lambda:run_compact(CompactDouble(True),text=DINNER['turns'][1]['user'])))
    for protocol,turn_class,run in cases:
        result=run();record,pipeline=result[:2]
        assert record.status.value=='completed',record.failure
        assert_replay(record)
        record=realize_record(record,engine=RealizationEngineV0(None),configured_maximum_characters=4000)
        assert record.realization_status.value=='accepted_shadow',record.realization_failure
        for capture in pipeline.two_call.captures:
            call=capture.model_dump(mode='json');payload=call['payload'];stage=call['stage']
            rows.append(dict(protocol=protocol,turn_class=turn_class,stage=stage,
                core_status=record.status.value,realization_status=record.realization_status.value,
                payload_characters=len(canonical(payload)),schema_characters=len(canonical(response_schema(stage,payload))),
                instruction_characters=len(prompt(stage,payload)),output_characters=len(canonical(call['proposal'])),
                attribution=call.get('attribution')))
    print(json.dumps(dict(schema='compact-offline-structural-report-v0.1',synthetic=True,
        external_provider_calls=0,provider_latency_p50_ms=None,provider_latency_p95_ms=None,
        provider_token_counts=None,rows=rows),indent=2))


if __name__=='__main__':main()
