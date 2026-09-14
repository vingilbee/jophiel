"""Trainable mass/volume fusion model used by every capture tier."""
from __future__ import annotations

import math

import torch
import torch.nn as nn
from torchvision.models import ResNet18_Weights, resnet18

# Order is checkpoint-stable and shared with the dataset and predictor.
NUMERIC_FEATURES = (
    "geometry_volume_ml", "geometry_confidence", "median_depth_m", "relief_m",
    "food_area_fraction", "density_prior_g_ml", "tier_ar", "tier_rgbd",
    "tier_monocular", "has_metric_geometry",
)


class MassFusionNet(nn.Module):
    """RGB semantics + explicit geometric measurements -> mass distribution.

    Outputs log mass, log volume, and log standard deviation. Log-space targets
    make errors on small and large meals comparable and keep predictions positive.
    """

    def __init__(self, *, pretrained: bool = True, freeze_backbone: bool = False) -> None:
        super().__init__()
        weights = ResNet18_Weights.DEFAULT if pretrained else None
        backbone = resnet18(weights=weights)
        image_dim = backbone.fc.in_features
        backbone.fc = nn.Identity()
        self.image_encoder = backbone
        if freeze_backbone:
            for parameter in self.image_encoder.parameters():
                parameter.requires_grad = False
        self.numeric_encoder = nn.Sequential(
            nn.LayerNorm(len(NUMERIC_FEATURES)), nn.Linear(len(NUMERIC_FEATURES), 64),
            nn.GELU(), nn.Dropout(0.1), nn.Linear(64, 64), nn.GELU(),
        )
        self.head = nn.Sequential(
            nn.Linear(image_dim + 64, 256), nn.GELU(), nn.Dropout(0.2),
            nn.Linear(256, 64), nn.GELU(), nn.Linear(64, 3),
        )

    def forward(self, image: torch.Tensor, numeric: torch.Tensor) -> dict[str, torch.Tensor]:
        output = self.head(torch.cat((self.image_encoder(image), self.numeric_encoder(numeric)), 1))
        return {
            "log_mass": output[:, 0],
            "log_volume": output[:, 1],
            "log_sigma": output[:, 2].clamp(math.log(0.03), math.log(1.5)),
        }


def gaussian_log_loss(pred: dict[str, torch.Tensor], mass_g: torch.Tensor) -> torch.Tensor:
    target = torch.log1p(mass_g.clamp_min(0))
    sigma = pred["log_sigma"].exp()
    return (0.5 * ((pred["log_mass"] - target) / sigma).square() + pred["log_sigma"]).mean()


def multitask_loss(
    pred: dict[str, torch.Tensor], mass_g: torch.Tensor, volume_ml: torch.Tensor,
    *, volume_weight: float = 0.25,
) -> tuple[torch.Tensor, dict[str, float]]:
    mass_loss = gaussian_log_loss(pred, mass_g)
    known_volume = torch.isfinite(volume_ml) & (volume_ml > 0)
    if known_volume.any():
        volume_loss = nn.functional.smooth_l1_loss(
            pred["log_volume"][known_volume], torch.log1p(volume_ml[known_volume])
        )
    else:
        volume_loss = mass_loss.new_zeros(())
    total = mass_loss + volume_weight * volume_loss
    return total, {"mass_nll": float(mass_loss.detach()), "volume_huber": float(volume_loss.detach())}
