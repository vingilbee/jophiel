# Appraisal fixture guide

Updated 2026-10-18. The fixture already exists; this replaces its completed
creation assignment. See the [data index](README.md) and [current system guide](../README.md).

## Scope and status

Per-scenario appraisal dimensions, unknown values, evidence and aggregation. Deterministic expected ratings do not independently validate model-backed Call 1 appraisal.

- Data: [appraisal_design_cases.json](appraisal_design_cases.json)
- Recorded status: `draft_for_human_review`.
- Cases at this review: 25. The JSON is the current inventory.

## Consumers

- Adapter/validator: [../appraisal/fixture_validator.py](../appraisal/fixture_validator.py)
- Tests: [test_appraisal.py](../../../../../../tests/test_appraisal.py)

## Maintaining the fixture

Keep case IDs, input provenance, schema versions and explicit expected results.
Represent uncertainty and unsupported cases instead of inventing facts. Inspect
the adapter and test assertions before changing a label. Change expectations only
when the intended contract changes, not merely because a run failed.

A passing case establishes the stated contract on its authored inputs. These
fixtures are not approved training labels or held-out evidence of general
cognitive ability. Do not use historical authoring prompts as new work requests.
