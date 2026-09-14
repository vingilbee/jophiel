"""Real-stack P0 auth/security audit; synthetic users and disposable QA storage only."""
import asyncio
import io
import json
import uuid
from pathlib import Path

import httpx
import redis
from PIL import Image
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from qa_env import ARTIFACTS, TEMP, configure

configure()

BASE = "http://127.0.0.1:58001"
ORIGIN = "http://127.0.0.1:54173"
WS = "ws://127.0.0.1:58001/api/chat/ws"
results = []


def record(case, ok, actual, expected):
    row = {"case": case, "status": "PASS" if ok else "FAIL", "actual": actual, "expected": expected}
    results.append(row)
    print(json.dumps(row), flush=True)
    (ARTIFACTS / "live-auth-security-results.json").write_text(json.dumps(results, indent=2))


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), (80, 160, 90)).save(out, format="PNG")
    return out.getvalue()


async def terminal(ws, timeout=25):
    frames = []
    async with asyncio.timeout(timeout):
        while True:
            try:
                frame = json.loads(await ws.recv())
            except ConnectionClosed as exc:
                return [{"type": "connection_closed", "code": exc.code, "reason": exc.reason}]
            frames.append(frame)
            if frame.get("type") in {"done", "stopped", "error"}:
                return frames


async def main():
    suffix = uuid.uuid4().hex[:10]
    origin_headers = {"Origin": ORIGIN}
    async with httpx.AsyncClient(base_url=BASE, headers=origin_headers, timeout=30) as a, httpx.AsyncClient(base_url=BASE, headers=origin_headers, timeout=30) as b:
        a_payload = {"name": "P0 Auth A", "email": f"p0-a-{suffix}@example.com", "password": "Synthetic-p0-password-2026"}
        b_payload = {"name": "P0 Auth B", "email": f"p0-b-{suffix}@example.com", "password": "Synthetic-p0-password-2026"}
        ra = await a.post("/api/auth/signup", json=a_payload)
        rb = await b.post("/api/auth/signup", json=b_payload)
        record("AUTH-05 signup A/B", ra.status_code == 201 and rb.status_code == 201, {"A": ra.status_code, "B": rb.status_code}, "201 for both")
        aid = ra.json().get("id")
        old_a = a.cookies.get("qa_session")
        set_cookie = ra.headers.get("set-cookie", "").lower()
        record("AUTH-05 cookie attributes", all(x in set_cookie for x in ["httponly", "samesite=lax", "path=/"]), {"httponly": "httponly" in set_cookie, "samesite_lax": "samesite=lax" in set_cookie, "path": "path=/" in set_cookie, "secure": "secure" in set_cookie}, "HttpOnly, SameSite=Lax, Path=/; Secure depends on QA HTTP")
        record("AUTH-05 current user", (await a.get("/api/auth/me")).status_code == 200, "authenticated", "200")

        old_cookie = {"Cookie": f"qa_session={old_a}"}
        signout = await a.post("/api/auth/signout")
        replay = await a.get("/api/auth/me", headers=old_cookie)
        record("AUTH-06 logout revokes old cookie", signout.status_code == 204 and replay.status_code == 401, {"signout": signout.status_code, "replay": replay.status_code}, "204 then 401")
        try:
            async with connect(WS, additional_headers=old_cookie, origin=ORIGIN):
                record("AUTH-06 revoked cookie new WebSocket", False, "accepted", "handshake rejected")
        except Exception as exc:
            record("AUTH-06 revoked cookie new WebSocket", "403" in str(exc), type(exc).__name__, "HTTP 403 handshake rejection")

        # Re-authenticate A, then revoke the same session while an existing socket is open.
        await a.post("/api/auth/signin", json=a_payload)
        live_token = a.cookies.get("qa_session")
        live_cookie = {"Cookie": f"qa_session={live_token}"}
        async with connect(WS, additional_headers=live_cookie, origin=ORIGIN) as ws:
            await a.post("/api/auth/signout")
            await ws.send(json.dumps({"type": "run", "content": "P0 test after logout"}))
            frames = await terminal(ws)
            record("AUTH-09 existing WebSocket after logout", frames[-1].get("type") in {"error", "connection_closed"}, [x.get("type") for x in frames], "error or policy close after session revocation")

        # B session revocation through the actual Redis session store.
        b_token = b.cookies.get("qa_session")
        from app.core.security import hash_session_token
        redis.Redis(host="127.0.0.1", port=56379).delete("session:" + hash_session_token(b_token))
        expired = await b.get("/api/auth/me")
        record("AUTH-07 Redis session expiry", expired.status_code == 401, expired.status_code, "401")
        await b.post("/api/auth/signin", json=b_payload)

        hostile = await b.post("/api/images", headers={"Origin": "https://hostile.example"}, files={"file": ("p0.png", image_bytes(), "image/png")})
        record("SEC-02 hostile-origin mutation", hostile.status_code == 403, hostile.status_code, "403")
        preflight = await b.options("/api/images", headers={"Origin": "https://hostile.example", "Access-Control-Request-Method": "POST"})
        record("SEC-02 hostile CORS preflight", preflight.status_code == 400 and "access-control-allow-origin" not in preflight.headers, {"status": preflight.status_code, "allow_origin": preflight.headers.get("access-control-allow-origin")}, "400 without allow-origin")

        # Account deletion and private image cleanup.
        upload = await b.post("/api/images", files={"file": ("owned.png", image_bytes(), "image/png")})
        image_id = upload.json().get("image_id") if upload.status_code == 201 else None
        user_dir = TEMP / "images" / b_payload["email"]  # replaced below after user ID is known
        b_id = (await b.get("/api/auth/me")).json().get("id")
        user_dir = TEMP / "images" / str(b_id)
        before = len(list(user_dir.glob("*"))) if user_dir.exists() else 0
        deletion = await b.delete("/api/auth/me")
        after_me = await b.get("/api/auth/me")
        after = len(list(user_dir.glob("*"))) if user_dir.exists() else 0
        record("AUTH-10 account deletion", deletion.status_code == 202 and after_me.status_code == 401, {"delete": deletion.status_code, "me": after_me.status_code, "image_id_created": bool(image_id)}, "202 then 401")
        record("DATA-05 private image cleanup", before > 0 and after == 0, {"files_before": before, "files_after": after}, "owned files removed")

        # B's deletion must not affect A's user record (A is re-created after its logout).
        await a.post("/api/auth/signin", json=a_payload)
        a_alive = await a.get("/api/auth/me")
        record("AUTH-10 other user unaffected", a_alive.status_code == 200, a_alive.status_code, "200")
        await a.delete("/api/auth/me")

    log_text = (ARTIFACTS / "live-console.log").read_text() if (ARTIFACTS / "live-console.log").exists() else ""
    record("AUTH-08 QA log contains no raw session value", old_a not in log_text, "redacted QA logging" if old_a not in log_text else "token present", "raw token absent")
    print(json.dumps({"total": len(results), "passed": sum(x["status"] == "PASS" for x in results), "failed": sum(x["status"] == "FAIL" for x in results)}))


if __name__ == "__main__":
    asyncio.run(main())
