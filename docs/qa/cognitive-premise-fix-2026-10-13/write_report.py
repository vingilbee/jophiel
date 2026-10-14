"""Render saved evidence only; does not call the model or tune implementation."""
import gzip,json,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent
load=lambda p:json.load(gzip.open(p))
files=sorted(OUT.glob('*-r*.json.gz'))
runs=[load(p) for p in files]
assert len(runs)==58 and all(x['turns'][0]['realization_status']=='accepted_shadow' for x in runs)
manifest=json.loads((OUT/'freeze-manifest.json').read_text())
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in manifest['files'].items())
notes={
 'hold_route_game':'Personal hypothetical preference routes as opinion, not artifact work. The two characters use different reasons/names; disagreement is not required.',
 'hold_route_lyrics':'Creative generation routes as collaborative_content_request with current scope. Capability isolation leaves a boundary response; this is not a premise or history failure.',
 'hold_route_file':'Actual file action routes as an unavailable capability; no external file is changed.',
 'hold_route_ambig':'Genuine referent ambiguity stays unresolved and receives a clarification.',
 'hold_cite_unicode':'Actual café choice survives punctuation and accent boundaries. Answer contains a repeated recall/explanation; no position rewrite. Existing opinion confidence mapping hedges the explanation.',
 'hold_cite_long':'Both exact bounded excerpts from the 2,366-character assistant message reach contribution. General quiet-start preference and festival-only exception remain distinct.',
 'hold_cite_speakers':'Assistant choice wins over the user’s contradictory allegation; both correctly recall the gentler ending.',
 'hold_same':'Both distinguish unchanged acoustic preference from stronger support; assessment relation=stronger_support.',
 'hold_reverse':'Both explain the genuine outdoors→indoors revision after strong wind. Opposing assessment incorrectly uses relation=not_applicable despite its view explicitly explaining a change; standard uses revised.',
 'hold_missing':'Both retain uncertainty; the user’s assertion of an earlier racing preference does not supply missing assistant evidence.'}
judgments=[]
for x in runs:
 if 'hold' not in x['run']:continue
 t=x['turns'][0]
 judgments.append(dict(run=x['run'],contract_accepted=True,position_answer_coherent=True,
  assessment_relation_coherent=x['run']!='opposing-hold_reverse-r1',note=notes[x['topic']]))
