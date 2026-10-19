# Conversational Action fixture guide

Updated 2026-10-18. The fixture already exists; this replaces its completed
creation assignment. See the [data index](README.md) and [current system guide](../README.md).

## Scope and status

Grounded candidates, evidence gates, selection and transitions for the deterministic/reference engine. V1 model-choice quality needs a separate real-model evaluation.

- Data: [conversational_action_design_cases.json](conversational_action_design_cases.json)
- Recorded status: `accepted_architecture_cases_pending_behavioral_calibration`.
- Cases at this review: 29. The JSON is the current inventory.

[conversational_action_design_cases_v2.json](conversational_action_design_cases_v2.json) supplies canonical runtime inputs and failure variants. Both fixture versions remain in use. The registry/design Markdown contains lists parsed by registry tests.

## Consumers

- Adapter/validator: [../action_selection_v0/fixture_adapter.py](../action_selection_v0/fixture_adapter.py)
- Tests: [test_action_v0_design_cases_runtime.py](../../../../../../tests/test_action_v0_design_cases_runtime.py)

## Maintaining the fixture

Keep case IDs, input provenance, schema versions and explicit expected results.
Represent uncertainty and unsupported cases instead of inventing facts. Inspect
the adapter and test assertions before changing a label. Change expectations only
when the intended contract changes, not merely because a run failed.

A passing case establishes the stated contract on its authored inputs. These
fixtures are not approved training labels or held-out evidence of general
cognitive ability. Do not use historical authoring prompts as new work requests.
