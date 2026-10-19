# Cognitive pipeline: orchestration reference

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

`CognitivePipelineV0` connects Perception, Interpretation, Goals, Requirements,
collaboration and the cognitive-turn runner under one request and trace.
Its default constructor supplies offline/reference engines; the service and
`two_call.orchestrator.install` select model-backed adapters for the configured
protocol. Do not infer live behavior from the default constructor alone.

Requests carry authorized messages, runtime, memories, personality, prior state
and goal snapshots. Canonical handoffs preserve ownership, versions, evidence,
unknowns and typed failures. Durable observation and Realization are separate
boundaries. A completed trace establishes contract acceptance, not label quality.

The current call sequence and conditional content stage are described in the
[current guide](README.md#implemented-pipeline).

## Source contracts and implementation

- [cognitive_pipeline_v0/models.py](cognitive_pipeline_v0/models.py)
- [cognitive_pipeline_v0/runner.py](cognitive_pipeline_v0/runner.py)
- [two_call/orchestrator.py](two_call/orchestrator.py)
- [live_shadow/service.py](live_shadow/service.py)
- [../../../../services/cognitive_shadow.py](../../../../services/cognitive_shadow.py)

## Verification and limits

- [test_cognitive_pipeline_v0.py](../../../../../tests/test_cognitive_pipeline_v0.py)
- [test_cognitive_pipeline_v0_contract.py](../../../../../tests/test_cognitive_pipeline_v0_contract.py)
- [test_cognitive_two_call_v1.py](../../../../../tests/test_cognitive_two_call_v1.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
