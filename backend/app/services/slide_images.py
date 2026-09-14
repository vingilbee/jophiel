"""Validate slide image bytes and issue narrowly scoped temporary fetch links."""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import time
import uuid
import warnings
from urllib.parse import quote, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.crypto import EncryptionKeyMissing, sign_value, verify_value
from app.services import asset_store

LINK_TTL = 900
MAX_BYTES = 20 * 1024 * 1024


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Use a public HTTPS image URL.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise ValueError("Google cannot fetch a local image URL.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Google cannot fetch a private image URL.")
    if len(value) > 2048:
        raise ValueError("Google image URLs must be at most 2048 characters.")
    return value


def public_base() -> str:
    base = get_settings().slide_image_public_base_url
    if not base:
        raise ValueError("Set SLIDE_IMAGE_PUBLIC_BASE_URL to this backend's public HTTPS origin to insert generated or uploaded images. Google cannot access localhost. Image search results can be inserted directly with image_url.")
    public_url(base)
    parsed = urlsplit(base)
    if parsed.query or parsed.path not in {"", "/"}:
        raise ValueError("SLIDE_IMAGE_PUBLIC_BASE_URL must be an HTTPS origin without a path or query.")
    return base.rstrip("/")


def save_image(user_id: uuid.UUID, data: bytes, filename: str = "slide-image") -> dict:
    """Decode and re-encode PNG to strip metadata and enforce Google's limits."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Image must be nonempty and at most 20 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > 25_000_000:
                    raise ValueError("Slide images must not exceed 25 megapixels.")
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.thumbnail((2048, 2048))
                output = io.BytesIO()
                image.save(output, format="PNG")
                width, height = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("Could not decode a supported slide image.") from exc
    name = filename.removesuffix(".png") + ".png"
    record = asset_store.save_bytes(user_id, output.getvalue(), filename=name, kind="generated")
    url = f"/api/assets/{record.asset_id}/raw"
    return {**record.to_dict(), "width": width, "height": height,
            "download_link": f"[Slide image]({url})"}


def temporary_url(user_id: uuid.UUID, asset_id: str) -> str:
    base = public_base()
    record = asset_store.get_record(user_id, asset_id)
    if record.media_type != "image/png":
        raise ValueError("Only normalized PNG images can be shared with Slides.")
    payload = base64.urlsafe_b64encode(json.dumps({
        "purpose": "slide-image", "user": str(user_id), "asset": asset_id,
        "expires": int(time.time()) + LINK_TTL,
    }).encode()).decode()
    return f"{base}/api/assets/slide-image/{quote(sign_value(payload), safe='')}"


def resolve_link(token: str):
    """No session required: the signed token authorizes this one image only."""
    try:
        payload = verify_value(token)
        if payload is None:
            raise ValueError("Invalid signature")
        data = json.loads(base64.urlsafe_b64decode(payload))
        now = int(time.time())
        if data["purpose"] != "slide-image" or not now < data["expires"] <= now + LINK_TTL:
            raise ValueError("Expired link")
        user = uuid.UUID(data["user"])
        record = asset_store.get_record(user, data["asset"])
        if record.media_type != "image/png":
            raise ValueError("Not a slide image")
        return asset_store.get_path(user, data["asset"])
    except (ValueError, TypeError, KeyError, EncryptionKeyMissing, asset_store.AssetNotFoundError) as exc:
        raise asset_store.AssetNotFoundError("Image link is invalid or expired.") from exc
