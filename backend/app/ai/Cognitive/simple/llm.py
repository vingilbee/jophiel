"""The teacher environment's model calls: one async OpenAI client, bounded concurrency, a usage log.

Offline only (data generation); nothing here is on a live chat path.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

DEFAULT_MODEL = 'gpt-6-luna'


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    by_stage: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, stage: str, usage: Any, seconds: float) -> None:
        self.calls += 1
        self.seconds += seconds
        p = getattr(usage, 'prompt_tokens', 0) or 0
        c = getattr(usage, 'completion_tokens', 0) or 0
        self.prompt_tokens += p
        self.completion_tokens += c
        row = self.by_stage.setdefault(stage, dict(calls=0, prompt_tokens=0, completion_tokens=0, seconds=0.0))
        row['calls'] += 1
        row['prompt_tokens'] += p
        row['completion_tokens'] += c
        row['seconds'] += seconds

    def to_dict(self) -> dict[str, Any]:
        return dict(calls=self.calls, failures=self.failures, prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens, seconds=round(self.seconds, 1),
                    by_stage={k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in self.by_stage.items()})


class LLM:
    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, concurrency: int = 24, timeout: float = 90.0):
        self.client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        self.model = model
        self.gate = asyncio.Semaphore(concurrency)
        self.usage = Usage()

    async def _create(self, stage: str, **params: Any) -> Any:
        delay = 2.0
        for attempt in range(6):
            async with self.gate:
                started = time.monotonic()
                try:
                    result = await self.client.chat.completions.create(model=self.model, store=False, **params)
                    self.usage.add(stage, result.usage, time.monotonic() - started)
                    return result
                except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                    error: Exception = exc
                except APIStatusError as exc:
                    if exc.status_code < 500:
                        self.usage.failures += 1
                        raise
                    error = exc
            self.usage.failures += 1
            if attempt == 5:
                raise error
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 30.0)
        raise RuntimeError('unreachable')

    @staticmethod
    def _params(reasoning: str | None, temperature: float | None) -> dict[str, Any]:
        params: dict[str, Any] = {'reasoning_effort': reasoning or 'none'}
        if reasoning in (None, 'none') and temperature is not None:
            # Reasoning runs accept only the default temperature.
            params['temperature'] = temperature
        return params

    async def text(self, stage: str, system: str, user: str, *, reasoning: str | None = None,
                   temperature: float | None = 1.0, max_tokens: int = 1200) -> str:
        result = await self._create(stage, messages=[{'role': 'system', 'content': system},
                                                     {'role': 'user', 'content': user}],
                                    max_completion_tokens=max_tokens, **self._params(reasoning, temperature))
        return (result.choices[0].message.content or '').strip()

    async def json(self, stage: str, system: str, user: str, schema: dict[str, Any], *,
                   reasoning: str | None = None, temperature: float | None = 0.0, max_tokens: int = 1500) -> dict[str, Any]:
        result = await self._create(
            stage, messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            response_format={'type': 'json_schema', 'json_schema': {'name': stage, 'strict': True, 'schema': schema}},
            max_completion_tokens=max_tokens, **self._params(reasoning, temperature))
        return json.loads(result.choices[0].message.content or '{}')

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        async with self.gate:
            result = await self.client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in result.data]
