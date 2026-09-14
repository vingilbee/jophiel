"""Shared pieces for the vision-language "per-100g" estimators.

The ``combined`` prediction mode asks a vision-language model (VLM) for a dish
name, an estimated portion size, and nutrition *per 100g*, then scales it by the
regressor's portion signal. That estimate can come from more than one backend
(the local Qwen3-VL, or the OpenAI multimodal API) — they differ only in *how*
they run the model, so the prompt and the JSON parsing live here and are shared.

A backend just needs to satisfy :class:`VLMBackend`: take a PIL image and return
the parsed estimate dict (or ``None`` on failure).
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Protocol, runtime_checkable

from PIL import Image

from app.ai.MacroVision.types import NutritionHints

logger = logging.getLogger(__name__)

# The instruction every VLM backend sends. Nutrition is requested per 100g (not
# total) so the regressor's calorie estimate can be used to correct the portion.
VLM_PROMPT = (
    "Analyze this food and return JSON only:\n"
    "{\n"
    '  "dish_name": "",\n'
    '  "estimated_portion_g": 0,\n'
    '  "base_nutrition_per_100g": {\n'
    '    "calories": 0, "fat": 0, "carbs": 0, "protein": 0\n'
    "  }\n"
    "}\n"
    "Return nutrition per 100g, NOT total — I will scale by portion size."
)


def parse_vlm_json(response: str) -> dict | None:
    """Extract the JSON object from a VLM's text response. ``None`` on failure.

    Backends may wrap the JSON in prose or code fences, so we pull the first
    ``{...}`` block out before decoding.
    """
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        logger.warning("VLM returned no parseable JSON: %r", response[:200])
        return None
    try:
        return validate_vlm_estimate(json.loads(match.group()))
    except json.JSONDecodeError:
        logger.warning("VLM JSON failed to decode: %r", match.group()[:200])
        return None


def validate_vlm_estimate(value: object) -> dict | None:
    """Validate model output before arithmetic; malformed output falls back."""
    if not isinstance(value, dict):
        return None
    dish = value.get("dish_name", "")
    portion = value.get("estimated_portion_g")
    base = value.get("base_nutrition_per_100g")
    if not isinstance(dish, str) or not isinstance(portion, (int, float)) or not isinstance(base, dict):
        return None
    if not math.isfinite(float(portion)) or float(portion) <= 0:
        return None
    nutrients: dict[str, float] = {}
    for field in ("calories", "fat", "carbs", "protein"):
        number = base.get(field)
        if not isinstance(number, (int, float)) or not math.isfinite(float(number)) or float(number) < 0:
            return None
        nutrients[field] = float(number)
    return {"dish_name": dish.strip(), "estimated_portion_g": float(portion), "base_nutrition_per_100g": nutrients}


@runtime_checkable
class VLMBackend(Protocol):
    """A per-100g nutrition estimator implemented by the API-backed VLM."""

    def estimate(self, img: Image.Image, depth=None, hints: NutritionHints | None = None) -> dict | None:
        """Return the parsed per-100g estimate dict, or ``None`` on failure."""
        ...
