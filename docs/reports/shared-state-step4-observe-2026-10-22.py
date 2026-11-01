"""Read-only inspection for explicit live frontend E2E fixture turn IDs. Never exports private thinking."""
import sys,json,time,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import ChatJob,ChatMessage,CoordinatorWork,CoordinatorOutput,RecallReceipt
turn=uuid.UUID(sys.argv[1]);wait='--wait' in sys.argv
samples=[]
for _ in range(180 if wait else 1):
 with SessionLocal() as db:
  work=list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id==turn).order_by(CoordinatorWork.created_at,CoordinatorWork.kind)))
  outputs=list(db.scalars(select(CoordinatorOutput).where(CoordinatorOutput.request_id==turn)))
  sample={'at':time.time(),'work':[{k:str(getattr(w,k)) if k in ('id',) else getattr(w,k) for k in ('id','kind','status','attempt')} for w in work],'outputs':[{'phase':o.phase,'decision':o.decision,'status':o.status} for o in outputs]}
  if not samples or sample['work']!=samples[-1]['work'] or sample['outputs']!=samples[-1]['outputs']:samples.append(sample)
  if not wait or not work or (all(w.status in ('completed','failed','expired','superseded','cancelled') for w in work) and any(o.phase=='follow_up' for o in outputs)):break
 time.sleep(.5)
with SessionLocal() as db:
 jobs=list(db.scalars(select(ChatJob).where(ChatJob.turn_id==turn)))
 messages=list(db.scalars(select(ChatMessage).where(ChatMessage.turn_id==turn)))
 receipts=list(db.scalars(select(RecallReceipt).where(RecallReceipt.assistant_message_id.in_([m.id for m in messages]))))
 work=list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id==turn)))
 outputs=list(db.scalars(select(CoordinatorOutput).where(CoordinatorOutput.request_id==turn)))
 data={'samples':samples,'jobs':[{'id':str(j.id),'turn_id':str(j.turn_id),'user_id':str(j.user_id),'status':j.status,'attempts':j.attempts,'error':j.last_error} for j in jobs],
 'messages':[{'id':str(m.id),'role':m.role,'content':m.content,'status':m.status} for m in messages],
 'work':[{**{k:getattr(w,k) for k in ('id','request_id','kind','status','dependency_ids','attempt','cancellation_generation','error_code','input_versions','created_at','updated_at')},'result':{k:v for k,v in (w.result or {}).items() if k!='thinking'}} for w in work],
 'outputs':[{k:getattr(o,k) for k in ('phase','decision','decision_origin','missing_capability','status','chat_job_id','assistant_message_id','created_at','updated_at')} for o in outputs],
 'receipts':[{k:getattr(r,k) for k in ('id','status','coverage','counts')} for r in receipts]}
trace=Path('var/shared_state.jsonl')
if trace.exists():
 data['traces']=[]
 for line in trace.read_text().splitlines():
  r=json.loads(line)
  w=r.get('work')
  if (isinstance(w,dict) and w.get('id') in {str(j.id) for j in jobs}) or (isinstance(w,str) and w in {str(item.id) for item in work}) or r.get('request')==str(turn):data['traces'].append(r)
print(json.dumps(data,default=str))
