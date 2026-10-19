# Durable shadow capture, observation and replay

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

The application captures turn inputs and queues observational cognitive work.
The shadow compiler resolves lineage and produces the pipeline request; observe
records a terminal result, and replay checks stored decisions and versions.
The pure `build_pipeline` constructor is not the configured worker factory.

Capture-time personality, authorized context, predecessor snapshots and version
provenance must remain reproducible. Later core-memory edits must not alter an
existing capture. Typed skips and failures are evidence, not accepted examples.

Shadow evaluation is separate from the visible reply and external execution.
Protocol configuration must be verified in the actual capture/trace; the mere
presence of V1 code does not establish that the worker selected V1.

## Source contracts and implementation

- [live_shadow/models.py](live_shadow/models.py)
- [live_shadow/compiler.py](live_shadow/compiler.py)
- [live_shadow/service.py](live_shadow/service.py)
- [live_shadow/digests.py](live_shadow/digests.py)
- [../../../../services/cognitive_shadow.py](../../../../services/cognitive_shadow.py)
- [../../../../workers/cognitive_shadow.py](../../../../workers/cognitive_shadow.py)

## Verification and limits

- [test_cognitive_shadow_contract.py](../../../../../tests/test_cognitive_shadow_contract.py)
- [test_cognitive_shadow_replay.py](../../../../../tests/test_cognitive_shadow_replay.py)
- [test_cognitive_shadow_persistence.py](../../../../../tests/test_cognitive_shadow_persistence.py)
- [test_cognitive_shadow_chat_isolation.py](../../../../../tests/test_cognitive_shadow_chat_isolation.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
