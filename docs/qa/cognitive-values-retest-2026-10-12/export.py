"""Read-only export of the three database-seeded frontend values conversations."""
import csv,gzip,json,uuid
from pathlib import Path
from collections import Counter
import psycopg
H=Path(__file__).resolve().parent
G=json.loads((H/'groups.json').read_text());ids=[uuid.UUID(g['conversation_id']) for g in G]
with psycopg.connect(host='127.0.0.1',port=55432,user='postgres',password='qa-disposable-only',dbname='cognitive_quality_1012',row_factory=psycopg.rows.dict_row) as c:
 D={'alembic_version':c.execute('select version_num from alembic_version').fetchall(),'core_memory':c.execute("select * from core_memory where name='columbina'").fetchall()}
 for table in ('conversations','chat_messages','cognitive_shadow_jobs','cognitive_shadow_traces'):
  col='id' if table=='conversations' else 'conversation_id'
  D[table]=c.execute('select * from '+table+' where '+col+' = any(%s)',(ids,)).fetchall()
# Serialize UUIDs/dates only; all model payloads remain intact.
D=json.loads(json.dumps(D,default=str,ensure_ascii=False))
with gzip.open(H/'raw.json.gz','wt',encoding='utf-8',compresslevel=9) as f:json.dump(D,f,ensure_ascii=False)
U={m['turn_id']:m for m in D['chat_messages'] if m['role']=='user'};A={m['turn_id']:m for m in D['chat_messages'] if m['role']=='assistant'}
T={t['turn_id']:t for t in D['cognitive_shadow_traces']};J={j['id']:j for j in D['cognitive_shadow_jobs']}
rows=[];summary=[]
for g in G:
 db=json.loads((H/(g['condition']+'-db.json')).read_text())
 ms=sorted([m for m in U.values() if m['conversation_id']==g['conversation_id']],key=lambda m:m['created_at'])
 cells=[]
 for i,m in enumerate(ms,1):
  t=T[m['turn_id']];cap=J[t['job_id']]['capture_payload'];req=t['compiled_request_payload'];p=t['pipeline_trace_payload'];r=t['realization_trace_payload'] or {}
  calls=[]
  for call in (p or {}).get('orchestration',{}).get('calls',[]):
   payload=call.get('payload') or {}
   if 'context' in payload and 'agent_values' in payload['context']:calls.append({'stage':call['stage'],'agent_values':payload['context']['agent_values']})
  row=dict(condition=g['condition'],turn=i,turn_id=m['turn_id'],conversation_id=g['conversation_id'],question=m['content'],visible=A[m['turn_id']]['content'],shadow=(r.get('final_candidate') or {}).get('text'),terminal=t['terminal_status'],failure=(t['failure_payload'] or {}).get('code'),stored_core_version=db['stored']['version'],captured_core_version=cap['core_memory']['version'],stored_values=db['stored']['values'],captured_values_field=cap.get('values','FIELD_ABSENT'),values_explicitly_omitted='values' in cap['omitted_inputs'],compiled_values=req.get('values') if req else None,model_agent_values=calls)
  assert row['stored_core_version']==row['captured_core_version']
  rows.append(row);cells.append(row)
 summary.append({'condition':g['condition'],'turns':len(cells),'database_values_nonempty':bool(db['stored']['values']),'values_omitted_count':sum(r['values_explicitly_omitted'] for r in cells),'compiled_empty_values_count':sum(r['compiled_values']==[] for r in cells),'completed':sum(r['terminal']=='completed' for r in cells),'failed':sum(r['terminal']=='failed' for r in cells),'fixed_traits_sha256':db['fixed_traits_sha256']})
assert len(rows)==18 and len(U)==len(A)==len(T)==18
assert len({x['fixed_traits_sha256'] for x in summary})==1
(H/'turns.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
with (H/'turns.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader()
 for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()})
(H/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
