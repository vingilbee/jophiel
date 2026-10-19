# Cognitive turn: canonical stage handoffs

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

The turn runner consumes the canonical Interpretation and stage inputs,
coordinates Appraisal, Dynamic State and Action processing, and records their
outputs and provenance. The containing pipeline owns its additional Goal,
Requirement and collaboration orchestration.

In V1, model-backed adapters participate in this runner. Describing the entire
current path as deterministic would be incorrect; state transitions and
validation remain code-owned while model decisions are captured for replay.

Preserve turn/conversation ownership, predecessor state and source references.
A failed stage must remain observable rather than being represented as a
successful cognitive result.

## Source contracts and implementation

- [cognitive_turn/models.py](cognitive_turn/models.py)
- [cognitive_turn/runner.py](cognitive_turn/runner.py)
- [cognitive_pipeline_v0/runner.py](cognitive_pipeline_v0/runner.py)
- [two_call/orchestrator.py](two_call/orchestrator.py)

## Verification and limits

- [test_cognitive_turn.py](../../../../../tests/test_cognitive_turn.py)
- [test_cognitive_turn_provenance.py](../../../../../tests/test_cognitive_turn_provenance.py)
- [test_action_v0_cognitive_turn_integration.py](../../../../../tests/test_action_v0_cognitive_turn_integration.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
