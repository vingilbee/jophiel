"""Step 6 interaction probes. Real disposable Postgres, scripted cognition/models/TTS.

Crash tests use separate interpreter processes or injected process-exit boundaries;
none evaluate trained cognition quality or use application data.
"""
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.ai.state.decisions import ResultDecision
from app.db.models import ChatJob, ChatMessage, CoordinatorOutput, CoordinatorWork
from app.services import chat as chat_service, cognition_decisions, coordinator
from app.workers import chat as worker
from test_autonomous_speech import propose
from test_coordinator import env, _run_chat, _say, _work  # noqa: F401
from db_support import database, db_env, create_conversation, create_user  # noqa: F401


@pytest.fixture
def responding(env, monkeypatch):
    monkeypatch.setattr(worker.desktop_game, 'summary_for', lambda *a, **k: None)
    monkeypatch.setattr(worker.inner_state_live, 'observe', lambda *a, **k: None)
    monkeypatch.setattr(worker.game_correction, 'start', lambda *a, **k: None)
    monkeypatch.setitem(cognition_decisions.PROVIDERS, 'result', lambda _: ResultDecision('respond', 'scripted-step6', True))
    env.calls['jev'] = ('not_required', 'not_required')
    return env


def test_autonomous_job_waits_for_active_normal_reply(responding, monkeypatch):
    e=responding
    with e.db() as db:
        c=db.get(type(e.conversation),e.conversation.id)
        tid=uuid.uuid4()
        m=chat_service.add_message(db,conversation=c,role='user',content='Hello',turn_id=tid)
        chat_service.enqueue_job(db,user_id=e.user.id,conversation=c,turn_id=tid,user_message_id=m.id,payload={'content':'Hello'})
        normal=chat_service.claim_next_job(db,lease_seconds=60)
    w=propose(e,monkeypatch)
    assert coordinator.execute_once()
    with e.db() as db: assert chat_service.claim_next_job(db,lease_seconds=60) is None
    asyncio.run(worker._generate(normal))
    assert _run_chat(e)
    with e.db() as db:
        rows=list(db.scalars(select(ChatMessage).where(ChatMessage.role=='assistant').order_by(ChatMessage.conversation_sequence)))
        assert [m.turn_id for m in rows]==[tid,coordinator.follow_up_turn(w.request_id)]
    assert len(e.announced)==1


def test_concurrent_duplicate_dispatch_reserves_one_job(responding,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    e=responding;w=propose(e,monkeypatch)
    with e.db() as db:
        db.get(CoordinatorWork,w.id).status='completed';db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:coordinator._conclude(w.id),range(2)))
    with e.db() as db:
        assert len(list(db.scalars(select(CoordinatorOutput))))==1
        assert len(list(db.scalars(select(ChatJob))))==1
    assert _run_chat(e) and not _run_chat(e)
    assert len(e.announced)==1


def test_queued_autonomous_work_cannot_change_conversation_target(responding,monkeypatch):
    e=responding;w=propose(e,monkeypatch)
    with e.db() as db:
        other=create_conversation(db,e.user)
    assert coordinator.execute_once() and _run_chat(e)
    with e.db() as db:
        replies=list(db.scalars(select(ChatMessage).where(ChatMessage.role=='assistant')))
        assert len(replies)==1 and replies[0].conversation_id==w.conversation_id!=other.id


def test_cross_user_conversation_target_is_rejected(responding):
    from app.services import autonomous_speech
    e=responding
    with e.db() as db:
        other=create_user(db);c=create_conversation(db,other)
        result=autonomous_speech.submit(db,user_id=e.user.id,activity_id=c.id,source='conversation',event_id=uuid.uuid4(),text='Private proposal',reasons=['thought: test'],commentary_mode='occasional')
        assert result.startswith('Rejected') and db.scalar(select(CoordinatorWork)) is None


