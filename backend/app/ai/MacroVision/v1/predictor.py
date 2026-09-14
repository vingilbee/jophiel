"""Three-tier MacroVision v1 inference orchestrator."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

import numpy as np
import torch
from PIL import Image
from torchvision.models import ResNet18_Weights

from .geometry import GeometryError, estimate_plate_volume, robust_fuse
from .models import NUMERIC_FEATURES, MassFusionNet
from .types import ARFrame, CameraIntrinsics, GeometryEstimate, NutritionPredictionV1


class MaskProvider(Protocol):
    def masks(self, image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
        """Return aligned (food_mask, support_mask) boolean arrays."""


class MetricDepthProvider(Protocol):
    def depth_meters(self, image: Image.Image) -> np.ndarray: ...


class MacroVisionV1Predictor:
    """Route RGB, RGB-D, and AR scans through a shared calibrated mass head."""

    def __init__(
        self,
        checkpoint: str | Path,
        *,
        mask_provider: MaskProvider | None = None,
        depth_provider: MetricDepthProvider | None = None,
        device: str | None = None,
    ) -> None:
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        payload = torch.load(checkpoint, map_location=self.device, weights_only=False)
        self.model = MassFusionNet(pretrained=False)
        self.model.load_state_dict(payload["model"])
        self.model.to(self.device).eval()
        self.feature_mean = torch.tensor(payload["feature_mean"], dtype=torch.float32, device=self.device)
        self.feature_std = torch.tensor(payload["feature_std"], dtype=torch.float32, device=self.device)
        self.mask_provider = mask_provider
        self.depth_provider = depth_provider
        self.image_transform = ResNet18_Weights.DEFAULT.transforms()

    @staticmethod
    def _features(
        tier: str, geometry: GeometryEstimate | None, food_mask: np.ndarray,
        density_prior_g_ml: float,
    ) -> dict[str, float]:
        area = float(np.asarray(food_mask, dtype=bool).mean())
        return {
            "geometry_volume_ml": geometry.volume_ml if geometry else 0.0,
            "geometry_confidence": geometry.confidence if geometry else 0.0,
            "median_depth_m": geometry.median_depth_m if geometry else 0.0,
            "relief_m": geometry.relief_m if geometry else 0.0,
            "food_area_fraction": area,
            "density_prior_g_ml": density_prior_g_ml,
            "tier_ar": float(tier == "ar_scan"),
            "tier_rgbd": float(tier == "rgbd"),
            "tier_monocular": float(tier == "monocular"),
            "has_metric_geometry": float(geometry is not None),
        }

    def _infer(
        self, image: Image.Image, tier: str, food_mask: np.ndarray,
        geometry: GeometryEstimate | None, density_prior_g_ml: float, dish: str | None,
    ) -> NutritionPredictionV1:
        raw = self._features(tier, geometry, food_mask, density_prior_g_ml)
        numeric = torch.tensor([[raw[key] for key in NUMERIC_FEATURES]], device=self.device)
        numeric = (numeric - self.feature_mean) / self.feature_std.clamp_min(1e-6)
        image_t = self.image_transform(image.convert("RGB")).unsqueeze(0).to(self.device)
        with torch.no_grad():
            output = self.model(image_t, numeric)
        learned_mass = float(torch.expm1(output["log_mass"])[0].clamp_min(0).cpu())
        learned_volume = float(torch.expm1(output["log_volume"])[0].clamp_min(0).cpu())
        sigma = float(output["log_sigma"].exp()[0].cpu())
        if geometry:
            physical_mass = geometry.volume_ml * density_prior_g_ml
            alpha = float(np.clip(geometry.confidence * (0.8 if tier == "ar_scan" else 0.65), 0.1, 0.85))
            mass = alpha * physical_mass + (1 - alpha) * learned_mass
            volume = geometry.volume_ml
            confidence = float(np.clip(0.35 + 0.6 * geometry.confidence, 0, 0.95))
        else:
            physical_mass, alpha, mass, volume = None, 0.0, learned_mass, learned_volume
            confidence = float(np.clip(math.exp(-sigma) * 0.55, 0.05, 0.55))
        # The head predicts uncertainty in log1p(mass), yielding asymmetric bounds.
        log_mass = math.log1p(max(mass, 0))
        z = 1.645  # central 90% interval
        low = max(0.0, math.expm1(log_mass - z * sigma))
        high = math.expm1(log_mass + z * sigma)
        return NutritionPredictionV1(
            estimated_weight_g=mass, estimated_volume_ml=volume, weight_low_g=low,
            weight_high_g=high, weight_confidence=confidence, weight_method=tier, dish=dish,
            diagnostics={
                "learned_mass_g": learned_mass, "physical_mass_g": physical_mass,
                "fusion_physical_weight": alpha, "features": raw,
                "geometry": geometry.diagnostics if geometry else None,
            },
        )

    def predict_rgbd(
        self, image: Image.Image, depth_m: np.ndarray, intrinsics: CameraIntrinsics,
        *, food_mask: np.ndarray | None = None, support_mask: np.ndarray | None = None,
        depth_confidence: np.ndarray | None = None, density_prior_g_ml: float = 1.0,
        dish: str | None = None,
    ) -> NutritionPredictionV1:
        if food_mask is None or support_mask is None:
            if self.mask_provider is None:
                raise GeometryError("food/support masks or a mask_provider are required")
            food_mask, support_mask = self.mask_provider.masks(image)
        geometry = estimate_plate_volume(
            depth_m, intrinsics, food_mask, support_mask, confidence_map=depth_confidence
        )
        return self._infer(image, "rgbd", food_mask, geometry, density_prior_g_ml, dish)

    def predict_ar_scan(
        self, frames: list[ARFrame], *, density_prior_g_ml: float = 1.0,
        dish: str | None = None,
    ) -> NutritionPredictionV1:
        estimates: list[GeometryEstimate] = []
        representative: ARFrame | None = None
        for frame in frames:
            food, support = frame.food_mask, frame.support_mask
            if food is None or support is None:
                if self.mask_provider is None:
                    continue
                food, support = self.mask_provider.masks(frame.image)
            depth = frame.depth_m
            if depth is None and self.depth_provider is not None:
                depth = self.depth_provider.depth_meters(frame.image)
            if depth is None:
                continue
            try:
                estimate = estimate_plate_volume(
                    depth, frame.intrinsics, food, support,
                    confidence_map=frame.depth_confidence,
                )
            except GeometryError:
                continue
            estimates.append(estimate)
            if representative is None or estimate.confidence > max(x.confidence for x in estimates[:-1]):
                representative = ARFrame(
                    frame.image, frame.intrinsics, frame.camera_transform, depth,
                    frame.depth_confidence, food, support, frame.timestamp_s,
                )
        if representative is None:
            raise GeometryError("AR scan has no frame with usable depth and masks")
        geometry = robust_fuse(estimates)
        return self._infer(
            representative.image, "ar_scan", representative.food_mask, geometry,
            density_prior_g_ml, dish,
        )

    def predict_rgb(
        self, image: Image.Image, *, food_mask: np.ndarray | None = None,
        density_prior_g_ml: float = 1.0, dish: str | None = None,
    ) -> NutritionPredictionV1:
        if food_mask is None:
            if self.mask_provider is None:
                raise GeometryError("food_mask or a mask_provider is required")
            food_mask, _ = self.mask_provider.masks(image)
        # Monocular predictions deliberately omit a fake metric geometry value.
        return self._infer(image, "monocular", food_mask, None, density_prior_g_ml, dish)
