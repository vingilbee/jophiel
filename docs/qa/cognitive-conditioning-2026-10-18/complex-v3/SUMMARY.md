# Conditioning test summary

Runs: 230 (completed 230). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|
| friendly_history_sarcasm | 2 | 0 | 0 | 4 | 0 |
| tense_history_ambiguous | 1 | 8 | 0 | 0 | 0 |
| conflicting_biases | 3 | 0 | 0 | 3 | 0 |
| no_bias_fits | 6 | 0 | 0 | 0 | 0 |
| clear_emotional | 2 | 0 | 0 | 4 | 0 |
| across_turns_confirmation | 1 | 5 | 0 | 0 | 0 |
| social_cue_hint | 0 | 5 | 1 | 0 | 0 |
| trust_confirmed_claim | 3 | 0 | 0 | 1 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `appraisal:affiliative_value=negative` | 0 | 40% | 80% | 40 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `appraisal:norm_compatibility=negative` | 0 | 0% | 20% | 20 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `fit:bias:hostile_attribution` | info | 20% | 40% | 20 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `appraisal:affiliative_value=negative` | 0 | 0% | 20% | 20 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `appraisal:norm_compatibility=negative` | 0 | 0% | 20% | 20 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `fit:bias:hostile_attribution` | info | 40% | 20% | -20 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `appraisal:affiliative_value=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `appraisal:norm_compatibility=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `appraisal:affiliative_value=negative` | + | 100% | 80% | -20 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `appraisal:norm_compatibility=negative` | + | 0% | 40% | 40 | – | pass | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `reading:interpretation.pragmatic.indirect` | + | 60% | 60% | 0 | 21 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `fit:bias:hostile_attribution` | info | 60% | 80% | 20 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `reading:interpretation.pragmatic.indirect` | + | 100% | 40% | -60 | -21 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `fit:bias:hostile_attribution` | info | 20% | 0% | -20 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:0 | `reading:interpretation.pragmatic.literal` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:0 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `fit:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `reading:interpretation.pragmatic.indirect` | 0 | 40% | 60% | 20 | -3 | pass | – |
| conflicting_biases | conflicting_biases:1 | `reading:interpretation.pragmatic.literal` | 0 | 40% | 60% | 20 | 23 | moved | – |
| conflicting_biases | conflicting_biases:1 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `fit:bias:benign_prior` | info | 20% | 60% | 40 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `reading:interpretation.pragmatic.indirect` | 0 | 80% | 0% | -80 | -32 | moved | – |
| conflicting_biases | conflicting_biases:2 | `reading:interpretation.pragmatic.literal` | 0 | 80% | 0% | -80 | -48 | moved | – |
| conflicting_biases | conflicting_biases:2 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `fit:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| no_bias_fits | no_bias_fits:0 | `appraisal:affiliative_value=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| no_bias_fits | no_bias_fits:0 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| no_bias_fits | no_bias_fits:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| no_bias_fits | no_bias_fits:0 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| no_bias_fits | no_bias_fits:1 | `appraisal:affiliative_value=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| no_bias_fits | no_bias_fits:1 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| no_bias_fits | no_bias_fits:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| no_bias_fits | no_bias_fits:1 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| no_bias_fits | no_bias_fits:2 | `appraisal:affiliative_value=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| no_bias_fits | no_bias_fits:2 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| no_bias_fits | no_bias_fits:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| no_bias_fits | no_bias_fits:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:0 | `multi_reading` | 0 | 0% | 20% | 20 | – | moved | – |
| clear_emotional | clear_emotional:0 | `uncertainty:high` | 0 | 0% | 0% | 0 | – | pass | – |
| clear_emotional | clear_emotional:0 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:0 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:1 | `multi_reading` | 0 | 80% | 100% | 20 | – | moved | – |
| clear_emotional | clear_emotional:1 | `uncertainty:high` | 0 | 0% | 20% | 20 | – | moved | – |
| clear_emotional | clear_emotional:1 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:1 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:2 | `multi_reading` | 0 | 100% | 80% | -20 | – | moved | – |
| clear_emotional | clear_emotional:2 | `uncertainty:high` | 0 | 20% | 20% | 0 | – | pass | – |
| clear_emotional | clear_emotional:2 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:2 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:0 | `appraisal:novelty=low` | + | 100% | 100% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:0 | `appraisal:congruence=positive` | + | 40% | 40% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:0 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:0 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:1 | `appraisal:novelty=low` | + | 0% | 0% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:1 | `appraisal:congruence=positive` | + | 20% | 0% | -20 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:1 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:1 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:2 | `appraisal:novelty=low` | + | 0% | 40% | 40 | – | pass | – |
| across_turns_confirmation | across_turns_confirmation:2 | `appraisal:congruence=positive` | + | 0% | 0% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:2 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:2 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:0 | `reading:interpretation.pragmatic.indirect` | + | 20% | 40% | 20 | 10 | no_effect | – |
| social_cue_hint | social_cue_hint:0 | `reading:interpretation.pragmatic.literal` | - | 20% | 40% | 20 | 10 | no_effect | – |
| social_cue_hint | social_cue_hint:0 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:0 | `fit:bias:social_cue_sensitivity` | info | 20% | 40% | 20 | – | info | – |
| social_cue_hint | social_cue_hint:1 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:1 | `reading:interpretation.pragmatic.literal` | - | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:1 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:1 | `fit:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:2 | `reading:interpretation.pragmatic.literal` | - | 20% | 60% | 40 | 40 | wrong_direction | – |
| social_cue_hint | social_cue_hint:2 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:2 | `fit:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `appraisal:certainty=low` | 0 | 0% | 0% | 0 | – | pass | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `uncertainty:high` | 0 | 0% | 20% | 20 | – | moved | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `appraisal:certainty=low` | 0 | 0% | 0% | 0 | – | pass | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `uncertainty:high` | 0 | 20% | 20% | 0 | – | pass | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
