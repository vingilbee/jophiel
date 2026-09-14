"""Reproducible HTTP/WS audit against the disposable QA instance.

Records failed product expectations instead of stopping at the first defect.
No credentials are written to the result file.
"""
import asyncio
import io
import json
import time
import uuid
from pathlib import Path

from qa_env import configure, ARTIFACTS, TEMP
configure()
import httpx
import redis
from PIL import Image
from websockets.asyncio.client import connect
from sqlalchemy import create_engine, text
from app.core.config import get_settings

BASE = "http://127.0.0.1:58000"
WS = "ws://127.0.0.1:58000/api/chat/ws"
results = []


def record(name, ok, actual, expected):
    row = {"case": name, "status": "PASS" if ok else "FAIL", "actual": actual, "expected": expected}
    results.append(row)
    print(json.dumps(row))
    (ARTIFACTS / "api-results.json").write_text(json.dumps(results, indent=2))


def status(name, response, expected):
    record(name, response.status_code == expected,
           {"status": response.status_code, "body": response.text[:220] if response.status_code >= 400 else "omitted"},
           {"status": expected})


def png():
    out = io.BytesIO()
    Image.new("RGB", (48, 32), (50, 180, 80)).save(out, format="PNG")
    return out.getvalue()


async def frames_until(ws, terminal=("done", "stopped", "error"), timeout=6):
    frames = []
    async with asyncio.timeout(timeout):
        while True:
            value = json.loads(await ws.recv())
            frames.append(value)
            if value.get("type") in terminal:
                return frames


