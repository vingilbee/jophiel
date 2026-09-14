"""High-level MacroVision predictor: food photo → macronutrients.

This is the public entry point for inference. A single :class:`MacroVisionPredictor`
loads every model once (per the "load once at startup, not per-request"
convention) and can then serve many predictions. Use :func:`get_predictor` to
get the process-wide cached instance from a FastAPI route / WebSocket handler.

Prediction modes
----------------
- ``regression`` : depth (ZoeDepth) + RGB → ViT cross-attention regressor.
- ``combined``   : a vision-language model (VLM) estimates per-100g nutrition
  and portion size; the regressor's calorie estimate is used as a portion-scale
  signal to correct the VLM's portion guess. Falls back to ``regression`` if the
  VLM is disabled or fails to return parseable JSON.

VLM backend
-----------
The ``combined`` mode uses the OpenAI multimodal API
(:mod:`app.ai.MacroVision.openai_vlm`). It does not download a local Qwen model
and requires ``OPENAI_API_KEY``. If disabled or unavailable, prediction falls
back to the regression model.

Input
-----
:meth:`MacroVisionPredictor.predict` accepts anything :func:`load_image`
handles: a filesystem path (``str`` / ``Path``), raw image ``bytes`` (e.g. an
uploaded file / WebSocket blob), a file-like object, or a ``PIL.Image``.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import IO, Any, Literal, TypedDict, Union

import numpy as np
import torch
from matplotlib import cm
from PIL import Image, ImageOps
from torchvision import transforms
from transformers import (
    BitsAndBytesConfig,
    ZoeDepthForDepthEstimation,
    ZoeDepthImageProcessor,
)

from app.ai.MacroVision.architecture import NutritionRegressor, build_key_map
from app.ai.MacroVision.types import NutritionHints
from app.ai.MacroVision.vlm import VLM_PROMPT, VLMBackend, parse_vlm_json
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Weight files live next to this package (gitignored, transferred manually).
_WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
_REGRESSOR_CKPT = _WEIGHTS_DIR / "VITRegression" / "best_model-2.pt"
_ZOEDEPTH_DIR = _WEIGHTS_DIR / "ZeoDepthFineTunes"

PredictionMode = Literal["combined", "regression"]

# Which model backs the "combined" mode's VLM step.
VLMBackendName = Literal["openai"]

# Anything the predictor knows how to turn into a PIL RGB image.
ImageInput = Union[str, Path, bytes, bytearray, IO[bytes], Image.Image]

# ImageNet stats — must match training preprocessing.
_rgb_transform = transforms.Compose(
    [
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)


@dataclass(slots=True)
class NutritionPrediction:
    """A single prediction result. JSON-serializable via :meth:`to_dict`."""

    calories: float
    fat: float
    carbs: float
    protein: float
    # Only populated by the combined (VLM) mode.
    dish: str | None = None
    portion_scale: float | None = None
    # Which path produced this: "combined", "regression", or "vlm".
    source: str = "regression"

    def to_dict(self) -> dict:
        """Rounded, JSON-friendly dict for API / WebSocket responses."""
        data = asdict(self)
        for key in ("calories", "fat", "carbs", "protein", "portion_scale"):
            if data[key] is not None:
                data[key] = round(float(data[key]), 1)
        return {k: v for k, v in data.items() if v is not None}


@dataclass(slots=True)
class DepthEstimate:
    """A metric depth map, summarized into numbers a caller can reason about.

    The raw ``(H, W)`` array is on ``depth_map`` and the regressor-shaped
    ``(1,3,224,224)`` tensor on ``depth_tensor``, but neither is any use to a
    language model — :meth:`to_dict` deliberately omits both and returns the
    distribution instead. ``relief_m`` (far minus near) is the number that
    actually matters for portion size: a tall bowl has more of it than a flat
    plate at the same camera distance.
    """

    width: int
    height: int
    min_m: float
    max_m: float
    mean_m: float
    median_m: float
    p10_m: float
    p90_m: float
    std_m: float
    # Full-resolution metric depth, and the standardized regressor input.
    depth_map: np.ndarray | None = None
    depth_tensor: torch.Tensor | None = None
    # Rendered false-colour depth image, when one was requested.
    depth_image: Image.Image | None = None

    @property
    def relief_m(self) -> float:
        """Depth spread across the scene, in metres."""
        return self.max_m - self.min_m

    def to_dict(self) -> dict:
        """JSON-friendly summary. Excludes the arrays — see the class docstring."""
        return {
            "width": self.width,
            "height": self.height,
            "min_m": round(self.min_m, 4),
            "max_m": round(self.max_m, 4),
            "mean_m": round(self.mean_m, 4),
            "median_m": round(self.median_m, 4),
            "p10_m": round(self.p10_m, 4),
            "p90_m": round(self.p90_m, 4),
            "std_m": round(self.std_m, 4),
            "relief_m": round(self.relief_m, 4),
            "depth_tensor_shape": (
                list(self.depth_tensor.shape) if self.depth_tensor is not None else None
            ),
        }


def _resolve_device(preferred: str | None) -> torch.device:
    if preferred:
        return torch.device(preferred)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_image(image: ImageInput) -> Image.Image:
    """Coerce any supported input into an RGB :class:`PIL.Image`."""
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image)).convert("RGB")
    if isinstance(image, (str, Path)):
        return Image.open(image).convert("RGB")
    # Assume a file-like object (has .read()).
    return ImageOps.exif_transpose(Image.open(image)).convert("RGB")


class MacroVisionPredictor:
    """Loads the depth, regression, and (optional) VLM models and predicts macros.

    Construct once and reuse — model loading is expensive. Prefer
    :func:`get_predictor` over instantiating directly so the instance is shared
    across the process.
    """

    def __init__(
        self,
        *,
        device: str | None = None,
        load_vlm: bool = True,
        vlm_backend: VLMBackendName = "openai",
        vlm_model: str = "Qwen/Qwen3-VL-8B-Instruct",
        openai_model: str = "gpt-4o-mini",
        openai_api_key: str | None = None,
    ) -> None:
        self.device = _resolve_device(device)
        logger.info("Loading MacroVision models on device=%s", self.device)

        self._load_regressor()
        self._load_zoedepth()

        # VLM step for "combined" mode. Only the selected backend is loaded.
        self.vlm_backend_name = vlm_backend
        self.vlm_model_id = vlm_model
        # A non-Qwen backend (e.g. OpenAI) satisfying the VLMBackend protocol.
        self.vlm_backend: VLMBackend | None = None
        if not load_vlm:
            logger.info("VLM disabled — 'combined' mode will fall back to regression")
        elif vlm_backend == "openai":
            logger.info("Using the OpenAI VLM backend (model=%s)", openai_model)
            self._load_openai_vlm(openai_model, openai_api_key)
        else:
            logger.warning("Local Qwen VLM is no longer supported; using regression fallback")

    # ── model loading ────────────────────────────────────────────────────────
    def _load_regressor(self) -> None:
        checkpoint = torch.load(
            _REGRESSOR_CKPT, map_location=self.device, weights_only=False
        )
        ckpt = checkpoint["model"]
        model = NutritionRegressor().to(self.device)
        key_map = build_key_map(list(ckpt.keys()), list(model.state_dict().keys()))
        remapped = {key_map[k]: v for k, v in ckpt.items()}
        model.load_state_dict(remapped, strict=True)
        model.eval()

        self.regressor = model
        self.label_mean = checkpoint["label_mean"].to(self.device)
        self.label_std = checkpoint["label_std"].to(self.device)

    def _load_zoedepth(self) -> None:
        self.zoe_processor = ZoeDepthImageProcessor.from_pretrained("Intel/zoedepth-nyu")
        self.zoe_model = ZoeDepthForDepthEstimation.from_pretrained(
            str(_ZOEDEPTH_DIR)
        ).to(self.device)
        self.zoe_model.eval()

    def _load_vlm(self, vlm_model: str) -> None:
        # Retained only for compatibility with old direct callers. The normal
        # predictor never dispatches to local Qwen; API VLM is the supported path.
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        # 4-bit quantization is CUDA-only (bitsandbytes). Elsewhere, load in
        # bf16 and let device_map place it.
        kwargs: dict = {"device_map": "auto"}
        if self.device.type == "cuda":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
        else:
            logger.warning(
                "device=%s is not CUDA — loading VLM without 4-bit quantization "
                "(this needs a lot of memory)",
                self.device,
            )
            kwargs["dtype"] = torch.bfloat16

        self.qwen_model = Qwen3VLForConditionalGeneration.from_pretrained(
            vlm_model, **kwargs
        )
        self.qwen_processor = AutoProcessor.from_pretrained(
            vlm_model, min_pixels=256 * 28 * 28, max_pixels=512 * 28 * 28
        )

    def _load_openai_vlm(self, openai_model: str, api_key: str | None) -> None:
        # Imported here so the openai dependency is only touched for this backend.
        from app.ai.MacroVision.openai_vlm import OpenAIVLMGenerator

        self.vlm_backend = OpenAIVLMGenerator(api_key=api_key, model=openai_model)

    @property
    def vlm_available(self) -> bool:
        return self.vlm_backend is not None

    # ── shared preprocessing ──────────────────────────────────────────────────

    def depth_meters(self, img: Image.Image) -> np.ndarray:
        """Run ZoeDepth once and return the raw metric depth map, ``(H, W)`` in metres.

        Everything depth-derived goes through here — the regressor's input
        tensor, the false-colour render, and the standalone depth tool — so a
        pipeline that needs two of them pays for inference once.
        """
        zoe_inputs = self.zoe_processor(images=img, return_tensors="pt").to(self.device)
        with torch.no_grad():
            depth_m = self.zoe_model(**zoe_inputs).predicted_depth  # (1, H, W) meters
        depth_np = depth_m.squeeze().cpu().numpy()
        logger.debug("Depth range: %.3fm - %.3fm", depth_np.min(), depth_np.max())
        return depth_np

    def depth_to_image(
        self, img: Image.Image, depth_np: np.ndarray | None = None
    ) -> Image.Image:
        """Render a metric depth map as a false-colour image, sized like ``img``.

        Pass ``depth_np`` to reuse a map you already computed; omit it and this
        runs ZoeDepth itself.
        """
        if depth_np is None:
            depth_np = self.depth_meters(img)

        # Normalize to 0-1 for the colormap.
        d_min, d_max = depth_np.min(), depth_np.max()
        depth_norm = (depth_np - d_min) / (d_max - d_min + 1e-8)

        # inferno/plasma read well for depth — monotonic in lightness, so near
        # and far stay distinguishable after the VLM downsamples it.
        colored = cm.inferno(depth_norm)  # (H, W, 4) RGBA floats in [0,1]
        colored_uint8 = (colored[:, :, :3] * 255).astype(np.uint8)  # drop alpha
        return Image.fromarray(colored_uint8).resize(img.size)

    def depth_tensor(
        self, img: Image.Image, depth_np: np.ndarray | None = None
    ) -> torch.Tensor:
        """Shape a metric depth map into the regressor's ``(1,3,224,224)`` input."""
        if depth_np is None:
            depth_np = self.depth_meters(img)

        depth_t = torch.tensor(depth_np, dtype=torch.float32).unsqueeze(0)  # (1,H,W)
        depth_t = transforms.functional.resize(depth_t, [224, 224])  # (1,224,224)
        depth_t = (depth_t - depth_t.mean()) / (depth_t.std() + 1e-8)  # standardize
        return depth_t.repeat(3, 1, 1).unsqueeze(0).to(self.device)  # (1,3,224,224)

    def _regress(
        self, img: Image.Image, depth_np: np.ndarray | None = None
    ) -> tuple[float, float, float, float]:
        """Run the ViT regressor; returns (calories, fat, carbs, protein) in real units."""
        depth_t = self.depth_tensor(img, depth_np)
        rgb_t = _rgb_transform(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            pred_norm = self.regressor(rgb_t, depth_t)
            pred_real = pred_norm * (self.label_std + 1e-8) + self.label_mean
        calories, fat, carbs, protein = pred_real[0].cpu().tolist()
        values = np.nan_to_num([calories, fat, carbs, protein], nan=0.0, posinf=0.0, neginf=0.0)
        calories, fat, carbs, protein = [max(0.0, float(value)) for value in values]
        return calories, fat, carbs, protein

    def _vlm_estimate(self, img: Image.Image, depth:Image.Image=None, hints:NutritionHints | None = None) -> dict | None:
        """Ask the VLM for per-100g nutrition + portion estimate. None on failure.

        Routes to the configured API-backed VLM. Unsupported or unavailable
        VLM configuration returns ``None`` so the caller can use regression.
        """
        hints = hints or {}
        if self.vlm_backend is not None:
            return self.vlm_backend.estimate(img, depth, hints)
        # Local Qwen is intentionally unsupported: the API-backed VLM is the
        # only configured path, and regression is the safe fallback.
        return None

    def _qwen_estimate(self, img: Image.Image) -> dict | None:
        """Local Qwen3-VL per-100g estimate."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img},
                    {"type": "text", "text": VLM_PROMPT},
                ],
            }
        ]

        text = self.qwen_processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, _ = process_vision_info(messages)
        inputs = self.qwen_processor(
            text=[text], images=image_inputs, return_tensors="pt"
        ).to(self.device)

        with torch.no_grad():
            output = self.qwen_model.generate(
                **inputs, max_new_tokens=256, do_sample=False
            )
        response = self.qwen_processor.decode(
            output[0][len(inputs.input_ids[0]) :], skip_special_tokens=True
        )
        return parse_vlm_json(response)

    # ── public prediction API ─────────────────────────────────────────────────
    def predict_regression(self, image: ImageInput) -> NutritionPrediction:
        """Regression-only prediction (no VLM)."""
        img = load_image(image)
        calories, fat, carbs, protein = self._regress(img)
        return NutritionPrediction(
            calories=calories,
            fat=fat,
            carbs=carbs,
            protein=protein,
            source="regression",
        )

    def predict_combined(self, image: ImageInput, hints: NutritionHints | None = None) -> NutritionPrediction:
        """VLM per-100g estimate scaled by the regressor's portion signal.

        Falls back to regression-only if the VLM is unavailable or its output
        can't be parsed.If ``hints["known_weight_g"]`` is set, that weight is
        used directly as the portion instead of the VLM's own estimate, and
        the regressor's portion-scale correction is skipped since the true
        portion is already known. Any other keys in ``hints`` are passed to
        the VLM as freeform context but don't otherwise affect this method.
        """
        hints = hints or {}
        img = load_image(image)
        # One ZoeDepth pass feeds both the VLM's depth view and the regressor.
        depth_np = self.depth_meters(img)
        depth_image = self.depth_to_image(img, depth_np)
        vlm = self._vlm_estimate(img, depth_image, hints)
        reg_cal, reg_fat, reg_carbs, reg_protein = self._regress(img, depth_np)
        print("PREDICT COMBINE", vlm, reg_cal)
        if not vlm:
            return NutritionPrediction(
                calories=reg_cal,
                fat=reg_fat,
                carbs=reg_carbs,
                protein=reg_protein,
                dish="unknown",
                portion_scale=1.0,
                source="regression",
            )

        base = vlm["base_nutrition_per_100g"]
        known_weight_g = hints.get("known_weight_g")
        portion = known_weight_g or vlm["estimated_portion_g"]
        # VLM gives per-100g nutrition; scale to the estimated portion.
        vlm_cal = base["calories"] * portion / 100
        vlm_fat = base["fat"] * portion / 100
        vlm_carbs = base["carbs"] * portion / 100
        vlm_protein = base["protein"] * portion / 100

        # Use the regressor's calorie estimate as a portion-scale correction:
        # if it reads ~2x the VLM's calories, the portion was likely underestimated.
        if known_weight_g is not None:
            portion_scale = 1.0
        elif vlm_cal > 0:
            portion_scale = float(np.clip(reg_cal / vlm_cal, 0.5, 2.0))
        else:
            portion_scale = 1.0
        return NutritionPrediction(
            calories=vlm_cal * portion_scale,
            fat=vlm_fat * portion_scale,
            carbs=vlm_carbs * portion_scale,
            protein=vlm_protein * portion_scale,
            dish=vlm["dish_name"],
            portion_scale=portion_scale,
            source="combined",
        )

    def estimate_depth(
        self,
        image: ImageInput,
        *,
        render_image: bool = True,
        include_tensor: bool = True,
    ) -> DepthEstimate:
        """Depth only — run ZoeDepth and skip the nutrition models entirely.

        Useful on its own for judging portion size and camera distance, and for
        checking *why* a macro prediction looks wrong: a depth map that reads
        flat usually means the photo was taken straight down with no relief for
        the model to work from.
        """
        img = load_image(image)
        depth_np = self.depth_meters(img)
        return DepthEstimate(
            width=int(depth_np.shape[1]),
            height=int(depth_np.shape[0]),
            min_m=float(depth_np.min()),
            max_m=float(depth_np.max()),
            mean_m=float(depth_np.mean()),
            median_m=float(np.median(depth_np)),
            p10_m=float(np.percentile(depth_np, 10)),
            p90_m=float(np.percentile(depth_np, 90)),
            std_m=float(depth_np.std()),
            depth_map=depth_np,
            depth_tensor=self.depth_tensor(img, depth_np) if include_tensor else None,
            depth_image=self.depth_to_image(img, depth_np) if render_image else None,
        )

    async def estimate_depth_async(
        self,
        image: ImageInput,
        *,
        render_image: bool = True,
        include_tensor: bool = True,
    ) -> DepthEstimate:
        """Async wrapper around :meth:`estimate_depth` (runs in a worker thread)."""
        return await asyncio.to_thread(
            self.estimate_depth,
            image,
            render_image=render_image,
            include_tensor=include_tensor,
        )

    def predict(
        self, image: ImageInput, *, mode: PredictionMode = "combined", hints:NutritionHints | None = None
    ) -> NutritionPrediction:
        """
        Predict macros. ``mode`` is "combined" (default) or "regression".

        ``hints`` is an open dict of anything the caller already knows about
        this meal (e.g. {"known_weight_g": 250, "known_ingredients": [...]}).
        Keys are optional and orchestrator-defined; unrecognized keys are
        still forwarded to the VLM as freeform context.
        """
        hints = hints or {}
        if mode == "regression":
            return self.predict_regression(image)
        return self.predict_combined(image, hints)

    async def predict_async(
        self, image: ImageInput, *, mode: PredictionMode = "combined", hints: NutritionHints | None = None
    ) -> NutritionPrediction:
        """Async wrapper for use in FastAPI routes / WebSocket handlers.

        Inference is blocking (GPU/CPU-bound), so it runs in a worker thread to
        avoid stalling the event loop. Read image bytes before calling if the
        source is a file-like object tied to the request.
        """
        hints = hints or {}
        return await asyncio.to_thread(self.predict, image, mode=mode, hints=hints)


@lru_cache
def get_predictor() -> MacroVisionPredictor:
    """Process-wide cached predictor, configured from settings.

    Call this once at startup (e.g. in the FastAPI lifespan) to warm the models,
    then reuse the returned instance for every request.
    """
    settings = get_settings()
    return MacroVisionPredictor(
        device=settings.macrovision_device,
        load_vlm=settings.macrovision_load_vlm,
        vlm_backend=settings.macrovision_vlm_backend,
        vlm_model=settings.macrovision_vlm_model,
        openai_model=settings.macrovision_openai_model,
        openai_api_key=settings.openai_api_key,
    )
