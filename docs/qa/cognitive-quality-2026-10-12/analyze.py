"""Summarize persisted frontend QA evidence; never calls a model or changes DB."""
import csv, gzip, json, sys
from pathlib import Path
from collections import Counter
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'backend'))
from app.ai.Cognitive.system.v0.two_call.contribution import Take, choose_take, take_scores
HERE=Path(__file__).resolve().parent
D=json.loads(gzip.decompress((HERE/'raw.json.gz').read_bytes())); G=json.loads((HERE/'groups.json').read_text())
messages={m['turn_id']:m for m in D['chat_messages'] if m['role']=='user'}
assistants={m['turn_id']:m for m in D['chat_messages'] if m['role']=='assistant'}
traces={t['turn_id']:t for t in D['cognitive_shadow_traces']}
rows=[];profiles={}
for g in G:
 turns=sorted([m for m in messages.values() if m['conversation_id']==g['conversation_id']],key=lambda m:m['created_at'])
 for number,m in enumerate(turns,1):
  t=traces[m['turn_id']];p=t['pipeline_trace_payload'] or {};r=t['realization_trace_payload'] or {};req=t['compiled_request_payload'] or {}
  rr=t['realization_request_payload'] or {};ss=((p.get('cognitive_turn') or {}).get('dynamic_state_update') or {}).get('snapshot') or {}
  opinions=[]
  for item in rr.get('content_items',[]):
   if item.get('source_kind')=='columbina_own_view':
    opinions.append({'predicate':item['predicate'],'text':item['arguments'][0]['value']})
  selected=[]
  for con in (p.get('orchestration') or {}).get('contributions',[]):
   if con['proposal']['question_kind']=='opinion':
    takes=tuple(Take.model_validate(x) for x in con['proposal']['takes'])
    request=SimpleNamespace(action_personality=SimpleNamespace(**req['action_personality']),interpretation_biases=SimpleNamespace(**req['interpretation_biases']))
    index=choose_take(takes,request)
    selected.append(dict(take=takes[index].model_dump(),scores=[str(x) for x in take_scores(takes,request)],index=index,takes=con['proposal']['takes']))
  if req and g['condition'] in ('columbina','blunt','playful'):profiles.setdefault(g['condition'],dict(action=req['action_personality'],biases=req['interpretation_biases']))
  final=(r.get('final_candidate') or {}).get('text')
  row=dict(evaluation_scope='visible_no_character_control' if g['condition']=='no_character' else 'shadow_candidate',**g,turn=number,turn_id=m['turn_id'],question=m['content'],visible=assistants[m['turn_id']]['content'],shadow=final,terminal=t['terminal_status'],realization=t['realization_status'],failure=(t.get('failure_payload') or {}).get('code'),selected=selected,worded_opinions=opinions,states={v['state_key']:v['score'] for v in ss.get('values',[]) if v['scope_type']=='conversation'},derived=ss.get('derived_controls'),delivery_controls=(rr.get('delivery_profile') or {}).get('controls'),segment_reset=t['segment_reset_reason'],predecessor=t['predecessor_trace_id'],values_count=len(req.get('values',[])),warnings=(r.get('validation') or {}).get('warnings'),moves=[x.get('definition_ref',{}).get('action_key') for x in p.get('action_selection',{}).get('moves',[])])
  rows.append(row)
(HERE/'turns.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
fields=['evaluation_scope','condition','replicate','turn','turn_id','question','visible','shadow','terminal','realization','failure','selected','states','derived','delivery_controls','segment_reset','predecessor']
with (HERE/'turns.csv').open('w',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
 for row in rows:writer.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row.get(k),(list,dict)) else row.get(k) for k in fields})
# Hold each recorded candidate pool fixed; vary only the deterministic personality scorer.
paired=[]
for row in rows:
 if row['condition'] not in ('columbina','blunt','playful'):continue
 for proposal in row['selected']:
  takes=tuple(Take.model_validate(t) for t in proposal['takes']);choices={}
  for name,profile in profiles.items():
   request=SimpleNamespace(action_personality=SimpleNamespace(**profile['action']),interpretation_biases=SimpleNamespace(**profile['biases']))
   i=choose_take(takes,request);choices[name]={'index':i,**takes[i].model_dump()}
  paired.append(dict(turn_id=row['turn_id'],condition=row['condition'],replicate=row['replicate'],turn=row['turn'],choices=choices))
(HERE/'paired-candidate-rescoring.json').write_text(json.dumps(paired,ensure_ascii=False,indent=2))
summary={}
for condition in dict.fromkeys(g['condition'] for g in G):
 cells=[r for r in rows if r['condition']==condition]
 summary[condition]={'turns':len(cells),'terminal':dict(Counter(r['terminal'] for r in cells)),'realization':dict(Counter(r['realization'] for r in cells)),'selected_stance':dict(Counter(s['take']['stance'] for r in cells for s in r['selected'])),'selected_tone':dict(Counter(s['take']['tone'] for r in cells for s in r['selected'])),'playfulness_intensity':dict(Counter((r['delivery_controls'] or {}).get('playfulness_intensity') for r in cells)),'literal_it_depends':sum('it depends' in (r['shadow'] or '').lower() for r in cells),'explicit_tentative':sum('tentativ' in (r['shadow'] or '').lower() for r in cells)}
summary['no_character']['interpretation_note']='Only visible replies are a no-character baseline. Its shadow remains Columbina and is not a no-character cognitive baseline.'
summary['paired_rescoring']={'pools':len(paired),'blunt_changes_choice':sum(p['choices']['blunt']['index']!=p['choices']['columbina']['index'] for p in paired),'playful_changes_choice':sum(p['choices']['playful']['index']!=p['choices']['columbina']['index'] for p in paired)}
(HERE/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
