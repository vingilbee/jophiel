import gzip,json,csv,collections
from pathlib import Path
from audit import checks,payload
OUT=Path(__file__).resolve().parent
runs=[json.load(gzip.open(p)) for p in sorted(OUT.glob('*-r*.json.gz'))]
assert len(runs)==92 and all('accepted_turns' in r for r in runs)
audits=[a for r in runs for a in checks(r)]
assert not any(v is False for a in audits for v in a.values())
assert not any(v is False for a in audits for st in a['stages'] for v in st.values())
assert all(all(v['history_exact'] and v['prior_accepted_reply_present'] for v in t['boundary_audit']) for r in runs for t in r['turns'])
assert len({json.dumps(r['personality'],sort_keys=True) for r in runs})==1
(OUT/'stage-audit.json').write_text(json.dumps(audits,indent=2))
md=(OUT/'FINDINGS.md').read_text().splitlines()+['','## Exact cases and intermediate evidence','']
observations=[]
for r in runs:
 md += [f"### {r['run']}",'',f"Family: {r['case']['family']}; contrast: {r['case']['contrast']}; history origin: {r['case']['source_mode']}. Accepted: {r['accepted_turns']}/{len(r['planned_prompts'])}. Raw evidence: [{r['run']}.json.gz]({r['run']}.json.gz).",'']
 if r['case'].get('notes'):md += [r['case']['notes'],'']
 for t in r['turns']:
  md += [f"#### Turn {t['index']}",'','**User (exact):**','',t['user'],'',f"Pipeline: `{t['status']}`. Realization: `{t['realization_status']}`.",'']
  if t.get('reply'):md += ['**Accepted cognitive response (exact):**','',t['reply'],'']
  else:md += ['**No accepted reply. The run stops here; nothing was inserted into its history.**','']
  if t['terminal']['failure']:md += ['Failure:','```json',json.dumps(t['terminal']['failure'],indent=2),'```','']
  md += ['<details><summary>Actual inputs, raw proposals, compilation and downstream evidence</summary>','']
  one=next(e for e in t['boundary'] if e['stage']=='call_one');p=payload(one)
  md += ['Actual Call 1 chronological input and focus indexing (from SDK arguments):','```json',json.dumps({'focus':p['focus'],'messages':p['dialogue_context']['messages']},ensure_ascii=False,indent=2),'```','']
  raw=json.loads(one['response']['choices'][0]['message']['content']) if one.get('response') else None
  md += ['Raw Call 1 response before adapter normalization:','```json',json.dumps(raw,ensure_ascii=False,indent=2),'```','']
  call=next((c for c in t['calls'] if c['stage']=='call_one'),None)
  md += ['Actual compiled proposal (null if compilation did not complete):','```json',json.dumps((call or {}).get('compiled_proposal'),ensure_ascii=False,indent=2),'```','']
  for e in t['boundary']:
   if e['stage']=='contribution':
    md += ['Actual contribution input (SDK boundary):','```json',json.dumps(payload(e),ensure_ascii=False,indent=2),'```','']
    md += ['Raw contribution response:','```json',e['response']['choices'][0]['message']['content'] if e.get('response') else json.dumps(e.get('error')),'```','']
  selected=[]
  for c in t['calls']:
   if c['stage']!='call_two':continue
   selected=[v for v in c['payload']['eligible_candidates'] if v['candidate_ref'] in c['proposal']['selected_candidate_refs']]
   md += ['Policy input conditions, raw selection and corresponding candidates:','```json',json.dumps({'conditions':c['payload']['planning_conditions'],'proposal':c['proposal'],'selected':selected},ensure_ascii=False,indent=2),'```','']
  tr=t['terminal']['pipeline_trace']
  if tr:
   md += ['Canonical goal definitions and selected moves:','```json',json.dumps({'goals':[g['definition_ref'] for g in tr['goal_snapshot']['records']],'moves':[m['definition_ref'] for m in tr['action_selection']['plan']['moves']]},indent=2),'```','']
  request=t['realized'].get('realization_request')
  if request:md += ['Realization authorized content and confidence:','```json',json.dumps(request['content_items'],ensure_ascii=False,indent=2),'```','']
  md += ['Actual-history boundary audit:','```json',json.dumps(t['boundary_audit'],indent=2),'```','','</details>','']
  observations.append(dict(run=r['run'],family=r['case']['family'],contrast=r['case']['contrast'],character=r['condition'],repeat=r['repeat'],turn=t['index'],pipeline=t['status'],realization=t['realization_status'],predicates=','.join(e['predicate_key'] for e in (raw or {}).get('events',[])),selected_actions=','.join(c['action_key'] for c in selected),failure=(t['terminal']['failure']or{}).get('code'),reply=t.get('reply')))
 if len(r['turns'])<len(r['planned_prompts']):
  md += ['**Unexecuted prompts after failure:**','']
  md += [f'{i}. {q}' for i,q in enumerate(r['planned_prompts'][len(r['turns']):],len(r['turns'])+1)]
  md += ['']
(OUT/'REPORT.md').write_text('\n'.join(md))
with (OUT/'observations.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(observations[0]));w.writeheader();w.writerows(observations)
print('Rendered',len(observations),'attempted turns with exact SDK inputs and outputs.')
