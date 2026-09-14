"""MacroVision v1: tiered, physically grounded food mass estimation."""
from .geometry import GeometryError, backproject, estimate_plate_volume, robust_fuse
from .predictor import MacroVisionV1Predictor
from .types import (
    ARFrame, CameraIntrinsics, FoodComponentPrediction, GeometryEstimate,
    NutritionPredictionV1,
)

__all__ = [
    "ARFrame", "CameraIntrinsics", "FoodComponentPrediction", "GeometryEstimate",
    "GeometryError", "MacroVisionV1Predictor", "NutritionPredictionV1",
    "backproject", "estimate_plate_volume", "robust_fuse",
]
