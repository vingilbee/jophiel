# Local shadow verification reference

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

The earlier activation assignment has been completed as a historical development
step. Its environment values and process instructions are not a current rollout
plan. Read the actual service configuration before any new canary.

For an explicitly requested run, record the protocol/model and captured versions,
confirm the worker is using the intended settings, inspect terminal stage status
and timing, and check replay and visible-chat isolation. Use the current trace
summary implementation for supported arguments. Keep provider failures distinct
from cognitive, reference, policy and Realization failures.

Historical latency runs describe their own models, samples and settings. They
do not establish current latency or authorize replacing the live response.

## Source contracts and implementation

- [../../../../services/cognitive_shadow.py](../../../../services/cognitive_shadow.py)
- [../../../../core/config.py](../../../../core/config.py)
- [trace_summary.py](trace_summary.py)
- [live_shadow/service.py](live_shadow/service.py)

## Verification and limits

- [test_cognitive_shadow_trace_summary.py](../../../../../tests/test_cognitive_shadow_trace_summary.py)
- [test_cognitive_shadow_worker.py](../../../../../tests/test_cognitive_shadow_worker.py)
- [test_cognitive_shadow_chat_isolation.py](../../../../../tests/test_cognitive_shadow_chat_isolation.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
