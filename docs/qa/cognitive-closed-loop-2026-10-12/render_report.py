"""Render exact transcripts and inspectable intermediate evidence; no quality scorer."""
import gzip,json,collections,hashlib,re
from pathlib import Path
from audit import checks,payload
OUT=Path(__file__).resolve().parent
runs=[json.load(gzip.open(p)) for p in sorted(OUT.glob('*-r*.json.gz'))]
rows=[(r,t) for r in runs for t in r['turns']]
audits=[a for r in runs for a in checks(r)]
assert not any(v is False for a in audits for k,v in a.items())
assert not any(v is False for a in audits for s in a['stages'] for k,v in s.items())
assert all(all(v.get('history_exact') and v.get('prior_accepted_reply_present') and v.get('character_exact',True) for v in t['boundary_audit']) for _,t in rows)
assert len({json.dumps(r['personality'],sort_keys=True) for r in runs})==1
assert all('accepted_turns' in r for r in runs),'Do not render unfinished experiments'
(OUT/'stage-audit.json').write_text(json.dumps(audits,indent=2))
(OUT/'summary.json').write_text(json.dumps([{k:r.get(k) for k in ['run','condition','topic','repeat','complete','accepted_turns','stop_reason']} for r in runs],indent=2))
accepted=sum(t.get('realization_status') in ('accepted','accepted_shadow') and t.get('status')=='completed' for _,t in rows)
stages=collections.Counter(e.get('stage') for _,t in rows for e in t['boundary'])
failures=collections.Counter()
for r,t in rows:
 if t.get('realization_status') in ('accepted','accepted_shadow') and t.get('status')=='completed':continue
 f=(t.get('terminal') or {}).get('failure')
 failures[f['code'] if f else 'realization.'+str(t.get('realization_status'))]+=1
md=['# Closed-loop cognitive conversation evaluation — 2026-10-12','',
 '## Scope and method','',
 'Real `gpt-6-luna` calls through the canonical `cognitive-two-call-v1` pipeline, contribution and realization providers. This is an in-process evaluation of cognitive conversation continuity, not a frontend, worker, or database persistence test. The development default remains outside this scope. No application fixes were implemented.','',
 'Only synthetic user prompts and a copied application character configuration were used. The process points database and Redis URLs at unavailable local ports and creates no application sessions, users or messages. Original live user histories were not read. `source-core.json` is the configuration-only version-2 Columbina snapshot already used in the preceding QA; `opposing-character.json` changes written values/principles/beliefs while every numeric projection remains byte-for-byte identical. Warmth 0.80, directness 0.62, curiosity 0.88, playfulness 0.58.','',
 'Four fixed eight-turn conversations × two written characters × three repeats were declared before the main run. A separately labeled six-turn small-talk cohort × two characters × three repeats was added after all three original standard small-talk chains stopped early. It uses explicit topic wording; it is not a continuation or replacement of a failed chain. Both cohorts and every attempted turn are included. Three repeats describe recurrence in this sample, not statistical reliability. There is no no-character baseline in this evaluation, so it cannot isolate Luna’s training defaults causally.','',
 'Each accepted realization becomes the exact next assistant record. The next turn receives the preceding completed terminal record, canonical state and goals, incrementally updated dialogue memory, and earlier interpreted-dialogue records. First-turn initialization occurs once per independent run. A failed pipeline or unaccepted realization ends that run immediately; remaining prompts are explicitly unexecuted. Contract-accepted declines remain actual replies and are fed forward. No placeholder, visible live answer, retry of a failed turn, or silent state reset is used. Realization’s existing maximum of two internal attempts remains enabled.','',
 'The SDK-boundary wrapper records the actual final `chat.completions.create` arguments and raw provider response before parsing. It does not record credentials. These are independent of the harness’s intended history. `store=false`, SDK retries zero, temperature zero, reasoning effort none, timeouts 60 seconds, cognitive output budget 6000 tokens, realization output budget 1200 tokens. Temperature zero does not make repeated calls deterministic.','',
 '## Input verification','',
 f'All {len(audits)} compiled turns passed independent stage-input checks; all recorded history checks passed. No successor reset or substituted assistant response was found. {sum(stages.values())} actual provider requests were retained, including failed/invalid outputs.','',
 '| Stage | Actual input checked | Written character, personality and state |','|---|---|---|',
 '| Interpretation / Call 1 | Complete captured chronological history, including her exact accepted replies; up to 24 messages | Exact four numeric traits, seven interpretation biases and pre-turn state scores/bands. Written character is intentionally not an input here. |',
 '| Contribution | Exact last eight messages including current user | Exact written character; numeric traits and charitable prior mapped to expected bands; mood from canonical pre-turn state. |',
 '| Action policy / Call 2 | Exact last eight messages including current user | Exact numeric traits and canonical post-appraisal state scores/bands. Character acts through contributed content, not a separately repeated character block. |',
 '| Realization | Exact last eight messages before current user, when present | Exact delivery controls; numeric trait source values and post-turn state reference/derived bands verified. Selected meaning arrives as authorized content. |','',
 'The eight-message windows are real limitations: later contribution/policy/realization inputs omit the first exchange, even though Call 1 still has it. `older_messages_outside_window` is recorded for every request. “History was sent” therefore does not imply every stage directly saw every old position. This matters for retrospective summaries and is separated from errors that occur while the relevant recent reply is present.','',
 'An initial smoke run stopped on a harness enum/string assumption after its first accepted response. It is preserved as `harness-smoke.json.gz`, excluded from conversation counts, and was never continued. The boundary-audit code was corrected before the two declared cohorts. A post-processing predecessor check was corrected to use terminal ID, as the production lineage contract specifies; no model run or state was changed.','',
 '## Contract acceptance and coverage','',
 f'{len(runs)} independent runs, {len(rows)} attempted turns, {accepted} accepted replies. {sum(r["complete"] for r in runs)} runs completed their entire declared conversation. {sum(r["accepted_turns"]>=6 for r in runs)} runs produced at least six consecutive accepted replies. {sum(len(r["planned_prompts"])-len(r["turns"]) for r in runs)} planned prompts were not executed after failure. Acceptance is not a quality pass.','',
 '| Conversation | Character | Full completed runs | Consecutive accepted replies per repeat |','|---|---|---|---|']
