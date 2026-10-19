# Interpretation fixture guide

Updated 2026-10-18. The fixture already exists; this replaces its completed
creation assignment. See the [data index](README.md) and [current system guide](../README.md).

## Scope and status

Semantic frames, hypothesis support, alternatives, evidence, ambiguity and routing. These are ontology/reference-engine cases; their expected labels are not outputs from the current V1 teacher.

- Data: [interpretation_design_cases.json](interpretation_design_cases.json)
- Recorded status: `draft_for_human_review`.
- Cases at this review: 27. The JSON is the current inventory.

## Consumers

- Adapter/validator: [../interpretation_v0/fixture_validator.py](../interpretation_v0/fixture_validator.py)
- Tests: [test_cognitive_turn_provenance.py](../../../../../../tests/test_cognitive_turn_provenance.py)

## Maintaining the fixture

Keep case IDs, input provenance, schema versions and explicit expected results.
Represent uncertainty and unsupported cases instead of inventing facts. Inspect
the adapter and test assertions before changing a label. Change expectations only
when the intended contract changes, not merely because a run failed.

A passing case establishes the stated contract on its authored inputs. These
fixtures are not approved training labels or held-out evidence of general
cognitive ability. Do not use historical authoring prompts as new work requests.
