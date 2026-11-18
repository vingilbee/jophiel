"""User-scoped storage for chat attachments and generated documents."""
from __future__ import annotations

import json
import re
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.services import storage_cleanup

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ASSET_ID_RE = re.compile(r"\A[0-9a-f]{16}\Z")
_ALLOWED = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".blend": "application/x-blender",
    ".fbx": "application/octet-stream",
    ".obj": "text/plain",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}


class InvalidAssetError(ValueError):
    pass


class AssetNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class AssetRecord:
    asset_id: str
    filename: str
    media_type: str
    size_bytes: int
    created_at: str
    kind: str = "source"
    derived_from: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _root() -> Path:
    configured = Path(get_settings().asset_store_dir)
    return configured if configured.is_absolute() else _BACKEND_ROOT / configured


def _user_dir(user_id: uuid.UUID | str) -> Path:
    return _root() / str(uuid.UUID(str(user_id)))


def delete_user_files(user_id: uuid.UUID | str) -> None:
    storage_cleanup.delete_user_files(_root(), user_id)


def _validate_id(asset_id: str) -> str:
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise AssetNotFoundError("Invalid asset id.")
    return asset_id


def save_bytes(
    user_id: uuid.UUID | str,
    data: bytes,
    *,
    filename: str | None,
    kind: str = "source",
    derived_from: str | None = None,
) -> AssetRecord:
    safe_name = Path((filename or "asset").replace("\\", "/")).name
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", safe_name)
    # Keep filenames within filesystem limits, including Unicode names.
    while len(safe_name.encode("utf-8")) > 220:
        safe_name = safe_name[1:]
    extension = Path(safe_name).suffix.lower()
    if extension not in _ALLOWED:
        raise InvalidAssetError("Unsupported asset. Use PDF, Office documents, text, CSV, images, or Blender/3D files.")
    if not data:
        raise InvalidAssetError("Empty upload.")
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise InvalidAssetError("The file is not a valid PDF.")
    maximum = get_settings().asset_max_upload_mb * 1024 * 1024
    if len(data) > maximum:
        raise InvalidAssetError(
            f"Asset is too large (max {get_settings().asset_max_upload_mb} MB)."
        )
    asset_id = secrets.token_hex(8)
    record = AssetRecord(
        asset_id=asset_id,
        filename=safe_name,
        media_type=_ALLOWED[extension],
        size_bytes=len(data),
        created_at=datetime.now(timezone.utc).isoformat(),
        kind=kind,
        derived_from=derived_from,
    )
    directory = _user_dir(user_id)
    with storage_cleanup.allow_user_write(_root(), user_id):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{asset_id}{extension}").write_bytes(data)
        (directory / f"{asset_id}.json").write_text(json.dumps(record.to_dict(), indent=2))
    return record


def get_record(user_id: uuid.UUID | str, asset_id: str) -> AssetRecord:
    _validate_id(asset_id)
    sidecar = _user_dir(user_id) / f"{asset_id}.json"
    if not sidecar.is_file():
        raise AssetNotFoundError("Asset not found.")
    return AssetRecord(**json.loads(sidecar.read_text()))


def get_path(user_id: uuid.UUID | str, asset_id: str) -> Path:
    record = get_record(user_id, asset_id)
    path = _user_dir(user_id) / f"{asset_id}{Path(record.filename).suffix.lower()}"
    if not path.is_file():
        raise AssetNotFoundError("Asset not found.")
    return path


def exists(user_id: uuid.UUID | str, asset_id: str) -> bool:
    try:
        get_path(user_id, asset_id)
        return True
    except AssetNotFoundError:
        return False


def list_assets(user_id: uuid.UUID | str, *, limit: int = 20) -> list[AssetRecord]:
    directory = _user_dir(user_id)
    if not directory.is_dir():
        return []
    records: list[AssetRecord] = []
    for sidecar in directory.glob("*.json"):
        try:
            records.append(AssetRecord(**json.loads(sidecar.read_text())))
        except (OSError, ValueError, TypeError):
            continue
    records.sort(key=lambda item: item.created_at, reverse=True)
    return records[:limit]


def derived_path(user_id: uuid.UUID | str, asset_id: str, suffix: str) -> Path:
    """Where something derived from an asset lives (e.g. its extracted text, ``.pages.jsonl``). Never ``.json``:
    ``list_assets`` reads every ``*.json`` as a record."""
    if suffix.endswith(".json"):
        raise ValueError("derived files must not end in .json")
    return _user_dir(user_id) / f"{_validate_id(asset_id)}{suffix}"


def write_derived(user_id: uuid.UUID | str, asset_id: str, suffix: str, text: str) -> None:
    """Write a derived file atomically (readers never see half of it), fenced like every user write."""
    path = derived_path(user_id, asset_id, suffix)
    with storage_cleanup.allow_user_write(_root(), user_id):
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
        partial.write_text(text, encoding="utf-8")
        partial.replace(path)


def read_bytes(user_id: uuid.UUID | str, asset_id: str) -> tuple[bytes, AssetRecord]:
    record = get_record(user_id, asset_id)
    return get_path(user_id, asset_id).read_bytes(), record
