"""Prediction service: validate an uploaded image and run the MacroVision model.

Keeps the route handler thin (per CLAUDE.md) — it owns upload handling and
delegates inference to the cached ``MacroVisionPredictor``. Inference is
GPU/CPU-bound, so the predictor runs it in a worker thread (``predict_async``)
to avoid blocking the event loop.

Validation lives in ``image_store`` so this path and the chat upload path
enforce the same rules and fail the same way on the same bad input.
"""
from __future__ import annotations

import logging
import uuid

from app.ai.MacroVision import PredictionMode, get_predictor
from app.schemas.prediction import PredictionOut
from app.services import image_store
from app.services.image_store import InvalidImageError

logger = logging.getLogger(__name__)

__all__ = ["InvalidImageError", "predict_from_image"]


async def predict_from_image(
    *,
    data: bytes,
    content_type: str | None,
    mode: PredictionMode = "combined",
    user_id: uuid.UUID | None = None,
    filename: str | None = None,
) -> PredictionOut:
    """Validate raw image bytes and return predicted macros.

    Pass ``user_id`` to also keep the photo in that user's image store; the
    returned ``image_id`` is then what the chat agent uses to talk about the
    same picture later ("why were the calories so high on that one?").

    Raises :class:`InvalidImageError` for a missing / oversized / non-image
    upload or one the decoder can't open. Model and inference failures
    propagate unchanged (the route maps them to 5xx).
    """
    img = image_store.decode_image(data, content_type)

    image_id: str | None = None
    if user_id is not None:
        try:
            image_id = image_store.save_bytes(
                user_id, data, content_type=content_type, filename=filename
            ).image_id
        except OSError:
            # A full or unwritable disk shouldn't cost the user their
            # prediction — they just don't get to refer back to the photo.
            logger.exception("Could not store the uploaded image; predicting anyway.")

    prediction = await get_predictor().predict_async(img, mode=mode)
    return PredictionOut(**prediction.to_dict(), image_id=image_id)
