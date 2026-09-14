"""Per-user store for food photos, and for images derived from them.

This is what makes the MacroVision tools usable from chat. An agent can't be
handed four megabytes of JPEG in a prompt, so an upload is written to disk once
and everything downstream — the predict endpoint, the MCP tools, the frontend —
refers to it by a short opaque ``image_id``.

Layout under ``settings.image_store_dir``::

    <root>/<user_id>/<image_id>.<ext>     the image itself
    <root>/<user_id>/<image_id>.json      sidecar metadata

The user id is a *directory*, not a column to be filtered on. A lookup for one
user therefore cannot reach another user's file — there is no query here that
could be written wrong, and the MCP server (which runs without a DB session)
gets that isolation for free. Ids are random rather than content hashes so a
caller can't probe whether some exact photo already exists.

Local disk is deliberate for now (CLAUDE.md → Decisions → Image storage).
Moving to S3/R2 later means reimplementing this module, not its callers.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import uuid
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.services import storage_cleanup

# Relative `image_store_dir` values resolve against the backend package root
# (the directory holding `app/`), so the store lands in the same place whether
# uvicorn was started from the repo root or from `backend/`.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Ids are hex so they're safe in a path and in a URL with no escaping. Anything
# not matching is rejected before it is ever joined onto a path — this is the
# path-traversal guard, so keep it an exact-match anchored pattern.
_IMAGE_ID_RE = re.compile(r"\A[0-9a-f]{16}\Z")

# Formats we accept. Anything Pillow can open but isn't here (e.g. multi-frame
# TIFF) is rejected rather than stored in a form the frontend can't render.
_ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class InvalidImageError(ValueError):
    """The bytes aren't a usable image (missing, oversized, or undecodable).

    Callers map this to HTTP 400 — it means the *upload* was bad, as distinct
    from a model failure, which is a 5xx.
    """


class ImageNotFoundError(LookupError):
    """No such image for this user (wrong id, or it belongs to someone else)."""


@dataclass(frozen=True, slots=True)
class ImageRecord:
    """Metadata about a stored image. Mirrors the sidecar JSON on disk."""

    image_id: str
    media_type: str
    width: int
    height: int
    size_bytes: int
    created_at: str
    # "upload" for something the user sent us, "depth" for a rendered depth map,
    # "screen" for a frame of the user's shared screen (ephemeral — see `prune`).
    kind: str = "upload"
    # Original client filename, when we were given one.
    filename: str | None = None
    # For derived images (depth maps), the image this was computed from.
    derived_from: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _root() -> Path:
    configured = Path(get_settings().image_store_dir)
    return configured if configured.is_absolute() else _BACKEND_ROOT / configured


def _user_dir(user_id: uuid.UUID | str) -> Path:
    # str(uuid.UUID) is always a safe path segment; coercing through UUID first
    # means a caller can't smuggle "../" in as a string user id.
    return _root() / str(uuid.UUID(str(user_id)))


def delete_user_files(user_id: uuid.UUID | str) -> None:
    storage_cleanup.delete_user_files(_root(), user_id)


def _sidecar(user_dir: Path, image_id: str) -> Path:
    return user_dir / f"{image_id}.json"


def _validate_id(image_id: str) -> str:
    if not _IMAGE_ID_RE.match(image_id):
        raise ImageNotFoundError(f"Not a valid image id: {image_id!r}")
    return image_id


def decode_image(data: bytes, content_type: str | None = None) -> Image.Image:
    """Validate raw bytes and decode them to an RGB image, or raise.

    Shared by the upload endpoint and the predict endpoint so both enforce the
    same size cap and produce the same error for the same bad input.
    """
    settings = get_settings()

    if not data:
        raise InvalidImageError("Empty upload.")
    if content_type is not None and not content_type.startswith("image/"):
        raise InvalidImageError(
            f"Expected an image, got content-type {content_type!r}."
        )

    max_bytes = settings.macrovision_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise InvalidImageError(
            f"Image is too large (max {settings.macrovision_max_upload_mb} MB)."
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            width, height = img.size
            max_pixels = settings.macrovision_max_image_pixels
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise InvalidImageError(
                    f"Image dimensions are too large (max {max_pixels:,} pixels)."
                )
            img.load()
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidImageError("Could not decode the uploaded image safely.") from exc

    if img.format not in _ALLOWED_FORMATS:
        raise InvalidImageError(
            f"Unsupported image format {img.format!r}. "
            f"Use one of: {', '.join(sorted(_ALLOWED_FORMATS))}."
        )
    output = ImageOps.exif_transpose(img).convert("RGB")
    output.format = img.format
    return output


def save_bytes(
    user_id: uuid.UUID | str,
    data: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    kind: str = "upload",
) -> ImageRecord:
    """Validate and store an uploaded image. Raises :class:`InvalidImageError`."""
    img = decode_image(data, content_type)
    extension = _ALLOWED_FORMATS[img.format]
    # Store the orientation-normalized pixels so later display and model reads
    # agree even when the client ignores EXIF metadata.
    normalized = io.BytesIO()
    img.save(normalized, format=img.format)
    return _write(
        user_id,
        normalized.getvalue(),
        extension=extension,
        width=img.width,
        height=img.height,
        kind=kind,
        filename=filename,
    )


def save_pil(
    user_id: uuid.UUID | str,
    image: Image.Image,
    *,
    kind: str = "upload",
    derived_from: str | None = None,
) -> ImageRecord:
    """Store an image we generated ourselves (e.g. a rendered depth map).

    Always written as PNG: depth renders are false-colour, and JPEG's chroma
    subsampling would smear exactly the gradients that make them readable.
    """
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    data = buffer.getvalue()
    return _write(
        user_id,
        data,
        extension=".png",
        width=image.width,
        height=image.height,
        kind=kind,
        derived_from=derived_from,
    )


def _write(
    user_id: uuid.UUID | str,
    data: bytes,
    *,
    extension: str,
    width: int,
    height: int,
    kind: str,
    filename: str | None = None,
    derived_from: str | None = None,
) -> ImageRecord:
    user_dir = _user_dir(user_id)

    image_id = secrets.token_hex(8)
    record = ImageRecord(
        image_id=image_id,
        media_type=_MEDIA_TYPES[extension],
        width=width,
        height=height,
        size_bytes=len(data),
        created_at=datetime.now(timezone.utc).isoformat(),
        kind=kind,
        filename=filename,
        derived_from=derived_from,
    )

    with storage_cleanup.allow_user_write(_root(), user_id):
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / f"{image_id}{extension}").write_bytes(data)
        # Publish metadata only after the bytes have been written.
        _sidecar(user_dir, image_id).write_text(json.dumps(record.to_dict(), indent=2))
    return record


def get_record(user_id: uuid.UUID | str, image_id: str) -> ImageRecord:
    """Metadata for one of this user's images. Raises :class:`ImageNotFoundError`."""
    _validate_id(image_id)
    path = _sidecar(_user_dir(user_id), image_id)
    if not path.is_file():
        raise ImageNotFoundError(f"No image {image_id!r} for this user.")
    return ImageRecord(**json.loads(path.read_text()))


