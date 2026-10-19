"""The frozen text encoder: small, multilingual, local; vectors cached per text."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

MODEL_NAME = 'intfloat/multilingual-e5-small'
DIM = 384


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


class Encoder:
    def __init__(self, cache_path: Path | None = None, device: str | None = None):
        self.cache_path = cache_path
        self.cache: dict[str, np.ndarray] = {}
        if cache_path and cache_path.exists():
            data = np.load(cache_path)
            self.cache = {k: data[k] for k in data.files}
        self._model = None
        self.device = device

    def _load(self):
        if self._model is None:
            import torch
            from transformers import AutoModel, AutoTokenizer
            self.device = self.device or ('mps' if torch.backends.mps.is_available() else 'cpu')
            self._tok = AutoTokenizer.from_pretrained(MODEL_NAME)
            self._model = AutoModel.from_pretrained(MODEL_NAME).to(self.device).eval()
        return self._model

    def encode(self, texts: list[str], batch: int = 64) -> np.ndarray:
        missing = sorted({t for t in texts if _key(t) not in self.cache})
        if missing:
            import torch
            model = self._load()
            for i in range(0, len(missing), batch):
                chunk = missing[i:i + batch]
                tokens = self._tok([f'query: {t}' for t in chunk], padding=True, truncation=True, max_length=256,
                                   return_tensors='pt').to(self.device)
                with torch.no_grad():
                    hidden = model(**tokens).last_hidden_state
                    mask = tokens['attention_mask'].unsqueeze(-1).float()
                    pooled = (hidden * mask).sum(1) / mask.sum(1)
                    pooled = torch.nn.functional.normalize(pooled, dim=-1).cpu().numpy()
                for text, vector in zip(chunk, pooled, strict=True):
                    self.cache[_key(text)] = vector.astype(np.float32)
            self.save()
        return np.stack([self.cache[_key(t)] for t in texts]) if texts else np.zeros((0, DIM), np.float32)

    def save(self) -> None:
        if self.cache_path:
            np.savez(self.cache_path, **self.cache)
