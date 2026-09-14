import io
import json
import math
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from PIL import Image
from pydantic import ValidationError

from app.ai.MacroVision.vlm import parse_vlm_json
from app.schemas.calorie_log import CalorieLogCreate, CalorieLogUpdate
from app.schemas.weight_log import WeightLogUpdate
from app.services import image_store


def test_tracking_updates_reject_explicit_null_and_nonfinite_values():
    for model, payload in [
        (WeightLogUpdate, {"weight": None}),
        (WeightLogUpdate, {"weight": math.inf}),
        (CalorieLogUpdate, {"calories": None}),
        (CalorieLogUpdate, {"calories": math.inf}),
        (CalorieLogCreate, {"calories": math.inf, "fat": 1, "carbs": 1, "protein": 1, "date": "2026-01-01"}),
    ]:
        with pytest.raises(ValidationError):
            model(**payload)


@pytest.mark.parametrize("response", [
    '{}',
    '{"dish_name":"food","estimated_portion_g":100,"base_nutrition_per_100g":{"calories":NaN,"fat":1,"carbs":1,"protein":1}}',
    '{"dish_name":"food","estimated_portion_g":100,"base_nutrition_per_100g":{"calories":1,"fat":-1,"carbs":1,"protein":1}}',
    '{"dish_name":"food","estimated_portion_g":"100","base_nutrition_per_100g":{"calories":1,"fat":1,"carbs":1,"protein":1}}',
])
def test_malformed_vlm_output_returns_none(response):
    assert parse_vlm_json(response) is None


def test_valid_vlm_output_is_normalized():
    value = parse_vlm_json(json.dumps({
        "dish_name": "  soup ", "estimated_portion_g": 250,
        "base_nutrition_per_100g": {"calories": 80, "fat": 2, "carbs": 10, "protein": 4},
    }))
    assert value["dish_name"] == "soup"
    assert value["estimated_portion_g"] == 250.0


def test_exif_orientation_is_normalized_for_model_and_storage(tmp_path, monkeypatch):
    settings = SimpleNamespace(macrovision_max_upload_mb=5, macrovision_max_image_pixels=40_000_000, image_store_dir=str(tmp_path))
    monkeypatch.setattr(image_store, "get_settings", lambda: settings)
    source = Image.new("RGB", (80, 40), "red")
    exif = source.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", exif=exif)
    data = buffer.getvalue()
    decoded = image_store.decode_image(data, "image/jpeg")
    assert decoded.size == (40, 80)
    user_id = uuid.uuid4()
    record = image_store.save_bytes(user_id, data, content_type="image/jpeg")
    stored, _ = image_store.read_bytes(user_id, record.image_id)
    assert Image.open(io.BytesIO(stored)).size == (40, 80)


def test_pixel_bomb_is_rejected_before_loading_pixels(monkeypatch):
    settings = SimpleNamespace(macrovision_max_upload_mb=5, macrovision_max_image_pixels=1_000)
    monkeypatch.setattr(image_store, "get_settings", lambda: settings)
    loaded = Mock()
    bomb = Mock(size=(10_000, 10_000), format="PNG", load=loaded)
    monkeypatch.setattr(image_store.Image, "open", Mock(return_value=bomb))
    with pytest.raises(image_store.InvalidImageError, match="dimensions are too large"):
        image_store.decode_image(b"synthetic", "image/png")
    loaded.assert_not_called()
