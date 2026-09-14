"""Small dependency-free multinomial Naive Bayes intent classifier."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .schemas import Intent

_TOKEN = re.compile(r"[\w']+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    words = [match.group(0).casefold() for match in _TOKEN.finditer(text)]
    return words + [f"{a}::{b}" for a, b in zip(words, words[1:])]


@dataclass(frozen=True)
class Prediction:
    label: Intent
    probabilities: dict[Intent, float]


class IntentClassifier:
    """Serializable local classifier; training and inference share one implementation."""

    artifact_version = 1

    def __init__(self, *, alpha: float = 1.0) -> None:
        if alpha <= 0:
            raise ValueError("alpha must be greater than zero")
        self.alpha = alpha
        self.class_counts: Counter[str] = Counter()
        self.token_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.total_tokens: Counter[str] = Counter()
        self.vocabulary: set[str] = set()

    @property
    def is_fitted(self) -> bool:
        return bool(self.class_counts)

    def fit(self, examples: list[tuple[str, Intent]]) -> "IntentClassifier":
        if not examples:
            raise ValueError("at least one training example is required")
        self.class_counts.clear()
        self.token_counts.clear()
        self.total_tokens.clear()
        self.vocabulary.clear()
        for text, intent in examples:
            label = intent.value
            tokens = tokenize(text)
            self.class_counts[label] += 1
            self.token_counts[label].update(tokens)
            self.total_tokens[label] += len(tokens)
            self.vocabulary.update(tokens)
        return self

    def predict(self, text: str) -> Prediction:
        if not self.is_fitted:
            raise RuntimeError("classifier is not fitted")
        tokens = tokenize(text)
        total_examples = sum(self.class_counts.values())
        vocabulary_size = max(len(self.vocabulary), 1)
        scores: dict[Intent, float] = {}
        for raw_label, class_count in self.class_counts.items():
            score = math.log(class_count / total_examples)
            denominator = self.total_tokens[raw_label] + self.alpha * vocabulary_size
            counts = self.token_counts[raw_label]
            for token in tokens:
                score += math.log((counts[token] + self.alpha) / denominator)
            scores[Intent(raw_label)] = score
        peak = max(scores.values())
        weights = {label: math.exp(score - peak) for label, score in scores.items()}
        total = sum(weights.values())
        probabilities = {label: weight / total for label, weight in weights.items()}
        label = max(probabilities, key=probabilities.__getitem__)
        return Prediction(label=label, probabilities=probabilities)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "artifact_version": self.artifact_version,
            "alpha": self.alpha,
            "class_counts": dict(self.class_counts),
            "token_counts": {label: dict(counts) for label, counts in self.token_counts.items()},
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "IntentClassifier":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("artifact_version") != cls.artifact_version:
            raise ValueError("unsupported intent-classifier artifact version")
        model = cls(alpha=float(payload["alpha"]))
        class_counts = payload["class_counts"]
        token_counts = payload["token_counts"]
        if not isinstance(class_counts, dict) or not isinstance(token_counts, dict):
            raise ValueError("invalid intent-classifier count tables")
        for label, count in class_counts.items():
            Intent(label)
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ValueError("class counts must be positive integers")
            if label not in token_counts:
                raise ValueError(f"missing token counts for class {label!r}")
            model.class_counts[label] = count
        for label, counts in token_counts.items():
            if label not in class_counts or not isinstance(counts, dict):
                raise ValueError(f"unexpected token-count class {label!r}")
            if any(
                not isinstance(token, str)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count <= 0
                for token, count in counts.items()
            ):
                raise ValueError(f"invalid token counts for class {label!r}")
            model.token_counts[label].update(counts)
            model.total_tokens[label] = sum(counts.values())
            model.vocabulary.update(counts)
        if not model.is_fitted:
            raise ValueError("intent-classifier artifact has no classes")
        return model
