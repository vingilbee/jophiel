"""Deterministic holdout evaluation for a reviewed intent dataset."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from ..classifier import IntentClassifier
from .dataset import IntentExample, load_examples


def _split(
    examples: list[IntentExample], *, test_fraction: float, seed: int
) -> tuple[list[IntentExample], list[IntentExample]]:
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between zero and one")
    grouped: dict[str, list[IntentExample]] = defaultdict(list)
    for example in examples:
        grouped[example.intent.value].append(example)

    train: list[IntentExample] = []
    test: list[IntentExample] = []
    randomizer = random.Random(seed)
    for label, items in sorted(grouped.items()):
        randomizer.shuffle(items)
        if len(items) < 2:
            raise ValueError(f"intent {label!r} needs at least two examples for holdout evaluation")
        test_size = min(len(items) - 1, max(1, round(len(items) * test_fraction)))
        test.extend(items[:test_size])
        train.extend(items[test_size:])
    return train, test


def evaluate(
    dataset_path: Path, *, test_fraction: float = 0.2, seed: int = 42, alpha: float = 1.0
) -> dict[str, object]:
    examples = load_examples(dataset_path)
    train, test = _split(examples, test_fraction=test_fraction, seed=seed)
    model = IntentClassifier(alpha=alpha).fit([(item.text, item.intent) for item in train])
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    correct = 0
    for item in test:
        predicted = model.predict(item.text).label
        confusion[item.intent.value][predicted.value] += 1
        correct += int(predicted is item.intent)

    per_class: dict[str, dict[str, float | int]] = {}
    labels = sorted({item.intent.value for item in examples})
    for label in labels:
        true_positive = confusion[label][label]
        support = sum(confusion[label].values())
        predicted_count = sum(row[label] for row in confusion.values())
        precision = true_positive / predicted_count if predicted_count else 0.0
        recall = true_positive / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }
    return {
        "seed": seed,
        "train_size": len(train),
        "test_size": len(test),
        "accuracy": round(correct / len(test), 6),
        "class_counts": dict(sorted(Counter(item.intent.value for item in examples).items())),
        "per_class": per_class,
        "confusion": {label: dict(sorted(row.items())) for label, row in sorted(confusion.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the local cognitive intent classifier")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--alpha", type=float, default=1.0)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.dataset, test_fraction=args.test_fraction, seed=args.seed, alpha=args.alpha), indent=2))


if __name__ == "__main__":
    main()
