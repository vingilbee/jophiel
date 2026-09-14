"""OpenAI-backed VLM estimator — an alternative to the local Qwen3-VL.

The local Qwen3-VL is accurate but heavy: it downloads an ~8B model and runs
inference locally (slow and memory-hungry off CUDA). This backend offloads that
step to OpenAI's multimodal chat API instead — same input (a PIL image) and same
output (the per-100g estimate dict from :mod:`app.ai.MacroVision.vlm`), so it
drops straight into the ``combined`` prediction path with no other changes.

Select it by setting ``MACROVISION_VLM_BACKEND=openai`` (and providing
``OPENAI_API_KEY``); Qwen is then never loaded.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any

from PIL import Image

from app.ai.MacroVision.types import NutritionHints
from app.ai.MacroVision.vlm import VLM_PROMPT, parse_vlm_json

logger = logging.getLogger(__name__)




class OpenAIVLMGenerator:
    """Estimate per-100g nutrition from a food photo via the OpenAI API.

    Satisfies the :class:`~app.ai.MacroVision.vlm.VLMBackend` protocol. The
    client call is synchronous — the predictor already runs inference in a worker
    thread (``predict_async``), so a blocking network call here is fine.
    """

    def __init__(
        self, *, api_key: str | None = None, model: str = "gpt-4o-mini"
    ) -> None:
        # Imported lazily so the `openai` dependency is only required when this
        # backend is actually selected.
        from openai import OpenAI

        self.model = model
        # OpenAI() falls back to the OPENAI_API_KEY env var when api_key is None.
        self._client = OpenAI(api_key=api_key) if api_key else OpenAI()
        logger.info("Using OpenAI VLM backend (model=%s)", model)

    @staticmethod
    def _data_url(img: Image.Image) -> str:
        """Encode a PIL image as a base64 JPEG data URL for the chat API."""
        buffer = io.BytesIO()
        # load_image() already converts to RGB, so JPEG is safe.
        img.save(buffer, format="JPEG", quality=90)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    
    def _hints_to_text(self, hints: NutritionHints) -> str | None:
        """Render known ingredients / notes as freeform context for the prompt.

        Weight is handled separately since it's woven into its own sentence
        below; this only covers the two purely-contextual fields.
        """
        lines = []
        ingredients = hints.get("known_ingredients")
        if ingredients:
            lines.append(f"Known ingredients: {', '.join(str(item) for item in ingredients)}.")
        notes = hints.get("extra_notes")
        if notes:
            lines.append(f"Additional notes from the user: {notes}")
        if not lines:
            return None
        lines.insert(0, "The user has also provided the following known information:")
        lines.append(
            "Use this information to improve accuracy where relevant, but still "
            "return your full estimate in the required JSON format."
        )
        return "\n".join(lines)

    def estimate(
        self, img: Image.Image, depth: Image.Image = None, hints: NutritionHints | None = None
    ) -> dict | None:
        """Ask the model for per-100g nutrition + portion. ``None`` on failure."""
        hints = hints or {}
        content = [
            {"type": "text", "text": VLM_PROMPT},
            {"type": "image_url", "image_url": {"url": self._data_url(img)}},
        ]
        if depth is not None:
            content.extend([
                {"type": "text", "text": "Use this depth estimate to improve portion accuracy."},
                {"type": "image_url", "image_url": {"url": self._data_url(depth)}},
            ])
        known_weight_g = hints.get("known_weight_g")
        if known_weight_g is not None:
            # Ground-truth weight helps the model calibrate density/composition
            # even though predict_combined will use this value directly rather
            # than the model's own estimated_portion_g.
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"The user has confirmed this portion weighs "
                        f"{known_weight_g:g}g. Use this to inform your per-100g "
                        "nutrition estimate, but still return your own "
                        "estimated_portion_g as usual."
                    ),
                }
            )
        hints_text = self._hints_to_text(hints)
        if hints_text:
            content.append({"type": "text", "text": hints_text})

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=256,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except Exception:
            logger.exception("OpenAI VLM request failed")
            return None

        content_text = response.choices[0].message.content or ""
        return parse_vlm_json(content_text)
