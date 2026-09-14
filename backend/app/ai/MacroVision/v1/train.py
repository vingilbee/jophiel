"""Kaggle/local CLI for fine-tuning the shared MacroVision v1 mass model."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from .dataset import MealMassDataset, feature_statistics
from .models import MassFusionNet, multitask_loss


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _run_epoch(model, loader, mean, std, device, optimizer=None, volume_weight=0.25):
    training = optimizer is not None
    model.train(training)
    totals = {"loss": 0.0, "mass_abs": 0.0, "mass_pct": 0.0, "count": 0}
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for batch in loader:
            image = batch["image"].to(device)
            numeric = (batch["numeric"].to(device) - mean) / std
            mass = batch["mass_g"].to(device)
            volume = batch["volume_ml"].to(device)
            pred = model(image, numeric)
            loss, _ = multitask_loss(pred, mass, volume, volume_weight=volume_weight)
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                optimizer.step()
            estimate = torch.expm1(pred["log_mass"]).clamp_min(0)
            count = mass.numel()
            totals["loss"] += float(loss) * count
            totals["mass_abs"] += float((estimate - mass).abs().sum())
            totals["mass_pct"] += float(((estimate - mass).abs() / mass.clamp_min(1)).sum())
            totals["count"] += count
    n = max(1, totals.pop("count"))
    return {"loss": totals["loss"] / n, "mass_mae_g": totals["mass_abs"] / n,
            "mass_mape": 100 * totals["mass_pct"] / n}


def train(args: argparse.Namespace) -> Path:
    seed_everything(args.seed)
    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))
    train_set = MealMassDataset(args.manifest, split="train", root=args.data_root)
    val_set = MealMassDataset(args.manifest, split="val", root=args.data_root)
    mean, std = (value.to(device) for value in feature_statistics(train_set))
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,
                              num_workers=args.workers, pin_memory=device.type == "cuda")
    val_loader = DataLoader(val_set, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.workers, pin_memory=device.type == "cuda")
    model = MassFusionNet(pretrained=not args.no_pretrained,
                          freeze_backbone=args.freeze_backbone).to(device)
    optimizer = torch.optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()),
                                  lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=3)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    best, best_path, history = float("inf"), output / "macrovision_v1_best.pt", []
    for epoch in range(1, args.epochs + 1):
        train_metrics = _run_epoch(model, train_loader, mean, std, device, optimizer, args.volume_weight)
        val_metrics = _run_epoch(model, val_loader, mean, std, device, None, args.volume_weight)
        scheduler.step(val_metrics["mass_mae_g"])
        record = {"epoch": epoch, "train": train_metrics, "val": val_metrics,
                  "lr": optimizer.param_groups[0]["lr"]}
        history.append(record)
        print(json.dumps(record))
        if val_metrics["mass_mae_g"] < best:
            best = val_metrics["mass_mae_g"]
            torch.save({
                "model": model.state_dict(), "feature_mean": mean.cpu(),
                "feature_std": std.cpu(), "epoch": epoch, "metrics": val_metrics,
                "args": vars(args), "format_version": 1,
            }, best_path)
    (output / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    return best_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--data-root")
    parser.add_argument("--output-dir", default="outputs/macrovision_v1")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--volume-weight", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device")
    parser.add_argument("--freeze-backbone", action="store_true")
    parser.add_argument("--no-pretrained", action="store_true")
    return parser


if __name__ == "__main__":
    print(train(build_parser().parse_args()))