def get_path(user_id: uuid.UUID | str, image_id: str) -> Path:
    """Filesystem path to one of this user's images.

    Raises :class:`ImageNotFoundError` for an unknown id, a malformed one, or
    one belonging to a different user — all three are the same answer, so a
    caller learns nothing about images that aren't theirs.
    """
    record = get_record(user_id, image_id)
    extension = next(
        ext for ext, media in _MEDIA_TYPES.items() if media == record.media_type
    )
    path = _user_dir(user_id) / f"{image_id}{extension}"
    if not path.is_file():
        raise ImageNotFoundError(f"No image {image_id!r} for this user.")
    return path


def exists(user_id: uuid.UUID | str, image_id: str) -> bool:
    """Whether this user has an image with this id. Never raises."""
    try:
        get_record(user_id, image_id)
    except (ImageNotFoundError, ValueError, OSError):
        return False
    return True


def read_bytes(user_id: uuid.UUID | str, image_id: str) -> tuple[bytes, str]:
    """Return ``(data, media_type)`` for one of this user's images."""
    record = get_record(user_id, image_id)
    return get_path(user_id, image_id).read_bytes(), record.media_type


def list_images(
    user_id: uuid.UUID | str, *, limit: int = 10, kind: str | None = "upload"
) -> list[ImageRecord]:
    """This user's images, newest first.

    Defaults to ``kind="upload"`` so "my recent photos" doesn't come back full
    of depth maps the agent generated itself. Pass ``kind=None`` for everything.
    """
    user_dir = _user_dir(user_id)
    if not user_dir.is_dir():
        return []

    records: list[ImageRecord] = []
    for sidecar in user_dir.glob("*.json"):
        try:
            records.append(ImageRecord(**json.loads(sidecar.read_text())))
        except (OSError, ValueError, TypeError):
            # A half-written or hand-edited sidecar shouldn't break the listing.
            continue

    if kind is not None:
        records = [r for r in records if r.kind == kind]
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records[:limit]


def prune(
    user_id: uuid.UUID | str,
    *,
    kind: str,
    keep: int,
    max_age_seconds: float,
    protect: str | None = None,
) -> int:
    """Delete this user's images of ``kind`` beyond a count and an age limit.

    Both bounds apply: the newest ``keep`` survive, and anything older than
    ``max_age_seconds`` goes even if that leaves nothing. That combination is
    what makes screen frames a *sense* rather than a recording — a user who
    shares their screen for an hour never accumulates an hour of scrollback,
    and one who disconnects stops leaving frames on disk at all.

    ``protect`` is the frame currently in play; it is never deleted, so a live
    session can't have the image out from under it mid-turn.

    Returns how many were removed. Never raises — pruning is housekeeping, and
    failing it must not fail the upload that triggered it.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
    removed = 0
    try:
        records = list_images(user_id, limit=10_000, kind=kind)
    except OSError:
        return 0

    for index, record in enumerate(records):  # newest first
        if record.image_id == protect:
            continue
        try:
            stale = datetime.fromisoformat(record.created_at).timestamp() < cutoff
        except ValueError:
            stale = True  # unparseable timestamp: treat as expired, not immortal
        if index < keep and not stale:
            continue
        try:
            if delete_image(user_id, record.image_id):
                removed += 1
        except OSError:
            continue
    return removed


def delete_image(user_id: uuid.UUID | str, image_id: str) -> bool:
    """Remove an image and its sidecar. True if something was deleted."""
    try:
        path = get_path(user_id, image_id)
    except ImageNotFoundError:
        return False
    path.unlink(missing_ok=True)
    _sidecar(_user_dir(user_id), image_id).unlink(missing_ok=True)
    return True
