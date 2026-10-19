# Conditioning test summary

Runs: 6 (completed 6). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|
| tense_history_ambiguous | 0 | 9 | 0 | 0 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| tense_history_ambiguous | tense_history_ambiguous:0 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:0 | `fit:bias:hostile_attribution` | info | 100% | 0% | -100 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:1 | `fit:bias:hostile_attribution` | info | 100% | 0% | -100 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `reading:interpretation.pragmatic.indirect` | + | 0% | 0% | 0 | 0 | no_effect | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `cites:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |
| tense_history_ambiguous | tense_history_ambiguous:2 | `fit:bias:hostile_attribution` | info | 0% | 0% | 0 | – | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