for topic in ['opinion','values','preference','revision','opinion_compact']:
 for condition in ['standard','opposing']:
  group=sorted([r for r in runs if r['topic']==topic and r['condition']==condition],key=lambda r:r['repeat'])
  md.append(f'| {topic} | {condition} | {sum(r["complete"] for r in group)}/{len(group)} | '+', '.join(str(r['accepted_turns']) for r in group)+' |')
md+=['','| Unaccepted-turn failure | Count |','|---|---|']
for code,n in failures.items():md.append(f'| `{code}` | {n} |')
md+=['','Generic `pipeline.trace_validation` failures are located only at completed-pipeline invariant validation by the stored safe failure. Earlier calls may look reasonable; these artifacts do not establish the particular violated invariant. No earlier semantic cause is invented. Invalid Call 1 span proposals are interpretation/understanding-compilation failures. Realization failures include all raw attempts and validation codes in the appendix and compressed traces.','',
 '## Cognitive judgments','', (OUT/'findings.md').read_text() if (OUT/'findings.md').exists() else 'Analysis pending.','',
 '## Reproduction and artifacts','',
 '`evaluate.py` contains the fixed prompts, synthetic ledger and boundary logger. Main: `backend/.venv/bin/python docs/qa/cognitive-closed-loop-2026-10-12/evaluate.py`; supplemental cohort: append `--compact`. It requires the existing provider credential and intentionally never serializes it. `audit.py` independently checks captured provider inputs against canonical request/state records. `render_report.py` refuses to render until all run artifacts are settled and checks pass.','',
 'Each `<character>-<conversation>-r<repeat>.json.gz` retains exact captures, compiled requests, pipeline traces when accepted, Call 1/Call 2 proposals and compilations, contribution basis/tensions, realization traces, exact SDK-boundary requests and raw provider outputs. `stage-audit.json` records per-stage character/personality/state checks. `summary.json` records coverage. The appendix below contains every exact accepted reply, every unaccepted attempt and relevant intermediate evidence.','',
 '## Exact conversations and intermediate evidence','']