def test_voice_cancelled_autonomous_reply_is_partial_and_never_announced(responding,monkeypatch):
    e=responding;w=propose(e,monkeypatch);assert coordinator.execute_once()
    with e.db() as db:
        j=db.scalar(select(ChatJob));j.payload={**j.payload,'voice':True};db.commit()
    synthesized=[]
    async def tts(clause):
        synthesized.append(clause)
        for _ in range(3): yield b'\x01\x00'*160
    async def mouth(**_):
        yield {'type':'token','content':'First spoken clause. '}
        yield {'type':'token','content':'Second clause must not be spoken.'}
    monkeypatch.setattr(worker,'stream_chat',mouth)
    monkeypatch.setattr(worker.tts_service,'synthesize',tts)
    monkeypatch.setattr(worker,'_cancelled',lambda _:any(x['type']=='audio_chunk' for x in e.published))
    assert _run_chat(e)
    assert sum(x['type']=='audio_chunk' for x in e.published)==1
    assert len(synthesized)==1 and not e.announced
    with e.db() as db:
        output=db.scalar(select(CoordinatorOutput))
        assert output.status=='partial'
        m=db.get(ChatMessage,output.assistant_message_id)
        assert m.status=='partial'
    assert not _run_chat(e)


def test_new_message_cancels_autonomous_reply_during_audio(responding,monkeypatch):
    e=responding;propose(e,monkeypatch);assert coordinator.execute_once()
    with e.db() as db:
        j=db.scalar(select(ChatJob));j.payload={**j.payload,'voice':True};db.commit()
    async def tts(_):
        yield b'\x01\x00'*160
        with e.db() as db:
            c=db.get(type(e.conversation),e.conversation.id)
            chat_service.add_message(db,conversation=c,role='user',content='Actually, a new question')
    monkeypatch.setattr(worker.tts_service,'synthesize',tts)
    assert _run_chat(e)
    with e.db() as db:
        assert db.scalar(select(CoordinatorOutput)).status=='cancelled'
        assert not list(db.scalars(select(ChatMessage).where(ChatMessage.role=='assistant')))
    assert not e.announced


_CHILD = r'''
import os,sys
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings
from app.services import coordinator,cognition_decisions,autonomous_speech,chat
from app.ai.state.decisions import ResultDecision
engine=create_engine(os.environ['TEST_DATABASE_URL'],connect_args={'options':'-csearch_path='+os.environ['STEP6_SCHEMA']})
coordinator.SessionLocal=sessionmaker(engine,expire_on_commit=False)
s=get_settings().model_copy(update={'redis_url':None,'shared_state_log_path':os.environ['STEP6_TRACE']})
coordinator.get_settings=lambda:s
chat.get_settings=lambda:s
cognition_decisions.PROVIDERS['result']=lambda _:ResultDecision('respond','scripted-step6',True)
autonomous_speech.agent_say.user_spoke_since=lambda *a:False
autonomous_speech.agent_say.allowed=lambda *a,**k:(True,'')
autonomous_speech.desktop_preview.redis_client=lambda:None
if sys.argv[1]=='accept_and_crash':
 with coordinator.SessionLocal() as db:
  work,_=coordinator._claim(db,'crashing-process');db.commit();db.expunge(work)
 coordinator._accept(work.id,'crashing-process',work.cancellation_generation,'completed',work.result or {'kind':'decision','conclusion':'Scripted completed conclusion'},None)
 os._exit(17)
print(coordinator.execute_once())
'''


def child(e,mode):
    with e.db() as db: schema=db.scalar(text('select current_schema()'))
    result=subprocess.run([sys.executable,'-c',_CHILD,mode],env={**os.environ,'STEP6_SCHEMA':schema,'STEP6_TRACE':str(e.log)},capture_output=True,text=True,timeout=30,cwd=Path(__file__).resolve().parents[1])
    assert result.returncode==(17 if mode=='accept_and_crash' else 0),result.stderr
    return result


def test_autonomous_result_survives_real_worker_process_exit(responding,monkeypatch):
    e=responding;propose(e,monkeypatch)
    child(e,'accept_and_crash');child(e,'restart')
    assert _run_chat(e) and not _run_chat(e)
    assert len(e.announced)==1