async def main():
    suffix = uuid.uuid4().hex[:10]
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as a, httpx.AsyncClient(base_url=BASE, timeout=15) as b, httpx.AsyncClient(base_url=BASE, timeout=15) as anon:
        for label, client in [("A", a), ("B", b)]:
            payload = {"name": f"QA {label}", "email": f"qa-{label.lower()}-{suffix}@example.com", "password": "Synthetic-qa-password-2026"}
            response = await client.post("/api/auth/signup", json=payload)
            status(f"AUTH-02 signup {label}", response, 201)
            if label == "A":
                aid = response.json()["id"]
                record("AUTH-02 API signup session contract", not client.cookies, {"session_created": bool(client.cookies)}, "API creates user without session; UI must handle this")
                status("AUTH-02 /me immediately after signup", await client.get("/api/auth/me"), 401)
            status(f"AUTH-05 signin {label}", await client.post("/api/auth/signin", json=payload), 200)
            status(f"AUTH-05 current user {label}", await client.get("/api/auth/me"), 200)
        token = a.cookies.get("qa_session")
        cookie = {"Cookie": f"qa_session={token}"}
        bid = (await b.get("/api/auth/me")).json()["id"]

        for endpoint in ["/api/auth/me", "/api/dashboard/weight-logs", "/api/dashboard/calorie-logs", "/api/images", "/api/assets", "/api/chat/conversations", "/api/integrations"]:
            status(f"SEC-01 anonymous GET {endpoint}", await anon.get(endpoint), 401)
        status("SEC-01 anonymous prediction", await anon.post("/api/predict", files={"file": ("test.png", png(), "image/png")}), 401)
        status("SEC-01 anonymous cognitive", await anon.post("/api/cognitive/inspect", json={"message":"hello"}), 401)

        status("AUTH-04 duplicate mixed-case email", await a.post("/api/auth/signup", json={"name":"Duplicate", "email":f"QA-A-{suffix}@EXAMPLE.COM", "password":"Synthetic-qa-password-2026"}), 409)
        status("AUTH-05 incorrect password", await anon.post("/api/auth/signin", json={"email":f"qa-a-{suffix}@example.com", "password":"wrong"}), 401)
        for field, value in [("name", ""), ("name", "x"*256), ("password", "x"*7), ("password", "x"*129), ("email", "invalid")]:
            payload = {"name":"Boundary", "email":f"bounds-{uuid.uuid4().hex}@example.com", "password":"Synthetic-qa-password-2026", field:value}
            status(f"AUTH-03 invalid {field} length={len(value)}", await anon.post("/api/auth/signup", json=payload), 422)
        status("AUTH-03 whitespace-only name", await anon.post("/api/auth/signup", json={"name":"   ","email":f"blank-{suffix}@example.com","password":"Synthetic-qa-password-2026"}), 422)

        for family, payload in [("weight-logs", {"weight":72.5,"date":"2026-09-07"}), ("calorie-logs", {"calories":600,"fat":20,"carbs":70,"protein":35,"date":"2026-09-07"})]:
            path = f"/api/dashboard/{family}"
            response = await a.post(path, json=payload)
            status(f"LOG-01 create {family}", response, 201)
            ident = response.json()["id"]
            row = path + "/" + ident
            for method in ("GET", "PATCH", "DELETE"):
                status(f"LOG-06 cross-user {method} {family}", await b.request(method, row, **({"json":{"date":"2026-09-08"}} if method == "PATCH" else {})), 404)
            status(f"LOG-02 partial date update {family}", await a.patch(row, json={"date":"2026-09-08"}), 200)
            response = await a.get(row)
            record(f"LOG-02 preserved fields {family}", all(response.json()[k]==v for k,v in payload.items() if k != "date"), response.json(), "only date changes")
            for body in [{}, {"date":None}, {next(k for k in payload if k != "date"):None}, {"unknown":123}]:
                status(f"LOG-03 invalid patch {family} {body}", await a.patch(row,json=body), 422)
            field = "weight" if family == "weight-logs" else "calories"
            for value, expected in ([(-1,422),(0,422),(0.01,201),(1000,201),(1000.01,422)] if field == "weight" else [(-1,422),(0,201),(1.25,201)]):
                status(f"LOG-04 {field}={value}", await a.post(path,json={**payload,field:value}), expected)
            response = await a.get(path,params={"start_date":"2026-09-08","end_date":"2026-09-08"})
            record(f"LOG-05 inclusive dates {family}", response.status_code==200 and len(response.json())==1 and response.json()[0]["id"]==ident, {"status":response.status_code,"count":len(response.json())}, "one matching date row")
            status(f"LOG-05 invalid date {family}", await a.get(path,params={"start_date":"not-date"}), 422)
            status(f"LOG-06 malformed UUID {family}", await a.get(path+"/bad"), 422)
            status(f"LOG-07 delete {family}", await a.delete(row), 204)
            status(f"LOG-07 delete again {family}", await a.delete(row), 404)

        response = await a.post("/api/images",files={"file":("fixture.png",png(),"image/png")})
        status("IMG-09 valid upload",response,201)
        image_id = response.json()["image_id"]
        for method,path in [("GET",f"/api/images/{image_id}/raw"),("DELETE",f"/api/images/{image_id}")]:
            status(f"IMG-10 foreign {method}",await b.request(method,path),404)
        for endpoint in ["/api/images","/api/predict"]:
            for filename,body,mime in [("empty.png",b"","image/png"),("broken.jpg",b"not an image","image/jpeg"),("text.txt",b"text","text/plain"),("too-big.png",b"0"*(10*1024*1024+1),"image/png")]:
                status(f"IMG-05/06 {endpoint} {filename}", await a.post(endpoint,files={"file":(filename,body,mime)}),400)
            status(f"IMG-05 missing multipart {endpoint}",await a.post(endpoint),422)
        response = await a.post("/api/predict?mode=regression",files={"file":("qa.png",png(),"image/png")})
        status("IMG-01 real route controlled predictor",response,200)
        record("IMG-01 prediction data contract",response.json().get("calories")==600.4 and bool(response.json().get("image_id")), response.json(),"controlled model output and stored image ID")
        status("IMG-02 invalid prediction mode",await a.post("/api/predict?mode=bad",files={"file":("qa.png",png(),"image/png")}),422)
        status("IMG-09 list invalid limit",await a.get("/api/images?limit=0"),422)

        for ext in ["pdf","docx","pptx","xlsx","txt","csv","png","jpg","jpeg","blend","fbx","obj","glb","gltf"]:
            # Extension acceptance is tested here, NOT format validity for dummy archives/meshes.
            data=b"%PDF-1.4\n%%EOF" if ext=="pdf" else b"synthetic fixture"
            response=await a.post("/api/assets",files={"file":("../QA."+ext.upper(),data,"application/octet-stream")})
            status(f"DOC-04 extension acceptance {ext}",response,201)
        response=await a.post("/api/assets",files={"file":("report.pdf",b"%PDF-1.4\n%%EOF","application/pdf")})
        asset_id=response.json()["asset_id"]
        for suffix2 in ["", "/raw", "/raw?preview=true"]:
            status(f"DOC-05 foreign asset {suffix2}",await b.get(f"/api/assets/{asset_id}{suffix2}"),404)
        for name,body in [("bad.pdf",b"fake"),("script.py",b"print(1)"),("empty.txt",b"")]:
            status(f"DOC-04 reject {name}",await a.post("/api/assets",files={"file":(name,body)}),400)
        response=await a.get(f"/api/assets/{asset_id}/raw?preview=true")
        record("DOC-06 preview headers",response.status_code==200 and response.headers.get("content-disposition","").startswith("inline") and response.headers.get("x-content-type-options")=="nosniff",dict((k,v) for k,v in response.headers.items() if k in ["content-disposition","cache-control","x-content-type-options"]),"inline PDF, private/no-store, nosniff")
        status("DOC-07 invalid signed image token",await anon.get("/api/assets/slide-image/invalid"),404)

        for i in range(14):
            await a.post("/api/images?kind=screen",files={"file":(f"screen{i}.png",png(),"image/png")})
        response=await a.get("/api/images?kind=screen&limit=100")
        record("SENSE-03 screen keep bound",len(response.json())==12,{"count":len(response.json())},"12 newest frames")
        response=await a.get("/api/images")
        record("SENSE-03 default list excludes screen",all(x["kind"]!="screen" for x in response.json()),{"kinds":[x["kind"] for x in response.json()]},"upload images only")

        for payload in [{"message":"Can you analyze this image?","image_count":1}, {"message":"x"*8000}]:
            response=await a.post("/api/cognitive/inspect",json=payload)
            status("COG-01 rules valid "+str(len(payload["message"])),response,200)
            record("COG-01 no online calls",response.json()["metrics"]["online_model_calls"]==0,response.json()["metrics"],"zero online calls")
        for payload in [{"message":""},{"message":"x"*8001},{"message":"x","image_count":9},{"message":"x","asset_count":5},{"message":"x","recent_messages":["x"]*25},{"message":"x","available_capabilities":["x"]*33}]:
            status("COG-03 boundary "+str(list(payload)),await a.post("/api/cognitive/inspect",json=payload),422)
        status("INT-01 integrations list",await a.get("/api/integrations"),200)
        status("INT-01 unconfigured authorize",await a.get("/api/integrations/google/authorize"),503)
        status("INT-01 unknown integration",await a.get("/api/integrations/unknown"),404)
        status("INT-03 callback without session",await anon.get("/api/integrations/google/callback?code=qa&state=bad"),303)
        status("INT-03 mismatched state",await a.get("/api/integrations/google/callback?code=qa&state=bad"),303)
        status("INT-05 disconnected delete",await a.delete("/api/integrations/google"),204)

        try:
            async with connect(WS,open_timeout=3):
                record("CHAT-06 anonymous socket",False,"accepted","denied")
        except Exception as exc:
            record("CHAT-06 anonymous socket", "403" in str(exc), type(exc).__name__, "HTTP 403 handshake rejection")
        async with connect(WS,additional_headers=cookie,origin="https://hostile.example") as ws:
            message=json.loads(await ws.recv())
            record("SEC-02 hostile-origin socket with cookie",False,{"accepted":True,"first_frame":message["type"]},"reject untrusted Origin even with a valid cookie")
        async with connect(WS,additional_headers=cookie) as ws:
            await ws.send(json.dumps({"type":"run","content":"QA persistence message"}))
            frames=await frames_until(ws)
            cid=next(x["conversation_id"] for x in frames if x["type"]=="meta")
            record("CHAT-01 real WS streaming",frames[-1]["type"]=="done" and any(x["type"]=="token" for x in frames),[x["type"] for x in frames],"meta/tokens/done")
            response=await a.get(f"/api/chat/conversations/{cid}")
            record("CHAT-02 persistence",len(response.json()["messages"])==2,{"roles":[x["role"] for x in response.json()["messages"]]},"one user and assistant message")
            for method in ["GET","DELETE"]:
                status("CHAT-06 foreign conversation "+method,await b.request(method,f"/api/chat/conversations/{cid}"),404)
            for value in ["bad-json",json.dumps({"type":"unknown"}),json.dumps({"type":"run","content":""}),json.dumps({"type":"run","content":"x"*8001}),json.dumps({"type":"run","content":"x","image_ids":[image_id]*9})]:
                await ws.send(value)
                frames=await frames_until(ws)
                record("CHAT-05 invalid WS payload",frames[-1]["type"]=="error",frames[-1],"controlled error")
            await ws.send(json.dumps({"type":"run","conversation_id":cid,"content":"QA interrupted turn"}))
            await frames_until(ws,terminal=("token",))
            await ws.send(json.dumps({"type":"stop"}))
            frames=await frames_until(ws)
            record("CHAT-03 real stop",frames[-1]["type"]=="stopped",[x["type"] for x in frames],"stopped terminal")
            await ws.send(json.dumps({"type":"voice_start"}))
            frames=await frames_until(ws)
            record("VOICE-06 disabled voice",frames[-1].get("detail")=="Voice is disabled.",frames[-1],"explicit disabled error")
            status("AUTH-06 signout",await a.post("/api/auth/signout"),204)
            response=await anon.get("/api/auth/me",headers=cookie)
            status("AUTH-06 replay revoked cookie",response,401)
            await ws.send(json.dumps({"type":"run","conversation_id":cid,"content":"QA after logout"}))
            frames=await frames_until(ws)
            record("AUTH-09 socket after logout",frames[-1]["type"]=="error",[x["type"] for x in frames],"reject turn after logout")

        # Expire the session in the dedicated Redis and prove an already-open socket stays authorized.
        a.cookies.set("qa_session",token)
        async with connect(WS,additional_headers=cookie) as ws:
            r=redis.Redis(host="127.0.0.1",port=56379)
            from app.core.security import hash_session_token
            key="session:"+hash_session_token(token)
            r.delete(key)
            status("AUTH-07 expired/revoked REST",await a.get("/api/auth/me"),401)
            await ws.send(json.dumps({"type":"run","conversation_id":cid,"content":"QA after explicit Redis revocation"}))
            frames=await frames_until(ws)
            record("AUTH-09 socket after actual Redis revocation",frames[-1]["type"]=="error",[x["type"] for x in frames],"revoked session cannot send new turns")
        # Re-login to test removal of files/data and logs before deleting the user.
        a.cookies.clear()  # remove the manually restored domainless cookie before replacing it
        status("AUTH-10 reauthenticate for deletion",await a.post("/api/auth/signin",json={"email":f"qa-a-{suffix}@example.com","password":"Synthetic-qa-password-2026"}),200)
        status("AUTH-10 delete account",await a.delete("/api/auth/me"),204)
        status("AUTH-10 deleted user no access",await a.get("/api/auth/me"),401)
        left_images=list((TEMP/"images"/aid).glob("*"))
        left_assets=list((TEMP/"assets"/aid).glob("*"))
        record("DATA-05 account file cleanup",not left_images and not left_assets,{"remaining_image_files":len(left_images),"remaining_asset_files":len(left_assets)},"deleted account's private files removed or explicit retention policy")
        status("AUTH-10 other user unaffected",await b.get("/api/auth/me"),200)
        log=(TEMP/"backend.log").read_text()
        record("AUTH-08 no raw session token logging", "signin attempted to log" not in log,{"token_print_observed_and_redacted": "signin attempted to log" in log},"no session token in logs")
        engine=create_engine(get_settings().database_url)
        with engine.connect() as db:
            counts={table:db.execute(text(f"SELECT count(*) FROM {table} WHERE user_id=:uid"),{"uid":aid}).scalar() for table in ["weight_logs","calorie_logs","conversations","long_term_memories","memory_jobs"]}
        record("AUTH-10 database cascade",all(v==0 for v in counts.values()),counts,"all deleted user rows removed")
    print(json.dumps({"total":len(results),"passed":sum(x["status"]=="PASS" for x in results),"failed":sum(x["status"]=="FAIL" for x in results)}))


if __name__ == "__main__":
    asyncio.run(main())
