"""Train an intent artifact offline.

Run from ``backend``:
    python -m app.ai.Cognitive.system.training.train_intent DATA.jsonl ARTIFACT.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from ..classifier import IntentClassifier
from .dataset import load_examples


def train(dataset_path: Path, artifact_path: Path, *, alpha: float = 1.0) -> dict[str, int]:
    examples = load_examples(dataset_path)
    model = IntentClassifier(alpha=alpha).fit([(item.text, item.intent) for item in examples])
    model.save(artifact_path)
    return dict(sorted(Counter(item.intent.value for item in examples).items()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the local cognitive intent classifier")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--alpha", type=float, default=1.0)
    args = parser.parse_args()
    counts = train(args.dataset, args.artifact, alpha=args.alpha)
    print(f"wrote {args.artifact} with class counts: {counts}")


if __name__ == "__main__":
    main()