def test_conversation_result_survives_real_worker_process_exit(responding):
    e=responding;e.calls['jev']=('not_required','required')
    _say(e)
    child(e,'accept_and_crash');child(e,'restart')
    # A committed result needs a recoverable conclusion decision for either source.
    with e.db() as db:
        output=db.scalar(select(CoordinatorOutput).where(CoordinatorOutput.phase=='follow_up'))
        assert output is not None,'Completed conversational mind result was orphaned after worker restart'
    assert _run_chat(e)


def test_restart_repairs_receipt_after_reply_commit_before_output_update(responding,monkeypatch):
    e=responding;propose(e,monkeypatch);assert coordinator.execute_once()
    actual=coordinator.follow_up_outcome
    def crash(*a,**k):raise SystemExit('crash after reply commit, before coordinator receipt')
    monkeypatch.setattr(coordinator,'follow_up_outcome',crash)
    with pytest.raises(SystemExit):_run_chat(e)
    monkeypatch.setattr(coordinator,'follow_up_outcome',actual)
    child(e,'restart')
    with e.db() as db:
        m=db.scalar(select(ChatMessage).where(ChatMessage.role=='assistant'))
        assert m is not None
        output=db.scalar(select(CoordinatorOutput))
        assert output.status=='delivered' and output.assistant_message_id==m.id,'Committed mouth reply left coordinator output permanently dispatched'
    assert not _run_chat(e)


def test_retry_after_completed_delivery_does_not_repeat_mouth(responding,monkeypatch):
    e=responding;propose(e,monkeypatch);assert coordinator.execute_once() and _run_chat(e)
    calls=len(e.inputs)
    with e.db() as db:
        j=db.scalar(select(ChatJob));j.status='processing';j.lease_expires_at=coordinator._now()-timedelta(seconds=1);db.commit()
    assert _run_chat(e)
    assert len(e.inputs)==calls and len(e.announced)==1
    with e.db() as db:assert len(list(db.scalars(select(ChatMessage).where(ChatMessage.role=='assistant'))))==1


@pytest.mark.parametrize('revision',['plan_revision','execution_generation','state_revision'])
@pytest.mark.parametrize('when',['before_cognition','before_mouth','during_mouth'])
def test_activity_revision_change_fences_autonomous_speech(responding,monkeypatch,revision,when):
    from app.db.models import DesktopEnvironment,DesktopSession
    from app.services import autonomous_speech
    e=responding
    monkeypatch.setattr(autonomous_speech.agent_say,'user_spoke_since',lambda *a:False)
    monkeypatch.setattr(autonomous_speech.agent_say,'allowed',lambda *a,**k:(True,''))
    monkeypatch.setattr(autonomous_speech.desktop_preview,'redis_client',lambda:None)
    with e.db() as db:
        host=DesktopEnvironment(owner_user_id=e.user.id,display_name='Step6 fixture',hosting_kind='game_pack',allowed_profile_ids=['minecraft_pack'])
        db.add(host);db.flush()
        session=DesktopSession(owner_user_id=e.user.id,environment_id=host.id,conversation_id=e.conversation.id,profile_id='minecraft_pack',profile_revision=1,state='running',control_owner='gameplay',max_duration_seconds=3600)
        db.add(session);db.commit();sid=session.id
        assert autonomous_speech.submit(db,user_id=e.user.id,activity_id=sid,source='minecraft',event_id=uuid.uuid4(),text='Old activity proposal',reasons=['thought: test'],commentary_mode='occasional').startswith('Proposed')
    def change():
        with e.db() as db:
            row=db.get(DesktopSession,sid);setattr(row,revision,getattr(row,revision)+1);db.commit()
    if when=='before_cognition':change()
    assert coordinator.execute_once()
    if when=='before_mouth':change()
    if when=='during_mouth':
        async def mouth(**_):
            yield {'type':'token','content':'Old plan wording.'}
            change()
        monkeypatch.setattr(worker,'stream_chat',mouth)
    ran=_run_chat(e)
    assert ran==(when!='before_cognition')
    assert not e.announced
    with e.db() as db:
        assert not list(db.scalars(select(ChatMessage).where(ChatMessage.role=='assistant')))
        output=db.scalar(select(CoordinatorOutput))
        assert output.status in ('cancelled','not_sent')


