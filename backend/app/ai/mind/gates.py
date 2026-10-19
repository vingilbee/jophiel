"""Gates: given one tick, how hard should she think? none / react / light / hard.

Three kinds of client answer the same question with the same level definitions:
- ``ChatGate``: an OpenAI chat model with a strict JSON schema (also the judge,
  with reasoning on, which labels ticks for the comparison).
- ``OpenAIDecisionsGate``: OpenAI's Decisions API (``POST /v1/decisions``).
- ``SystemOneGate``: the TypeSafe "System One" format, served by Jev on
  OpenRouter (``/api/alpha/decisions``) and by Strands Decider running locally
  (``strands-decider serve`` -> ``/v1/systemone``); also TypeSafe directly
  (``api.typesafe.ai/v1/systemone``).

Each call returns a ``GateResult`` and never raises: errors are results too,
because a gate that fails is part of what the comparison measures.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

import httpx

from app.ai.mind.situation import GENERAL, Situation

LEVELS = ("none", "react", "light", "hard")
# General: a source adds its own examples per level (``Situation.examples``).
LEVEL_GUIDE = {
    "none": "Nothing here needs her attention beyond what she is already doing: a routine moment, nothing new "
            "from the user.",
    "react": "Needs an instant, short reaction with no time to think: danger or harm right now, or a moment worth "
             "blurting out.",
    "light": "Worth a short thought or one short line, but no planning: something new and mildly interesting, "
             "something the user did or said, a simple question, a small adjustment.",
    "hard": "Something doesn't fit what she expected and is worth working out, and there is time for it: a result "
            "that contradicts what she was doing, something of hers changed without her doing it, the place isn't "
            "what her plan assumed. Not the user talking (her reply answers that) and not routine progress. Never "
            "under urgent pressure: then it's react, and the thinking waits until after.",
}


def level_guide(situation: Situation = GENERAL) -> dict[str, str]:
    return {level: guide + (f" Here, for example: {situation.examples[level]}." if situation.examples.get(level)
                            else "")
            for level, guide in LEVEL_GUIDE.items()}


def question(situation: Situation = GENERAL) -> str:
    return (f"How hard should Columbina think about this moment? She is an AI companion; right now she is "
            f"{situation.describe()}. Judge the 'Changed since last tick' lines first; the rest is context.")


def instructions(situation: Situation = GENERAL, *, judge: bool = False) -> str:
    text = (f"You are the attention gate of Columbina, an AI companion. Right now she is {situation.describe()}. "
            "Every second you see what changed since the last tick, how much time she has (pressure), and the "
            "whole situation. Decide how hard she should think about this moment:\n"
            + "\n".join(f"- {level}: {guide}" for level, guide in level_guide(situation).items())
            + "\nMost ticks are none. ")
    if judge:
        return text + ("You are labelling ticks to grade faster gates, so take care: pick the level a thoughtful "
                       "person in her place would need here. Answer with the level and a reason of at most 25 words.")
    return text + "Answer with the level and a reason of at most 12 words."


@dataclass
class GateResult:
    gate: str
    level: str | None
    reason: str | None = None
    probabilities: dict | None = None
    latency_ms: float = 0.0
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def capped(level: str | None, pressure: str) -> str | None:
    """Under urgent pressure there is no time for hard thinking: it becomes react.
    (Recordings before 2026-10-14 call that pressure "combat".)"""
    return "react" if level == "hard" and pressure in ("urgent", "combat") else level


def _timed(gate: str, call) -> GateResult:
    started = time.perf_counter()
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 - a failing gate is a measured outcome
        result = GateResult(gate, None, error=f"{type(exc).__name__}: {exc}"[:300])
    result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return result


def _reasoning_ok(model: str) -> bool:
    return model.startswith(("gpt-5", "gpt-6", "o"))


class ChatGate:
    """An OpenAI chat model with a strict schema: {"level", "reason"}."""

    def __init__(self, model: str, *, api_key: str, reasoning_effort: str | None = None, judge: bool = False,
                 timeout: float = 30.0, name: str | None = None):
        from openai import OpenAI

        self.model = model
        self.reasoning_effort = reasoning_effort
        self.judge = judge
        self.max_tokens = 4000 if reasoning_effort not in (None, "none") else 80
        self.name = name or (f"{model}:{reasoning_effort}" if reasoning_effort else model)
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def decide(self, text: str, situation: Situation = GENERAL) -> GateResult:
        return _timed(self.name, lambda: self._call(text, situation))

    def _call(self, text: str, situation: Situation) -> GateResult:
        schema = {
            "type": "object",
            "properties": {"level": {"type": "string", "enum": list(LEVELS)}, "reason": {"type": "string"}},
            "required": ["level", "reason"],
            "additionalProperties": False,
        }
        kwargs = {}
        if self.reasoning_effort and _reasoning_ok(self.model):
            kwargs["reasoning_effort"] = self.reasoning_effort
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": instructions(situation, judge=self.judge)},
                      {"role": "user", "content": text}],
            response_format={"type": "json_schema", "json_schema": {"name": "gate", "strict": True, "schema": schema}},
            max_completion_tokens=self.max_tokens,
            store=False,
            **kwargs,
        )
        out = json.loads(response.choices[0].message.content or "{}")
        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None)
        return GateResult(
            self.name, out.get("level"), reason=out.get("reason"),
            input_tokens=getattr(usage, "prompt_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None) if details else None,
            output_tokens=getattr(usage, "completion_tokens", None),
        )


class OpenAIDecisionsGate:
    """OpenAI's Decisions API: one choice question over the tick text."""

    URL = "https://api.openai.com/v1/decisions"

    def __init__(self, model: str = "gpt-6-luna", *, api_key: str, timeout: float = 30.0):
        self.model = model
        self.name = f"openai-decisions:{model}"
        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})

    def body(self, text: str, situation: Situation = GENERAL) -> dict:
        return {
            "model": self.model,
            "input": text,
            "questions": [{
                "type": "choice", "name": "level", "instructions": question(situation),
                "choices": [{"value": level, "description": guide} for level, guide in level_guide(situation).items()],
            }],
        }

    def decide(self, text: str, situation: Situation = GENERAL) -> GateResult:
        return _timed(self.name, lambda: self.parse(self._post(text, situation)))

    def _post(self, text: str, situation: Situation) -> dict:
        response = self.client.post(self.URL, json=self.body(text, situation))
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    def parse(self, data: dict) -> GateResult:
        answers = data.get("answers") or []
        answer = next((a for a in answers if a.get("name") == "level"), answers[0] if answers else {})
        if answer.get("type") == "refusal":
            return GateResult(self.name, None, error="refusal")
        probabilities = {p.get("value"): p.get("probability") for p in answer.get("probabilities") or []}
        usage = data.get("usage") or {}
        return GateResult(
            self.name, answer.get("choice"), probabilities=probabilities or None,
            input_tokens=usage.get("input_tokens"),
            cached_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


class SystemOneGate:
    """The TypeSafe "System One" format: a state plus questions keyed by name.
    Jev on OpenRouter, or Strands Decider served locally."""

    def __init__(self, name: str, url: str, model: str, *, api_key: str | None = None, timeout: float = 30.0):
        self.name = name
        self.url = url
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.Client(timeout=timeout, headers=headers)

    def body(self, text: str, situation: Situation = GENERAL) -> dict:
        return {
            "model": self.model,
            "state": {"tick": text},
            "questions": {"level": {"type": "choice", "instructions": question(situation),
                                    "criteria": level_guide(situation)}},
        }

    def decide(self, text: str, situation: Situation = GENERAL) -> GateResult:
        return _timed(self.name, lambda: self.parse(self._post(text, situation)))

    def _post(self, text: str, situation: Situation) -> dict:
        response = self.client.post(self.url, json=self.body(text, situation))
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    def parse(self, data: dict) -> GateResult:
        answer = (data.get("answers") or {}).get("level") or {}
        usage = data.get("usage") or {}
        return GateResult(
            self.name, answer.get("choice"), probabilities=answer.get("probabilities"),
            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
            cost_usd=usage.get("cost"),
        )


def jev(api_key: str, model: str = "typesafe/jev-1.13") -> SystemOneGate:
    """Jev through OpenRouter."""
    return SystemOneGate(f"jev:{model}", "https://openrouter.ai/api/alpha/decisions", model, api_key=api_key)


def typesafe(api_key: str, model: str = "jev-latest", base_url: str = "https://api.typesafe.ai/v1",
             timeout: float = 30.0) -> SystemOneGate:
    """Jev from TypeSafe directly."""
    return SystemOneGate(f"typesafe:{model}", base_url.rstrip("/") + "/systemone", model, api_key=api_key,
                         timeout=timeout)


def strands(url: str, model: str = "StrandsAgents/strands-decider-2B-hobson-v19") -> SystemOneGate:
    return SystemOneGate("strands-decider-2b", url.rstrip("/") + "/v1/systemone", model)


def level_from(result: GateResult, threshold: float) -> str | None:
    """The level to act on: danger by the gate's top choice, thinking only when
    the gate is at least ``threshold`` sure it's needed (light and hard together).
    Without probabilities, the gate's own choice."""
    p = result.probabilities
    if not p:
        return result.level
    if max(p, key=lambda level: p.get(level) or 0) == "react":
        return "react"
    if (p.get("light") or 0) + (p.get("hard") or 0) < threshold:
        return "none"
    return "hard" if (p.get("hard") or 0) > (p.get("light") or 0) else "light"
