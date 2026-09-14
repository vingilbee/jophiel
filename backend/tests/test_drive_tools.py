import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.integrations.google import oauth, scopes
from app.ai.tools.drive import api, server
from app.api.deps import get_current_user
from app.api.routes.assets import router
from app.services import asset_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    owner = uuid.uuid4()
    monkeypatch.setenv("DRIVE_USER_ID", str(owner))
    monkeypatch.setenv("DRIVE_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(asset_store, "get_settings", lambda: SimpleNamespace(
        asset_store_dir=str(tmp_path), asset_max_upload_mb=1))
    return owner


def test_oauth_requests_drive_and_hints_account(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    monkeypatch.setattr(oauth, "get_settings", lambda: SimpleNamespace(
        google_oauth_client_id="client", google_oauth_client_secret="secret",
        google_oauth_redirect_uri="http://localhost/callback",
        google_account_username="agent@example.com"))
    params = parse_qs(urlparse(oauth.build_authorization_url(state="state")).query)
    assert scopes.DRIVE in params["scope"][0].split()
    assert params["login_hint"] == ["agent@example.com"]
    assert params["access_type"] == ["offline"]
    assert scopes.has_required_scopes({scopes.DRIVE})
    assert scopes.has_required_scopes({scopes.GMAIL_MODIFY})
    assert not scopes.has_required_scopes({scopes.OPENID})


def test_export_pdf_download_preview_and_user_isolation(store, monkeypatch):
    pdf = b"%PDF-1.4\n%%EOF\n"
    async def fake_request(service, method, path, **kwargs):
        if path.endswith("/export"):
            assert kwargs["params"] == {"mimeType": "application/pdf"}
            return pdf
        return {"name": 'Nutrition résumé', "mimeType": "application/vnd.google-apps.document"}
    monkeypatch.setattr(server, "request", fake_request)
    result = asyncio.run(server.drive_export_file("document-id"))
    assert result["media_type"] == "application/pdf"
    assert result["download_url"] in result["download_link"]

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=store)
    with TestClient(app) as client:
        url = result["download_url"]
        assert client.get(url).content == pdf
        assert client.get(url).headers["content-disposition"].startswith("attachment;")
        response = client.get(url + "?preview=true")
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].startswith("inline;")
        assert client.get(url, headers={"Range": "bytes=0-4"}).status_code == 206
        assert client.get(url.removesuffix("/raw")).json()["filename"] == "Nutrition résumé.pdf"
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
        assert client.get(url).status_code == 404
        assert client.get(url.removesuffix("/raw")).status_code == 404


def test_move_reads_and_removes_old_parents(monkeypatch):
    calls = []
    async def fake_request(service, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"parents": ["old-folder"]} if method == "GET" else {"id": "file"}
    monkeypatch.setattr(server, "request", fake_request)
    asyncio.run(server.drive_move_file("file", "new-folder"))
    assert calls[1][2]["params"]["removeParents"] == "old-folder"
    assert calls[1][2]["params"]["addParents"] == "new-folder"


def test_deletion_and_identifier_validation(monkeypatch):
    with pytest.raises(ValueError, match="explicitly requested"):
        asyncio.run(server.drive_delete_permanently("file"))
    for value in ["../files", "https://evil.example", "id?alt=media", "id/permissions"]:
        with pytest.raises(ValueError):
            api.file_id(value)


def test_upload_cannot_read_other_users_asset(store, monkeypatch):
    record = asset_store.save_bytes(uuid.uuid4(), b"private", filename="secret.txt")
    with pytest.raises(asset_store.AssetNotFoundError):
        asyncio.run(server.drive_upload_asset(record.asset_id))


def test_partial_doc_creation_returns_existing_id(monkeypatch):
    async def fake_request(service, method, path, **kwargs):
        if path.endswith(":batchUpdate"):
            raise ValueError("Google API error 400")
        return {"documentId": "created-doc"}
    monkeypatch.setattr(server, "request", fake_request)
    result = asyncio.run(server.docs_create("Report", "Content"))
    assert result["id"] == "created-doc"
    assert result["content_inserted"] is False


def test_api_auth_and_binary_response(store, monkeypatch):
    original_client = httpx.AsyncClient
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.host == "www.googleapis.com"
        return httpx.Response(200, content=b"%PDF-test")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    assert asyncio.run(api.request("drive", "GET", "/files/file/export", binary=True)) == b"%PDF-test"


def test_api_errors_do_not_leak_response_or_token(store, monkeypatch):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(
            403, json={"error": "sensitive upstream detail"}))))
    with pytest.raises(ValueError) as error:
        asyncio.run(api.request("drive", "GET", "/files"))
    assert "403" in str(error.value)
    assert "sensitive" not in str(error.value)
    assert "test-token" not in str(error.value)


def test_mcp_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"docs_create", "slides_create", "drive_export_file", "drive_move_file"} <= names
    # Check schemas can be serialized for the parent MCP client.
    json.dumps([tool.model_dump() for tool in tools])


def test_drive_and_gmail_tools_are_independently_gated(monkeypatch):
    from app.ai import chat
    from app.ai.context import AgentRuntimeContext
    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace(
        drive_tools_enabled=True, gmail_tools_enabled=True))
    context = AgentRuntimeContext(user_id=uuid.uuid4(), drive_access_token="drive-token")
    assert set(chat._resolve_connections(context)) == {"drive"}
    assert "drive_connected" in context.capabilities()
    assert "gmail_connected" not in context.capabilities()
    context.drive_access_token = None
    context.gmail_access_token = "gmail-token"
    assert set(chat._resolve_connections(context)) == {"gmail"}


def test_pdf_validation_and_filename_sanitization(store):
    with pytest.raises(asset_store.InvalidAssetError, match="valid PDF"):
        asset_store.save_bytes(store, b"<html>bad</html>", filename="bad.pdf")
    record = asset_store.save_bytes(store, b"%PDF-1.4", filename="../résumé\r\n.pdf")
    assert record.filename == "résumé.pdf"


@pytest.mark.parametrize("reason,service,expected", [
    ("SERVICE_DISABLED", "slides", "Google Slides API is disabled"),
    ("accessNotConfigured", "docs", "Google Docs API is disabled"),
    ("ACCESS_TOKEN_SCOPE_INSUFFICIENT", "drive", "Reconnect Google"),
    ("insufficientFilePermissions", "drive", "editor access"),
    ("storageQuotaExceeded", "drive", "storage is full"),
    ("userRateLimitExceeded", "drive", "quota or rate limit"),
])
def test_google_errors_explain_the_specific_fix(reason, service, expected):
    payload = {"error": {"message": "private upstream text", "details": [{"reason": reason}]}}
    hint = api.error_hint(403, payload, service)
    assert expected in hint
    assert "private upstream" not in hint


def test_unknown_error_reasons_are_not_reflected():
    hint = api.error_hint(403, {"error": {"errors": [{"reason": "secret-value"}]}}, "slides")
    assert "secret-value" not in hint
