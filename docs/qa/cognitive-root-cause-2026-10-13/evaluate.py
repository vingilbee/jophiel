"""Isolated real-model closed-loop evaluation; deliberately no failure fallback."""
import copy, gzip, hashlib, json, os, sys, time, traceback
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from dotenv import dotenv_values
key = dotenv_values(ROOT / 'backend/.env').get('OPENAI_API_KEY')
os.environ['DATABASE_URL'] = 'postgresql+psycopg://localhost:1/closed_loop_no_database'
os.environ['REDIS_URL'] = 'redis://localhost:1/0'
from app.core.config import Settings
from app.ai.Cognitive.system.v0.personality_runtime_v0 import build_source, PersonalityRuntimeMapperV0
from app.ai.Cognitive.system.v0.contracts.character import character_from_identity
from app.ai.Cognitive.system.v0.contracts.dialogue_memory import DialogueResetReason, turn_context_digest
from app.ai.Cognitive.system.v0.contracts.dialogue_interpretation import DialogueInterpretationRecord, InterpretedUpdate
from app.ai.Cognitive.system.v0.dialogue_memory_v0 import DialogueMemoryUpdaterV0, compile_turn_context
from app.ai.Cognitive.system.v0.live_shadow.models import (CoreMemoryLineage, CapturedDialogueMemory,
    CAPTURE_SCHEMA_VERSION_V04, CapturedInterpretedDialogue)
from app.ai.Cognitive.system.v0.live_shadow.compiler import PredecessorRecord
from app.ai.Cognitive.system.v0.live_shadow.digests import payload_digest
from app.ai.Cognitive.system.v0.live_shadow.service import ObservationContext, build_pipeline, observe
from app.ai.Cognitive.system.v0.live_shadow.realization import realize_record
from app.ai.Cognitive.system.v0.two_call.orchestrator import install
from app.ai.Cognitive.system.v0.two_call.provider import OpenAITwoCallModel
from app.ai.Cognitive.system.v0.realization_provider import OpenAIRealizationModel
from app.ai.Cognitive.system.v0.realization_v0.engine import RealizationEngineV0
from dialogue_memory_support import Script
from openai import OpenAI

fixture_file='fixtures-wave2.json' if '--wave2' in sys.argv else 'fixtures.json'
FIXTURES = json.loads((OUT/fixture_file).read_text())
CASES = {c['id']:c for c in FIXTURES['static']+FIXTURES['closed']}
CORPUS = {k:c.get('turns',[c.get('prompt')]) for k,c in CASES.items()}

def save(result, name):
    temp=OUT/(name+'.pending.gz')
    with gzip.open(temp,'wt') as f:json.dump(result,f,ensure_ascii=False)
    os.replace(temp,OUT/(name+'.json.gz'))

def dump(x):
    return x.model_dump(mode='json') if hasattr(x, 'model_dump') else x

class Boundary:
    """Observe SDK arguments after adapter construction, before network send."""
    def __init__(self, records):
        self.real = OpenAI(api_key=key, timeout=60, max_retries=0)
        self.records = records
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        entry = {'request': copy.deepcopy(kwargs)}
        self.records.append(entry)
        try:
            result = self.real.chat.completions.create(**kwargs)
            entry['response'] = result.model_dump(mode='json')
            return result
        except Exception as exc:
            entry['error'] = {'type': type(exc).__name__, 'status_code': getattr(exc, 'status_code', None)}
            raise

def payload_of(entry):
    content = entry['request']['messages'][-1]['content']
    if content.startswith('INPUT_DATA\n'):
        return json.loads(content[len('INPUT_DATA\n'):-len('\nEND_INPUT_DATA')])
    return json.loads(content)

