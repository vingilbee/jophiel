"""Step-7 system probe: real runner loops/DB/memory/capture, scripted slow mind and model seams."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import select, func

from app.ai.state.background import PreparationResult, SinkReceipt
from app.db.models import ChatJob, CoordinatorWork, Observation, ObservationProcessing, WorkingItem
from app.services import chat as chat_service, coordinator, coordinator_runtime, background_components as components
from app.services import memory_manager, observation, observation_adapters
from app.workers import runner, chat as chat_worker, observation as observation_worker
from test_background_coordination import bg  # noqa: F401
from test_coordinator import env  # noqa: F401
from db_support import database, db_env, create_conversation  # noqa: F401


def wait_for(check, timeout=12):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=check()
        if value:return value
        time.sleep(.02)
    raise AssertionError('Integrated worker progress did not arrive before timeout')


@pytest.mark.parametrize('adapters',['scripted_success','unavailable'])
def test_real_worker_loops_progress_while_mind_is_held(bg,monkeypatch,adapters):
    settings=components.get_settings().model_copy(update={
        'memory_manager_enabled':True,'observation_enabled':True,
        'observation_preparation_enabled':True,'mind_enabled':False,'desktop_agent_enabled':False,
        'recall_enabled':False,'whatsapp_enabled':False,
        'mind_inner_state_enabled':False,
        'coordinator_executor_slots':1,'coordinator_heartbeat_seconds':1,'redis_url':None})
    monkeypatch.setattr(runner,'get_settings',lambda:settings)
    from app.db import session as database_sessions
    monkeypatch.setattr(database_sessions, 'SessionLocal', bg.db)
    monkeypatch.setattr(observation_worker,'SessionLocal',bg.db)
    monkeypatch.setattr(runner.memory,'run_once',lambda:False)  # separate legacy ingestion has no jobs in this fixture
    monkeypatch.setattr(runner.file_deletion,'run_once',lambda:False)
    from app.services import cognitive_live
    monkeypatch.setattr(cognitive_live, 'warm', lambda:None)
    monkeypatch.setattr(chat_worker.desktop_game,'summary_for',lambda *a,**k:None)
    monkeypatch.setattr(chat_worker.inner_state_live,'observe',lambda *a,**k:None)
    monkeypatch.setattr(chat_worker.game_correction,'start',lambda *a,**k:None)
    runtime=coordinator_runtime.Runtime(1)
    monkeypatch.setattr(coordinator_runtime,'_instance',runtime)
    started,release,stop=Event(),Event(),Event()
    clock=time.monotonic();timeline=[]
    def mark(name):timeline.append({'event':name,'elapsed_ms':round((time.monotonic()-clock)*1000)})
    def slow(work):
        mark('mind_started');started.set()
        assert release.wait(25),'System harness did not release its scripted mind'
        mark('mind_released')
        return {'kind':'decision','conclusion':'Private scripted outcome'},'completed',None
    monkeypatch.setattr(coordinator,'_run_mind',slow)
    if adapters=='scripted_success':
        monkeypatch.setattr(components,'prepare_observation',lambda evidence:PreparationResult(
            output_refs=(f'prepared:{evidence.event_id}',),processing_version='system-test-v1',coverage=evidence.source_refs))
        monkeypatch.setattr(components,'cognition_sink',lambda event:SinkReceipt('consumed'))
    else:
        monkeypatch.setattr(components,'prepare_observation',components.unavailable_preparation)
        monkeypatch.setattr(components,'cognition_sink',components.unavailable_cognition)
    with bg.db() as db:
        observation.adapter(db,observation_adapters.CONVERSATION)
        db.commit()
    def enqueue(conversation_id,message):
        with bg.db() as db:
            c=db.get(type(bg.conversation),conversation_id);tid=uuid.uuid4()
            m=chat_service.add_message(db,conversation=c,role='user',content=message,turn_id=tid)
            job=chat_service.enqueue_job(db,user_id=bg.user.id,conversation=c,turn_id=tid,
                user_message_id=m.id,payload={'content':message})
            db.commit();return job.id,tid
    def job_done(job_id):
        with bg.db() as db:return db.get(ChatJob,job_id).status=='completed'
    first,turn=enqueue(bg.conversation.id,'Synthetic request whose thinking is deliberately held open')
    errors=[]
    def run():
        try:runner.run(stop)
        except BaseException as exc:errors.append(repr(exc))
    thread=Thread(target=run,name='step7-system-supervisor');thread.start()
    try:
        assert started.wait(8)
        wait_for(lambda:job_done(first));mark('first_immediate_reply_completed')
        bg.calls['jev']=('not_required','not_required')
        with bg.db() as db:
            other=create_conversation(db,bg.user)
            item=memory_manager.note_commitment(db,bg.user.id,'Synthetic overdue fixture',owner='user',
                due_at=coordinator._now()-timedelta(minutes=1))
            item_id=item.id;db.commit()
        second,second_turn=enqueue(other.id,'A normal chat reply while another conversation is thinking')
        wait_for(lambda:job_done(second));mark('independent_chat_completed')
        def independent_done():
            with bg.db() as db:
                rows=list(db.scalars(select(CoordinatorWork)))
                obs=list(db.scalars(select(Observation)))
                wanted='completed' if adapters=='scripted_success' else 'blocked'
                return (db.get(WorkingItem,item_id).status=='needs_review'
                    and any(w.kind=='memory' and w.status=='completed' for w in rows)
                    and any(w.kind=='observation' and w.status==wanted for w in rows)
                    and len(obs)>=4)
        wait_for(independent_done);mark('memory_and_capture_preparation_progressed')
        views={v.component:v for v in components.projections(bg.user.id,bg.conversation.id)}
        assert views['mind'].status=='running'
        assert views['memory'].last_result_ref and views['observation'].last_result_ref
        assert not release.is_set(),'Independence must be proven before releasing mind'
        with bg.db() as db:
            mind=db.scalar(select(CoordinatorWork).where(CoordinatorWork.request_id==turn,CoordinatorWork.kind=='mind'))
            assert mind.status=='running'
            snapshot={'views':{k:asdict(v) for k,v in views.items()},'mind_work_id':str(mind.id)}
        release.set()
        def mind_finished():
            with bg.db() as db:
                return db.get(CoordinatorWork,mind.id).status=='completed'
        wait_for(mind_finished);mark('mind_completed')
        def handoff_settled():
            with bg.db() as db:
                return db.scalar(select(CoordinatorWork.id).where(CoordinatorWork.idempotency_key==f'cognition:{mind.id}',
                    CoordinatorWork.status==('completed' if adapters=='scripted_success' else 'blocked')))
        wait_for(handoff_settled);mark('typed_mind_handoff_settled')
        with bg.db() as db:
            observation_count=db.scalar(select(func.count()).select_from(Observation))
            preparation_count=db.scalar(select(func.count()).select_from(CoordinatorWork).where(CoordinatorWork.kind=='observation'))
        time.sleep(1.2)
        with bg.db() as db:
            assert db.scalar(select(func.count()).select_from(Observation))==observation_count
            assert db.scalar(select(func.count()).select_from(CoordinatorWork).where(CoordinatorWork.kind=='observation'))==preparation_count
            snapshot['work']=[{'id':str(w.id),'kind':w.kind,'status':w.status,'attempt':w.attempt} for w in db.scalars(select(CoordinatorWork))]
            snapshot['observation_count']=observation_count
            snapshot['receipts']=[{'consumer':r.consumer,'status':r.status,'observation_id':r.observation_id} for r in db.scalars(select(ObservationProcessing))]
        mark('unchanged_heartbeat_did_not_duplicate_source_work')
        snapshot.update(adapters=adapters,timeline=timeline,mind_disabled_flag=settings.mind_enabled)
        target=Path(__file__).resolve().parents[2]/'docs/reports'/f'shared-state-step7-system-{adapters}-2026-10-22.json'
        target.write_text(json.dumps(snapshot,indent=2,default=str))
    finally:
        release.set();stop.set();coordinator_runtime.wake.set();coordinator_runtime.capture_wake.set()
        thread.join(10)
    assert not thread.is_alive() and not errors,errors
