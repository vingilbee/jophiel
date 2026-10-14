"""Read-only evidence export for the updated-character frontend retest."""
import csv,gzip,json,uuid
from pathlib import Path
from collections import Counter
import psycopg
H=Path(__file__).resolve().parent
G=json.loads((H/'groups.json').read_text());ids=[uuid.UUID(g['conversation_id']) for g in G]
with psycopg.connect(host='127.0.0.1',port=55432,user='postgres',password='qa-disposable-only',dbname='cognitive_character_1012',row_factory=psycopg.rows.dict_row) as c:
 D={'alembic_version':c.execute('select version_num from alembic_version').fetchall(),'core_memory':c.execute("select * from core_memory where name='columbina'").fetchall()}
 for table in ('conversations','chat_messages','cognitive_shadow_jobs','cognitive_shadow_traces'):
  col='id' if table=='conversations' else 'conversation_id'
  D[table]=c.execute('select * from '+table+' where '+col+' = any(%s)',(ids,)).fetchall()
D=json.loads(json.dumps(D,default=str,ensure_ascii=False))
with gzip.open(H/'raw.json.gz','wt',encoding='utf-8',compresslevel=9) as f:json.dump(D,f,ensure_ascii=False)
U={m['turn_id']:m for m in D['chat_messages'] if m['role']=='user'};A={m['turn_id']:m for m in D['chat_messages'] if m['role']=='assistant'};T={t['turn_id']:t for t in D['cognitive_shadow_traces']};J={j['id']:j for j in D['cognitive_shadow_jobs']}
rows=[];summary=[]
for g in G:
 ms=sorted([m for m in U.values() if m['conversation_id']==g['conversation_id']],key=lambda m:m['created_at'])
 for i,m in enumerate(ms,1):
  t=T[m['turn_id']];cap=J[t['job_id']]['capture_payload'];req=t['compiled_request_payload'] or {};p=t['pipeline_trace_payload'] or {};r=t['realization_trace_payload'] or {};rr=t['realization_request_payload'] or {}
  con=p.get('orchestration',{}).get('contributions',[]);ss=p.get('cognitive_turn',{}).get('dynamic_state_update',{}).get('snapshot',{})
  views=[{'schema':v['proposal']['schema_version'],'proposal':v['proposal'],'supplied_character':v['payload'].get('character'),'mood':v['payload'].get('mood'),'personality':v['payload'].get('personality'),'prompt_version':v['provenance'].get('prompt_version'),'input_version':v['payload'].get('schema_version'),'prompt_hash':v['prompt_hash']} for v in con]
  opinions=[item for item in rr.get('content_items',[]) if item.get('source_kind')=='columbina_own_view']
  rows.append(dict(**g,turn=i,turn_id=m['turn_id'],question=m['content'],visible=A[m['turn_id']]['content'],visible_status=A[m['turn_id']]['status'],shadow=(r.get('final_candidate') or {}).get('text'),terminal=t['terminal_status'],realization_status=t['realization_status'],failure=(t['failure_payload'] or {}).get('code'),realization_failure=t.get('realization_failure_payload'),core_ref=req.get('core_memory_ref'),captured_core_version=cap['core_memory']['version'],captured_character=cap.get('character'),compiled_character=req.get('character'),legacy_values=req.get('values'),action_personality=req.get('action_personality'),contributions=views,worded_opinions=opinions,moves=[x.get('definition_ref',{}).get('action_key') for x in p.get('action_selection',{}).get('moves',[])],states={v['state_key']:v['score'] for v in ss.get('values',[]) if v['scope_type']=='conversation'},derived=ss.get('derived_controls'),delivery_controls=rr.get('delivery_profile',{}).get('controls'),state_delivery_sources=rr.get('delivery_profile',{}).get('state_sources'),predecessor=t['predecessor_trace_id'],segment_reset=t['segment_reset_reason'],realization_validation=r.get('validation'),realization_attempts=r.get('attempts')))
for condition in dict.fromkeys(g['condition'] for g in G):
 cells=[r for r in rows if r['condition']==condition]
 views=[v for r in cells for v in r['contributions'] if v['proposal']['question_kind']=='opinion']
 summary.append({'condition':condition,'turns':len(cells),'character_present':sum(bool(r['compiled_character']) for r in cells),'terminal':dict(Counter(r['terminal'] for r in cells)),'realization':dict(Counter(r['realization_status'] for r in cells)),'opinion_proposals':len(views),'basis_counts':dict(Counter(b['kind'] for v in views for b in (v['proposal'].get('view') or {}).get('basis',[]))),'input_versions':dict(Counter(v['input_version'] for r in cells for v in r['contributions'])),'playfulness_intensity':dict(Counter((r['delivery_controls'] or {}).get('playfulness_intensity') for r in cells)),'explicit_tentative_replies':sum('tentativ' in (r['shadow'] or '').lower() for r in cells),'literal_it_depends_replies':sum('it depends' in (r['shadow'] or '').lower() for r in cells)})
assert len(rows)==93 and len(U)==len(A)==len(T)==93
assert all(r['visible_status']=='completed' and r['visible'] for r in rows)
assert all(bool(r['captured_character'])==(r['condition']!='no_character') for r in rows)
assert all(bool(r['compiled_character'])==(r['condition']!='no_character') for r in rows)
# Verify model-supplied sections exactly match the compiled character, when supplied.
for r in rows:
 for v in r['contributions']:
  if v['input_version']=='contribution-input-v1.2':
   assert v['supplied_character']=={s:r['compiled_character'][s] for s in ['values','principles','beliefs']}
(H/'turns.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));(H/'summary.json').write_text(json.dumps(summary,indent=2))
fields=['condition','replicate','turn','turn_id','question','visible','shadow','terminal','realization_status','failure','core_ref','compiled_character','contributions','moves','states','derived','delivery_controls','segment_reset']
with (H/'turns.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
 for r in rows:w.writerow({k:json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(dict,list)) else r.get(k) for k in fields})
print(json.dumps(summary,indent=2))
