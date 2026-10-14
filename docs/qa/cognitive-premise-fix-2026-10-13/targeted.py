"""Small paired fork test. No live data or hidden reply replacement."""
import importlib.util,json,gzip,sys,copy,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
OUT=Path(__file__).resolve().parent
OLD=OUT.parent/'cognitive-root-cause-2026-10-13'
spec=importlib.util.spec_from_file_location('closed_evaluation',OLD/'evaluate.py')
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e);e.OUT=OUT
from app.ai.Cognitive.system.v0.live_shadow.models import CognitiveShadowCapture,CognitiveShadowTerminalRecord

def fork(condition,branch,question,phase,repeat):
 prefix=json.load(gzip.open(OUT/(condition+'-prefix-r1.json.gz')))
 first=prefix['turns'][0];old_capture=CognitiveShadowCapture.model_validate(first['capture'])
 old_terminal=CognitiveShadowTerminalRecord.model_validate(first['terminal'])
 assert first['realization_status']=='accepted_shadow' and old_terminal.pipeline_trace
 script=e.Script(prefix['run']);script.add('user',first['user'],turn=1);script.add('assistant',first['reply'],turn=1)
 updater=e.DialogueMemoryUpdaterV0();snapshot=updater.genesis(user_ref=script.user,conversation_ref=script.conversation,revision=1,segment_start_sequence=1,reset_reason=e.DialogueResetReason.INITIAL_BOOTSTRAP)
 snapshot=updater.apply(snapshot,script.events(),continue_lineage=False)
 focus,event=script.focus(question,turn=2);context=e.compile_turn_context(snapshot,event,cognitive_turn_id=script.turn(2))
 one=next(c for c in first['calls'] if c['stage']=='call_one')
 updates=tuple(e.InterpretedUpdate.model_validate(v) for v in (one.get('compiled_proposal')or{}).get('dialogue_updates',()))
 records=(e.DialogueInterpretationRecord(source_message_ref=old_capture.focus_message_id,source_conversation_sequence=1,updates=updates),)
 capture=script.v03_capture(question,turn=2,personality=old_capture.personality,core_memory=old_capture.core_memory,character=old_capture.character,
  schema_version=e.CAPTURE_SCHEMA_VERSION_V04,capability_keys=(),dialogue=e.CapturedDialogueMemory(status='available',turn_context=context,turn_context_digest=e.turn_context_digest(context)),interpreted_dialogue=e.CapturedInterpretedDialogue(records=records))
 pred=e.PredecessorRecord(terminal_id=old_terminal.terminal_id,turn_id=old_terminal.turn_id,user_ref=old_terminal.user_ref,conversation_ref=old_terminal.conversation_ref,focus_message_id=old_capture.focus_message_id,segment_id=old_terminal.segment_id,status=old_terminal.status,pipeline_trace=old_terminal.pipeline_trace)
 name=f'{phase}-{condition}-{branch}-r{repeat}';row=dict(index=2,user=question,boundary=[])
 result=dict(run=name,condition=condition,branch=branch,repeat=repeat,phase=phase,personality=prefix['personality'],character=prefix['character'],planned_prompts=[question],common_prefix_sha256=hashlib.sha256((OUT/(condition+'-prefix-r1.json.gz')).read_bytes()).hexdigest(),turns=[row])
 client=e.Boundary(row['boundary']);model=e.OpenAITwoCallModel(model_id='gpt-6-luna',api_key=e.key,timeout_seconds=60,max_output_tokens=6000,client=client)
 pipeline=e.install(e.build_pipeline(capture.turn_id),model,protocol='cognitive-two-call-v1')
 record=e.observe(capture,pred,e.ObservationContext(job_id=capture.capture_id,attempt=1,capture_digest=e.payload_digest(capture),started_at='2026-10-13T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
 row.update(capture=e.dump(capture),terminal=e.dump(record),calls=[e.dump(c) for c in pipeline.two_call.captures],contributions=[e.dump(c) for c in pipeline.two_call.contribution_captures],reference_table=copy.deepcopy(pipeline.two_call.references.entries))
 realized=e.realize_record(record,engine=e.RealizationEngineV0(e.OpenAIRealizationModel(model_id='gpt-6-luna',api_key=e.key,timeout_seconds=60,client=client),max_attempts=2),configured_maximum_characters=1200,dialogue_context=context)
 row.update(realized=e.dump(realized),status=record.status.value,realization_status=realized.realization_status.value,reply=realized.realization_trace.final_candidate.text if realized.realization_trace and realized.realization_trace.final_candidate else None)
 e.audit(row,capture,pipeline,first['reply'])
 assert record.compiled_request.previous_state==old_terminal.pipeline_trace.cognitive_turn.dynamic_state_update.snapshot
 assert record.predecessor_trace_id==old_terminal.terminal_id and record.segment_reset_reason is None
 result['accepted_turns']=int(row['realization_status']=='accepted_shadow');result['complete']=bool(result['accepted_turns'])
 e.save(result,name);print(name,row['status'],row['realization_status'],row['reply'],flush=True)
 return result

if __name__=='__main__':
 if sys.argv[1]=='prefix':
  prompt='Which name do you personally prefer for an ordinary notebook: Notes or The Cabinet of Wandering Thoughts?'
  e.CASES={'prefix':dict(id='prefix',family='development',contrast='recorded_position',source_mode='own_accepted_response')};e.CORPUS={'prefix':[prompt]}
  for c in ['standard','opposing']:e.run(c,'prefix',1)
 elif sys.argv[1]=='branches':
  phase=sys.argv[2];fixtures=json.loads((OUT/'branch-fixtures.json').read_text())
  jobs=[(c,b,q,phase,r) for c,branches in fixtures.items() for b,q in branches.items() for r in [1,2]]
  with ThreadPoolExecutor(max_workers=3) as pool:
   for future in as_completed([pool.submit(fork,*j) for j in jobs]):future.result()
 elif sys.argv[1]=='holdout':
  reserved=json.loads((OLD/'reserved-unfamiliar.json').read_text())
  cases={c['id']:(c|dict(family='independent_holdout',source_mode='reserved_controlled_history',notes='Frozen implementation; first model evaluation.',contrast=c['contrast'])) for c in reserved['cases']}
  for c in cases.values():c['history']=c.get('history',[])
  e.CASES=cases;e.CORPUS={k:[v['prompt']] for k,v in cases.items()}
  with ThreadPoolExecutor(max_workers=3) as pool:
   for future in as_completed([pool.submit(e.run,c,k,1) for c in ['standard','opposing'] for k in cases]):future.result()
