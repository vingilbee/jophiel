import asyncio
import base64
import io
import uuid
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.ai.tools.drive import image_tools, server
from app.api.routes.assets import router
from app.core import crypto
from app.services import asset_store, slide_images


def png(width=80, height=60):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        asset_store_dir=str(tmp_path), asset_max_upload_mb=20,
        google_token_encryption_key="test-signing-key", openai_api_key="test-api-key",
        slide_image_public_base_url="https://images.example.com",
        slide_image_model="gpt-image-2",
    )
    for module in (asset_store, slide_images, image_tools, crypto):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    return uuid.uuid4(), settings


def test_signed_link_serves_only_the_image_and_expires(configured, monkeypatch):
    user, _ = configured
    record = slide_images.save_image(user, png())
    monkeypatch.setattr(slide_images.time, "time", lambda: 1000)
    url = slide_images.temporary_url(user, record["asset_id"])
    token = unquote(urlsplit(url).path.rsplit("/", 1)[1])
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        # This endpoint works without login, but only with this valid token.
        result = client.get(urlsplit(url).path)
        assert result.status_code == 200
        assert result.headers["content-type"] == "image/png"
        assert result.headers["cache-control"] == "private, no-store"
        assert result.content.startswith(b"\x89PNG")
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        assert client.get("/api/assets/slide-image/" + tampered).status_code == 404
        monkeypatch.setattr(slide_images.time, "time", lambda: 1900)
        assert client.get(urlsplit(url).path).status_code == 404


def test_cannot_sign_other_users_file_or_nonimage(configured):
    user, _ = configured
    record = slide_images.save_image(user, png())
    with pytest.raises(asset_store.AssetNotFoundError):
        slide_images.temporary_url(uuid.uuid4(), record["asset_id"])
    text = asset_store.save_bytes(user, b"secret", filename="secret.txt")
    with pytest.raises(ValueError, match="normalized PNG"):
        slide_images.temporary_url(user, text.asset_id)


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost", "https://127.0.0.1", "https://10.0.0.2", "https://user:pass@example.com", "https://[::1]", "file:///tmp/image.png"])
def test_private_and_credential_urls_rejected(url):
    with pytest.raises(ValueError):
        slide_images.public_url(url)


def test_images_are_normalized_and_bounded(configured):
    user, _ = configured
    record = slide_images.save_image(user, png(3000, 1000))
    assert record["width"] == 2048
    assert record["height"] < 1000
    with pytest.raises(ValueError, match="decode"):
        slide_images.save_image(user, b"<html>not an image</html>")


def test_search_returns_urls_and_plain_text_credits(monkeypatch):
    original_client = httpx.AsyncClient
    def handle(request):
        assert request.url.host == "commons.wikimedia.org"
        assert request.url.params["gsrnamespace"] == "6"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"query": {"pages": [{
            "title": "File:Healthy plate.jpg", "index": 1,
            "imageinfo": [{"mime": "image/jpeg", "thumburl": "https://upload.wikimedia.org/example.jpg",
                           "descriptionurl": "https://commons.wikimedia.org/wiki/File:Plate.jpg",
                           "width": 1000, "height": 800, "extmetadata": {
                               "Artist": {"value": "<a href='x'>Artist &amp; Team</a>"},
                               "LicenseShortName": {"value": "CC BY 4.0"},
                           }}],
        }]}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handle)))
    result = asyncio.run(image_tools.search_images("healthy plate"))["results"][0]
    assert result["author"] == "Artist & Team"
    assert "CC BY 4.0" in result["attribution"]
    assert result["image_url"].startswith("https://upload.wikimedia.org/")


def test_generate_saves_private_asset(configured, monkeypatch):
    user, _ = configured
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-api-key"
            self.images = self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def generate(self, **kwargs):
            assert kwargs["model"] == "gpt-image-2"
            assert kwargs["n"] == 1
            assert kwargs["output_format"] == "png"
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png()).decode())])
    monkeypatch.setattr(image_tools, "AsyncOpenAI", FakeClient)
    result = asyncio.run(image_tools.generate_image(user, "A blue sky", "1536x1024", "medium"))
    assert result["generated"] is True
    assert asset_store.exists(user, result["asset_id"])
    assert not asset_store.exists(uuid.uuid4(), result["asset_id"])
    assert "test-api-key" not in str(result)


def presentation():
    return {"slides": [{"objectId": "slide_1"}], "pageSize": {
        "width": {"magnitude": 9144000, "unit": "EMU"},
        "height": {"magnitude": 5143500, "unit": "EMU"},
    }}


def test_insert_search_image_adds_credit_and_alt_text(configured, monkeypatch):
    user, _ = configured
    calls = []
    async def fake_request(service, method, path, **kwargs):
        calls.append((method, kwargs))
        return presentation() if method == "GET" else {"replies": []}
    monkeypatch.setattr(image_tools, "request", fake_request)
    result = asyncio.run(image_tools.insert_image(
        user, "presentation", "slide_1", image_url="https://upload.wikimedia.org/image.jpg",
        attribution="Photographer, CC BY 4.0", source_url="https://commons.wikimedia.org/wiki/File:Image.jpg", alt_text="A plate of vegetables"))
    updates = calls[-1][1]["body"]["requests"]
    assert updates[0]["createImage"]["url"] == "https://upload.wikimedia.org/image.jpg"
    assert updates[1]["updatePageElementAltText"]["title"] == "A plate of vegetables"
    assert updates[3]["insertText"]["text"] == "Photographer, CC BY 4.0"
    assert result["inserted"] is True


def test_insert_local_image_uses_temporary_link(configured, monkeypatch):
    user, _ = configured
    record = slide_images.save_image(user, png())
    async def fake_request(service, method, path, **kwargs):
        if method == "GET":
            return presentation()
        url = kwargs["body"]["requests"][0]["createImage"]["url"]
        assert url.startswith("https://images.example.com/api/assets/slide-image/")
        token = unquote(url.rsplit("/", 1)[1])
        assert slide_images.resolve_link(token).exists()
        return {}
    monkeypatch.setattr(image_tools, "request", fake_request)
    result = asyncio.run(image_tools.insert_image(user, "presentation", "slide_1", asset_id=record["asset_id"]))
    assert result["inserted"]
    assert "slide-image/" not in str(result)


def test_insert_fails_before_mutation_for_bad_geometry_or_missing_host(configured, monkeypatch):
    user, settings = configured
    async def fake_request(service, method, path, **kwargs):
        assert method == "GET", "Must not mutate Google when placement is invalid"
        return presentation()
    monkeypatch.setattr(image_tools, "request", fake_request)
    with pytest.raises(ValueError, match="outside"):
        asyncio.run(image_tools.insert_image(user, "presentation", "slide_1", image_url="https://example.com/p.png", width=900))
    with pytest.raises(ValueError, match="Slide ID"):
        asyncio.run(image_tools.insert_image(user, "presentation", "missing", image_url="https://example.com/p.png"))
    settings.slide_image_public_base_url = None
    with pytest.raises(ValueError, match="SLIDE_IMAGE_PUBLIC_BASE_URL"):
        asyncio.run(image_tools.insert_image(user, "presentation", "slide_1", asset_id="0123456789abcdef"))


def test_all_three_tools_exposed():
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert {"image_search", "image_generate", "slides_insert_image"} <= names
