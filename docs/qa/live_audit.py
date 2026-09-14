"""Bounded live check: actual local nutrition models, remote VLM and chat.

Only a synthetic fixture/message is transmitted; no Google/mail/desktop tools.
"""
import asyncio
import io
import json
import time
import uuid
import math
from qa_env import ARTIFACTS
import httpx
from PIL import Image,ImageDraw
from websockets.asyncio.client import connect

async def main():
    rows=[]
    def record(name,ok,actual,expected):
        row={"test":name,"status":"PASS" if ok else "FAIL","actual":actual,"expected":expected}
        rows.append(row);print(json.dumps(row),flush=True)
        (ARTIFACTS/"live-results.json").write_text(json.dumps(rows,indent=2))
    base="http://127.0.0.1:58001"
    # Draw a test-only plate. It is not a real meal or an accuracy reference.
    im=Image.new("RGB",(384,384),(140,110,90));draw=ImageDraw.Draw(im)
    draw.ellipse((30,30,354,354),fill=(240,240,230));draw.ellipse((85,80,205,205),fill=(225,213,177));draw.rectangle((180,170,290,270),fill=(140,85,40));draw.ellipse((75,205,165,290),fill=(45,130,50))
    out=io.BytesIO();im.save(out,format="JPEG")
    (ARTIFACTS/"synthetic-meal-fixture.jpg").write_bytes(out.getvalue())
    async with httpx.AsyncClient(base_url=base,timeout=240) as client:
        payload={"name":"Live QA", "email":f"qa-live-{uuid.uuid4().hex}@example.com","password":"Synthetic-live-password-2026"}
        r=await client.post("/api/auth/signup",json=payload)
        record("LIVE signup",r.status_code==201,{"status":r.status_code},"201")
        r=await client.post("/api/auth/signin",json=payload)
        record("LIVE signin",r.status_code==200,{"status":r.status_code},"200")
        cookie={"Cookie":"qa_session="+client.cookies.get("qa_session")}
        start=time.perf_counter()
        try:
            async with connect("ws://127.0.0.1:58001/api/chat/ws",additional_headers=cookie) as ws:
                await ws.send(json.dumps({"type":"run","content":"For a synthetic QA check, explain protein in one short sentence. Do not call any tools."}))
                types=[];answer="";first=None
                async with asyncio.timeout(65):
                    while True:
                        msg=json.loads(await ws.recv());types.append(msg["type"])
                        if msg["type"]=="token":
                            if first is None:first=time.perf_counter()-start
                            answer+=msg["content"]
                        if msg["type"] in ("done","error","stopped"):break
                record("LIVE real orchestrator WS",types[-1]=="done" and bool(answer),{"frames":types,"reply":answer,"first_token_seconds":first,"total_seconds":round(time.perf_counter()-start,3)},"live streamed reply persists")
        except Exception as exc:
            record("LIVE real orchestrator WS",False,type(exc).__name__,"live streamed reply")
        for mode in ["regression","combined","regression"]:
            start=time.perf_counter()
            try:
                r=await client.post("/api/predict",params={"mode":mode},files={"file":("synthetic-meal.jpg",out.getvalue(),"image/jpeg")})
                elapsed=round(time.perf_counter()-start,3)
                body=r.json() if r.status_code==200 else {"error":r.text[:150]}
                ok=r.status_code==200 and all(math.isfinite(body[x]) and body[x]>=0 for x in ["calories","fat","carbs","protein"])
                record("LIVE actual model "+mode,ok,{"status":r.status_code,"seconds":elapsed,"result":body},"finite nonnegative output; no accuracy conclusion from synthetic drawing")
            except Exception as exc:
                record("LIVE actual model "+mode,False,{"error":type(exc).__name__,"seconds":round(time.perf_counter()-start,3)},"bounded successful inference")
                break
        r=await client.delete("/api/auth/me")
        record("LIVE delete test account",r.status_code==204,{"status":r.status_code},"204")

if __name__=="__main__":asyncio.run(main())
