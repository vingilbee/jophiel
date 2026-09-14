"""Data contracts shared by all MacroVision v1 capture tiers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np
from PIL import Image

CaptureTier = Literal["ar_scan", "rgbd", "monocular"]


@dataclass(frozen=True, slots=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def scaled(self, width: int, height: int) -> "CameraIntrinsics":
        sx, sy = width / self.width, height / self.height
        return CameraIntrinsics(
            self.fx * sx, self.fy * sy, self.cx * sx, self.cy * sy, width, height
        )


@dataclass(slots=True)
class ARFrame:
    """One RGB frame plus metric camera metadata supplied by ARKit/ARCore."""

    image: Image.Image
    intrinsics: CameraIntrinsics
    camera_transform: np.ndarray
    depth_m: np.ndarray | None = None
    depth_confidence: np.ndarray | None = None
    food_mask: np.ndarray | None = None
    support_mask: np.ndarray | None = None
    timestamp_s: float = 0.0


@dataclass(slots=True)
class GeometryEstimate:
    volume_ml: float
    confidence: float
    valid_pixel_count: int
    median_depth_m: float
    relief_m: float
    diagnostics: dict[str, float | int | str] = field(default_factory=dict)


@dataclass(slots=True)
class FoodComponentPrediction:
    name: str
    volume_ml: float | None
    density_g_ml: float | None
    weight_g: float
    confidence: float


@dataclass(slots=True)
class NutritionPredictionV1:
    estimated_weight_g: float
    estimated_volume_ml: float | None
    weight_low_g: float
    weight_high_g: float
    weight_confidence: float
    weight_method: CaptureTier
    calories: float | None = None
    fat: float | None = None
    carbs: float | None = None
    protein: float | None = None
    dish: str | None = None
    components: list[FoodComponentPrediction] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in (
            "estimated_weight_g", "estimated_volume_ml", "weight_low_g",
            "weight_high_g", "weight_confidence", "calories", "fat", "carbs",
            "protein",
        ):
            if data[key] is not None:
                data[key] = round(float(data[key]), 2)
        return data
