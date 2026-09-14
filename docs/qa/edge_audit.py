"""Additional real transport boundaries and small, bounded concurrency checks."""
import asyncio
import io
import json
import struct
import zlib
import uuid
import time
from qa_env import configure,ARTIFACTS
configure()
import httpx
from PIL import Image
from websockets.asyncio.client import connect

rows=[]
def record(name,ok,actual,expected):
    row={"test":name,"status":"PASS" if ok else "FAIL","actual":actual,"expected":expected}
    rows.append(row);print(json.dumps(row),flush=True)
    (ARTIFACTS/"edge-results.json").write_text(json.dumps(rows,indent=2))

async def drain(ws,timeout=5):
    frames=[]
    async with asyncio.timeout(timeout):
        while True:
            frame=json.loads(await ws.recv());frames.append(frame)
            if frame['type'] in ['done','stopped','error']:return frames

async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:58000",timeout=15) as a, httpx.AsyncClient(base_url="http://127.0.0.1:58000",timeout=15) as b:
        for label,c in [('a',a),('b',b)]:
            payload={"name":"Edge QA "+label,"email":f"edge-{uuid.uuid4().hex}@example.com","password":"Synthetic-edge-password-2026"}
            await c.post('/api/auth/signup',json=payload);await c.post('/api/auth/signin',json=payload)
        path='/api/dashboard/calorie-logs'
        r=await a.post(path,content='{"calories":1e309,"fat":0,"carbs":0,"protein":0,"date":"2026-09-07"}',headers={'Content-Type':'application/json'})
        record('LOG-04 nonfinite number request',r.status_code==422,{'status':r.status_code},'422 before persistence')
        r=await a.get(path)
        record('LOG-04 list after nonfinite create',r.status_code==200,{'status':r.status_code},'bad numeric input cannot poison list serialization')
        # Header alone declares enormous dimensions; Pillow rejects before allocation.
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        huge=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',20000,20000,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00'*4))+chunk(b'IEND',b'')
        for endpoint in ['/api/images','/api/predict']:
            r=await a.post(endpoint,files={'file':('oversized-dimensions.png',huge,'image/png')})
            record('IMG-07 pixel bomb '+endpoint,r.status_code==400,{'status':r.status_code,'body':r.text[:120]},'controlled 400 for decompression-bomb input')
        buf=io.BytesIO();Image.new('RGB',(4,4)).save(buf,format='PNG')
        r=await a.post('/api/images',files={'file':('private.png',buf.getvalue(),'image/png')});owned=r.json()['image_id']
        wsurl='ws://127.0.0.1:58000/api/chat/ws'
        headers=lambda c:{'Cookie':'qa_session='+c.cookies.get('qa_session')}
        async with connect(wsurl,additional_headers=headers(a)) as ws:
            await ws.send(json.dumps({'type':'run','content':'Create owner conversation'}));frames=await drain(ws)
            cid=next(x['conversation_id'] for x in frames if x['type']=='meta')
        async with connect(wsurl,additional_headers=headers(b)) as ws:
            await ws.send(json.dumps({'type':'run','conversation_id':cid,'content':'Foreign ID QA','image_ids':[owned]}));frames=await drain(ws)
            newcid=next(x['conversation_id'] for x in frames if x['type']=='meta')
            detail=(await b.get('/api/chat/conversations/'+newcid)).json()
            record('CHAT-06 foreign conversation WS safely creates new',newcid!=cid and len(detail['messages'])==2,{'new_owned_conversation':newcid!=cid,'message_count':len(detail['messages'])},'current service contract starts a new owned conversation')
            record('CHAT-10 foreign attachment discarded',not detail['messages'][0]['image_ids'],{'image_ids':detail['messages'][0]['image_ids']},'no foreign attachment passed or persisted')
            await ws.send(json.dumps({'type':'sense','timezone':'Invalid/Timezone','route':'/chat','screen_image_id':owned,'vision_enabled':True}))
            # Ignore independent state snapshots; bounded response should be safe.
            await ws.send(json.dumps({'type':'run','content':'After invalid timezone'}));frames=await drain(ws)
            record('SENSE-02 invalid timezone recovery',frames[-1]['type']=='done',[x['type'] for x in frames],'safe fallback, valid next turn')
        # Verify other user's stop cannot cancel A.
        async with connect(wsurl,additional_headers=headers(a)) as wa, connect(wsurl,additional_headers=headers(b)) as wb:
            await wa.send(json.dumps({'type':'run','content':'Cross-user stop check'}))
            await wb.send(json.dumps({'type':'stop','conversation_id':cid}))
            frames=await drain(wa)
            record('CHAT-11 cross-user stop isolation',frames[-1]['type']=='done',[x['type'] for x in frames],'B cannot stop A')
        times=[]
        async def get_images():
            start=time.perf_counter();r=await b.get('/api/images');times.append((time.perf_counter()-start)*1000);return r.status_code
        codes=await asyncio.gather(*(get_images() for _ in range(30)))
        times.sort()
        record('PERF-01 bounded 30 concurrent ordinary reads',all(x==200 for x in codes),{'requests':30,'errors':sum(x!=200 for x in codes),'p50_ms':round(times[15],1),'p95_ms':round(times[28],1)},'all succeed; exploratory local latency only')
        response=await b.options('/api/images',headers={'Origin':'https://hostile.example','Access-Control-Request-Method':'POST'})
        record('SEC-02 hostile CORS preflight',response.status_code==400 and 'access-control-allow-origin' not in response.headers,{'status':response.status_code,'allow_origin':response.headers.get('access-control-allow-origin')},'untrusted origin not allowed by CORS')
        r=await b.post('/api/images',headers={'Origin':'https://hostile.example'},files={'file':('csrf.png',buf.getvalue(),'image/png')})
        record('SEC-02 hostile Origin mutation with explicit cookie',r.status_code in [400,403],{'status':r.status_code,'allow_origin':r.headers.get('access-control-allow-origin')},'server rejects untrusted Origin; browser exploitability also depends on SameSite topology')
        for c in [a,b]:await c.delete('/api/auth/me')
    print(json.dumps({'total':len(rows),'pass':sum(r['status']=='PASS' for r in rows),'fail':sum(r['status']=='FAIL' for r in rows)}))

if __name__=='__main__':asyncio.run(main())
