"""Independent checks of recorded SDK-boundary inputs and canonical lineage."""
import gzip,json,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.ai.Cognitive.system.v0.cognitive_pipeline_v0.models import CognitivePipelineRequest
from app.ai.Cognitive.system.v0.two_call.contribution import personality_view,mood_view

def payload(entry):
 c=entry['request']['messages'][-1]['content']
 return json.loads(c[len('INPUT_DATA\n'):-len('\nEND_INPUT_DATA')]) if c.startswith('INPUT_DATA\n') else json.loads(c)

def checks(run):
 result=[];previous=None
 for t in run['turns']:
  terminal=t.get('terminal')
  if not terminal:continue
  req=terminal.get('compiled_request')
  if not req:continue
  request=CognitivePipelineRequest.model_validate(req)
  traits=req['action_personality']
  expected_traits={f'persona:{k}':traits[k] for k in ['curiosity','warmth','playfulness','directness']}
  ch=t['capture']['character']
  row={'run':run['run'],'turn':t['index'],'compiled_character_exact':req['character']==ch,
       'compiled_numeric_exact':all(req[k]==run['personality'][k] for k in ['interpretation_biases','action_personality','state_personality']),
       'state_lineage_exact':previous is None or req['previous_state']==previous['pipeline_trace']['cognitive_turn']['dynamic_state_update']['snapshot'],
       'no_reset':previous is None or terminal['segment_reset_reason'] is None,
       'predecessor_terminal_exact':previous is None or terminal['predecessor_trace_id']==previous['terminal_id'],
       'stages':[]}
  for e in t['boundary']:
   p=payload(e);stage=e.get('stage',e['request']['response_format']['json_schema']['name'])
   c={'stage':stage}
   if 'context' in p:
    c['traits_exact']={v['ref']:v['value'] for v in p['context']['persona_traits']}==expected_traits
    c['biases_exact']={v['ref'].split(':',1)[1]:v['value'] for v in p['context']['interpretation_biases']}=={k:v for k,v in req['interpretation_biases'].items() if isinstance(v,(float,int))}
    expected=[{k:v[k] for k in ['state_key','scope_type','status','band','score']} for v in req['previous_state']['values'] if v['active']]
    actual=[{k:v[k] for k in ['state_key','scope_type','status','band','score']} for v in p['context']['dynamic_state_before']['values']]
    c['state_before_exact']=expected==actual
   if 'character' in p:
    c['character_exact']=p['character']=={k:ch[k] for k in ['values','principles','beliefs']}
    c['traits_projection_exact']=p['personality']==personality_view(request)
    c['mood_before_exact']=p['mood']==mood_view(request)
   if p.get('followup_context'):
    f=p['followup_context']
    c['followup_question_exact']=p['question_topic']==f['resolved_question']
    proposed=[v['followup'] for call in t['calls'] if call['stage']=='call_one'
              for v in call['proposal']['events'] if v.get('followup') and v['followup']['resolved_question']==f['resolved_question']]
    c['followup_matches_interpretation']=len(proposed)==1 and proposed[0]['operation']==f['operation']
    c['followup_sources']=[]
    for source in f['sources']:
     i=int(source['message_ref'].split(':')[1]);message=t['capture']['messages'][i]
     check={'ref':source['message_ref'],'role':source['role'],
       'excerpt_exact':message['content'][source['start']:source['end']]==source['text'],
       'speaker_exact':source['speaker']==message['role'],
       'source_message_exact':source.get('source_message_text',message['content'])==message['content'],
       'position_is_previous_assistant':source['role'] not in ('earlier_position','recent_position') or (message['role']=='assistant' and i<len(t['capture']['messages'])-1),
       'outside_recent_window':i<len(t['capture']['messages'])-8}
     c['followup_sources'].append(check)
    c['all_followup_sources_valid']=all(all(s[k] for k in ['excerpt_exact','speaker_exact','source_message_exact','position_is_previous_assistant']) for s in c['followup_sources'])
   if 'state_after' in p:
    c['traits_exact']={v['ref']:v['value'] for v in p['personality']}==expected_traits
    trace=terminal.get('pipeline_trace')
    if trace:
     state=trace['cognitive_turn']['dynamic_state_update']['snapshot']
     expected=[{k:v[k] for k in ['state_key','scope_type','status','band','score']} for v in state['values'] if v['active']]
     actual=[{k:v[k] for k in ['state_key','scope_type','status','band','score']} for v in p['state_after']['values']]
     c['state_after_exact']=expected==actual
   if 'delivery_controls' in p:
    profile=t['realized']['realization_request']['delivery_profile']
    c['delivery_controls_exact']=p['delivery_controls']==profile['controls']
    c['numeric_sources_exact']=all(float(v['value'])==traits[v['trait']] for v in profile['trait_sources'])
    state=terminal['pipeline_trace']['cognitive_turn']['dynamic_state_update']['snapshot']
    c['post_state_source_exact']=profile['state_snapshot_ref']==state['state_snapshot_id']
    c['state_bands_exact']=all(v['band']==state['derived_controls'][v['control']] for v in profile['state_sources'])
   row['stages'].append(c)
  result.append(row)
  if t.get('realization_status') in ('accepted','accepted_shadow') and terminal['status']=='completed':previous=terminal
 return result

if __name__=='__main__':
 rows=[]
 for p in sorted(OUT.glob('*-r*.json.gz')):
  rows.extend(checks(json.load(gzip.open(p))))
 (OUT/'stage-audit.json').write_text(json.dumps(rows,indent=2))
 failures=[]
 for r in rows:
  failures.extend((r['run'],r['turn'],k) for k,v in r.items() if v is False)
  failures.extend((r['run'],r['turn'],s['stage'],k) for s in r['stages'] for k,v in s.items() if v is False)
 print('turns audited',len(rows),'mismatches',failures)
