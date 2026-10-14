"""Scripted offline interventions isolate deterministic effects, not model quality."""
import copy,gzip,json,sys,os
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
os.environ['DATABASE_URL']='postgresql+psycopg://localhost:1/no_database';os.environ['REDIS_URL']='redis://localhost:1/0'
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
from test_cognitive_two_call_v1 import V1Double,run,select
from app.ai.Cognitive.system.v0.live_shadow.models import CognitiveShadowCapture

origin=json.load(gzip.open(OUT/'standard-route_watch-r1.json.gz'))['turns'][0]
capture=CognitiveShadowCapture.model_validate(origin['capture'])
baseline=origin['calls'][0]['proposal']
def view(p):return dict(schema_version='contribution-proposal-v2',question_kind='opinion',claims=[],view=dict(text='Hypothetically, I would choose the name Quiet Current.',confidence='high',basis=[dict(kind='reasoning',ref=None)],tensions=[]))
def policy(p):
 keys={c['action_key'] for c in p['eligible_candidates']}
 if any(c['blocking'] for c in p['planning_conditions']):
  key='seek_information.clarify' if 'seek_information.clarify' in keys else 'commit_or_coordinate.decline_request'
 else:key=next(k for k in ['provide_information.answer','commit_or_coordinate.decline_request','give_feedback.acknowledge'] if k in keys)
 return select(key)(p)
out=[]
for kind in ['observed_hypothetical','same_opinion_direct','forced_artifact_direct']:
 def build(p,kind=kind):
  raw=copy.deepcopy(baseline)
  if kind=='same_opinion_direct':raw['events'][0]['arguments']['embedding']['value']='direct'
  if kind=='forced_artifact_direct':
   event=copy.deepcopy(raw['events'][0]);event['predicate_key']='collaborative_content_request'
   event['arguments']={k:dict(kind='null') for k in ['operation','artifact_type','target_ref','constraints','requested_form','outcome_certainty','embedding']}
   event['arguments'].update(operation=dict(kind='enum',value='create'),artifact_type=dict(kind='enum',value='other'),outcome_certainty=dict(kind='enum',value='clear'),embedding=dict(kind='enum',value='direct'))
   event['readings'][0]['hypothesis_key']='interpretation.goal.cause_action';event['followup']=None
   raw['events'][0]=event
  return raw
 model=V1Double(build,policy,contribution=view)
 record,pipeline,_=run(model,capture=capture)
 trace=record.pipeline_trace
 out.append(dict(intervention=kind,model_calls=[{'stage':s,'payload':p} for s,p in model.calls],
  terminal=record.model_dump(mode='json'),raw_proposals=[c.model_dump(mode='json') for c in pipeline.two_call.captures],
  selected_actions=[m.definition_ref.action_key for m in trace.action_selection.plan.moves] if trace else None))
from test_cognitive_followup_handoff import followup_builder
older=json.load(gzip.open(OUT/'standard-cite_older-r1.json.gz'))['turns'][0]
question='Explain the unchanged optional-breaks stance and the stronger support from participants, using the earlier and recent assistant statements.'
sources=[('earlier_position',1,'I support optional breaks, never compulsory ones'),
         ('recent_position',9,'That strengthens my support for optional breaks; it does not make them compulsory'),
         ('new_information',8,'participants say optional pauses help them concentrate')]
def older_view(p):
 return dict(schema_version='contribution-proposal-v2',question_kind='general_knowledge',
  claims=[dict(text='The stance stayed optional breaks, never compulsory ones; participant reports strengthened its support.',confidence='high')],view=None)
model=V1Double(followup_builder(question,sources),policy,contribution=older_view)
record,pipeline,_=run(model,capture=CognitiveShadowCapture.model_validate(older['capture']))
trace=record.pipeline_trace
out.append(dict(intervention='same_older_history_forced_explanation',
 model_calls=[{'stage':s,'payload':p} for s,p in model.calls],terminal=record.model_dump(mode='json'),
 raw_proposals=[c.model_dump(mode='json') for c in pipeline.two_call.captures],
 selected_actions=[m.definition_ref.action_key for m in trace.action_selection.plan.moves] if trace else None))
with gzip.open(OUT/'counterfactuals.json.gz','wt') as f:json.dump(out,f,ensure_ascii=False)
for r in out:print(r['intervention'],r['terminal']['status'],r['selected_actions'],(r['terminal']['failure']or{}).get('code'))
