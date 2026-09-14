import numpy as np

from app.ai.MacroVision.v1.geometry import estimate_plate_volume, robust_fuse
from app.ai.MacroVision.v1.types import CameraIntrinsics


def test_plate_volume_recovers_rectangular_height_field():
    h = w = 100
    depth = np.ones((h, w), dtype=np.float32)
    food = np.zeros((h, w), dtype=bool)
    food[40:60, 40:60] = True
    depth[food] = 0.95  # five centimetres toward camera
    support = ~food
    intrinsics = CameraIntrinsics(100, 100, 49.5, 49.5, w, h)

    result = estimate_plate_volume(depth, intrinsics, food, support)

    # At z=1m and f=100px, each pixel is about 1cm². 400 * 1cm² * 5cm = 2000mL.
    assert 1850 < result.volume_ml < 2150
    assert result.confidence > 0.8


def test_robust_fusion_rejects_large_outlier():
    from app.ai.MacroVision.v1.types import GeometryEstimate

    def estimate(volume):
        return GeometryEstimate(volume, 0.9, 100, 1.0, 0.05)

    result = robust_fuse([estimate(100), estimate(102), estimate(98), estimate(900)])
    assert 98 <= result.volume_ml <= 102
    assert result.diagnostics["kept_frames"] == 3