def audit(row, capture, pipeline, previous):
    expected = [(str(m.role), m.content) for m in capture.messages]
    checks = []
    for entry in row['boundary']:
        p = payload_of(entry)
        schema = entry['request']['response_format']['json_schema']['name']
        stage = schema.split('_v')[0]
        history = None
        if 'dialogue_context' in p:
            history = [(m['speaker'], m['text']) for m in p['dialogue_context']['messages']]
            target = expected
        elif 'conversation_window' in p:
            history = [(m['speaker'].replace('columbina','assistant'), m['text']) for m in p['conversation_window']]
            target = [v for v in expected[-8:] if v==expected[-1] or len(v[1])<=2000]
        elif p.get('conversation_context'):
            history = [(m['speaker'].replace('columbina','assistant'), m['text']) for m in p['conversation_context']['messages']]
            target = [v for v in expected[:-1] if len(v[1])<=2000][-8:]
        if history is not None:
            checks.append({'stage': stage, 'history_exact': history == target,
                           'messages_received': len(history), 'prior_accepted_reply_present':
                           not previous or any(t == previous for r,t in history if r == 'assistant'),
                           'older_messages_outside_window': len(expected)-(1 if 'conversation_context' in p else 0)-len(target)})
        if 'character' in p:
            checks[-1]['character_exact'] = p['character'] == {s: [dump(v) for v in getattr(capture.character,s)] for s in ['values','principles','beliefs']}
        entry['stage'] = stage
    row['boundary_audit'] = checks
    if any(c.get('history_exact') is False or c.get('prior_accepted_reply_present') is False or c.get('character_exact') is False for c in checks):
        raise RuntimeError('Boundary audit mismatch; conversation cannot be evaluated')

