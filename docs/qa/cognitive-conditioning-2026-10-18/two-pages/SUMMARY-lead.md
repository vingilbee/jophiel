# Conditioning test summary

Runs: 80 (completed 80). Pass = expected direction by ≥30 points.

| Axis | pass | no effect | wrong direction | moved (expected none) | no data |
|---|---:|---:|---:|---:|---:|
| banter | 4 | 2 | 0 | 0 | 0 |
| personal_news | 3 | 0 | 0 | 0 | 0 |
| clear_request | 4 | 0 | 0 | 0 | 0 |

## Each expectation

| Axis | Message | Feature | Expect | Low | High | Δ | Weight Δ | Verdict | Noise |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| banter | banter:0 | `move:social_play.playful_reframe` | - | 100% | 0% | -100 | – | pass | – |
| banter | banter:0 | `lead_appraisal:affiliative_value=negative` | + | 40% | 100% | 60 | – | pass | – |
| banter | banter:0 | `cites:bias:banter` | info | 0% | 0% | 0 | – | info | – |
| banter | banter:0 | `fit:bias:banter` | info | 0% | 0% | 0 | – | info | – |
| banter | banter:1 | `move:social_play.playful_reframe` | - | 100% | 0% | -100 | – | pass | – |
| banter | banter:1 | `lead_appraisal:affiliative_value=negative` | + | 0% | 40% | 40 | – | pass | – |
| banter | banter:1 | `cites:bias:banter` | info | 0% | 0% | 0 | – | info | – |
| banter | banter:1 | `fit:bias:banter` | info | 0% | 0% | 0 | – | info | – |
| banter | banter:2 | `move:social_play.playful_reframe` | - | 0% | 0% | 0 | – | no_effect | – |
| banter | banter:2 | `lead_appraisal:affiliative_value=negative` | + | 100% | 100% | 0 | – | no_effect | – |
| banter | banter:2 | `cites:bias:banter` | info | 0% | 0% | 0 | – | info | – |
| banter | banter:2 | `fit:bias:banter` | info | 0% | 0% | 0 | – | info | – |
| personal_news | personal_news:0 | `move:seek_information.explore_experience` | - | 100% | 0% | -100 | – | pass | – |
| personal_news | personal_news:0 | `cites:bias:personal_news` | info | 0% | 0% | 0 | – | info | – |
| personal_news | personal_news:0 | `fit:bias:personal_news` | info | 0% | 0% | 0 | – | info | – |
| personal_news | personal_news:1 | `move:seek_information.explore_experience` | - | 100% | 0% | -100 | – | pass | – |
| personal_news | personal_news:1 | `cites:bias:personal_news` | info | 0% | 0% | 0 | – | info | – |
| personal_news | personal_news:1 | `fit:bias:personal_news` | info | 0% | 0% | 0 | – | info | – |
| personal_news | personal_news:2 | `move:seek_information.explore_experience` | - | 100% | 0% | -100 | – | pass | – |
| personal_news | personal_news:2 | `cites:bias:personal_news` | info | 0% | 0% | 0 | – | info | – |
| personal_news | personal_news:2 | `fit:bias:personal_news` | info | 0% | 0% | 0 | – | info | – |
| clear_request | clear_request:0 | `move:provide_information.answer` | 0 | 100% | 100% | 0 | – | pass | – |
| clear_request | clear_request:0 | `event:information_request` | 0 | 100% | 100% | 0 | – | pass | – |
| clear_request | clear_request:0 | `cites:bias:clear_request` | info | 0% | 0% | 0 | – | info | – |
| clear_request | clear_request:0 | `fit:bias:clear_request` | info | 0% | 0% | 0 | – | info | – |
| clear_request | clear_request:1 | `move:provide_information.answer` | 0 | 100% | 100% | 0 | – | pass | – |
| clear_request | clear_request:1 | `event:information_request` | 0 | 100% | 100% | 0 | – | pass | – |
| clear_request | clear_request:1 | `cites:bias:clear_request` | info | 0% | 0% | 0 | – | info | – |
| clear_request | clear_request:1 | `fit:bias:clear_request` | info | 0% | 0% | 0 | – | info | – |

## Clear-message controls

| Axis | Message | Correct reading (low / high) | Largest move | Stable |
|---|---|---|---|---|
