"""Neural-net definitions for the MacroVision nutrition regressor.

This module is pure model code — the ``nn.Module`` classes and the helper that
remaps the training checkpoint's parameter names onto them. It has no side
effects at import time (no weight loading); the heavy lifting lives in
:mod:`app.ai.MacroVision.predictor`.

Architecture
------------
The regressor takes two 224x224x3 tensors — the RGB food image and a metric
depth map (from fine-tuned ZoeDepth, repeated across 3 channels) — encodes each
with a ViT-base, fuses them with cross-attention (RGB queries, depth keys /
values), mean-pools the fused tokens, and regresses 4 values:
``[calories, fat, carbs, protein]`` in normalized space (see ``label_mean`` /
``label_std`` in the checkpoint).
"""
from __future__ import annotations

import importlib
import logging
import re

import torch.nn as nn
import transformers.models.vit.modeling_vit as _vit_module

logger = logging.getLogger(__name__)

# Force the real HuggingFace ViTModel, bypassing any local module shadowing.
_ViTModel = _vit_module.ViTModel


class CrossAttentionFusion(nn.Module):
    """Fuse RGB and depth token sequences via multi-head cross-attention.

    RGB tokens attend over depth tokens; the attended result is added back to
    the RGB tokens (residual) and layer-normed.
    """

    def __init__(self, dim: int = 768, heads: int = 8) -> None:
        super().__init__()
        self.dim = dim
        self.attn = nn.MultiheadAttention(
            embed_dim=dim, num_heads=heads, batch_first=True
        )
        self.norm = nn.LayerNorm(dim)

    def forward(self, rgb_tokens, depth_tokens):
        fused, _ = self.attn(query=rgb_tokens, key=depth_tokens, value=depth_tokens)
        return self.norm(rgb_tokens + fused)


class NutritionRegressor(nn.Module):
    """RGB + depth ViT encoders → cross-attention fusion → macro regression head."""

    def __init__(self) -> None:
        super().__init__()
        self.rgb_vit = _ViTModel.from_pretrained("google/vit-base-patch16-224")
        self.depth_vit = _ViTModel.from_pretrained("google/vit-base-patch16-224")

        dim = self.rgb_vit.config.hidden_size  # 768
        self.fusion = CrossAttentionFusion(dim=dim)
        self.regressor = nn.Sequential(
            nn.Linear(dim, 512),
            nn.GELU(),
            nn.Dropout(0.2),
            nn.Linear(512, 128),
            nn.GELU(),
            nn.Linear(128, 4),
        )

    def forward(self, rgb, depth):
        # Drop the [CLS] token (index 0); keep patch tokens only.
        rgb_tokens = self.rgb_vit(rgb).last_hidden_state[:, 1:, :]
        depth_tokens = self.depth_vit(depth).last_hidden_state[:, 1:, :]
        fused = self.fusion(rgb_tokens, depth_tokens)
        pooled = fused.mean(dim=1)
        return self.regressor(pooled)


# Checkpoint parameter names use the stock HF ViT layout
# (``rgb_vit.encoder.layer.N.attention.attention.query.weight``), while the ViT
# build in this environment exposes a flattened layout
# (``rgb_vit.layers.N.attention.q_proj.weight``). These regex rules bridge them.
_KEY_MAP_RULES: dict[str, str] = {
    # attention
    r"(.+)\.encoder\.layer\.(\d+)\.attention\.attention\.query\.(.+)": r"\1.layers.\2.attention.q_proj.\3",
    r"(.+)\.encoder\.layer\.(\d+)\.attention\.attention\.key\.(.+)": r"\1.layers.\2.attention.k_proj.\3",
    r"(.+)\.encoder\.layer\.(\d+)\.attention\.attention\.value\.(.+)": r"\1.layers.\2.attention.v_proj.\3",
    r"(.+)\.encoder\.layer\.(\d+)\.attention\.output\.dense\.(.+)": r"\1.layers.\2.attention.o_proj.\3",
    # MLP
    r"(.+)\.encoder\.layer\.(\d+)\.intermediate\.dense\.(.+)": r"\1.layers.\2.mlp.fc1.\3",
    r"(.+)\.encoder\.layer\.(\d+)\.output\.dense\.(.+)": r"\1.layers.\2.mlp.fc2.\3",
    # layernorms
    r"(.+)\.encoder\.layer\.(\d+)\.layernorm_before\.(.+)": r"\1.layers.\2.layernorm_before.\3",
    r"(.+)\.encoder\.layer\.(\d+)\.layernorm_after\.(.+)": r"\1.layers.\2.layernorm_after.\3",
}


def build_key_map(ckpt_keys, model_keys) -> dict[str, str]:
    """Map checkpoint parameter names onto the current model's parameter names.

    Returns a ``{checkpoint_key: model_key}`` dict. Logs a warning if any mapped
    key is absent from the model's state dict (a sign the layouts diverged).
    """
    model_keys = set(model_keys)
    result: dict[str, str] = {}
    for ckpt_key in ckpt_keys:
        new_key = ckpt_key
        for pattern, replacement in _KEY_MAP_RULES.items():
            new_key, n = re.subn(pattern, replacement, new_key)
            if n:
                break
        result[ckpt_key] = new_key

    missing = [v for v in result.values() if v not in model_keys]
    if missing:
        logger.warning("Checkpoint keys not found in model (first 5): %s", missing[:5])
    else:
        logger.info("All %d checkpoint keys mapped successfully", len(result))

    return result


# ViTConfig is re-exported for callers that need the real HF config alongside
# the un-shadowed ViTModel above.
ViTConfig = importlib.import_module("transformers").ViTConfig
