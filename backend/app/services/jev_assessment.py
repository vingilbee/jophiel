"""The conversation's Jev call (docs/SHARED_STATE_STEP4_SPEC.md): one request, two decisions. Contract and
parse in ``app/ai/state/assessment.py``.

Its own adapter, not the game gate's (``app/ai/mind/gates.py`` asks "how hard should she think about this
tick" and steers away from user speech). Same provider and key as the mind's Jev (``TYPESAFE_API_KEY``,
``TYPESAFE_BASE_URL``), its own model and timeout. Never raises: a failure is an ``error`` assessment, which
keeps the turn unassessed. No retry: one call is ~60 ms, and a second inside the timeout buys little; the
timeout bounds what a slow provider can add to a reply.
"""
from __future__ import annotations

import time
import uuid
from functools import lru_cache
from typing import Any

import httpx

from app.ai.state import assessment as contract
from app.core.config import get_settings


def enabled() -> bool:
    s = get_settings()
    return bool(s.shared_state_assessment_enabled and s.typesafe_api_key)


@lru_cache(maxsize=2)
def _client(key: str, timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {key}"})


def assess(*, trigger_ref: str, input_versions: dict[str, str], state: dict[str, Any],
           post=None) -> contract.Assessment:
    s = get_settings()
    if not enabled():
        reason = ("SHARED_STATE_ASSESSMENT_ENABLED is off" if not s.shared_state_assessment_enabled
                  else "no TYPESAFE_API_KEY")
        return contract.Assessment.disabled(trigger_ref, reason)
    url = s.typesafe_base_url.rstrip("/") + "/systemone"
    request = contract.body(s.shared_state_assessment_model, state)
    started = time.perf_counter()
    try:
        if post is None:
            response = _client(s.typesafe_api_key, s.shared_state_assessment_timeout_seconds).post(url, json=request)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            data = response.json()
        else:
            data = post(url, request)
    except Exception as exc:  # noqa: BLE001 - an unavailable assessor is an unassessed turn, said as such
        return contract.Assessment(
            assessment_id=str(uuid.uuid4()), trigger_ref=trigger_ref,
            input_versions=input_versions, status="error", provider="typesafe",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            error=f"{type(exc).__name__}: {exc}"[:200])
    return contract.parse(data, trigger_ref=trigger_ref, input_versions=input_versions, provider="typesafe",
                          latency_ms=round((time.perf_counter() - started) * 1000, 1))
