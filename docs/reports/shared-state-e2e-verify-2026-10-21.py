import json,uuid,statistics
from pathlib import Path
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import ChatJob,ChatMessage,RecallReceipt,RecallReference
root=Path('../docs/reports')
traces=[json.loads(l) for l in Path('var/shared_state.jsonl').read_text().splitlines()]
result={'checks':[], 'traces':[], 'jobs':[], 'receipts':[]}
with SessionLocal() as db:
 for mode in ['normal','cache','reading','degraded']:
  e=json.loads((root/f'shared-state-e2e-{mode}-2026-10-21.json').read_text())
  assert not e.get('failure') and not e['errors']
  for t in e['turns']:
   turn=uuid.UUID(t['sent']['turn_id'])
   jobs=list(db.scalars(select(ChatJob).where(ChatJob.turn_id==turn)))
   assert len(jobs)==1 and jobs[0].status=='completed' and jobs[0].attempts==1
   job=jobs[0]
   messages=list(db.scalars(select(ChatMessage).where(ChatMessage.turn_id==turn)))
   assert len(messages)==2
   assistant=next(m for m in messages if m.role=='assistant')
   assert assistant.status=='completed' and assistant.content==t['reply']
   rendered=t['reply']
   import re
   rendered=re.sub(r'\[\[sticker:[a-z0-9-]+\]\]','',rendered).strip()
   assert rendered in t['body'],t['label']
   matching=[r for r in traces if r['event']==f'message:{job.user_message_id}']
   assert len(matching)==1
   trace=matching[0]
   assert trace['work']['id']==str(job.id) and trace['delivery']['assistant_message_id']==str(assistant.id)
   assert trace['delivery']['outcome']=='completed'
   assert trace['calls']['fresh_recall_before_reply']==0 and trace['calls']['mind']==0
   assert sum(f['type']=='done' for f in t['frames'])==1
   assert sum(f['type']=='ack' for f in t['frames'])==1
   route=next(f for f in t['frames'] if f['type']=='route')
   assert route['outcome']==('degraded' if mode=='degraded' else 'unassessed')
   assert trace['cognition']['status']==('disabled' if mode=='degraded' else 'available')
   receipt=db.scalar(select(RecallReceipt).where(RecallReceipt.assistant_message_id==assistant.id))
   refs=list(db.scalars(select(RecallReference).where(RecallReference.receipt_id==receipt.id))) if receipt else []
   result['checks'].append({'mode':mode,'label':t['label'],'turn_id':str(turn),'pass':True,'first_token_ms':t['firstTokenMs'],'total_ms':t['totalMs'],'rendered_text_visible':True,'calls':trace['calls'],'cognition':trace['cognition']['status'],'arm':trace['cognition']['reason'],'references':len(refs)})
   result['traces'].append(trace)
   result['jobs'].append({'id':str(job.id),'turn_id':str(turn),'user_id':str(job.user_id),'status':job.status,'attempts':job.attempts,'last_error':job.last_error})
   if receipt:
    result['receipts'].append({'id':receipt.id,'turn_id':str(turn),'status':receipt.status,'coverage':receipt.coverage,'counts':receipt.counts,'references':[{c.name:str(getattr(r,c.name)) for c in RecallReference.__table__.columns} for r in refs]})
normal=[r for r in result['checks'] if r['mode']=='normal']
result['summary']={'turns':len(result['checks']),'all_pass':True,'normal_first_token_median_ms':statistics.median(r['first_token_ms'] for r in normal),'normal_first_token_min_ms':min(r['first_token_ms'] for r in normal),'normal_first_token_max_ms':max(r['first_token_ms'] for r in normal),'normal_total_median_ms':statistics.median(r['total_ms'] for r in normal)}
(root/'shared-state-e2e-verification-2026-10-21.json').write_text(json.dumps(result,indent=2,default=str))
print(json.dumps({'summary':result['summary'],'checks':result['checks']},indent=2))
