"""Add explicit metric geometry features to a training manifest.

Input rows need depth_path (.npy, metres), food_mask_path, support_mask_path and
camera intrinsics fx/fy/cx/cy/width/height. Paths are relative to --data-root.
Failed rows are retained as monocular examples unless --drop-failures is set.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image

from .dataset import write_manifest
from .geometry import GeometryError, estimate_plate_volume
from .types import CameraIntrinsics


def _mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L")) > 127


def main(args: argparse.Namespace) -> None:
    source, root = Path(args.manifest), Path(args.data_root)
    with source.open(newline="", encoding="utf-8") as handle:
        input_rows = list(csv.DictReader(handle))
    output_rows, failures = [], 0
    for row in input_rows:
        try:
            depth = np.load(root / row["depth_path"]).astype(np.float32)
            food = _mask(root / row["food_mask_path"])
            support = _mask(root / row["support_mask_path"])
            h, w = depth.squeeze().shape
            intrinsics = CameraIntrinsics(
                float(row["fx"]), float(row["fy"]), float(row["cx"]), float(row["cy"]),
                int(row.get("width") or w), int(row.get("height") or h),
            )
            estimate = estimate_plate_volume(depth, intrinsics, food, support)
            row.update({
                "volume_ml": estimate.volume_ml,
                "geometry_volume_ml": estimate.volume_ml,
                "geometry_confidence": estimate.confidence,
                "median_depth_m": estimate.median_depth_m,
                "relief_m": estimate.relief_m,
                "food_area_fraction": float(food.mean()),
                "tier_ar": int(row.get("tier") == "ar_scan"),
                "tier_rgbd": int(row.get("tier", "rgbd") == "rgbd"),
                "tier_monocular": 0,
                "has_metric_geometry": 1,
            })
            output_rows.append(row)
        except (KeyError, ValueError, OSError, GeometryError) as exc:
            failures += 1
            if args.drop_failures:
                continue
            row.update({"tier_ar": 0, "tier_rgbd": 0, "tier_monocular": 1,
                        "has_metric_geometry": 0, "geometry_error": str(exc)})
            output_rows.append(row)
    write_manifest(output_rows, args.output)
    print(f"wrote {len(output_rows)} rows ({failures} geometry failures) to {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--drop-failures", action="store_true")
    main(parser.parse_args())
