"""MacroVision: predict macronutrients (calories, fat, carbs, protein) from a food photo.

Public surface:
    - get_predictor        : cached, process-wide MacroVisionPredictor (load once)
    - MacroVisionPredictor : the model bundle; .predict(image) / .predict_async(image)
    - NutritionPrediction  : result dataclass with .to_dict() for JSON responses

Typical use from a FastAPI route / WebSocket handler:

    from app.ai.MacroVision import get_predictor

    predictor = get_predictor()                 # warmed once at startup
    result = await predictor.predict_async(image_bytes)
    return result.to_dict()
"""
from app.ai.MacroVision.predictor import (
    DepthEstimate,
    ImageInput,
    MacroVisionPredictor,
    NutritionPrediction,
    PredictionMode,
    get_predictor,
    load_image,
)

__all__ = [
    "get_predictor",
    "MacroVisionPredictor",
    "NutritionPrediction",
    "DepthEstimate",
    "PredictionMode",
    "ImageInput",
    "load_image",
]
