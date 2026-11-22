"""Firebase Cloud Messaging, HTTP v1, sent straight from the backend (no Expo push service).

Data-only, high-priority messages: the phone builds the notification itself (wording, stacking, clearing). The
OAuth access token comes from the Firebase service account (``fcm_service_account_file``): a JWT signed with
its key is exchanged at Google's token endpoint, and the token is cached until shortly before it expires.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class NotConfigured(Exception):
    """No Firebase service account is configured."""


@dataclass(frozen=True)
class Result:
    """``sent``; ``unregistered`` (the device token is dead: forget the device); ``retry`` (try later);
    ``failed`` (this message will never go)."""

    outcome: str
    detail: str | None = None
    retry_after: float | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class _Credentials:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0.0
        self._account: dict | None = None
        self._path: str | None = None

    def account(self) -> dict:
        path = get_settings().fcm_service_account_file
        if not path:
            raise NotConfigured("fcm_service_account_file is not set")
        if self._account is None or self._path != path:
            self._account = json.loads(Path(path).expanduser().read_text())
            self._path = path
            self._token = None
        return self._account

    def project_id(self) -> str:
        return get_settings().fcm_project_id or self.account()["project_id"]

    def access_token(self, client: httpx.Client) -> str:
        with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            account = self.account()
            now = int(time.time())
            header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
            claims = _b64(json.dumps({"iss": account["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                                      "iat": now, "exp": now + 3600}).encode())
            key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
            signature = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            assertion = f"{header}.{claims}.{_b64(signature)}"
            response = client.post(TOKEN_URL, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.time() + float(body.get("expires_in", 3600))
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None


_credentials = _Credentials()


def configured() -> bool:
    return bool(get_settings().fcm_service_account_file)


def send(device_token: str, data: dict[str, str], *, client: httpx.Client | None = None,
         ttl_seconds: int | None = None) -> Result:
    """Send one data-only message to one device. Never raises for provider errors: the result says what to
    do next."""
    own = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        try:
            access = _credentials.access_token(client)
            project = _credentials.project_id()
        except NotConfigured as exc:
            return Result("failed", str(exc))
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return Result("retry", f"FCM credentials: {exc}", 60.0)
        message = {"message": {"token": device_token, "data": {k: str(v) for k, v in data.items()},
                               "android": {"priority": "HIGH"}}}
        if ttl_seconds is not None:
            message["message"]["android"]["ttl"] = f"{ttl_seconds}s"
        try:
            response = client.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                                   headers={"Authorization": f"Bearer {access}"}, json=message)
        except httpx.HTTPError as exc:
            return Result("retry", f"FCM unreachable: {exc}")
        return _result(response)
    finally:
        if own:
            client.close()


def _result(response: httpx.Response) -> Result:
    if response.status_code == 200:
        return Result("sent")
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    codes = {detail.get("errorCode") for detail in error.get("details", []) if isinstance(detail, dict)}
    detail = f"{response.status_code} {error.get('status') or ''} {error.get('message') or ''}".strip()
    # Only FCM's own word that the registration token is dead forgets a device. A bare 404 is also what a
    # wrong project id gives, and that must never delete every phone.
    if "UNREGISTERED" in codes or _names_bad_token(error):
        return Result("unregistered", detail)
    if response.status_code == 404:
        logger.error("FCM 404 without UNREGISTERED: check the project id (%s)", detail)
        return Result("failed", f"FCM 404, check the project ID: {detail}")
    if response.status_code == 401:
        _credentials.invalidate()
        return Result("retry", detail, 5.0)
    if response.status_code in (429, 500, 502, 503, 504) or "UNAVAILABLE" in codes or "INTERNAL" in codes:
        retry_after = response.headers.get("retry-after")
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None
        return Result("retry", detail, wait)
    return Result("failed", detail)


def _names_bad_token(error: dict) -> bool:
    """INVALID_ARGUMENT that FCM attributes to the registration token itself (a field violation on
    ``message.token``), not to the payload."""
    for detail in error.get("details", []):
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("fieldViolations", []) or []:
            if isinstance(violation, dict) and violation.get("field") == "message.token":
                return True
    return False
