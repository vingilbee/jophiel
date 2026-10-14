"""Offline root-cause inspection of real boundaries; no provider calls or fixes."""
import copy,gzip,json,sys,os,collections
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
os.environ['DATABASE_URL']='postgresql+psycopg://localhost:1/no_database'
os.environ['REDIS_URL']='redis://localhost:1/0'
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
import jsonschema
from app.ai.Cognitive.system.v0.two_call import v1
from audit import payload

def schema_check(raw,schema):
 try:jsonschema.validate(raw,schema);return {'accepted':True}
 except Exception as e:return {'accepted':False,'error':str(e)[:300]}

def compile_check(raw,p,refs):
 try:return {'accepted':True,'compiled':v1.compile_understanding(raw,p,refs).model_dump(mode='json')}
 except Exception as e:return {'accepted':False,'type':type(e).__name__,'error':str(e)}

def citations(raw,p):
 texts={m['ref']:m for m in p['dialogue_context']['messages']}
 out=[]
 def walk(value,path,source=None):
  if isinstance(value,dict):
   source=value.get('message_ref',source)
   for k,v in value.items():walk(v,path+'.'+k,source)
  elif isinstance(value,list):
   for i,v in enumerate(value):walk(v,path+f'[{i}]',source)
  elif isinstance(value,str) and value.startswith('span:'):
   ref=source or p['focus']['message_ref'];m=texts.get(ref);tokens=v1.focus_tokens(m['text']) if m else ()
   row={'path':path,'span':value,'source_ref':ref,'speaker':m['speaker'] if m else None,'token_count':len(tokens)}
   try:
    a,b=v1.span_bounds(value,tokens);row.update(valid=True,start=a,end=b,excerpt=m['text'][a:b],excerpt_over_600=b-a>600)
   except Exception as e:row.update(valid=False,error=str(e))
   out.append(row)
 walk(raw,'raw')
 return out

def inspect(run):
 rows=[]
 for t in run['turns']:
  b=next((e for e in t['boundary'] if e['stage']=='call_one'),None)
  if not b or not b.get('response'):continue
  raw=json.loads(b['response']['choices'][0]['message']['content']);p=payload(b)
  c=next((c for c in t['calls'] if c['stage']=='call_one'),None)
  refs=t.get('reference_table') or (c or {}).get('reference_table') or {}
  row={'run':run['run'],'case':run['case']['id'],'family':run['case']['family'],'contrast':run['case']['contrast'],
       'turn':t['index'],'schema':schema_check(raw,b['request']['response_format']['json_schema']['schema']),
       'compilation_replay':compile_check(raw,p,refs),
       'raw_predicates':[e['predicate_key'] for e in raw['events']],
       'embeddings':[{k:v for k,v in e['arguments'].get('embedding',{}).items()} for e in raw['events']],
       'citations':citations(raw,p),'raw_events':raw['events'],
       'compiled_actual':c.get('compiled_proposal') if c else None,
       'contribution_inputs':[payload(e) for e in t['boundary'] if e['stage']=='contribution'],
       'contribution_proposals':[c['proposal'] for c in t['contributions']],
       'terminal_status':t['status'],'realization_status':t['realization_status'],
       'failure':t['terminal']['failure'],'reply':t.get('reply')}
  rows.append(row)
 return rows

def probes(runs):
 out=[]
 run=next(r for r in runs if r['topic']=='cite_short' and r['condition']=='standard')
 t=run['turns'][0];b=t['boundary'][0];p=payload(b);raw=json.loads(b['response']['choices'][0]['message']['content'])
 position=next((i,j) for i,e in enumerate(raw['events']) if e.get('followup') for j,s in enumerate(e['followup']['sources']) if s['role'] in ['earlier_position','recent_position'])
 i,j=position;cite=raw['events'][i]['followup']['sources'][j]['citation'];source=next(m for m in p['dialogue_context']['messages'] if m['ref']==cite['message_ref']);n=len(v1.focus_tokens(source['text']))
 for name,ref in [('inclusive_last',f'span:0-{n-1}'),('exclusive_style_one_past',f'span:0-{n}'),('reversed_range','span:2-1')]:
  value=copy.deepcopy(raw);value['events'][i]['followup']['sources'][j]['citation']['span_ref']=ref
  out.append({'probe':name,'origin':run['run'],'source_token_count':n,'mutated_ref':ref,
    'schema':schema_check(value,b['request']['response_format']['json_schema']['schema']),
    'compiler':compile_check(value,p,t['reference_table'])})
 value=copy.deepcopy(raw);value['events'][i]['followup']['sources'][j]['citation']['message_ref']=p['focus']['message_ref'];value['events'][i]['followup']['sources'][j]['citation']['span_ref']='span:0'
 out.append({'probe':'position_role_assigned_current_user','schema':schema_check(value,b['request']['response_format']['json_schema']['schema']),'compiler':compile_check(value,p,t['reference_table'])})
 # Deliberately wrong semantic restatement: checks existence/bounds, not entailment.
 value=copy.deepcopy(raw);value['events'][i]['followup']['resolved_question']='Explain why the assistant supposedly preferred midnight rather than dawn.'
 out.append({'probe':'contradictory_but_sourced_restatement','schema':schema_check(value,b['request']['response_format']['json_schema']['schema']),'compiler':compile_check(value,p,t['reference_table'])})
 # The same literal source span has an independent 600-character limit.
 for name in ['cite_long_under','cite_long_over']:
  r=next(r for r in runs if r['topic']==name and r['condition']=='standard');t=r['turns'][0];b=t['boundary'][0];p=payload(b);raw=json.loads(b['response']['choices'][0]['message']['content'])
  pair=next((i,j) for i,e in enumerate(raw['events']) if e.get('followup') for j,s in enumerate(e['followup']['sources']) if s['role'] in ['earlier_position','recent_position'])
  i,j=pair;ref=raw['events'][i]['followup']['sources'][j]['citation']['message_ref'];m=next(m for m in p['dialogue_context']['messages'] if m['ref']==ref);n=len(v1.focus_tokens(m['text']))
  value=copy.deepcopy(raw);value['events'][i]['followup']['sources'][j]['citation']['span_ref']=f'span:0-{n-1}'
  out.append({'probe':name+'_whole_valid_token_range','source_characters':len(m['text']),'schema':schema_check(value,b['request']['response_format']['json_schema']['schema']),'compiler':compile_check(value,p,t['reference_table'])})
 return out

if __name__=='__main__':
 runs=[json.load(gzip.open(f)) for f in sorted(OUT.glob('*-r*.json.gz'))]
 rows=[v for r in runs for v in inspect(r)]
 with gzip.open(OUT/'inspection.json.gz','wt') as f:json.dump(rows,f,ensure_ascii=False)
 if '--probes' in sys.argv:(OUT/'contract-probes.json').write_text(json.dumps(probes(runs),ensure_ascii=False,indent=2))
 print('Inspected',len(rows),'real Call 1 boundaries. Schema rejected:',sum(not v['schema']['accepted'] for v in rows),
       'compile rejected:',sum(not v['compilation_replay']['accepted'] for v in rows),
       'invalid numeric citations:',sum(not c['valid'] for v in rows for c in v['citations']))
