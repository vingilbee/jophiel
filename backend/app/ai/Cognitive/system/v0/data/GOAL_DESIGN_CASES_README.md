# Goal fixture guide

Updated 2026-10-18. The fixture already exists; this replaces its completed
creation assignment. See the [data index](README.md) and [current system guide](../README.md).

## Scope and status

Goal definitions, formation, lifecycle, requirements, priority and evidence. The adapter translates authored cases to canonical inputs; expectations must not be fed into runtime decisions.

- Data: [goal_design_cases.json](goal_design_cases.json)
- Recorded status: `draft_for_human_review`.
- Cases at this review: 36. The JSON is the current inventory.

## Consumers

- Adapter/validator: [../goal_v0/fixture_adapter.py](../goal_v0/fixture_adapter.py)
- Tests: [test_goal_v0_design_cases_runtime.py](../../../../../../tests/test_goal_v0_design_cases_runtime.py)

## Maintaining the fixture

Keep case IDs, input provenance, schema versions and explicit expected results.
Represent uncertainty and unsupported cases instead of inventing facts. Inspect
the adapter and test assertions before changing a label. Change expectations only
when the intended contract changes, not merely because a run failed.

A passing case establishes the stated contract on its authored inputs. These
fixtures are not approved training labels or held-out evidence of general
cognitive ability. Do not use historical authoring prompts as new work requests.
