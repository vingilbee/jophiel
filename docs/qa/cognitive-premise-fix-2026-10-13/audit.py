"""Independent boundary, source-binding and predecessor audit; no provider calls."""
import importlib.util,gzip,json
from pathlib import Path
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('prior_audit',OUT.parent/'cognitive-root-cause-2026-10-13/audit.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
rows=[]
for path in sorted(OUT.glob('*-r*.json.gz')):
 run=json.load(gzip.open(path));checks=a.checks(run)
 for t,c in zip(run['turns'],checks):
  c['history_audits_exact']=all(v.get('history_exact',True) and v.get('prior_accepted_reply_present',True) and v.get('character_exact',True) for v in t.get('boundary_audit',[]))
  if 'common_prefix_sha256' in run:
   prefix_path=OUT/(run['condition']+'-prefix-r1.json.gz')
   import hashlib
   prefix=json.load(gzip.open(prefix_path))['turns'][0];prev=prefix['terminal'];cur=t['terminal']
   c['prefix_file_exact']=hashlib.sha256(prefix_path.read_bytes()).hexdigest()==run['common_prefix_sha256']
   c['state_lineage_exact']=cur['compiled_request']['previous_state']==prev['pipeline_trace']['cognitive_turn']['dynamic_state_update']['snapshot']
   c['predecessor_terminal_exact']=cur['predecessor_trace_id']==prev['terminal_id']
   c['no_reset']=cur['segment_reset_reason'] is None
  for entry in t['boundary']:
   p=a.payload(entry)
   if 'premise_context' not in p:continue
   statements=p['premise_context']['assistant_statements'];messages=t['capture']['messages']
   c['premise_current_user_exact']=p['premise_context']['user_utterance']==t['user']
   valid=True
   for s in statements:
    index=int(s['message_ref'].split(':')[1]);m=messages[index]
    valid &= m['role']=='assistant' and index<len(messages)-1
    if s['coverage']=='full_message':valid &= s['text']==m['content']
    else:
     _,_,start,end=s['ref'].split(':');valid &= m['content'][int(start):int(end)]==s['text']
   c['premise_speech_exact']=bool(valid)
   for capture in t['contributions']:
    if capture['payload']!=p:continue
    refs=capture['proposal']['premise_assessment']['position_refs']
    expected=[next(s for s in statements if s['ref']==ref) for ref in refs]
    c['premise_compilation_exact']=capture['compiled_proposal']==dict(assessment=capture['proposal']['premise_assessment'],recorded_speech=expected)
 rows.extend(checks)
(OUT/'stage-audit.json').write_text(json.dumps(rows,indent=2))
failures=[]
for r in rows:
 failures.extend((r['run'],r['turn'],k) for k,v in r.items() if v is False)
 failures.extend((r['run'],r['turn'],s['stage'],k) for s in r['stages'] for k,v in s.items() if v is False)
print('turns audited',len(rows),'mismatches',failures)
assert not failures
