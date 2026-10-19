# Conditioning test summary

Runs: 280 (completed 280). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|
| benign_prior | 0 | 9 | 0 | 0 | 0 |
| trust_prior | 2 | 7 | 0 | 0 | 0 |
| threat_sensitivity | 1 | 8 | 0 | 0 | 0 |
| hostile_attribution | 2 | 7 | 0 | 0 | 0 |
| social_cue_sensitivity | 0 | 9 | 0 | 0 | 0 |
| confirmation_tendency | 1 | 8 | 0 | 0 | 0 |
| ambiguity_tolerance | 0 | 7 | 2 | 0 | 0 |
| social_affiliation_prior | 0 | 9 | 0 | 0 | 0 |
| state | 1 | 18 | 1 | 0 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| benign_prior | benign_prior:0 | `lead_appraisal:affiliative_value=negative` | - | 0% | 0% | 0 | – | no_effect | – |
| benign_prior | benign_prior:0 | `lead_appraisal:congruence=negative` | - | 0% | 0% | 0 | – | no_effect | – |
| benign_prior | benign_prior:0 | `leads:interpretation.pragmatic.literal` | + | 0% | 0% | 0 | 0 | no_effect | – |
| benign_prior | benign_prior:0 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| benign_prior | benign_prior:0 | `fit:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| benign_prior | benign_prior:1 | `lead_appraisal:affiliative_value=negative` | - | 0% | 0% | 0 | – | no_effect | – |
| benign_prior | benign_prior:1 | `lead_appraisal:congruence=negative` | - | 0% | 0% | 0 | – | no_effect | – |
| benign_prior | benign_prior:1 | `leads:interpretation.pragmatic.literal` | + | 0% | 0% | 0 | 23 | no_effect | – |
| benign_prior | benign_prior:1 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| benign_prior | benign_prior:1 | `fit:bias:benign_prior` | info | 20% | 60% | 40 | – | info | – |
| benign_prior | benign_prior:2 | `lead_appraisal:affiliative_value=negative` | - | 0% | 0% | 0 | – | no_effect | – |
| benign_prior | benign_prior:2 | `lead_appraisal:congruence=negative` | - | 0% | 0% | 0 | – | no_effect | – |
| benign_prior | benign_prior:2 | `leads:interpretation.pragmatic.literal` | + | 20% | 40% | 20 | 20 | no_effect | – |
| benign_prior | benign_prior:2 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| benign_prior | benign_prior:2 | `fit:bias:benign_prior` | info | 20% | 0% | -20 | – | info | – |
| trust_prior | trust_prior:0 | `uncertainty:high` | - | 0% | 20% | 20 | – | no_effect | – |
| trust_prior | trust_prior:0 | `lead_appraisal:certainty=high` | + | 40% | 80% | 40 | – | pass | – |
| trust_prior | trust_prior:0 | `lead_appraisal:certainty=low` | - | 0% | 0% | 0 | – | no_effect | – |
| trust_prior | trust_prior:0 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_prior | trust_prior:0 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_prior | trust_prior:1 | `uncertainty:high` | - | 0% | 0% | 0 | – | no_effect | – |
| trust_prior | trust_prior:1 | `lead_appraisal:certainty=high` | + | 20% | 0% | -20 | – | no_effect | – |
| trust_prior | trust_prior:1 | `lead_appraisal:certainty=low` | - | 0% | 0% | 0 | – | no_effect | – |
| trust_prior | trust_prior:1 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_prior | trust_prior:1 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_prior | trust_prior:2 | `uncertainty:high` | - | 0% | 0% | 0 | – | no_effect | – |
| trust_prior | trust_prior:2 | `lead_appraisal:certainty=high` | + | 40% | 80% | 40 | – | pass | – |
| trust_prior | trust_prior:2 | `lead_appraisal:certainty=low` | - | 0% | 0% | 0 | – | no_effect | – |
| trust_prior | trust_prior:2 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_prior | trust_prior:2 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| threat_sensitivity | threat_sensitivity:0 | `lead_appraisal:relevance=high` | + | 0% | 40% | 40 | – | pass | – |
| threat_sensitivity | threat_sensitivity:0 | `lead_appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:0 | `lead_appraisal:congruence=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:0 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| threat_sensitivity | threat_sensitivity:0 | `fit:bias:threat_sensitivity` | info | 0% | 40% | 40 | – | info | – |
| threat_sensitivity | threat_sensitivity:1 | `lead_appraisal:relevance=high` | + | 20% | 0% | -20 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:1 | `lead_appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:1 | `lead_appraisal:congruence=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:1 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| threat_sensitivity | threat_sensitivity:1 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| threat_sensitivity | threat_sensitivity:2 | `lead_appraisal:relevance=high` | + | 0% | 20% | 20 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:2 | `lead_appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:2 | `lead_appraisal:congruence=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| threat_sensitivity | threat_sensitivity:2 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| threat_sensitivity | threat_sensitivity:2 | `fit:bias:threat_sensitivity` | info | 0% | 20% | 20 | – | info | – |
| hostile_attribution | hostile_attribution:0 | `lead_appraisal:affiliative_value=negative` | + | 80% | 80% | 0 | – | no_effect | – |
| hostile_attribution | hostile_attribution:0 | `lead_appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| hostile_attribution | hostile_attribution:0 | `leads:interpretation.pragmatic.indirect` | + | 20% | 20% | 0 | -15 | no_effect | – |
| hostile_attribution | hostile_attribution:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| hostile_attribution | hostile_attribution:0 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| hostile_attribution | hostile_attribution:1 | `lead_appraisal:affiliative_value=negative` | + | 20% | 60% | 40 | – | pass | – |
| hostile_attribution | hostile_attribution:1 | `lead_appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| hostile_attribution | hostile_attribution:1 | `leads:interpretation.pragmatic.indirect` | + | 0% | 40% | 40 | 29 | no_effect | – |
| hostile_attribution | hostile_attribution:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| hostile_attribution | hostile_attribution:1 | `fit:bias:hostile_attribution` | info | 40% | 0% | -40 | – | info | – |
| hostile_attribution | hostile_attribution:2 | `lead_appraisal:affiliative_value=negative` | + | 60% | 100% | 40 | – | pass | – |
| hostile_attribution | hostile_attribution:2 | `lead_appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| hostile_attribution | hostile_attribution:2 | `leads:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 4 | no_effect | – |
| hostile_attribution | hostile_attribution:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| hostile_attribution | hostile_attribution:2 | `fit:bias:hostile_attribution` | info | 20% | 20% | 0 | – | info | – |
| social_cue_sensitivity | social_cue_sensitivity:0 | `leads:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | -6 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:0 | `leads:interpretation.pragmatic.tone_situation_mismatch` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:0 | `leads:interpretation.pragmatic.literal` | - | 80% | 100% | 20 | 22 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:0 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_sensitivity | social_cue_sensitivity:0 | `fit:bias:social_cue_sensitivity` | info | 40% | 0% | -40 | – | info | – |
| social_cue_sensitivity | social_cue_sensitivity:1 | `leads:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:1 | `leads:interpretation.pragmatic.tone_situation_mismatch` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:1 | `leads:interpretation.pragmatic.literal` | - | 20% | 0% | -20 | -20 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:1 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_sensitivity | social_cue_sensitivity:1 | `fit:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_sensitivity | social_cue_sensitivity:2 | `leads:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:2 | `leads:interpretation.pragmatic.tone_situation_mismatch` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:2 | `leads:interpretation.pragmatic.literal` | - | 0% | 20% | 20 | 20 | no_effect | – |
| social_cue_sensitivity | social_cue_sensitivity:2 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_sensitivity | social_cue_sensitivity:2 | `fit:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| confirmation_tendency | confirmation_tendency:0 | `lead_appraisal:novelty=low` | + | 20% | 60% | 40 | – | pass | – |
| confirmation_tendency | confirmation_tendency:0 | `lead_appraisal:congruence=positive` | + | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:0 | `lead_appraisal:novelty=high` | - | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:0 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| confirmation_tendency | confirmation_tendency:0 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| confirmation_tendency | confirmation_tendency:1 | `lead_appraisal:novelty=low` | + | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:1 | `lead_appraisal:congruence=positive` | + | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:1 | `lead_appraisal:novelty=high` | - | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:1 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| confirmation_tendency | confirmation_tendency:1 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| confirmation_tendency | confirmation_tendency:2 | `lead_appraisal:novelty=low` | + | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:2 | `lead_appraisal:congruence=positive` | + | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:2 | `lead_appraisal:novelty=high` | - | 0% | 0% | 0 | – | no_effect | – |
| confirmation_tendency | confirmation_tendency:2 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| confirmation_tendency | confirmation_tendency:2 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| ambiguity_tolerance | ambiguity_tolerance:0 | `multi_reading` | + | 60% | 20% | -40 | – | wrong_direction | – |
| ambiguity_tolerance | ambiguity_tolerance:0 | `move_family:seek_information` | - | 100% | 100% | 0 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:0 | `unknown_any` | - | 100% | 100% | 0 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:0 | `cites:bias:ambiguity_tolerance` | info | 0% | 0% | 0 | – | info | – |
| ambiguity_tolerance | ambiguity_tolerance:0 | `fit:bias:ambiguity_tolerance` | info | 0% | 0% | 0 | – | info | – |
| ambiguity_tolerance | ambiguity_tolerance:1 | `multi_reading` | + | 100% | 100% | 0 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:1 | `move_family:seek_information` | - | 0% | 40% | 40 | – | wrong_direction | – |
| ambiguity_tolerance | ambiguity_tolerance:1 | `unknown_any` | - | 40% | 40% | 0 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:1 | `cites:bias:ambiguity_tolerance` | info | 0% | 0% | 0 | – | info | – |
| ambiguity_tolerance | ambiguity_tolerance:1 | `fit:bias:ambiguity_tolerance` | info | 0% | 0% | 0 | – | info | – |
| ambiguity_tolerance | ambiguity_tolerance:2 | `multi_reading` | + | 80% | 100% | 20 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:2 | `move_family:seek_information` | - | 100% | 100% | 0 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:2 | `unknown_any` | - | 100% | 100% | 0 | – | no_effect | – |
| ambiguity_tolerance | ambiguity_tolerance:2 | `cites:bias:ambiguity_tolerance` | info | 0% | 0% | 0 | – | info | – |
| ambiguity_tolerance | ambiguity_tolerance:2 | `fit:bias:ambiguity_tolerance` | info | 0% | 0% | 0 | – | info | – |
| social_affiliation_prior | social_affiliation_prior:0 | `leads:interpretation.goal.maintain_social_connection` | + | 100% | 100% | 0 | 0 | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:0 | `lead_appraisal:affiliative_value=positive` | + | 100% | 100% | 0 | – | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:0 | `leads:interpretation.goal.obtain_information` | - | 0% | 0% | 0 | 0 | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:0 | `cites:bias:social_affiliation_prior` | info | 0% | 0% | 0 | – | info | – |
| social_affiliation_prior | social_affiliation_prior:0 | `fit:bias:social_affiliation_prior` | info | 0% | 0% | 0 | – | info | – |
| social_affiliation_prior | social_affiliation_prior:1 | `leads:interpretation.goal.maintain_social_connection` | + | 100% | 100% | 0 | 0 | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:1 | `lead_appraisal:affiliative_value=positive` | + | 100% | 100% | 0 | – | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:1 | `leads:interpretation.goal.obtain_information` | - | 0% | 0% | 0 | 0 | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:1 | `cites:bias:social_affiliation_prior` | info | 0% | 0% | 0 | – | info | – |
| social_affiliation_prior | social_affiliation_prior:1 | `fit:bias:social_affiliation_prior` | info | 0% | 0% | 0 | – | info | – |
| social_affiliation_prior | social_affiliation_prior:2 | `leads:interpretation.goal.maintain_social_connection` | + | 100% | 100% | 0 | 0 | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:2 | `lead_appraisal:affiliative_value=positive` | + | 100% | 100% | 0 | – | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:2 | `leads:interpretation.goal.obtain_information` | - | 0% | 0% | 0 | 0 | no_effect | – |
| social_affiliation_prior | social_affiliation_prior:2 | `cites:bias:social_affiliation_prior` | info | 0% | 0% | 0 | – | info | – |
| social_affiliation_prior | social_affiliation_prior:2 | `fit:bias:social_affiliation_prior` | info | 0% | 0% | 0 | – | info | – |
| state | state:0 | `lead_appraisal:congruence=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:0 | `lead_appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:0 | `leads:interpretation.goal.repair_shared_understanding` | + | 0% | 0% | 0 | 0 | no_effect | – |
| state | state:0 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:0 | `move_family:support_socially` | - | 0% | 0% | 0 | – | no_effect | – |
| state | state:0 | `cites:state` | info | 0% | 0% | 0 | – | info | – |
| state | state:1 | `lead_appraisal:congruence=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| state | state:1 | `lead_appraisal:affiliative_value=negative` | + | 100% | 60% | -40 | – | wrong_direction | – |
| state | state:1 | `leads:interpretation.goal.repair_shared_understanding` | + | 0% | 20% | 20 | 8 | no_effect | – |
| state | state:1 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:1 | `move_family:support_socially` | - | 0% | 0% | 0 | – | no_effect | – |
| state | state:1 | `cites:state` | info | 0% | 100% | 100 | – | info | – |
| state | state:2 | `lead_appraisal:congruence=negative` | + | 0% | 60% | 60 | – | pass | – |
| state | state:2 | `lead_appraisal:affiliative_value=negative` | + | 0% | 20% | 20 | – | no_effect | – |
| state | state:2 | `leads:interpretation.goal.repair_shared_understanding` | + | 0% | 0% | 0 | 0 | no_effect | – |
| state | state:2 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:2 | `move_family:support_socially` | - | 0% | 0% | 0 | – | no_effect | – |
| state | state:2 | `cites:state` | info | 40% | 100% | 60 | – | info | – |
| state | state:3 | `lead_appraisal:congruence=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:3 | `lead_appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:3 | `leads:interpretation.goal.repair_shared_understanding` | + | 0% | 0% | 0 | 0 | no_effect | – |
| state | state:3 | `move:manage_dialogue.repair` | + | 0% | 0% | 0 | – | no_effect | – |
| state | state:3 | `move_family:support_socially` | - | 0% | 0% | 0 | – | no_effect | – |
| state | state:3 | `cites:state` | info | 100% | 0% | -100 | – | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
