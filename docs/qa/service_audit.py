"""Controlled failure/concurrency tests, with real disposable PostgreSQL."""
import concurrent.futures
import io
import json
import threading
import uuid
from types import SimpleNamespace

from qa_env import configure, ARTIFACTS
configure()
from PIL import Image
from sqlalchemy import select
from app.core.config import get_settings
from app.db.models import User, Conversation, ChatMessage, MemoryJob, LongTermMemory
from app.db.session import SessionLocal
from app.schemas.long_term_memory import MemoryCandidate
from app.services import long_term_memory as memory, image_store
from app.ai.MacroVision.predictor import MacroVisionPredictor
from app.ai.MacroVision.vlm import parse_vlm_json

rows=[]
def record(name,ok,actual,expected):
    row={"test":name,"status":"PASS" if ok else "FAIL","actual":actual,"expected":expected}
    rows.append(row)
    print(json.dumps(row))
    (ARTIFACTS/"service-results.json").write_text(json.dumps(rows,indent=2))

settings=get_settings()
settings.long_term_memory_enabled=True
settings.redis_url=None  # test durable polling without Redis wakeups
memory._embedding=lambda _:None
candidate=MemoryCandidate(kind="preference",normalized_key="preference:qa",content="I prefer QA green tea",importance=.9,confidence=1)
memory._extract=lambda *_:[candidate]
with SessionLocal() as db:
    a=User(name="Memory QA",email=f"memory-{uuid.uuid4().hex}@example.com",hashed_password="synthetic-not-a-login")
    b=User(name="Memory B",email=f"memory-{uuid.uuid4().hex}@example.com",hashed_password="synthetic-not-a-login")
    db.add_all([a,b]);db.commit(); aid=a.id;bid=b.id
    conv=Conversation(user_id=aid,title="Memory audit")
    db.add(conv);db.commit();cid=conv.id
    def new_job():
        user=ChatMessage(conversation_id=cid,role="user",content=candidate.content)
        db.add(user);db.commit()
        assistant=ChatMessage(conversation_id=cid,role="assistant",content="Noted for QA")
        db.add(assistant);db.commit()
        return memory.schedule_consolidation(db,user_id=aid,conversation_id=cid,assistant_message_id=assistant.id)
    job=new_job();jid=job.id
    again=memory.schedule_consolidation(db,user_id=aid,conversation_id=cid,assistant_message_id=job.assistant_message_id)
    record("MEM-02 enqueue idempotency",again.id==jid,str(again.id==jid),"same durable job")
    record("MEM-04 pending job without Redis",memory.next_pending_job_id(db)==jid,"pending row discoverable","polling sees durable job")
    ok=memory.process_job(db,jid)
    record("MEM-02 process and persist",ok and db.scalar(select(LongTermMemory).where(LongTermMemory.user_id==aid)) is not None,ok,"completed with memory row")
    record("MEM-02 completed retry no-op",memory.process_job(db,jid) is False,False,"already completed not reprocessed")
    record("MEM-06 lexical fallback", "green tea" in (memory.retrieve_for_prompt(aid,"tea") or ""),memory.retrieve_for_prompt(aid,"tea"),"relevant memory without embedding")
    record("MEM-05 user isolation",memory.retrieve_for_prompt(bid,"tea") is None,memory.retrieve_for_prompt(bid,"tea"),"no other user's fact")
    candidate=candidate.model_copy(update={"content":"I prefer QA black tea"})
    job=new_job();memory.process_job(db,job.id)
    db.expire_all()
    records=list(db.scalars(select(LongTermMemory).where(LongTermMemory.user_id==aid)))
    record("MEM-03 correction",len(records)==1 and "black tea" in records[0].content,{"rows":len(records),"content":records[0].content},"updated single fact")
    candidate=candidate.model_copy(update={"operation":"forget"})
    job=new_job();memory.process_job(db,job.id)
    record("MEM-03 retraction",memory.retrieve_for_prompt(aid,"tea") is None,memory.retrieve_for_prompt(aid,"tea"),"deleted memory not retrieved")
    candidate=candidate.model_copy(update={"operation":"upsert"})
    job=new_job(); crash_id=job.id
    job.status="processing";job.attempts=1;db.commit()
    selected=memory.next_pending_job_id(db)
    record("MEM-04 crash recovery after processing commit",selected==crash_id,{"job_status":"processing","selected_crashed_job":selected==crash_id},"worker can reclaim abandoned in-progress job")
    job.status="failed";db.commit()
    job=new_job(); concurrent_id=job.id

