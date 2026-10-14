"""Evidence-only summaries, canonical state deltas and exact conversation transcript."""
import gzip,json,hashlib,statistics,collections
from pathlib import Path
OUT=Path(__file__).resolve().parent
runs=[json.load(gzip.open(p)) for p in sorted(OUT.glob('*-r*.json.gz'))]
assert len(runs)==24 and all('accepted_turns' in r for r in runs), 'wait for every run to terminate'
for f in ['freeze-manifest.json','contrast-freeze.json']:
 manifest=json.loads((OUT/f).read_text())
 assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in manifest['files'].items()), 'frozen source changed'
rows=[];states=[];first=[];failures=[]
for r in runs:
 for t in r['turns']:
  terminal=t.get('terminal')or{};f=terminal.get('failure')or{}
  trace=terminal.get('pipeline_trace')or{}
  view=[c['proposal'].get('view') for c in t.get('contributions',[]) if c['proposal'].get('view')]
  assessments=[c['proposal'].get('premise_assessment') for c in t.get('contributions',[])]
  row=dict(run=r['run'],turn=t['index'],accepted=t.get('realization_status')=='accepted_shadow',
    reply=t.get('reply'),contribution_views=view,assessments=assessments,pipeline_failure=f.get('code'),elapsed_seconds=t['elapsed_seconds'])
  if t.get('realization_status')=='rejected':
   row['realization_validation']=t['realized']['realization_trace']['validation']
  rows.append(row)
  if not row['accepted']:failures.append(row)
  if t['index']==1:first.append(row|dict(configuration=r['configuration']))
  req=terminal.get('compiled_request')
  if req and trace:
   update=trace['cognitive_turn']['dynamic_state_update'];before=req['previous_state']
   def key(v):return v['state_key'],v['scope_type'],v['scope_ref']
   previous={key(v):v for v in before['values']}
   deltas=[]
   for v in update['snapshot']['values']:
    p=previous.get(key(v))
    deltas.append(dict(state_key=v['state_key'],scope_type=v['scope_type'],scope_ref=v['scope_ref'],
      before=p['score'] if p else None,after=v['score'],band_before=p['band'] if p else None,band_after=v['band'],
      seeded=p is None,rule_refs=v.get('rule_contribution_refs',[]),evidence_refs=v['evidence_refs']))
   states.append(dict(run=r['run'],turn=t['index'],changes=deltas,contributions=update['contributions'],controls=update['snapshot']['derived_controls']))
(OUT/'semantic-evidence.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
(OUT/'state-trajectories.json').write_text(json.dumps(states,ensure_ascii=False,indent=2))
(OUT/'matched-opening-evidence.json').write_text(json.dumps(first,ensure_ascii=False,indent=2))
# Reconstruct existing-value deltas from current rule contributions and clamps.
changed=0;mismatches=[]
for row in states:
 for value in row['changes']:
  if value['before'] is None or value['before']==value['after']:continue
  applicable=[c for c in row['contributions'] if all(c[k]==value[k] for k in ['state_key','scope_type','scope_ref'])]
  delta=sum(c['bounded_step']*(1 if c['direction']=='increase' else -1 if c['direction']=='decrease' else 0) for c in applicable)
  floor=-100 if value['state_key'] in ['state.valence','state.social_alignment'] else 0
  expected=max(floor,min(100,value['before']+delta));changed+=1
  if expected!=value['after']:mismatches.append(dict(run=row['run'],turn=row['turn'],value=value,expected=expected))
(OUT/'state-delta-audit.json').write_text(json.dumps(dict(changed_existing_values_checked=changed,delta_reconstruction_mismatches=mismatches),indent=2))
assert not mismatches

summary=dict(runs=len(runs),attempted_turns=len(rows),accepted_turns=sum(r['accepted'] for r in rows),complete_chains=sum(r['complete'] for r in runs),
 original_runs=sum('conversational' not in r['run'] for r in runs),original_complete=sum(r['complete'] for r in runs if 'conversational' not in r['run']),
 contrast_runs=sum('conversational' in r['run'] for r in runs),contrast_complete=sum(r['complete'] for r in runs if 'conversational' in r['run']),
 pipeline_failures=dict(collections.Counter(r['pipeline_failure'] for r in failures if r['pipeline_failure'])),
 realization_rejections=sum('realization_validation' in r for r in failures),median_accepted_turn_seconds=statistics.median(r['elapsed_seconds'] for r in rows if r['accepted']),
 chain_results=[{k:r.get(k) for k in ['run','accepted_turns','complete','stop_reason']} for r in runs])
(OUT/'results.json').write_text(json.dumps(summary,indent=2))
lines=['# Exact closed-loop conversations','', 'Every assistant text below is an accepted cognitive response actually fed into the next turn. An unaccepted turn stops its chain; raw proposals remain in the linked evidence.','']
for r in runs:
 lines+=['## '+r['run'],'','Configuration: `'+json.dumps(r['configuration'])+'`','']
 for t in r['turns']:
  lines+=['### Turn '+str(t['index']),'','User: '+t['user'],'']
  if t.get('realization_status')=='accepted_shadow':lines+=['Assistant: '+t['reply'],'']
  else:
   f=(t.get('terminal')or{}).get('failure')or{}
   lines+=['**STOPPED; no accepted assistant response.** Pipeline: '+str(f.get('code'))+'; realization: '+str(t.get('realization_status')),'']
  if t.get('contributions'):
   lines+=['Contribution evidence:','```json',json.dumps([dict(proposal=c['proposal'],compiled=c.get('compiled_proposal')) for c in t['contributions']],ensure_ascii=False,indent=2),'```','']
 lines+=['Full stage inputs, raw model outputs and terminal/state: ['+r['run']+'.json.gz]('+r['run']+'.json.gz)','']
(OUT/'TRANSCRIPTS.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(summary,indent=2))