def run(condition, topic, repeat):
    name = f'{condition}-{topic}-r{repeat}'
    script = Script(name)
    case = CASES[topic]
    for i,m in enumerate(case.get('history',[])):
        script.add(m['role'],m['text'],turn=900+i)
    initial_records = len(script.records)
    accepted_count = 0
    previous_reply = None
    settings = Settings(_env_file=None, openai_api_key=key, cognitive_shadow_collaboration_model=None)
    source = json.loads((OUT/'source-core.json').read_text())
    ref = f"core_memory:{source['name']}:v{source['version']}"
    core = CoreMemoryLineage(core_memory_ref=ref, name=source['name'], version=source['version'], schema_version=source['schema_version'])
    groups = {k:source[k] for k in ['identity','traits','perception_priors','interpretation_biases','drives','action_style','values','learning_policy','locked_fields']}
    projection = PersonalityRuntimeMapperV0().compile(build_source(name=source['name'],version=source['version'],schema_version=source['schema_version'],groups=groups))
    identity = copy.deepcopy(source['identity'])
    if condition == 'opposing':
        identity['character'] = json.loads((OUT/'opposing-character.json').read_text())
    character = character_from_identity(identity, core_memory_ref=ref)
    updater = DialogueMemoryUpdaterV0()
    snapshot = updater.genesis(user_ref=script.user,conversation_ref=script.conversation,revision=1,
        segment_start_sequence=1,reset_reason=DialogueResetReason.INITIAL_BOOTSTRAP)
    if initial_records:
        snapshot=updater.apply(snapshot,script.events(),continue_lineage=False)
    previous = None
    interpreted = []
    result = {'run':name,'condition':condition,'topic':topic,'repeat':repeat,'planned_prompts':CORPUS[topic],
              'case':case,'personality':dump(projection),'character':dump(character),'turns':[]}
    for index, text in enumerate(CORPUS[topic], 1):
        row = {'index':index,'user':text,'boundary':[]}
        result['turns'].append(row)
        start = time.monotonic()
        try:
            focus,event = script.focus(text, turn=index)
            context = compile_turn_context(snapshot,event,cognitive_turn_id=script.turn(index))
            capture = script.v03_capture(text, turn=index, personality=projection,core_memory=core,character=character,
                schema_version=CAPTURE_SCHEMA_VERSION_V04, capability_keys=(),
                dialogue=CapturedDialogueMemory(status='available',turn_context=context,turn_context_digest=turn_context_digest(context)),
                interpreted_dialogue=CapturedInterpretedDialogue(records=tuple(interpreted)),
                **({'previous_user_message_id':None} if index==1 else {}))
            row['capture'] = dump(capture)
            client = Boundary(row['boundary'])
            model = OpenAITwoCallModel(model_id='gpt-6-luna',api_key=key,timeout_seconds=60,max_output_tokens=6000,client=client)
            pipeline = install(build_pipeline(capture.turn_id),model,protocol='cognitive-two-call-v1')
            record = observe(capture,previous,ObservationContext(job_id=capture.capture_id,attempt=1,
                capture_digest=payload_digest(capture),started_at='2026-10-13T00:00:00+00:00'),pipeline_factory=lambda _:pipeline)
            row['terminal'] = dump(record)
            row['reference_table'] = copy.deepcopy(pipeline.two_call.references.entries)
            row['understanding_registry'] = pipeline.two_call.understanding_registry
            row['calls'] = [dump(c) for c in pipeline.two_call.captures]
            row['contributions'] = [dump(c) for c in pipeline.two_call.contribution_captures]
            engine = RealizationEngineV0(OpenAIRealizationModel(model_id='gpt-6-luna',api_key=key,timeout_seconds=60,client=client),max_attempts=2)
            realized = realize_record(record,engine=engine,configured_maximum_characters=settings.cognitive_shadow_realization_max_characters,dialogue_context=context)
            row['realized'] = dump(realized)
            row['status'] = record.status.value
            row['realization_status'] = realized.realization_status.value
            trace = realized.realization_trace
            row['reply'] = trace.final_candidate.text if trace and trace.final_candidate else None
            audit(row,capture,pipeline,previous_reply)
            if record.status.value != 'completed' or row['realization_status'] not in ('accepted','accepted_shadow') or not row['reply']:
                result['stop_reason'] = 'unaccepted_turn'
                break
            call1 = next(c for c in pipeline.two_call.captures if c.stage == 'call_one')
            updates = tuple(InterpretedUpdate.model_validate(v) for v in (call1.compiled_proposal or {}).get('dialogue_updates',()))
            interpreted.append(DialogueInterpretationRecord(source_message_ref=focus.message_ref,source_conversation_sequence=focus.conversation_sequence,updates=updates))
            script.add('user',text,turn=index)
            script.add('assistant',row['reply'],turn=index)
            snapshot = updater.apply(snapshot,script.events(len(script.records)-2),continue_lineage=bool(initial_records) or index>1)
            accepted_count += 1
            previous_reply = row['reply']
            previous = PredecessorRecord(terminal_id=record.terminal_id,turn_id=record.turn_id,user_ref=record.user_ref,
                conversation_ref=record.conversation_ref,focus_message_id=capture.focus_message_id,segment_id=record.segment_id,
                status=record.status,pipeline_trace=record.pipeline_trace)
        except Exception as exc:
            row['harness_error'] = {'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
            result['stop_reason'] = 'harness_error'
            break
        finally:
            row['elapsed_seconds'] = round(time.monotonic()-start,2)
            print(name,index,row.get('status'),row.get('realization_status'),row.get('harness_error',{}).get('type'),flush=True)
            save(result,name)
    result['complete'] = accepted_count == len(CORPUS[topic])
    result['accepted_turns'] = accepted_count
    save(result,name)
    return {k:result.get(k) for k in ['run','condition','topic','repeat','complete','accepted_turns','stop_reason']}

if __name__ == '__main__':
    jobs = [(c,t,r) for c in ['standard','opposing'] for t in CASES for r in range(1,3)]
    if '--smoke' in sys.argv:jobs=jobs[:1]
    if '--family' in sys.argv:
        family=sys.argv[sys.argv.index('--family')+1];jobs=[j for j in jobs if CASES[j[1]]['family']==family]
    if '--resume' in sys.argv:jobs=[j for j in jobs if not (OUT/('-'.join(map(str,[j[0],j[1],'r'+str(j[2])]))+'.json.gz')).exists()]
    rows=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run,*j) for j in jobs]
        for future in as_completed(futures):
            rows.append(future.result())
            (OUT/'summary.json').write_text(json.dumps(rows,indent=2))