def test_quiet_mode_enabled_during_mouth_cancels_proposal(responding,monkeypatch):
    from app.services import autonomous_speech
    e=responding;propose(e,monkeypatch);assert coordinator.execute_once()
    async def mouth(**_):
        yield {'type':'token','content':'Queued commentary.'}
        monkeypatch.setattr(autonomous_speech.agent_say,'allowed',lambda *a,**k:(False,'quiet'))
    monkeypatch.setattr(worker,'stream_chat',mouth)
    assert _run_chat(e) and not e.announced
    with e.db() as db:
        assert not list(db.scalars(select(ChatMessage).where(ChatMessage.role=='assistant')))
        assert db.scalar(select(CoordinatorOutput)).status=='cancelled'


def test_pending_announcement_retries_without_regenerating(responding, monkeypatch):
    e = responding
    propose(e, monkeypatch)
    coordinator.execute_once()
    monkeypatch.setattr(worker, '_announce', lambda *a: False)
    assert _run_chat(e)
    calls = len(e.inputs)
    with e.db() as db:
        output = db.scalar(select(CoordinatorOutput))
        assert output.status == 'delivered' and output.announcement_pending
        message_id = output.assistant_message_id
    published = []
    monkeypatch.setattr(worker, '_announce', lambda job, reply, mid: published.append(mid) or True)
    assert coordinator.execute_once()
    assert not coordinator.execute_once()
    assert published == [message_id] and len(e.inputs) == calls
    with e.db() as db:
        assert not db.scalar(select(CoordinatorOutput)).announcement_pending


def test_legacy_dispatched_receipt_is_reconciled_and_announced(responding, monkeypatch):
    e = responding
    propose(e, monkeypatch)
    coordinator.execute_once()
    assert _run_chat(e)
    with e.db() as db:
        output = db.scalar(select(CoordinatorOutput))
        mid = output.assistant_message_id
        output.status, output.assistant_message_id = 'dispatched', None
        db.commit()
    announced = []
    monkeypatch.setattr(worker, '_announce', lambda job, reply, mid: announced.append(mid) or True)
    assert coordinator.execute_once()
    assert announced == [mid]
    with e.db() as db:
        output = db.scalar(select(CoordinatorOutput))
        assert output.status == 'delivered' and output.assistant_message_id == mid
        assert not output.announcement_pending


@pytest.mark.parametrize('foreign_turn', [False, True])
def test_reconnect_history_uses_owned_pending_turn_not_latest_chat(responding, monkeypatch, foreign_turn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import chat as route
    from app.core.config import get_settings
    from types import SimpleNamespace

    e = responding
    with e.db() as db:
        target = chat_service.get_conversation(db, user_id=e.user.id, conversation_id=e.conversation.id)
        if foreign_turn:
            owner = create_user(db)
            target = create_conversation(db, owner)
        turn = uuid.uuid4()
        chat_service.enqueue_job(db, user_id=target.user_id, conversation=target, turn_id=turn,
                                 payload={'content': 'Pending'})
        latest = create_conversation(db, e.user)
    monkeypatch.setattr(route, 'SessionLocal', e.db)
    monkeypatch.setattr(route.auth_service, 'get_user_for_token', lambda *a: e.user)
    monkeypatch.setattr(route, '_active_jobs_for_user', lambda *a: [])
    monkeypatch.setattr(route.awareness_service, 'build', lambda *a, **kw: SimpleNamespace(to_dict=lambda: {}))
    app = FastAPI()
    app.include_router(route.router, prefix='/api')
    settings = get_settings()
    with TestClient(app) as client:
        with client.websocket_connect(f'/api/chat/ws?resume_turn_id={turn}', headers={
            'Origin': settings.cors_origins[0], 'Cookie': f'{settings.session_cookie_name}=test',
        }) as ws:
            frame = ws.receive_json()
            assert frame['type'] == 'history'
            assert frame['conversation_id'] == str(latest.id if foreign_turn else e.conversation.id)
