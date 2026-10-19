# Perception: current deterministic cue extraction

Current component reference, checked 2026-10-18. This replaces the completed
implementation assignment previously at this path. Start with the
[current guide](README.md) for the overall goal and evaluation status.

Perception extracts typed, evidence-linked observations from messages and trusted
runtime facts. The cue manifest owns definitions, lifecycle and active/shadow
status. The Interpretation handoff filters cues according to eligibility.

The current teacher consumes deterministic cues. A learned cue detector remains
a separate future capability requiring reviewed labels and evaluation. Cue
activations do not establish sarcasm, intent, hidden affect, or factual truth.

Preserve source evidence and cue provenance. Personality belongs to downstream
conditioning rather than rewriting the observed message. Design-case fixtures
exercise extraction and schema behavior; they are not a learned-model benchmark.

## Source contracts and implementation

- [perception_v0/models.py](perception_v0/models.py)
- [perception_v0/manifest.py](perception_v0/manifest.py)
- [perception_v0/engine.py](perception_v0/engine.py)
- [perception_v0/evidence.py](perception_v0/evidence.py)
- [perception_v0/fixture_validator.py](perception_v0/fixture_validator.py)

## Verification and limits

- [test_perception_v0.py](../../../../../tests/test_perception_v0.py)
- [test_perception_v0_contract.py](../../../../../tests/test_perception_v0_contract.py)

These tests establish their stated contract or regression coverage. Scripted
model outputs and deterministic fixtures do not establish real-model cognitive
quality. Historical numbered-section references to this file refer to the
retired implementation brief; use the source contracts above for current details.
