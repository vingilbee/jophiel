"""Metric volume estimation from aligned RGB-D observations.

Depth is never normalized here. Values are metres. A support plane is fitted to
visible plate/table pixels and food height is integrated over the corresponding
metric pixel footprint on that plane.
"""
from __future__ import annotations

import numpy as np

from .types import CameraIntrinsics, GeometryEstimate


class GeometryError(ValueError):
    pass


def _bool_mask(mask: np.ndarray, shape: tuple[int, int], name: str) -> np.ndarray:
    value = np.asarray(mask).squeeze().astype(bool)
    if value.shape != shape:
        raise GeometryError(f"{name} shape {value.shape} does not match depth {shape}")
    return value


def backproject(depth_m: np.ndarray, intrinsics: CameraIntrinsics) -> np.ndarray:
    """Convert an HxW metric depth image to an HxWx3 camera-space point map."""
    depth = np.asarray(depth_m, dtype=np.float64).squeeze()
    if depth.ndim != 2:
        raise GeometryError("depth_m must be a 2D array")
    h, w = depth.shape
    k = intrinsics.scaled(w, h)
    yy, xx = np.mgrid[:h, :w]
    x = (xx - k.cx) * depth / k.fx
    y = (yy - k.cy) * depth / k.fy
    return np.stack((x, y, depth), axis=-1)


def fit_support_plane(points: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Robustly fit n·x+d=0, returning unit normal, offset, and RMSE."""
    selected = points[mask]
    selected = selected[np.isfinite(selected).all(axis=1) & (selected[:, 2] > 0)]
    if len(selected) < 64:
        raise GeometryError("at least 64 valid support pixels are required")
    # Trim depth extremes before SVD; this removes common mask-edge contamination.
    lo, hi = np.quantile(selected[:, 2], [0.05, 0.95])
    selected = selected[(selected[:, 2] >= lo) & (selected[:, 2] <= hi)]
    center = np.median(selected, axis=0)
    _, _, vh = np.linalg.svd(selected - center, full_matrices=False)
    normal = vh[-1]
    normal /= np.linalg.norm(normal) + 1e-12
    if normal[2] > 0:
        normal = -normal
    offset = -float(normal @ center)
    residual = selected @ normal + offset
    # Refit after removing gross residual outliers.
    cutoff = max(0.002, float(np.quantile(np.abs(residual), 0.9)))
    selected = selected[np.abs(residual) <= cutoff]
    center = selected.mean(axis=0)
    _, _, vh = np.linalg.svd(selected - center, full_matrices=False)
    normal = vh[-1] / (np.linalg.norm(vh[-1]) + 1e-12)
    if normal[2] > 0:
        normal = -normal
    offset = -float(normal @ center)
    rmse = float(np.sqrt(np.mean((selected @ normal + offset) ** 2)))
    return normal, offset, rmse


def _plane_pixel_area(
    shape: tuple[int, int], intrinsics: CameraIntrinsics, normal: np.ndarray, offset: float
) -> np.ndarray:
    """Approximate each pixel's metric footprint on the fitted support plane."""
    h, w = shape
    k = intrinsics.scaled(w, h)
    yy, xx = np.mgrid[:h, :w]
    rays = np.stack(((xx - k.cx) / k.fx, (yy - k.cy) / k.fy, np.ones_like(xx)), -1)
    denom = rays @ normal
    t = -offset / np.where(np.abs(denom) < 1e-8, np.nan, denom)
    plane = rays * t[..., None]
    dx = np.gradient(plane, axis=1)
    dy = np.gradient(plane, axis=0)
    return np.linalg.norm(np.cross(dx, dy), axis=-1)


def estimate_plate_volume(
    depth_m: np.ndarray,
    intrinsics: CameraIntrinsics,
    food_mask: np.ndarray,
    support_mask: np.ndarray,
    *,
    confidence_map: np.ndarray | None = None,
    min_confidence: float = 0.5,
    max_height_m: float = 0.30,
) -> GeometryEstimate:
    """Estimate food volume above a locally planar plate/table surface."""
    depth = np.asarray(depth_m, dtype=np.float64).squeeze()
    if depth.ndim != 2:
        raise GeometryError("depth_m must be HxW")
    food = _bool_mask(food_mask, depth.shape, "food_mask")
    support = _bool_mask(support_mask, depth.shape, "support_mask") & ~food
    valid = np.isfinite(depth) & (depth > 0)
    if confidence_map is not None:
        confidence = np.asarray(confidence_map, dtype=float).squeeze()
        if confidence.shape != depth.shape:
            raise GeometryError("confidence_map must match depth shape")
        valid &= confidence >= min_confidence
    food &= valid
    support &= valid
    points = backproject(depth, intrinsics)
    normal, offset, plane_rmse = fit_support_plane(points, support)
    signed = points @ normal + offset
    # Normal points toward the camera, so food above the plate is positive.
    height = np.clip(signed, 0.0, max_height_m)
    area = _plane_pixel_area(depth.shape, intrinsics, normal, offset)
    usable = food & np.isfinite(height) & np.isfinite(area) & (area > 0)
    count = int(usable.sum())
    if count < 32:
        raise GeometryError("at least 32 valid food pixels are required")
    volume_ml = float(np.sum(height[usable] * area[usable]) * 1_000_000.0)
    depths = depth[usable]
    relief = float(np.quantile(height[usable], 0.95) - np.quantile(height[usable], 0.05))
    coverage = min(1.0, count / max(1, int(food.sum())))
    plane_score = float(np.exp(-plane_rmse / 0.01))
    geometry_confidence = float(np.clip(coverage * plane_score, 0.0, 1.0))
    return GeometryEstimate(
        volume_ml=volume_ml,
        confidence=geometry_confidence,
        valid_pixel_count=count,
        median_depth_m=float(np.median(depths)),
        relief_m=relief,
        diagnostics={"plane_rmse_m": plane_rmse, "pixel_coverage": coverage},
    )


def robust_fuse(estimates: list[GeometryEstimate]) -> GeometryEstimate:
    """Confidence-weighted robust fusion for a multi-frame AR scan."""
    if not estimates:
        raise GeometryError("no valid frame-level geometry estimates")
    volumes = np.asarray([x.volume_ml for x in estimates])
    median = float(np.median(volumes))
    mad = float(np.median(np.abs(volumes - median)))
    tolerance = max(10.0, 3.5 * 1.4826 * mad)
    keep = np.abs(volumes - median) <= tolerance
    weights = np.asarray([x.confidence for x in estimates])[keep] + 1e-3
    kept = [x for x, ok in zip(estimates, keep, strict=True) if ok]
    fused_volume = float(np.average([x.volume_ml for x in kept], weights=weights))
    consistency = float(np.exp(-mad / max(median, 1.0)))
    return GeometryEstimate(
        volume_ml=fused_volume,
        confidence=float(np.clip(np.mean(weights) * consistency, 0, 1)),
        valid_pixel_count=sum(x.valid_pixel_count for x in kept),
        median_depth_m=float(np.median([x.median_depth_m for x in kept])),
        relief_m=float(np.median([x.relief_m for x in kept])),
        diagnostics={
            "input_frames": len(estimates), "kept_frames": len(kept),
            "volume_mad_ml": mad,
        },
    )
