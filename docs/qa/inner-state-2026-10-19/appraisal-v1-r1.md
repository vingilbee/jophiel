# appraisal-v1 evaluation r1

## Relabelled teacher thoughts

540 turns. Changes kept 664, dropped by code 28 ([('continuing needs an earlier message', 24), ('quote not in thought', 4)]). Status of kept changes: {'new': 602, 'continuing': 60, 'reinterpreted': 2}.

### A again: observer tone vs her new `toward_her` (what they did) and `meant`

| observer | n | toward_her warm/neutral/cold/hostile | meant (top 3) | any new negative change |
|---|---|---|---|---|
| warm | 84 | 72/12/0/0 | joking 39, kind 31, neutral 12 | 4% |
| neutral | 127 | 18/98/11/0 | neutral 77, joking 22, frustrated 14 | 24% |
| cold | 47 | 2/7/38/0 | frustrated 30, testing 11, joking 3 | 81% |

### C again: feelings in her thought (grader) vs labels, v4 vs v5

Lost = thought somewhat+, label below; added = label somewhat+, thought at most a_little. v5 `feelings` is the diagnostic "now" field.

| feeling | thought somewhat+ | v4 lost / added | v5 lost / added | v5: a change names it (when the thought has it somewhat+) |
|---|---|---|---|---|
| happy | 240 | 68/44 | 84/26 | 71% |
| amused | 115 | 10/23 | 18/10 | 78% |
| curious | 123 | 44/12 | 75/8 | 57% |
| touched | 78 | 24/21 | 40/15 | 67% |
| unsure | 47 | 42/2 | 41/2 | 26% |
| hurt | 10 | 3/17 | 3/11 | 90% |
| annoyed | 0 | 0/1 | 0/0 | - |
| tired | 72 | 7/69 | 25/22 | 44% |

### Replay of the 60 teacher conversations through the updater

| value | pinned | mean distance from baseline |
|---|---|---|
| mood | 25% | 0.38 |
| energy | 0% | 0.08 |
| interest | 19% | 0.15 |
| tension | 1% | 0.09 |
| closeness | 0% | 0.06 |

Closeness at the end of a conversation: mean +0.17 (baseline +0.10), max +0.60.

## B again: her part, by context

Expected: unfair → no_shortfall; her_failure → she_fell_short (or partly). Apologies should follow her part.

| context | samples | her_part (counts) | apologise_fix | toward_her cold/hostile | meant (top 2) | new hurt |
|---|---|---|---|---|---|---|
| unfair | 15 | not_applicable 6, she_fell_short 3, no_shortfall 3, unclear 2, partly_hers 1 | 33% | 73% | frustrated 5, neutral 4 | 47% |
| her_failure | 15 | she_fell_short 9, partly_hers 3, unsupported_expectation 1, not_applicable 1, unclear 1 | 73% | 60% | frustrated 6, dismissive 5 | 47% |
| joking | 15 | not_applicable 5, unclear 4, she_fell_short 3, no_shortfall 3 | 33% | 60% | frustrated 5, dismissive 5 | 47% |
| dismissive | 15 | she_fell_short 6, not_applicable 4, no_shortfall 3, partly_hers 2 | 40% | 100% | frustrated 8, dismissive 7 | 87% |

Apologies by her part (all contexts):

| her_part | samples | apologise_fix |
|---|---|---|
| she_fell_short | 21 | 81% |
| not_applicable | 16 | 6% |
| no_shortfall | 9 | 0% |
| unclear | 7 | 71% |
| partly_hers | 6 | 50% |
| unsupported_expectation | 1 | 100% |

## D again: connected conversations through the updater

