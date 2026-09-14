# MacroVision v1

An isolated, three-tier food **mass** pipeline. The existing MacroVision package
is unchanged.

## Tiers

1. `predict_ar_scan`: multiple ARKit/ARCore frames with intrinsics, poses and
   preferably aligned metric depth. Valid frame volumes are robustly fused.
2. `predict_rgbd`: one RGB image, aligned metric depth, intrinsics, food mask,
   and visible plate/table support mask.
3. `predict_rgb`: one RGB image and food mask. This is a learned fallback with
   deliberately lower confidence and a wider 90% interval.

The geometry path fits a physical support plane and integrates food height over
the metric pixel footprint. It never independently normalizes a depth map.

## What is and is not implemented

- Implemented: contracts, raw RGB-D plate geometry, robust multi-frame fusion,
  capture-tier routing, physical/learned mass fusion, uncertainty intervals,
  manifest dataset, training CLI, Nutrition5k manifest preparation, tests, and
  a Kaggle notebook.
- Supplied by adapters: YOLO/SAM food and support masks and ZoeDepth metric
  predictions. Implement `MaskProvider` and `MetricDepthProvider` from
  `predictor.py` around the models already used by the application.
- Mobile responsibility: ARKit/ARCore capture must send RGB, raw depth (when
  available), confidence, intrinsics and the 4x4 camera transform. A backend
  cannot reconstruct metadata that a JPEG upload discarded.
- Current AR mode fuses frame-level metric RGB-D volumes. Depth-free, pose-based
  multi-view point-cloud reconstruction is a later backend and must not be
  represented as completed by this package.
- Plate/table geometry is supported. Bowl/cup volume needs a calibrated interior
  model and intentionally raises/falls back at the adapter level rather than
  silently treating a curved bowl as a plane.

## Training manifest

Required CSV fields:

```text
image_path,mass_g,split
```

Recommended fields:

```text
volume_ml,geometry_volume_ml,geometry_confidence,median_depth_m,relief_m,
food_area_fraction,density_prior_g_ml,tier_ar,tier_rgbd,tier_monocular,
has_metric_geometry,dish_id
```

Rows without geometry should use zeros and `tier_monocular=1`. Do not put the
same physical dish in different splits. `prepare_nutrition5k.py` uses a stable
dish-level split.

## Local commands

From `backend/`:

```bash
uv run python -m app.ai.MacroVision.v1.prepare_nutrition5k \
  --dataset-root /data/nutrition5k \
  --metadata-csv /data/nutrition5k/metadata/dish_metadata_cafe1.csv \
  --output /tmp/nutrition5k_v1.csv

uv run python -m app.ai.MacroVision.v1.train \
  --manifest /tmp/nutrition5k_v1.csv \
  --data-root /data/nutrition5k \
  --output-dir outputs/macrovision_v1
```

For a meaningful Tier 1/2 model, add `depth_path`, `food_mask_path`,
`support_mask_path`, and `fx/fy/cx/cy/width/height` to the source manifest, then
run `python -m app.ai.MacroVision.v1.preprocess_geometry`. Nutrition5k's fixed
rig can alternatively supply its published explicit volume scalar. Training an
all-zero geometry manifest only trains the Tier 3 fallback.

## Checkpoint

The best checkpoint contains model weights, numeric feature mean/std, arguments,
and validation metrics. Inference refuses to operate without this checkpoint so
an untrained random model cannot accidentally reach production.