(OUT/'independent-judgments.json').write_text(json.dumps(judgments,indent=2))
lines=['# Targeted recorded-position premise fix — 2026-10-13','',
'## Outcome','',
'The final targeted check passes: 12/12 accepted replies, with 4/4 false-exception premises rejected, 4/4 neutral follow-ups retaining the prior choice, and 4/4 conflicting-context follow-ups making a justified narrower exception. The reserved evaluation completes 20/20 turns; the position explanations are coherent, but one contribution assessment has an incorrect relation label. That reserved result was not used for tuning.','',
'**Baseline qualification:** the literal “I’ll make an exception” historical rewrite did not recur in this small baseline. Standard affirmed the exception question with “yes” in 2/2 repeats while explicitly saying it already preferred that choice; opposing rejected the premise in 2/2. This supports a framing ambiguity, not a claim that all baseline answers reversed their earlier position.','',
'## Design and isolation','',
'- Two generic written characters, identical numeric traits (warmth .8, directness .62, curiosity .88, playfulness .58). Standard chose the playful notebook name; opposing chose the plain one. Neither is required to always agree or disagree with the user.',
'- Real gpt-6-luna calls through the cognitive pipeline and realization, temperature 0, store=false, SDK retries disabled. No DB or Redis connection; both are deliberately pointed at unavailable local ports. No live user history or external artifacts.',
'- Each character first generated its own accepted cognitive reply. Each branch restored that same canonical predecessor and post-turn dynamic state, then supplied that exact accepted reply as assistant history. No visible-live reply, replacement, or reset was used. These are paired two-turn conversation forks, not a new broad 6–10-turn evaluation.',
'- Twelve baseline branches, twelve first implementation branches, twelve refined implementation branches, two accepted prefixes, and twenty reserved turns: 58 accepted model turns total. Each branch has two repeats. Reserved cases have one evaluation per character; no recurrence claim is made from one held-out occurrence.',
'- Reserved cases use their prewritten controlled histories, not generated conversational prefixes. They were not run during development or used as development fixtures. Their definitions had been inspected before this turn’s implementation, so the set is evaluation-independent but not strictly investigator-blinded. The original reserved file is unchanged.',
'- This is isolated real-provider pipeline evaluation, not a frontend/browser or live database test.','',
'## Mechanism implemented','',
'Argument registry v1.11 selects contribution-input-v1.4 / contribution-proposal-v3 for written-character runs. A code-owned premise_context carries the exact current utterance plus recent assistant statements and checked older source-linked excerpts. It does not generate a summary of an old opinion or use a character value as historical evidence.','',
'Before the answer, contribution assesses the user’s asserted or presupposed relationship to that recorded speech. A separate relation represents the current judgment: unchanged, stronger support, exception, revision, uncertainty, or not applicable. Neutral questions do not assert a change. An exception question can carry a false presupposition even when its historical clause is accurate. The current judgment can still change after rejecting a false premise.','',
'Code restricts and validates evidence references, retains the exact bound speech in compiled_proposal, and rejects tampered compiled assessment/evidence on trace validation. The compiler does not force a position, choose a preference, or override the semantic verdict. Call 2 and realization receive the resulting view through the existing content pipeline. Older registry v1.10 inputs keep their original schema/prompt; older replay and existing confidence policy remain in place. No opinion-memory subsystem or topic-specific rules were added.','',
'Implementation: `backend/app/ai/Cognitive/system/v0/two_call/premise.py`, `contribution_premise_v1.txt`, versioned contribution/provider/orchestrator contracts, and registry membership in ontology. Existing uncommitted speech-scope/source-passage changes were retained.','',
'## Development evidence and recurrence','',
'| Branch | Baseline | Final implementation | What stayed fixed |','|---|---|---|---|',
'| Neutral | 4/4 same choice | 4/4 same choice; no asserted change | Actual earlier choice and ordinary-notebook scope |',
'| Incorrect exception premise | 2/4 ambiguous affirmation; 2/4 explicit rejection | 4/4 contradicted premise, explicit rejection | No fabricated earlier preference |',
'| Conflicting new context | 4/4 contextual switch | 4/4 exception; switched choice for class/comedy | General taste retained; new context can outweigh it |','',
'The first implementation already produced coherent replies in all 12 branches, but exposed an assessment-contract ambiguity: opposing false-premise repeat 1 labeled a compound premise supported while its reason said no exception was needed. Some neutral questions also labeled the historical clause rather than the asserted relationship. The overlay was clarified during development to isolate the user’s relationship premise from the historical facts and from the current judgment. The complete 12-branch check was then repeated as after_v2. All final false-premise assessments are contradicted; all neutral and conflict questions have null premise / no_asserted_change. All cite the exact accepted prior:1 statement. No held-out case was run until these checks passed.','',
'## Input and downstream verification','',
'`stage-audit.json` independently audits all 58 turns with zero input, source, or state mismatches. It checks actual SDK-boundary histories, exact accepted-prefix presence, written character, numeric trait/bias projections, dynamic state before/after, realization delivery sources, source speaker and substring boundaries, premise context, exact compiled evidence, predecessor terminal, and absence of branch resets. `freeze-manifest.json` hashes the cognitive Python/prompt sources, fixture/harness, and reserved file before holdout; all hashes remain unchanged after evaluation.','',
'Local checks: 249 passed across premise, character, two-call, follow-up, continuity and orchestration regressions; 35 neighboring scope/source and action-evidence tests passed. After the prompt clarification, all 10 premise tests passed again. The tests include evidence outside the recent window, rejecting user/foreign references, tamper detection, missing-evidence uncertainty, all permitted current relations after a false premise, and old-version replay. The parameterized relation tests verify no deterministic opinion freeze; real conflicting branches establish behavioral flexibility.','',
'## Independent reserved evaluation','',
'Contract acceptance is 20/20, with zero citation/boundary stops. On the four comparative scenarios (stronger support, long-message narrow exception, genuine reversal, missing earlier view), all 8 answers preserve the recorded positions and appropriate qualifications. Relation metadata agrees in 7/8; the one mismatch is detailed below. Other reserved cases retain the expected preference/creation/action/ambiguity categories or correctly recall the assistant’s speech. Creative generation is unavailable under this isolated capability configuration; successful contract acceptance is not a claim that a title artifact was generated.','',
'| Reserved case | Evidence-based result |','|---|---|']
for k,n in notes.items():lines.append('| '+k+' | '+n+' |')
lines += ['', '### Remaining assessment inconsistency — not tuned', '',
'In opposing-hold_reverse-r1 the exact prior statements are “I would host the exhibition outdoors.” and “I would move it indoors.”, with the user’s intervening strong-wind information in the actual contribution history. Contribution cites both exact statements. Its assessment says relation=not_applicable because the user did not assert a change, while its own view correctly says “I first recommended hosting the exhibition outdoors, then changed to indoors after the strong wind was raised.” Compilation preserves the assessment; Call 2 selects the single answer candidate; realization preserves the genuine revision. The earliest inconsistency is therefore contribution assessment, not missing history, compilation, action selection, or realization. It occurred in 1/2 character evaluations of this case; this is not a repeated within-character failure count.','',
'The smallest candidate follow-up is to distinguish applicability of the user-premise verdict from applicability of comparing the current and recorded positions, so absence of an asserted change does not itself make the relation unavailable. The trace demonstrates this need for the diagnostic label; it does not demonstrate a need for additional opinion memory or a deterministic revision rule. Any later change must preserve neutral comparisons, genuinely unrelated questions, missing-position uncertainty, and honest narrower exceptions. No such change has been implemented from held-out evidence.','',
'### Limits and neighboring risks','',
'The new check is semantic model reasoning with deterministic evidence binding; a valid citation does not guarantee a correct interpretation. Older messages not selected by interpretation can remain unavailable, and long excerpts can omit relevant qualifications. Extra structured assessment adds tokens/latency and an additional contract failure surface. Misclassifying a possibility as a presupposition can overcorrect a user; treating every difference as a reversal can suppress contextual exceptions. The paired real checks retain revisions, exceptions and uncertainty, but this small set cannot establish long-run continuity or a general recurrence rate. Do not score balance, warmth, brevity, uncertainty, or personality agreement by themselves as defects.','',
'## Reproduction and evidence files','',
'Run from the repo root with the existing backend environment and provider key configured (no key is saved here):','',
'```sh','backend/.venv/bin/python docs/qa/cognitive-premise-fix-2026-10-13/targeted.py prefix',
'backend/.venv/bin/python docs/qa/cognitive-premise-fix-2026-10-13/targeted.py branches after_v2',
'backend/.venv/bin/python docs/qa/cognitive-premise-fix-2026-10-13/audit.py',
'```','',
'For exact reproduction of these paired results, retain the saved prefixes rather than rerunning prefix. The baseline captures preserve their exact v1.10 prompts and raw responses; reproducing the baseline generation requires its earlier implementation. The holdout command is `targeted.py holdout`; it was executed once only after the freeze. It will spend provider usage and should be treated as a new evaluation, not as an untouched reserve again.','',
'`branch-fixtures.json`, `targeted.py`, `audit.py`, `source-core.json`, `opposing-character.json`, and the unchanged `../cognitive-root-cause-2026-10-13/reserved-unfamiliar.json` reproduce inputs. Every .json.gz file contains the exact provider request/response, schemas, prompts, captures, canonical terminal/state, interpretation, contribution, compilation, policy, realization and boundary audits. `independent-judgments.json` records semantic judgments separately from contract acceptance.','',
'## Exact conversation evidence','']
for x in runs:
 if 'prefix' not in x['run']:continue
 t=x['turns'][0];lines += ['### '+x['run'],'','User: '+t['user'],'','Accepted cognitive reply: '+t['reply'],'']
for phase in ['before','after','after_v2']:
 lines += ['### '+phase,'']
 for x in runs:
  if x.get('phase')!=phase:continue
  t=x['turns'][0];lines += ['#### '+x['run'],'','User: '+t['user'],'','Contribution:', '```json',json.dumps(t['contributions'][0]['proposal'],ensure_ascii=False,indent=2),'```','', 'Accepted reply: '+t['reply'],'','Full evidence: ['+x['run']+'.json.gz]('+x['run']+'.json.gz)','']
lines += ['### Reserved cases — exact inputs and responses','']
for x in runs:
 if 'hold' not in x['run']:continue
 t=x['turns'][0];lines += ['#### '+x['run'],'','History:','```json',json.dumps(x['case'].get('history',[]),ensure_ascii=False,indent=2),'```','', 'User: '+t['user'],'','Contribution (if invoked):','```json',json.dumps([c['proposal'] for c in t['contributions']],ensure_ascii=False,indent=2),'```','', 'Accepted reply: '+t['reply'],'', 'Full evidence: ['+x['run']+'.json.gz]('+x['run']+'.json.gz)','']
(OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
print('Report saved; 58 accepted turns, 20 held-out judgments, frozen sources unchanged.')
