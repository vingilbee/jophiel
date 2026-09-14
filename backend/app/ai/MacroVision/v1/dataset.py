"""Manifest-driven dataset for MacroVision v1 fine-tuning.

The CSV format intentionally works with Nutrition5k and private calibration data.
Required columns: image_path, mass_g, split. Optional columns correspond to the
numeric features in models.NUMERIC_FEATURES plus volume_ml and dish_id.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision.models import ResNet18_Weights

from .models import NUMERIC_FEATURES


class MealMassDataset(Dataset):
    def __init__(self, manifest: str | Path, *, split: str, root: str | Path | None = None) -> None:
        self.manifest = Path(manifest)
        self.root = Path(root) if root else self.manifest.parent
        with self.manifest.open(newline="", encoding="utf-8") as handle:
            self.rows = [row for row in csv.DictReader(handle) if row.get("split", "train") == split]
        if not self.rows:
            raise ValueError(f"manifest has no rows for split={split!r}")
        self.transform = ResNet18_Weights.DEFAULT.transforms()

    def __len__(self) -> int:
        return len(self.rows)

    @staticmethod
    def _number(row: dict[str, str], key: str, default: float = 0.0) -> float:
        value = row.get(key, "")
        return float(value) if value not in (None, "") else default

    def __getitem__(self, index: int) -> dict[str, object]:
        row = self.rows[index]
        path = Path(row["image_path"])
        if not path.is_absolute():
            path = self.root / path
        with Image.open(path) as opened:
            image = self.transform(opened.convert("RGB"))
        numeric = torch.tensor([self._number(row, key) for key in NUMERIC_FEATURES], dtype=torch.float32)
        volume = self._number(row, "volume_ml", float("nan"))
        return {
            "image": image,
            "numeric": numeric,
            "mass_g": torch.tensor(self._number(row, "mass_g"), dtype=torch.float32),
            "volume_ml": torch.tensor(volume, dtype=torch.float32),
            "dish_id": row.get("dish_id", path.stem),
        }


def feature_statistics(dataset: MealMassDataset) -> tuple[torch.Tensor, torch.Tensor]:
    values = torch.stack([dataset[i]["numeric"] for i in range(len(dataset))])
    mean = values.mean(0)
    std = values.std(0).clamp_min(1e-6)
    return mean, std


def write_manifest(rows: list[dict[str, object]], destination: str | Path) -> None:
    """Write rows with a stable schema; useful from the Kaggle preparation cell."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = ["image_path", "mass_g", "volume_ml", "split", "dish_id", *NUMERIC_FEATURES]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
