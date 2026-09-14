"""Async Google API transport. Credentials never leave Google's API hosts."""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

BASES = {
    "drive": "https://www.googleapis.com/drive/v3",
    "docs": "https://docs.googleapis.com/v1",
    "slides": "https://slides.googleapis.com/v1",
    "upload": "https://www.googleapis.com/upload/drive/v3",
}
FILE_FIELDS = "id,name,mimeType,parents,webViewLink,trashed,size,modifiedTime,description"


def error_hint(status: int, payload: Any, service: str) -> str:
    """Translate structured Google reasons without reflecting remote text/secrets."""
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    reasons = set()
    for key in ("errors", "details"):
        entries = error.get(key, [])
        if isinstance(entries, list):
            reasons.update(item["reason"] for item in entries
                           if isinstance(item, dict) and isinstance(item.get("reason"), str))
    label, api_name = {
        "docs": ("Google Docs API", "docs.googleapis.com"),
        "slides": ("Google Slides API", "slides.googleapis.com"),
    }.get(service, ("Google Drive API", "drive.googleapis.com"))
    if reasons & {"SERVICE_DISABLED", "accessNotConfigured", "serviceDisabled"}:
        return (f"{label} is disabled in the Google Cloud project used by this OAuth client. "
                f"Enable it under APIs & Services → Library ({api_name}), then retry. "
                "Adding OAuth scopes does not enable an API. [SERVICE_DISABLED]")
    if reasons & {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}:
        return ("The connected token lacks the required scope. Reconnect Google in Integrations "
                "and grant Drive access; adding scopes in Cloud Console alone does not update an existing token. "
                "[ACCESS_TOKEN_SCOPE_INSUFFICIENT]")
    if "insufficientFilePermissions" in reasons:
        return "The connected Google account cannot edit this file. Grant it editor access or choose a file it owns. [insufficientFilePermissions]"
    if reasons & {"rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED", "dailyLimitExceeded"}:
        return "Google API quota or rate limit reached. Retry later or check this project's API quotas. [RATE_LIMIT_EXCEEDED]"
    if "storageQuotaExceeded" in reasons:
        return "This Google account's Drive storage is full. Free space or increase its storage quota. [storageQuotaExceeded]"
    if "domainPolicy" in reasons:
        return "The Google Workspace administrator's policy blocks this operation. [domainPolicy]"
    return {
        401: "Google session expired; retry in a new chat turn to refresh it, or reconnect Google.",
        403: f"{label} denied this operation. Check file access, granted OAuth scopes, and whether this API is enabled in the OAuth client's Cloud project.",
        404: "File not found or this Google account cannot access it.",
        429: "Google rate limit reached; try again later.",
    }.get(status, "Check the request parameters before retrying.")


def file_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Use the file ID returned by Google, not a URL or path.")
    return value


async def request(
    service: str, method: str, path: str, *, params: dict | None = None,
    body: dict | None = None, content: bytes | None = None,
    content_type: str | None = None, binary: bool = False,
) -> Any:
    token = os.environ.get("DRIVE_ACCESS_TOKEN")
    if not token:
        raise ValueError("Connect Google in Integrations first.")
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        async with client.stream(
            method, BASES[service] + path, params=params, headers=headers,
            json=body, content=content,
        ) as response:
            if response.is_error:
                error_bytes = bytearray()
                async for chunk in response.aiter_bytes():
                    error_bytes.extend(chunk[:65536 - len(error_bytes)])
                    if len(error_bytes) >= 65536:
                        break
                import json
                try:
                    payload = json.loads(error_bytes)
                except ValueError:
                    payload = {}
                raise ValueError(f"Google API error {response.status_code}. " + error_hint(response.status_code, payload, service))
            chunks = bytearray()
            # Google's Workspace exports are capped at 10 MB; also bound downloads.
            from app.core.config import get_settings
            maximum = get_settings().asset_max_upload_mb * 1024 * 1024
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > maximum:
                    raise ValueError("Google file exceeds the configured asset size limit.")
            if binary:
                return bytes(chunks)
            if not chunks:
                return {"ok": True}
            import json
            return json.loads(chunks)
