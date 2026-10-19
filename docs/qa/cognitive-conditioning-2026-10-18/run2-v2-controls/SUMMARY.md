# Conditioning test summary

Runs: 216 (completed 216). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
| benign_prior | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:novelty=moderate` 0 | yes |
| trust_prior | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:novelty=moderate` -33 | no |
| threat_sensitivity | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:agency=columbina` 67 | no |
| hostile_attribution | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:novelty=moderate` -33 | no |
| social_cue_sensitivity | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:agency=columbina` 33 | no |
| confirmation_tendency | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 33 | no |
| ambiguity_tolerance | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:novelty=moderate` 33 | no |
| social_affiliation_prior | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:agency=columbina` 33 | no |
| state | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `event:information_request` 0 | yes |
| benign_prior | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=moderate` -33 | no |
| trust_prior | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=moderate` -33 | no |
| threat_sensitivity | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=moderate` 33 | no |
| hostile_attribution | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:relevance=high` -67 | no |
| social_cue_sensitivity | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 100 | no |
| confirmation_tendency | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=moderate` -33 | no |
| ambiguity_tolerance | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.goal.prevent_or_stop_action` 0 | yes |
| social_affiliation_prior | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -33 | no |
| state | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.goal.prevent_or_stop_action` 0 | yes |
| benign_prior | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.goal.transfer_information` -33 | no |
| trust_prior | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -67 | no |
| threat_sensitivity | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `appraisal:controllability=moderate` 33 | no |
| hostile_attribution | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -33 | no |
| social_cue_sensitivity | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `appraisal:novelty=moderate` 0 | yes |
| confirmation_tendency | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -33 | no |
| ambiguity_tolerance | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `appraisal:novelty=moderate` 0 | yes |
| social_affiliation_prior | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 100 | no |
| state | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.goal.repair_shared_understanding` 0 | yes |
| benign_prior | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:certainty=moderate` 67 | no |
| trust_prior | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:norm_compatibility=neutral` -33 | no |
| threat_sensitivity | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:norm_compatibility=neutral` 33 | no |
| hostile_attribution | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:certainty=moderate` -67 | no |
| social_cue_sensitivity | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:certainty=moderate` 67 | no |
| confirmation_tendency | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:norm_compatibility=neutral` 33 | no |
| ambiguity_tolerance | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:norm_compatibility=neutral` -33 | no |
| social_affiliation_prior | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:norm_compatibility=neutral` -33 | no |
| state | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 33 | no |