calls=[]
entered=threading.Event()
release=threading.Event()
def held_extract(*args):
    calls.append(threading.get_ident())
    entered.set()
    release.wait(4)
    return []
memory._extract=held_extract
def process():
    with SessionLocal() as db:
        return memory.process_job(db,concurrent_id)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    first=pool.submit(process)
    entered.wait(3)
    second=pool.submit(process)
    try: second.result(timeout=.7)
    except concurrent.futures.TimeoutError: pass
    release.set()
    outcomes=[first.result(),second.result()]
record("MEM-05 concurrent job claim",len(calls)==1,{"extraction_calls":len(calls),"outcomes":outcomes},"one worker extracts/processes a job")

# Exercise actual prediction combination with only expensive tensor steps replaced.
predictor=object.__new__(MacroVisionPredictor)
predictor.depth_meters=lambda _:None
predictor.depth_to_image=lambda *_:Image.new("RGB",(2,2))
predictor._regress=lambda *_:(500.,20.,60.,25.)
for label,raw in [("missing fields",'{"dish_name":"rice"}'),("wrong numeric type",'{"dish_name":"rice","estimated_portion_g":"many","base_nutrition_per_100g":{"calories":100,"fat":1,"carbs":20,"protein":2}}'),("negative nutrition",'{"dish_name":"rice","estimated_portion_g":100,"base_nutrition_per_100g":{"calories":-100,"fat":-1,"carbs":-20,"protein":-2}}'),("nonfinite",'{"dish_name":"rice","estimated_portion_g":100,"base_nutrition_per_100g":{"calories":NaN,"fat":1,"carbs":20,"protein":2}}')]:
    predictor._vlm_estimate=lambda *_,r=raw:parse_vlm_json(r)
    try:
        result=predictor.predict_combined(Image.new("RGB",(2,2))).to_dict()
        record("IMG-03 malformed VLM "+label,result["source"]=="regression",str(result),"validated regression fallback")
    except Exception as exc:
        record("IMG-03 malformed VLM "+label,False,type(exc).__name__+": "+str(exc),"validated regression fallback without exception")
predictor.vlm_backend=None;predictor.qwen_model=object()
# Restore actual dispatcher: detects API signature mismatch before loading Qwen.
try:
    MacroVisionPredictor._vlm_estimate(predictor,Image.new("RGB",(2,2)))
    record("IMG-02 local Qwen dispatch",True,"called","signature accepts hints")
except Exception as exc:
    record("IMG-02 local Qwen dispatch",not isinstance(exc,TypeError),type(exc).__name__+": "+str(exc),"dispatcher matches _qwen_estimate signature")

im=Image.new("RGB",(80,40));exif=im.getexif();exif[274]=6
buf=io.BytesIO();im.save(buf,format="JPEG",exif=exif)
decoded=image_store.decode_image(buf.getvalue(),"image/jpeg")
record("IMG-07 EXIF orientation normalized",decoded.size==(40,80),{"decoded_size":decoded.size,"orientation":decoded.getexif().get(274)},"portrait 40x80 before model processing")

print(json.dumps({"total":len(rows),"pass":sum(r["status"]=="PASS" for r in rows),"fail":sum(r["status"]=="FAIL" for r in rows)}))
