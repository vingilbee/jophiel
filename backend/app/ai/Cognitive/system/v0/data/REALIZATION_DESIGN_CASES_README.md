# Realization fixture guide

Updated 2026-10-18. The fixture already exists; this replaces its completed
creation assignment. See the [data index](README.md) and [current system guide](../README.md).

## Scope and status

Compiled requests, scripted wording, source alignment, attribution, uncertainty and adversarial mutations. The required_coverage map in the JSON retains the case coverage inventory; this is not a natural-language quality benchmark.

- Data: [realization_design_cases.json](realization_design_cases.json)
- Recorded status: `draft_for_human_review`.
- Cases at this review: 23. The JSON is the current inventory.

[realization_v0_support.py](../../../../../../tests/realization_v0_support.py) constructs the authored plan/content and scripted candidates. Historical coverage numbers refer to the original brief, not current section numbers.

## Consumers

- Tests: [test_realization_v0_design_cases.py](../../../../../../tests/test_realization_v0_design_cases.py)

## Maintaining the fixture

Keep case IDs, input provenance, schema versions and explicit expected results.
Represent uncertainty and unsupported cases instead of inventing facts. Inspect
the adapter and test assertions before changing a label. Change expectations only
when the intended contract changes, not merely because a run failed.

A passing case establishes the stated contract on its authored inputs. These
fixtures are not approved training labels or held-out evidence of general
cognitive ability. Do not use historical authoring prompts as new work requests.
