import uuid
from types import SimpleNamespace

import pytest

from app.services import asset_store


@pytest.fixture
def configured_store(tmp_path, monkeypatch):
    settings = SimpleNamespace(asset_store_dir=str(tmp_path), asset_max_upload_mb=1)
    monkeypatch.setattr(asset_store, "get_settings", lambda: settings)
    return tmp_path


def test_asset_store_is_user_scoped(configured_store):
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    record = asset_store.save_bytes(owner, b"o Cube\n", filename="mesh.obj")

    assert asset_store.get_path(owner, record.asset_id).read_bytes() == b"o Cube\n"
    assert not asset_store.exists(stranger, record.asset_id)
    with pytest.raises(asset_store.AssetNotFoundError):
        asset_store.get_path(stranger, record.asset_id)


def test_asset_store_rejects_paths_and_unsupported_files(configured_store):
    owner = uuid.uuid4()
    record = asset_store.save_bytes(owner, b"solid", filename="../character.glb")
    assert record.filename == "character.glb"

    with pytest.raises(asset_store.InvalidAssetError):
        asset_store.save_bytes(owner, b"payload", filename="script.py")
    with pytest.raises(asset_store.AssetNotFoundError):
        asset_store.get_path(owner, "../../etc/passwd")
