"""Offline replay of the saved text-boundary failure; no model or DB calls."""
import sys,json,gzip,traceback,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
os.environ['DATABASE_URL']='postgresql+psycopg://localhost:1/no_db';os.environ['REDIS_URL']='redis://localhost:1/0'
from test_cognitive_two_call_v1 import V1Double
from app.ai.Cognitive.system.v0.live_shadow.service import build_pipeline
from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineRequest
from app.ai.Cognitive.system.v0.live_shadow.models import CognitiveShadowCapture
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
x=json.load(gzip.open(OUT/'opposing-base-calm-club_values-r2.json.gz'));t=x['turns'][-1]
model=V1Double(one=lambda p:t['calls'][0]['proposal'],contribution=lambda p:t['contributions'][0]['proposal'])
pipeline=install(build_pipeline(t['capture']['turn_id']),model,protocol='cognitive-two-call-v1')
pipeline.two_call.dialogue_context=CognitiveShadowCapture.model_validate(t['capture']).dialogue.turn_context
try:
 pipeline.run(CognitivePipelineRequest.model_validate(t['terminal']['compiled_request']))
 raise AssertionError('failure was not reproduced')
except Exception as exc:
 s=traceback.format_exc()
 assert 'surface anchors must be single-line valid text' in s
 (OUT/'requirement-failure-offline-replay.txt').write_text(s)
 print('Reproduced downstream surface-anchor rejection from saved proposal; no network calls.')
