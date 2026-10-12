"""Content-free monotonic spans; nested spans are never added to wall time."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Literal

from pydantic import Field, model_serializer, model_validator
from .contracts.interpretation import FrozenModel


class LatencySpan(FrozenModel):
    # v0.3 (protocol cognitive-two-call-v1) adds the content subsystem run
    # before Call 2 and the gated plan compilation after it.
    schema_version: Literal['cognitive-latency-span-v0.1', 'cognitive-latency-span-v0.2', 'cognitive-latency-span-v0.3'] = 'cognitive-latency-span-v0.1'
    span_id: str = Field(pattern=r"^(appraisal|call_one|call_two|contribution)\.(payload_compile|provider_attempt|response_compile|content_preparation|plan_compilation)\.[0-9]+$")
    parent_span_id: None = None
    stage: Literal['appraisal', 'call_one', 'call_two', 'contribution'] = 'appraisal'
    component: Literal['payload_compile', 'provider_attempt', 'response_compile', 'content_preparation', 'plan_compilation']
    implementation: Literal['model'] = 'model'
    status: Literal['completed', 'failed', 'timed_out', 'cancelled', 'skipped']
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    attempt_number: int | None = Field(default=None, ge=1)
    worker_attempt: int | None = Field(default=None, ge=1)
    blocking_for_response: bool = False
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    provider_wall_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    parse_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


    @model_validator(mode='after')
    def versioned_component(self):
        if (self.component in V03_COMPONENTS) != (self.schema_version == 'cognitive-latency-span-v0.3'):
            raise ValueError('content_preparation and plan_compilation spans are span schema v0.3, and only they')
        return self

    @model_serializer(mode='wrap')
    def omit_absent_worker_attempt(self, handler):
        data = handler(self)
        for key in ("reasoning_tokens","provider_wall_ms","parse_ms"):
            if getattr(self,key) is None:data.pop(key,None)
        if self.worker_attempt is None:
            data.pop('worker_attempt',None)
        return data

V03_COMPONENTS = frozenset({'content_preparation', 'plan_compilation'})


class SpanRecorder:
    def __init__(self, clock: Callable[[], float] = time.monotonic, *, stage='appraisal'):
        self.clock = clock
        self.stage = stage
        self.worker_attempt = None
        self.spans: list[LatencySpan] = []

    @contextmanager
    def measure(self, component, *, attempt_number=None, skipped=False):
        started = self.clock()
        status = 'skipped' if skipped else 'completed'
        try:
            yield
        except BaseException as exc:
            status = 'cancelled' if not isinstance(exc, Exception) else (
                'timed_out' if getattr(exc, 'code', None) == 'timeout' else 'failed')
            raise
        finally:
            version = ("cognitive-latency-span-v0.1" if self.stage == "appraisal" else
                       "cognitive-latency-span-v0.3" if component in V03_COMPONENTS else "cognitive-latency-span-v0.2")
            self.spans.append(LatencySpan(schema_version=version,
                stage=self.stage, span_id=f"{self.stage}.{component}.{len(self.spans) + 1}", component=component, status=status,
                elapsed_ms=(self.clock() - started) * 1000, attempt_number=attempt_number,worker_attempt=self.worker_attempt))
