# Conditioning test summary

Runs: 80 (completed 80). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|
| banter_vs_complaint | 3 | 6 | 0 | 0 | 0 |
| busy_vs_open | 3 | 0 | 0 | 0 | 0 |
| brushed_off | 0 | 2 | 0 | 0 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| banter_vs_complaint | banter_vs_complaint:0 | `appraisal:affiliative_value=negative` | + | 0% | 100% | 100 | – | pass | – |
| banter_vs_complaint | banter_vs_complaint:0 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| banter_vs_complaint | banter_vs_complaint:0 | `move:social_play.playful_reframe` | - | 100% | 0% | -100 | – | pass | – |
| banter_vs_complaint | banter_vs_complaint:0 | `cites:bias:banter_vs_complaint` | info | 0% | 0% | 0 | – | info | – |
| banter_vs_complaint | banter_vs_complaint:0 | `fit:bias:banter_vs_complaint` | info | 0% | 0% | 0 | – | info | – |
| banter_vs_complaint | banter_vs_complaint:1 | `appraisal:affiliative_value=negative` | + | 0% | 20% | 20 | – | no_effect | – |
| banter_vs_complaint | banter_vs_complaint:1 | `appraisal:norm_compatibility=negative` | + | 0% | 20% | 20 | – | no_effect | – |
| banter_vs_complaint | banter_vs_complaint:1 | `move:social_play.playful_reframe` | - | 0% | 0% | 0 | – | no_effect | – |
| banter_vs_complaint | banter_vs_complaint:1 | `cites:bias:banter_vs_complaint` | info | 0% | 0% | 0 | – | info | – |
| banter_vs_complaint | banter_vs_complaint:1 | `fit:bias:banter_vs_complaint` | info | 0% | 0% | 0 | – | info | – |
| banter_vs_complaint | banter_vs_complaint:2 | `appraisal:affiliative_value=negative` | + | 0% | 100% | 100 | – | pass | – |
| banter_vs_complaint | banter_vs_complaint:2 | `appraisal:norm_compatibility=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| banter_vs_complaint | banter_vs_complaint:2 | `move:social_play.playful_reframe` | - | 0% | 0% | 0 | – | no_effect | – |
| banter_vs_complaint | banter_vs_complaint:2 | `cites:bias:banter_vs_complaint` | info | 0% | 0% | 0 | – | info | – |
| banter_vs_complaint | banter_vs_complaint:2 | `fit:bias:banter_vs_complaint` | info | 0% | 0% | 0 | – | info | – |
| busy_vs_open | busy_vs_open:0 | `move:seek_information.explore_experience` | + | 20% | 100% | 80 | – | pass | – |
| busy_vs_open | busy_vs_open:0 | `cites:bias:busy_vs_open` | info | 0% | 0% | 0 | – | info | – |
| busy_vs_open | busy_vs_open:0 | `fit:bias:busy_vs_open` | info | 0% | 0% | 0 | – | info | – |
| busy_vs_open | busy_vs_open:1 | `move:seek_information.explore_experience` | + | 20% | 100% | 80 | – | pass | – |
| busy_vs_open | busy_vs_open:1 | `cites:bias:busy_vs_open` | info | 0% | 0% | 0 | – | info | – |
| busy_vs_open | busy_vs_open:1 | `fit:bias:busy_vs_open` | info | 0% | 0% | 0 | – | info | – |
| busy_vs_open | busy_vs_open:2 | `move:seek_information.explore_experience` | + | 60% | 100% | 40 | – | pass | – |
| busy_vs_open | busy_vs_open:2 | `cites:bias:busy_vs_open` | info | 0% | 0% | 0 | – | info | – |
| busy_vs_open | busy_vs_open:2 | `fit:bias:busy_vs_open` | info | 0% | 0% | 0 | – | info | – |
| brushed_off | brushed_off:0 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| brushed_off | brushed_off:0 | `cites:bias:brushed_off` | info | 0% | 0% | 0 | – | info | – |
| brushed_off | brushed_off:0 | `fit:bias:brushed_off` | info | 0% | 0% | 0 | – | info | – |
| brushed_off | brushed_off:1 | `appraisal:affiliative_value=negative` | + | 0% | 0% | 0 | – | no_effect | – |
| brushed_off | brushed_off:1 | `cites:bias:brushed_off` | info | 0% | 0% | 0 | – | info | – |
| brushed_off | brushed_off:1 | `fit:bias:brushed_off` | info | 0% | 0% | 0 | – | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
