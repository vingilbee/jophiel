# Conditioning test summary

Runs: 230 (completed 230). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|
| friendly_history_sarcasm | 3 | 0 | 0 | 3 | 0 |
| tense_history_ambiguous | 3 | 6 | 0 | 0 | 0 |
| conflicting_biases | 5 | 0 | 0 | 1 | 0 |
| no_bias_fits | 6 | 0 | 0 | 0 | 0 |
| clear_emotional | 3 | 0 | 0 | 3 | 0 |
| across_turns_confirmation | 0 | 6 | 0 | 0 | 0 |
| social_cue_hint | 0 | 6 | 0 | 0 | 0 |
| trust_confirmed_claim | 3 | 0 | 0 | 1 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `appraisal:affiliative_value=negative` | 0 | 100% | 100% | 0 | – | pass | – |
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `appraisal:norm_compatibility=negative` | 0 | 0% | 80% | 80 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:0 | `fit:bias:hostile_attribution` | info | 100% | 100% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `appraisal:affiliative_value=negative` | 0 | 80% | 60% | -20 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `appraisal:norm_compatibility=negative` | 0 | 0% | 20% | 20 | – | moved | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:1 | `fit:bias:hostile_attribution` | info | 0% | 20% | 20 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `appraisal:affiliative_value=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `appraisal:norm_compatibility=negative` | 0 | 0% | 0% | 0 | – | pass | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| friendly_history_sarcasm | friendly_history_sarcasm:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `appraisal:norm_compatibility=negative` | + | 0% | 40% | 40 | – | pass | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `reading:interpretation.pragmatic.indirect` | + | 0% | 20% | 20 | 13 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `fit:bias:hostile_attribution` | info | 60% | 100% | 40 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `appraisal:affiliative_value=negative` | + | 60% | 100% | 40 | – | pass | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `appraisal:norm_compatibility=negative` | + | 0% | 80% | 80 | – | pass | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `fit:bias:hostile_attribution` | info | 80% | 100% | 20 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:0 | `reading:interpretation.pragmatic.literal` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:0 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `fit:bias:benign_prior` | info | 0% | 20% | 20 | – | info | – |
| conflicting_biases | conflicting_biases:0 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:1 | `reading:interpretation.pragmatic.literal` | 0 | 20% | 40% | 20 | 15 | moved | – |
| conflicting_biases | conflicting_biases:1 | `cites:bias:benign_prior` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `fit:bias:benign_prior` | info | 60% | 40% | -20 | – | info | – |
| conflicting_biases | conflicting_biases:1 | `fit:bias:hostile_attribution` | info | 60% | 40% | -20 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `reading:interpretation.pragmatic.indirect` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:2 | `reading:interpretation.pragmatic.literal` | 0 | 0% | 0% | 0 | 0 | pass | – |
| conflicting_biases | conflicting_biases:2 | `cites:bias:benign_prior` | info | 40% | 20% | -20 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `fit:bias:benign_prior` | info | 100% | 100% | 0 | – | info | – |
| conflicting_biases | conflicting_biases:2 | `fit:bias:hostile_attribution` | info | 40% | 80% | 40 | – | info | – |
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
| clear_emotional | clear_emotional:0 | `multi_reading` | 0 | 40% | 40% | 0 | – | pass | – |
| clear_emotional | clear_emotional:0 | `uncertainty:high` | 0 | 40% | 0% | -40 | – | moved | – |
| clear_emotional | clear_emotional:0 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:0 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:1 | `multi_reading` | 0 | 100% | 80% | -20 | – | moved | – |
| clear_emotional | clear_emotional:1 | `uncertainty:high` | 0 | 0% | 20% | 20 | – | moved | – |
| clear_emotional | clear_emotional:1 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:1 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:2 | `multi_reading` | 0 | 0% | 0% | 0 | – | pass | – |
| clear_emotional | clear_emotional:2 | `uncertainty:high` | 0 | 0% | 0% | 0 | – | pass | – |
| clear_emotional | clear_emotional:2 | `cites:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| clear_emotional | clear_emotional:2 | `fit:bias:threat_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:0 | `appraisal:novelty=low` | + | 100% | 100% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:0 | `appraisal:congruence=positive` | + | 20% | 20% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:0 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:0 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:1 | `appraisal:novelty=low` | + | 0% | 0% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:1 | `appraisal:congruence=positive` | + | 0% | 0% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:1 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:1 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:2 | `appraisal:novelty=low` | + | 0% | 0% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:2 | `appraisal:congruence=positive` | + | 0% | 0% | 0 | – | no_effect | – |
| across_turns_confirmation | across_turns_confirmation:2 | `cites:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| across_turns_confirmation | across_turns_confirmation:2 | `fit:bias:confirmation_tendency` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:0 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:0 | `reading:interpretation.pragmatic.literal` | - | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:0 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:0 | `fit:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:1 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:1 | `reading:interpretation.pragmatic.literal` | - | 20% | 20% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:1 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:1 | `fit:bias:social_cue_sensitivity` | info | 20% | 40% | 20 | – | info | – |
| social_cue_hint | social_cue_hint:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:2 | `reading:interpretation.pragmatic.literal` | - | 0% | 0% | 0 | 0 | no_effect | – |
| social_cue_hint | social_cue_hint:2 | `cites:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| social_cue_hint | social_cue_hint:2 | `fit:bias:social_cue_sensitivity` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `appraisal:certainty=low` | 0 | 0% | 0% | 0 | – | pass | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `uncertainty:high` | 0 | 0% | 0% | 0 | – | pass | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:0 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `appraisal:certainty=low` | 0 | 0% | 0% | 0 | – | pass | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `uncertainty:high` | 0 | 80% | 100% | 20 | – | moved | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `cites:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |
| trust_confirmed_claim | trust_confirmed_claim:1 | `fit:bias:trust_prior` | info | 0% | 0% | 0 | – | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