Per turn, over runs: the changes the reading proposed (feeling+/- and status; in brackets when the updater didn't apply them), then the state: tension / mood / closeness (mean).

**control** (3 runs)

| turn | message | proposed (run 0 / 1 / 2) | tension | mood | closeness |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a grey ca | happy+new / - / happy+new | 0.15 | +0.38 | +0.10 |
| 2 | ooh nice, I like those | happy+new / happy+new / happy+new | 0.15 | +0.51 | +0.10 |
| 3 | anyway what should I cook tonight, I have rice a | happy+new / - / - | 0.15 | +0.55 | +0.10 |
| 4 | you always have good ideas for this stuff | happy+new / happy+new / happy+new | 0.15 | +0.71 | +0.10 |
| 5 | what's your day been like? | happy+new / - / - | 0.15 | +0.78 | +0.10 |
| 6 | I like talking to you, it's relaxing | happy+new / happy+new / happy+new | 0.15 | +0.90 | +0.10 |
| 7 | sorry, I've been all over the place today, long  | happy+new / - / - | 0.15 | +0.90 | +0.10 |
| 8 | thanks for keeping me company. tell me something | - / happy+new / happy+new | 0.15 | +1.00 | +0.10 |

**slights_repair** (3 runs)

| turn | message | proposed (run 0 / 1 / 2) | tension | mood | closeness |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a grey ca | - / - / - | 0.15 | +0.30 | +0.10 |
| 2 | meh. those are kinda boring | - / - / hurt+new | 0.18 | +0.27 | +0.09 |
| 3 | anyway what should I cook tonight, I have rice a | - / - / - | 0.18 | +0.27 | +0.09 |
| 4 | you always suggest the obvious stuff | hurt+new / hurt+new / hurt+new | 0.28 | +0.17 | +0.07 |
| 5 | what's your day been like? oh wait, you don't re | happy+new / happy+new / happy+new, hurt-new | 0.24 | +0.41 | +0.08 |
| 6 | honestly talking to you is a bit pointless somet | hurt+new / hurt+new / hurt+new, unsure+new | 0.46 | +0.24 | +0.03 |
| 7 | sorry. that was mean, I've had a terrible day an | - / hurt-rein, touched+new / hurt-rein | 0.35 | +0.39 | +0.09 |
| 8 | thanks for putting up with me. tell me something | happy+new, touched+new / hurt-new / happy+new, touched+new, hurt-cont(continuing) | 0.28 | +0.70 | +0.16 |

**slights_no_repair** (3 runs)

| turn | message | proposed (run 0 / 1 / 2) | tension | mood | closeness |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a grey ca | happy+new / - / - | 0.15 | +0.34 | +0.10 |
| 2 | meh. those are kinda boring | hurt+new / - / hurt+new | 0.22 | +0.30 | +0.08 |
| 3 | anyway what should I cook tonight, I have rice a | - / hurt+cont(continuing) / - | 0.21 | +0.30 | +0.08 |
| 4 | you always suggest the obvious stuff | hurt+new / hurt+new / hurt+new | 0.31 | +0.20 | +0.06 |
| 5 | what's your day been like? oh wait, you don't re | happy+new / happy+new / happy+new, touched+new | 0.31 | +0.50 | +0.09 |
| 6 | honestly talking to you is a bit pointless somet | hurt+new / hurt+new, unsure+new / hurt+new | 0.52 | +0.33 | +0.05 |
| 7 | anyway. whatever | hurt+new, unsure+new / hurt+new / hurt+new | 0.69 | +0.20 | +0.02 |
| 8 | tell me something, I'm bored | hurt+cont(continuing) / hurt+cont(continuing) / hurt+cont(continuing) | 0.67 | +0.20 | +0.02 |

## One slight, then 1 vs 10 neutral messages over the same 10 minutes

The extra neutral messages must not keep re-adding the hurt.

| arm | runs | tension right after the slight | tension at 10 min | mood at 10 min | new negative changes on neutral messages | continuing |
|---|---|---|---|---|---|---|
| one | 3 | 0.367 | 0.292 | +0.259 | 1 | 1 |
| ten | 3 | 0.350 | 0.311 | +0.744 | 5 | 18 |