for r in runs:
 md += [f'### {r["run"]}', '',f'Accepted turns: {r["accepted_turns"]}/{len(r["planned_prompts"])}. Complete: {r["complete"]}. Raw evidence: [{r["run"]}.json.gz]({r["run"]}.json.gz).','']
 for t in r['turns']:
  md += [f'#### Turn {t["index"]}', '', '**User (exact):**', '',t['user'],'',
          f'**Contract:** pipeline `{t.get("status")}`; realization `{t.get("realization_status")}`.','']
  if t.get('reply'):
   md += ['**Accepted cognitive reply (exact; fed forward if another turn follows):**','',t['reply'],'']
  else:md += ['**No accepted reply. This run stopped; nothing was inserted into history.**','']
  f=(t.get('terminal') or {}).get('failure')
  if f:md += [f'Failure: `{f["code"]}`. Stage `{f["stage"]}`. {f["safe_message"]}','']
  if t.get('harness_error'):md += ['Harness error: '+json.dumps(t['harness_error']),'']
  md+=['<details><summary>Intermediate evidence and actual-input audit</summary>','']
  c1=next((c for c in t.get('calls',[]) if c['stage']=='call_one'),None)
  if c1:
   md+=['Interpretation — compiled frames and dialogue updates:','```json',json.dumps(c1.get('compiled_proposal'),ensure_ascii=False,indent=2),'```','']
  for c in t.get('contributions',[]):
   md+=['Contribution — exact proposed view/claims, confidence, cited basis and recognized tensions:','```json',json.dumps(c.get('proposal'),ensure_ascii=False,indent=2),'```','']
  c2=next((c for c in t.get('calls',[]) if c['stage']=='call_two'),None)
  if c2:
   selected=(c2.get('proposal') or {}).get('selected_candidate_refs',[])
   candidates=[c for c in c2['payload']['eligible_candidates'] if c['candidate_ref'] in selected]
   md+=['Action policy — exact selection and corresponding candidates:','```json',json.dumps({'proposal':c2.get('proposal'),'selected_candidates':candidates},ensure_ascii=False,indent=2),'```','']
  rr=(t.get('realized') or {}).get('realization_request')
  rt=(t.get('realized') or {}).get('realization_trace')
  if rr:
   md+=['Realization — authorized selected content (epistemic status/confidence retained):','```json',json.dumps(rr['content_items'],ensure_ascii=False,indent=2),'```','']
  if rt:
   for a in rt['attempts']:
    md+=[f'Realization attempt {a["attempt_number"]}: `{a["outcome"]}`; provider error `{a.get("provider_error_code")}`.','']
    if a.get('candidate') and (a['outcome'] not in ('accepted','candidate_accepted') or not t.get('reply')):md+=['Unaccepted attempt text (not fed into the next turn):','',a['candidate']['text'],'']
    if a.get('validation'):md+=['```json',json.dumps(a['validation'],ensure_ascii=False,indent=2),'```','']
  if not c1:
   for e in t['boundary']:
    if e.get('stage')=='call_one' and e.get('response'):
     md+=['Call 1 raw rejected provider output:','```json',e['response']['choices'][0]['message']['content'],'```','']
  md+=['Actual SDK-boundary history audit:','```json',json.dumps(t.get('boundary_audit'),indent=2),'```','', '</details>','']
 if len(r['turns'])<len(r['planned_prompts']):
  md+=['**Remaining prompts, not executed:**','']
  md += [f'{i}. {p}' for i,p in enumerate(r['planned_prompts'][len(r['turns']):],len(r['turns'])+1)]
  md+=['']
(OUT/'REPORT.md').write_text('\n'.join(md))
print(json.dumps({'runs':len(runs),'attempts':len(rows),'accepted':accepted,'full':sum(r['complete'] for r in runs),'failures':failures,'stage_calls':stages},indent=2))
