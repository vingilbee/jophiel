# Conditioning test summary

Runs: 880 (completed 880). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | no data |
|---|---:|---:|---:|---:|
| benign_prior | 0 | 8 | 1 | 0 |
| trust_prior | 2 | 7 | 0 | 0 |
| threat_sensitivity | 1 | 8 | 0 | 0 |
| hostile_attribution | 1 | 7 | 1 | 0 |
| social_cue_sensitivity | 1 | 7 | 1 | 0 |
| confirmation_tendency | 2 | 7 | 0 | 0 |
| ambiguity_tolerance | 1 | 6 | 2 | 0 |
| social_affiliation_prior | 2 | 7 | 0 | 0 |
| state | 5 | 14 | 1 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---|---:|
| benign_prior | benign_prior:0 | `appraisal:affiliative_value=negative` | - | 0% | 0% | 0 | no_effect | 40% |
| benign_prior | benign_prior:0 | `appraisal:congruence=negative` | - | 0% | 0% | 0 | no_effect | 40% |
| benign_prior | benign_prior:0 | `reading:interpretation.pragmatic.literal` | + | 100% | 80% | -20 | no_effect | 40% |
| benign_prior | benign_prior:0 | `cites:bias:benign_prior` | info | 0% | 100% | 100 | info | – |
| benign_prior | benign_prior:1 | `appraisal:affiliative_value=negative` | - | 0% | 0% | 0 | no_effect | 60% |
| benign_prior | benign_prior:1 | `appraisal:congruence=negative` | - | 0% | 0% | 0 | no_effect | 60% |
| benign_prior | benign_prior:1 | `reading:interpretation.pragmatic.literal` | + | 40% | 40% | 0 | no_effect | 60% |
| benign_prior | benign_prior:1 | `cites:bias:benign_prior` | info | 20% | 100% | 80 | info | – |
| benign_prior | benign_prior:2 | `appraisal:affiliative_value=negative` | - | 0% | 0% | 0 | no_effect | 60% |
| benign_prior | benign_prior:2 | `appraisal:congruence=negative` | - | 0% | 0% | 0 | no_effect | 60% |
| benign_prior | benign_prior:2 | `reading:interpretation.pragmatic.literal` | + | 40% | 0% | -40 | wrong_direction | 60% |
| benign_prior | benign_prior:2 | `cites:bias:benign_prior` | info | 20% | 80% | 60 | info | – |
| trust_prior | trust_prior:0 | `uncertainty:high` | - | 0% | 0% | 0 | no_effect | 20% |
| trust_prior | trust_prior:0 | `appraisal:certainty=high` | + | 0% | 100% | 100 | pass | 20% |
| trust_prior | trust_prior:0 | `appraisal:certainty=low` | - | 0% | 0% | 0 | no_effect | 20% |
| trust_prior | trust_prior:0 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | info | – |
| trust_prior | trust_prior:1 | `uncertainty:high` | - | 0% | 0% | 0 | no_effect | 60% |
| trust_prior | trust_prior:1 | `appraisal:certainty=high` | + | 0% | 0% | 0 | no_effect | 60% |
| trust_prior | trust_prior:1 | `appraisal:certainty=low` | - | 0% | 0% | 0 | no_effect | 60% |
| trust_prior | trust_prior:1 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | info | – |
| trust_prior | trust_prior:2 | `uncertainty:high` | - | 0% | 0% | 0 | no_effect | 0% |
| trust_prior | trust_prior:2 | `appraisal:certainty=high` | + | 0% | 100% | 100 | pass | 0% |
| trust_prior | trust_prior:2 | `appraisal:certainty=low` | - | 0% | 0% | 0 | no_effect | 0% |
| trust_prior | trust_prior:2 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | info | – |
| threat_sensitivity | threat_sensitivity:0 | `appraisal:relevance=high` | + | 0% | 0% | 0 | no_effect | 20% |
| threat_sensitivity | threat_sensitivity:0 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | no_effect | 20% |
| threat_sensitivity | threat_sensitivity:0 | `appraisal:congruence=negative` | + | 100% | 100% | 0 | no_effect | 20% |
| threat_sensitivity | threat_sensitivity:0 | `cites:bias:threat_sensitivity` | info | 0% | 60% | 60 | info | – |
| threat_sensitivity | threat_sensitivity:1 | `appraisal:relevance=high` | + | 80% | 100% | 20 | no_effect | 40% |
| threat_sensitivity | threat_sensitivity:1 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | no_effect | 40% |
| threat_sensitivity | threat_sensitivity:1 | `appraisal:congruence=negative` | + | 0% | 0% | 0 | no_effect | 40% |
| threat_sensitivity | threat_sensitivity:1 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | info | – |
| threat_sensitivity | threat_sensitivity:2 | `appraisal:relevance=high` | + | 0% | 0% | 0 | no_effect | 60% |
| threat_sensitivity | threat_sensitivity:2 | `appraisal:affiliative_value=negative` | + | 60% | 100% | 40 | pass | 60% |
| threat_sensitivity | threat_sensitivity:2 | `appraisal:congruence=negative` | + | 100% | 100% | 0 | no_effect | 60% |
| threat_sensitivity | threat_sensitivity:2 | `cites:bias:threat_sensitivity` | info | 0% | 20% | 20 | info | – |
| hostile_attribution | hostile_attribution:0 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | no_effect | 60% |
| hostile_attribution | hostile_attribution:0 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | no_effect | 60% |
| hostile_attribution | hostile_attribution:0 | `reading:interpretation.pragmatic.indirect` | + | 40% | 0% | -40 | wrong_direction | 60% |
| hostile_attribution | hostile_attribution:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | info | – |
| hostile_attribution | hostile_attribution:1 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | no_effect | 40% |
| hostile_attribution | hostile_attribution:1 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | no_effect | 40% |
| hostile_attribution | hostile_attribution:1 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | no_effect | 40% |
| hostile_attribution | hostile_attribution:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | info | – |
| hostile_attribution | hostile_attribution:2 | `appraisal:affiliative_value=negative` | + | 40% | 100% | 60 | pass | 20% |
| hostile_attribution | hostile_attribution:2 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | no_effect | 20% |
| hostile_attribution | hostile_attribution:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | no_effect | 20% |
| hostile_attribution | hostile_attribution:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | info | – |
| social_cue_sensitivity | social_cue_sensitivity:0 | `reading:interpretation.pragmatic.indirect` | + | 20% | 80% | 60 | pass | 40% |
| social_cue_sensitivity | social_cue_sensitivity:0 | `reading:interpretation.pragmatic.tone_situation_mismatch` | + | 0% | 0% | 0 | no_effect | 40% |
| social_cue_sensitivity | social_cue_sensitivity:0 | `reading:interpretation.pragmatic.literal` | - | 100% | 100% | 0 | no_effect | 40% |
| social_cue_sensitivity | social_cue_sensitivity:0 | `cites:bias:social_cue_sensitivity` | info | 0% | 100% | 100 | info | – |
| social_cue_sensitivity | social_cue_sensitivity:1 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | no_effect | 60% |
| social_cue_sensitivity | social_cue_sensitivity:1 | `reading:interpretation.pragmatic.tone_situation_mismatch` | + | 0% | 0% | 0 | no_effect | 60% |
| social_cue_sensitivity | social_cue_sensitivity:1 | `reading:interpretation.pragmatic.literal` | - | 0% | 100% | 100 | wrong_direction | 60% |
| social_cue_sensitivity | social_cue_sensitivity:1 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | info | – |
| social_cue_sensitivity | social_cue_sensitivity:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | no_effect | 40% |
| social_cue_sensitivity | social_cue_sensitivity:2 | `reading:interpretation.pragmatic.tone_situation_mismatch` | + | 0% | 0% | 0 | no_effect | 40% |
| social_cue_sensitivity | social_cue_sensitivity:2 | `reading:interpretation.pragmatic.literal` | - | 20% | 40% | 20 | no_effect | 40% |
| social_cue_sensitivity | social_cue_sensitivity:2 | `cites:bias:social_cue_sensitivity` | info | 0% | 20% | 20 | info | – |
| confirmation_tendency | confirmation_tendency:0 | `appraisal:novelty=low` | + | 40% | 100% | 60 | pass | 20% |
| confirmation_tendency | confirmation_tendency:0 | `appraisal:congruence=positive` | + | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:0 | `appraisal:novelty=high` | - | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:0 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | info | – |
| confirmation_tendency | confirmation_tendency:1 | `appraisal:novelty=low` | + | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:1 | `appraisal:congruence=positive` | + | 0% | 40% | 40 | pass | 20% |
| confirmation_tendency | confirmation_tendency:1 | `appraisal:novelty=high` | - | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:1 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | info | – |
| confirmation_tendency | confirmation_tendency:2 | `appraisal:novelty=low` | + | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:2 | `appraisal:congruence=positive` | + | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:2 | `appraisal:novelty=high` | - | 0% | 0% | 0 | no_effect | 20% |
| confirmation_tendency | confirmation_tendency:2 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | info | – |
| ambiguity_tolerance | ambiguity_tolerance:0 | `multi_reading` | + | 0% | 20% | 20 | no_effect | 40% |
| ambiguity_tolerance | ambiguity_tolerance:0 | `move_family:seek_information` | - | 100% | 100% | 0 | no_effect | 40% |
| ambiguity_tolerance | ambiguity_tolerance:0 | `unknown_any` | - | 100% | 100% | 0 | no_effect | 40% |
| ambiguity_tolerance | ambiguity_tolerance:0 | `cites:bias:ambiguity_tolerance` | info | 100% | 100% | 0 | info | – |
| ambiguity_tolerance | ambiguity_tolerance:1 | `multi_reading` | + | 60% | 20% | -40 | wrong_direction | 40% |
| ambiguity_tolerance | ambiguity_tolerance:1 | `move_family:seek_information` | - | 0% | 80% | 80 | wrong_direction | 40% |
| ambiguity_tolerance | ambiguity_tolerance:1 | `unknown_any` | - | 100% | 80% | -20 | no_effect | 40% |
| ambiguity_tolerance | ambiguity_tolerance:1 | `cites:bias:ambiguity_tolerance` | info | 100% | 100% | 0 | info | – |
| ambiguity_tolerance | ambiguity_tolerance:2 | `multi_reading` | + | 20% | 80% | 60 | pass | 40% |
| ambiguity_tolerance | ambiguity_tolerance:2 | `move_family:seek_information` | - | 100% | 100% | 0 | no_effect | 40% |
| ambiguity_tolerance | ambiguity_tolerance:2 | `unknown_any` | - | 100% | 100% | 0 | no_effect | 40% |
| ambiguity_tolerance | ambiguity_tolerance:2 | `cites:bias:ambiguity_tolerance` | info | 100% | 100% | 0 | info | – |
| social_affiliation_prior | social_affiliation_prior:0 | `reading:interpretation.goal.maintain_social_connection` | + | 100% | 100% | 0 | no_effect | 60% |
| social_affiliation_prior | social_affiliation_prior:0 | `appraisal:affiliative_value=positive` | + | 100% | 100% | 0 | no_effect | 60% |
| social_affiliation_prior | social_affiliation_prior:0 | `reading:interpretation.goal.obtain_information` | - | 0% | 0% | 0 | no_effect | 60% |
| social_affiliation_prior | social_affiliation_prior:0 | `cites:bias:social_affiliation_prior` | info | 0% | 100% | 100 | info | – |
| social_affiliation_prior | social_affiliation_prior:1 | `reading:interpretation.goal.maintain_social_connection` | + | 100% | 100% | 0 | no_effect | 40% |
| social_affiliation_prior | social_affiliation_prior:1 | `appraisal:affiliative_value=positive` | + | 100% | 100% | 0 | no_effect | 40% |
| social_affiliation_prior | social_affiliation_prior:1 | `reading:interpretation.goal.obtain_information` | - | 60% | 0% | -60 | pass | 40% |
| social_affiliation_prior | social_affiliation_prior:1 | `cites:bias:social_affiliation_prior` | info | 0% | 100% | 100 | info | – |
| social_affiliation_prior | social_affiliation_prior:2 | `reading:interpretation.goal.maintain_social_connection` | + | 0% | 0% | 0 | no_effect | 40% |
| social_affiliation_prior | social_affiliation_prior:2 | `appraisal:affiliative_value=positive` | + | 20% | 100% | 80 | pass | 40% |
| social_affiliation_prior | social_affiliation_prior:2 | `reading:interpretation.goal.obtain_information` | - | 0% | 0% | 0 | no_effect | 40% |
| social_affiliation_prior | social_affiliation_prior:2 | `cites:bias:social_affiliation_prior` | info | 0% | 0% | 0 | info | – |
| state | state:0 | `appraisal:congruence=negative` | + | 0% | 100% | 100 | pass | – |
| state | state:0 | `appraisal:affiliative_value=negative` | + | 0% | 100% | 100 | pass | – |
| state | state:0 | `reading:interpretation.goal.repair_shared_understanding` | + | 0% | 0% | 0 | no_effect | – |
| state | state:0 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | no_effect | – |
| state | state:0 | `move_family:support_socially` | - | 0% | 0% | 0 | no_effect | – |
| state | state:0 | `cites:state` | info | 100% | 100% | 0 | info | – |
| state | state:1 | `appraisal:congruence=negative` | + | 80% | 100% | 20 | no_effect | – |
| state | state:1 | `appraisal:affiliative_value=negative` | + | 60% | 100% | 40 | pass | – |
| state | state:1 | `reading:interpretation.goal.repair_shared_understanding` | + | 60% | 0% | -60 | wrong_direction | – |
| state | state:1 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | no_effect | – |
| state | state:1 | `move_family:support_socially` | - | 0% | 0% | 0 | no_effect | – |
| state | state:1 | `cites:state` | info | 100% | 100% | 0 | info | – |
| state | state:2 | `appraisal:congruence=negative` | + | 0% | 100% | 100 | pass | – |
| state | state:2 | `appraisal:affiliative_value=negative` | + | 0% | 100% | 100 | pass | – |
| state | state:2 | `reading:interpretation.goal.repair_shared_understanding` | + | 0% | 0% | 0 | no_effect | – |
| state | state:2 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | no_effect | – |
| state | state:2 | `move_family:support_socially` | - | 0% | 0% | 0 | no_effect | – |
| state | state:2 | `cites:state` | info | 100% | 100% | 0 | info | – |
| state | state:3 | `appraisal:congruence=negative` | + | 0% | 20% | 20 | no_effect | – |
| state | state:3 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | no_effect | – |
| state | state:3 | `reading:interpretation.goal.repair_shared_understanding` | + | 0% | 0% | 0 | no_effect | – |
| state | state:3 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | no_effect | – |
| state | state:3 | `move_family:support_socially` | - | 0% | 0% | 0 | no_effect | – |
| state | state:3 | `cites:state` | info | 100% | 100% | 0 | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
| benign_prior | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `event:information_request` 0 | yes |
| trust_prior | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -20 | no |
| threat_sensitivity | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 20 | no |
| hostile_attribution | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `event:information_request` 0 | yes |
| social_cue_sensitivity | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -80 | no |
| confirmation_tendency | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `appraisal:relevance=moderate` 20 | no |
| ambiguity_tolerance | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 40 | no |
| social_affiliation_prior | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `event:information_request` 0 | yes |
| state | Can you tell me what the capital of Canada is? | `event:information_request` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 20 | no |
| benign_prior | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=low` 20 | no |
| trust_prior | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 40 | no |
| threat_sensitivity | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:controllability=high` 0 | yes |
| hostile_attribution | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -40 | no |
| social_cue_sensitivity | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=low` -20 | no |
| confirmation_tendency | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -20 | no |
| ambiguity_tolerance | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:agency=columbina` 40 | no |
| social_affiliation_prior | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `appraisal:novelty=low` -100 | no |
| state | Please stop asking me follow-up questions for now. | `reading:interpretation.goal.prevent_or_stop_action` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -20 | no |
| benign_prior | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -20 | no |
| trust_prior | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -40 | no |
| threat_sensitivity | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `appraisal:relevance=moderate` -20 | no |
| hostile_attribution | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `appraisal:relevance=moderate` 60 | no |
| social_cue_sensitivity | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 20 | no |
| confirmation_tendency | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `appraisal:relevance=moderate` 0 | yes |
| ambiguity_tolerance | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -20 | no |
| social_affiliation_prior | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 40 | no |
| state | I meant Tuesday, not Thursday. | `event:correction` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -40 | no |
| benign_prior | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 60 | no |
| trust_prior | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:certainty=moderate` -100 | no |
| threat_sensitivity | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:certainty=moderate` 40 | no |
| hostile_attribution | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` -60 | no |
| social_cue_sensitivity | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:affiliative_value=positive` 100 | no |
| confirmation_tendency | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:certainty=moderate` 40 | no |
| ambiguity_tolerance | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `reading:interpretation.goal.maintain_social_connection` 60 | no |
| social_affiliation_prior | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `appraisal:affiliative_value=neutral` -80 | no |
| state | My sister is visiting this weekend. | `event:statement` 100% / 100% ✓ | `reading:interpretation.pragmatic.literal` 80 | no |
