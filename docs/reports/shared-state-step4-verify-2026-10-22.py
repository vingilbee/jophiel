"""Read-only checks for the explicit step-4 E2E runs; never exports mind's private thinking."""
import json,sys,statistics
from collections import Counter
from pathlib import Path
from datetime import datetime
from sqlalchemy import select
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.db.session import SessionLocal
from app.db.models import ChatJob,ChatMessage,CoordinatorWork,CoordinatorOutput
root=Path(__file__).resolve().parent
traces=[json.loads(l) for l in Path('var/shared_state.jsonl').read_text().splitlines()]
checks=[];turns=[]
with SessionLocal() as db:
 for fixture in ['clean','remaining','synthetic','cacheprobe']:
  e=json.loads((root/f'shared-state-step4-{fixture}-2026-10-22.json').read_text())
  assert not e.get('failure') and not e['errors']
  for t in e['turns']:
   import uuid
   tid=uuid.UUID(t['sent']['turn_id']);uid=uuid.UUID(e['user']['id'])
   jobs=list(db.scalars(select(ChatJob).where(ChatJob.turn_id==tid,ChatJob.user_id==uid)))
   assert len(jobs)==1 and jobs[0].status=='completed' and jobs[0].attempts==1
   ms=list(db.scalars(select(ChatMessage).where(ChatMessage.turn_id==tid)))
   assert len(ms)==2
   assistants=[m for m in ms if m.role=='assistant'];assert len(assistants)==1 and assistants[0].content==t['reply'] and assistants[0].status=='completed'
   matching=[r for r in traces if r.get('event')==f'message:{jobs[0].user_message_id}'];assert len(matching)==1
   r=matching[0]
   assert r['assessment']['status']=='ok' and r['calls']['jev']==1
   assert t['transportPass'] and t['renderedTextVisible']
   assert sum(f['type']=='done' for f in t['frames'])==1
   work=list(db.scalars(select(CoordinatorWork).where(CoordinatorWork.request_id==tid)))
   outputs=list(db.scalars(select(CoordinatorOutput).where(CoordinatorOutput.request_id==tid)))
   lifecycle=[]
   if r['route']['route'] in ('mind','recall_then_mind'):
    mind=next(w for w in work if w.kind=='mind')
    assert mind.status=='completed' and mind.result and not mind.result.get('error')
    immediate=next(o for o in outputs if o.phase=='immediate');follow=next(o for o in outputs if o.phase=='follow_up')
    assert immediate.status=='delivered' and follow.decision=='blocked' and follow.status=='not_sent' and follow.chat_job_id is None
    lifecycle=[{'kind':w.kind,'status':w.status,'dependency_ids':w.dependency_ids,'created_at':str(w.created_at),'updated_at':str(w.updated_at),'latency_ms':(w.result or {}).get('latency_ms'),'evidence_status':(w.result or {}).get('evidence_status')} for w in work]
    if r['route']['route']=='recall_then_mind':
     recall=next(w for w in work if w.kind=='recall');assert str(recall.id) in mind.dependency_ids and recall.status=='completed' and mind.result['evidence_status']=='matched'
   check={'fixture':fixture,'label':t['label'],'turn_id':str(tid),'transport_pass':True,'expected':t['expected'],'assessment':r['assessment'],'route':r['route']['route'],'calls':r['calls'],'first_token_ms':t['firstTokenMs'],'immediate_ms':t['immediateMs'],'recall':r['recall'],'memory_cache':r['dependencies']['memory_cache'],'work':lifecycle}
   checks.append(check);turns.append(t)
counts=Counter(c['route'] for c in checks)
summary={'turns':len(checks),'transport_passes':len(checks),'assessment_successes':len(checks),'route_counts':dict(counts),'provisional_label_matches':sum(all(c['assessment'][k]==v for k,v in c['expected'].items()) for c in checks),'jev_latency_median_ms':statistics.median(c['assessment']['latency_ms'] for c in checks),'jev_latency_range_ms':[min(c['assessment']['latency_ms'] for c in checks),max(c['assessment']['latency_ms'] for c in checks)],'first_token_median_ms':statistics.median(c['first_token_ms'] for c in checks),'first_token_range_ms':[min(c['first_token_ms'] for c in checks),max(c['first_token_ms'] for c in checks)],'blocked_completed_mind_results':sum(bool(c['work']) for c in checks)}
out={'summary':summary,'checks':checks}
(root/'shared-state-step4-verification-2026-10-22.json').write_text(json.dumps(out,indent=2))
print(json.dumps(summary,indent=2))
