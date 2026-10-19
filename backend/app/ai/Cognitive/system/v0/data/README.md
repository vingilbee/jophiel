# Cognitive V0 fixtures and schema references

This directory contains typed design/regression fixtures, registries and earlier
schema proposals. It is not the package-root intent-classifier JSONL training
directory. Start with the [current cognitive guide](../README.md).

| Fixture | Purpose / consumer |
| --- | --- |
| [Perception cases](perception_design_cases.json) | Observable cues; `perception_v0/fixture_validator.py` and perception tests |
| [Interpretation cases](INTERPRETATION_DESIGN_CASES_README.md) | Grounding, hypotheses and uncertainty in the ontology/reference path |
| [Appraisal cases](APPRAISAL_DESIGN_CASES_README.md) | Appraisal policy and aggregation |
| [Dynamic-state cases](DYNAMIC_STATE_DESIGN_CASES_README.md) | Bounded state updates and outcomes |
| [Goal cases](GOAL_DESIGN_CASES_README.md) | Goal formation, lifecycle and requirements |
| [Action cases](CONVERSATIONAL_ACTION_DESIGN_CASES_README.md) | Candidate construction, gates and deterministic planning |
| [Dialogue-memory cases](dialogue_memory_design_cases.json) | Conversation-state update/projection tests |
| [Realization cases](REALIZATION_DESIGN_CASES_README.md) | Request compilation and scripted wording validation |
| [Action registry](CONVERSATIONAL_ACTION_REGISTRY_V0.md) | Lifecycle and effect tables also parsed by registry tests |

JSON status fields describe the datasets; successful regression execution does
not promote them to training truth. Historical food/artifact scenarios provide
specialist coverage, not the general-conversation product brief.

`APPRAISAL_SCHEMA_V0.md`, `DYNAMIC_STATE_SCHEMA_V0.md`, and
`OUTCOME_OBSERVATION_SCHEMA_V0.md` retain design rationale. Their status notes
identify runtime contracts and distinguish implemented subsets from proposals.
Use the linked source models for actual fields and versions.

Current conditioning fixtures live in
[docs/qa/cognitive-conditioning-2026-10-18](../../../../../../../docs/qa/cognitive-conditioning-2026-10-18/).
The teacher's candidate record projection is
[two_call/v1_training.py](../two_call/v1_training.py). Neither is equivalent to
these historical design-case labels.
