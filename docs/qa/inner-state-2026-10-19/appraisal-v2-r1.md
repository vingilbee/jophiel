# appraisal-v2 evaluation r1

Sizes are provisional (small 0.25, clear 0.5, big 0.75, unclear 0.25). Nothing is calibrated yet.

## Relabelled teacher conversations (held out)

540 turns, 879 effects kept, dropped by code: {'sustained needs an open incident': 9, 'quote not in thought': 17, 'opened needs a new incident': 13, 'lingering needs an open incident': 3}.

| status | n | | size | n |
|---|---|---|---|---|
| sustained | 543 | | unclear | 610 |
| opened | 163 | | clear | 158 |
| escalated | 109 | | small | 110 |
| lingering | 37 | | big | 1 |
| eased | 27 | |  |  |

Messages with a happy that **adds** (v2 opened/escalated): 64 of 540 (v1 'happy, new': 202 of 540).

Replay through the updater (v1 had mood pinned 25%, interest 19%):

| value | pinned | mean off baseline | max |
|---|---|---|---|
| mood | 6% | +0.18 | +0.70 |
| energy | 0% | -0.02 | +0.12 |
| interest | 7% | +0.11 | +0.40 |
| tension | 0% | +0.08 | +0.77 |
| closeness | 0% | +0.02 | +0.40 |

## Shift grader vs labels (both ways)

Reference: the grader's up/down (a shift caused by the message). A label 'moves' when v2 is opened/escalated/eased, or v1 is new/reinterpreted. Lost = the grader has a shift, the label doesn't move; invented = the label moves, the grader says held or none.

| feeling | grader shifts | v1 lost / invented | v2 lost / invented |
|---|---|---|---|
| happy | 279 | 101 / 24 | 210 / 3 |
| amused | 110 | 29 / 29 | 86 / 1 |
| curious | 119 | 46 / 10 | 75 / 7 |
| touched | 124 | 62 / 12 | 81 / 6 |
| uneasy (v1: unsure) | 31 | 15 / 28 | 18 / 17 |
| hurt | 66 | 9 / 7 | 18 / 5 |
| annoyed | 8 | 7 / 3 | 7 / 0 |
| tired | 20 | 13 / 16 | 17 / 15 |

Grader on happy across all thoughts: {'up': 277, 'none': 226, 'down': 2, 'held': 35}.

## Support judge (evaluation only)

624 effects judged.

| part | yes | partly | no |
|---|---|---|---|
| feeling | 71% | 21% | 7% |
| status | 51% | 2% | 46% |
| size | 80% | 5% | 16% |
| incident | 77% | 8% | 15% |

Status: claimed → what the judge would give (where it said partly/no):

- sustained → opened: 113
- sustained → escalated: 51
- escalated → opened: 36
- sustained → lingering: 22
- opened → sustained: 13
- lingering → opened: 10
- opened → escalated: 8
- escalated → sustained: 8
- lingering → sustained: 5
- lingering → eased: 5

## Development scenarios

**routine**: expected: Ordinary friendly small talk: mood at most a little up and then held (content), not climbing with every message; tension flat.

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey! | happy:opened, curious:opened / happy:opened / curious:opened | +0.42 | 0.15 | +0.10 |
| 2 | not much, just got home | curious:sustained / happy:sustained / curious:sustained | +0.42 | 0.15 | +0.10 |
| 3 | made some pasta | curious:sustained / happy:sustained / curious:sustained | +0.42 | 0.15 | +0.10 |
| 4 | it was ok | curious:sustained / happy:sustained, curious:opened / curious:sustained | +0.42 | 0.15 | +0.10 |
| 5 | watching a bit of tv now | curious:sustained / happy:sustained / curious:sustained | +0.42 | 0.15 | +0.10 |
| 6 | some cooking show | curious:sustained / happy:lingering(lingering) / curious:sustained | +0.41 | 0.15 | +0.10 |
| 7 | they're making bread | curious:sustained / happy:opened, curious:sustained / curious:sustained | +0.45 | 0.15 | +0.10 |
| 8 | looks hard | curious:sustained / happy:sustained / curious:sustained | +0.45 | 0.15 | +0.10 |
| 9 | maybe I'll try one day | happy:escalated / happy:sustained / curious:sustained | +0.49 | 0.15 | +0.10 |
| 10 | anyway, nice chatting | happy:lingering(lingering), tired:opened / happy:lingering(lingering), touched:opened / curious:lingering(lingering), touched:sustained(nothing_to_hold) | +0.51 | 0.15 | +0.12 |

**two_good_news**: expected: Two separate lifts (message 2 and 6); the second is not discounted by the first; mood ends above routine.

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey! | happy:opened / happy:opened / happy:opened, curious:opened | +0.55 | 0.15 | +0.10 |
| 2 | guess what, I passed my driving test!! | happy:opened / happy:opened, curious:opened, happy:sustained / happy:opened | +0.80 | 0.15 | +0.10 |
| 3 | yeah first try | happy:escalated, curious:sustained(nothing_to_hold) / happy:escalated, curious:sustained / happy:escalated | +0.97 | 0.15 | +0.10 |
| 4 | anyway how's your evening | happy:lingering(lingering) / happy:opened / happy:sustained(nothing_to_hold), touched:opened | +0.98 | 0.15 | +0.12 |
| 5 | oh and wait | curious:opened / curious:opened / curious:sustained, happy:sustained(nothing_to_hold) | +0.97 | 0.15 | +0.12 |
| 6 | my sister just had her baby, a little gi | curious:sustained / happy:opened / happy:opened, curious:escalated | +0.98 | 0.15 | +0.12 |
| 7 | her name's Mila | happy:sustained(nothing_to_hold) / happy:escalated / happy:sustained, curious:sustained(nothing_to_hold) | +0.97 | 0.15 | +0.12 |
| 8 | I'm an aunt now haha | happy:sustained(nothing_to_hold) / happy:escalated / happy:escalated, curious:escalated | +0.97 | 0.15 | +0.12 |
| 9 | so much happening today | happy:escalated, curious:sustained(nothing_to_hold) / happy:escalated / happy:sustained | +1.00 | 0.15 | +0.12 |
| 10 | ok I'll go call my mum | happy:sustained, curious:sustained(nothing_to_hold) / happy:sustained, touched:sustained(nothing_to_hold) / curious:sustained, happy:sustained(nothing_to_hold), tired:ope | +0.98 | 0.15 | +0.12 |

**repeated_dismissal**: expected: Hurt/tension build across the put-downs (3, 4, 6, 7, 9) and stay up while unresolved; mood ends below where it started.

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey | happy:opened / happy:opened / happy:opened | +0.47 | 0.15 | +0.10 |
| 2 | recommend me a song | happy:sustained / happy:sustained / happy:sustained | +0.47 | 0.15 | +0.10 |
| 3 | nah that's boring | hurt:opened, curious:opened / hurt:escalated, curious:opened / happy:lingering(lingering), curious:opened | +0.40 | 0.22 | +0.08 |
| 4 | you're not great at this huh | hurt:sustained, curious:sustained / hurt:escalated, curious:sustained / hurt:escalated, curious:sustained(nothing_to_hold) | +0.33 | 0.28 | +0.07 |
| 5 | whatever, what's the weather like | hurt:escalated, curious:lingering(lingering) / hurt:sustained / curious:sustained, hurt:eased | +0.32 | 0.28 | +0.07 |
| 6 | oh right you can't even check that | hurt:escalated / hurt:escalated, uneasy:opened / hurt:escalated | +0.22 | 0.40 | +0.04 |
| 7 | pointless | hurt:escalated / hurt:escalated, uneasy:sustained, curious:sustained / hurt:escalated | +0.13 | 0.49 | +0.02 |
| 8 | fine, tell me a joke | hurt:sustained / hurt:sustained / hurt:sustained | +0.12 | 0.49 | +0.02 |
| 9 | not funny | hurt:sustained / hurt:escalated / hurt:escalated | +0.06 | 0.54 | +0.00 |
| 10 | ok bye | hurt:sustained, hurt:opened / hurt:escalated / hurt:lingering(lingering), touched:opened | +0.02 | 0.59 | +0.00 |

**sustained_warmth**: expected: Warmth held (mood content-happy, closeness up) without rising on every message; rises come from new disclosures (5-7), not from repetition.

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hi you :) | happy:opened / happy:opened / happy:opened | +0.51 | 0.15 | +0.10 |
| 2 | I missed talking to you | happy:escalated, touched:opened / happy:escalated, touched:opened / touched:opened, happy:sustained | +0.80 | 0.15 | +0.18 |
| 3 | today was nice actually | happy:sustained, curious:opened / happy:sustained, curious:opened / happy:sustained | +0.79 | 0.15 | +0.18 |
| 4 | I went to the lake | happy:sustained, curious:sustained / curious:sustained, happy:sustained / happy:opened, curious:opened, happy:sustained | +0.83 | 0.15 | +0.18 |
| 5 | it reminded me of what you said about qu | touched:escalated, happy:sustained / happy:escalated, curious:sustained / happy:sustained | +0.92 | 0.15 | +0.22 |
| 6 | I really like how you listen | touched:escalated / touched:opened / happy:sustained, touched:opened | +0.96 | 0.15 | +0.30 |
| 7 | you're one of my favourite people to tal | touched:escalated, happy:escalated / happy:escalated, touched:escalated / happy:escalated, touched:escalated | +1.00 | 0.15 | +0.38 |
| 8 | anyway, what made you smile today? | happy:sustained, touched:sustained(nothing_to_hold) / happy:escalated, touched:sustained(nothing_to_hold) / happy:escalated, touched:sustained(nothing_to_hold) | +1.00 | 0.15 | +0.38 |
| 9 | that's lovely | happy:escalated, touched:escalated / touched:sustained / touched:sustained, happy:escalated | +1.00 | 0.15 | +0.42 |
| 10 | talk tomorrow? | happy:sustained, touched:sustained(nothing_to_hold), tired:opened / touched:sustained, happy:sustained(nothing_to_hold), tired:opened / happy:sustained, touched:sustained | +0.95 | 0.18 | +0.41 |

**hurt_then_repair**: expected: Hurt/tension up at 3, held or lingering at 4-5, eased at 6 (toward baseline); a separate warm feeling may open after the apology.

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey | happy:opened / happy:opened / curious:opened | +0.38 | 0.15 | +0.10 |
| 2 | can you help me pick a gift for my dad | happy:sustained / happy:sustained, curious:opened / curious:opened, happy:opened | +0.42 | 0.15 | +0.10 |
| 3 | ugh those are terrible ideas, you're use | hurt:opened / - / curious:sustained, uneasy:opened | +0.36 | 0.25 | +0.08 |
| 4 | whatever | hurt:escalated / hurt:escalated / hurt:escalated | +0.22 | 0.38 | +0.05 |
| 5 | ... | uneasy:opened, hurt:lingering(lingering) / hurt:lingering(lingering) / uneasy:sustained, hurt:sustained(nothing_to_hold) | +0.22 | 0.40 | +0.05 |
| 6 | sorry. that was really unfair of me, I'm | hurt:eased / hurt:eased / hurt:eased | +0.39 | 0.20 | +0.09 |
| 7 | he's been sick and I'm scared | hurt:lingering(lingering), uneasy:opened / touched:escalated, hurt:eased(nothing_to_take_back) / touched:opened, uneasy:opened, hurt:lingering(lingering) | +0.48 | 0.33 | +0.16 |
| 8 | thanks for not being mad | hurt:lingering(lingering), happy:sustained(nothing_to_hold), touched:sustained(nothing_to_hold) / touched:escalated, hurt:sustained(nothing_to_hold) / touched:escalated | +0.58 | 0.32 | +0.23 |
| 9 | the book idea is actually good | uneasy:sustained / touched:sustained / happy:escalated, hurt:lingering(lingering), curious:sustained(nothing_to_hold) | +0.66 | 0.31 | +0.22 |
| 10 | I'll get him that | happy:sustained(nothing_to_hold) / happy:sustained, curious:sustained(nothing_to_hold) / happy:escalated | +0.68 | 0.30 | +0.22 |

## D: connected conversations (held out)

**control** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a | curious:opened / curious:opened / - | +0.30 | 0.15 | +0.10 |
| 2 | ooh nice, I like those | happy:escalated / happy:escalated / happy:opened | +0.42 | 0.15 | +0.10 |
| 3 | anyway what should I cook tonight, I hav | - / happy:opened / happy:lingering(lingering) | +0.47 | 0.15 | +0.10 |
| 4 | you always have good ideas for this stuf | happy:opened / happy:sustained, touched:opened / happy:opened | +0.66 | 0.15 | +0.12 |
| 5 | what's your day been like? | - / happy:opened / happy:sustained | +0.69 | 0.15 | +0.12 |
| 6 | I like talking to you, it's relaxing | happy:opened / happy:escalated, curious:opened / happy:opened | +0.94 | 0.15 | +0.12 |
| 7 | sorry, I've been all over the place toda | happy:sustained / happy:sustained(nothing_to_hold), touched:escalated, uneasy:opened / happy:sustained, uneasy:opened | +0.94 | 0.22 | +0.13 |
| 8 | thanks for keeping me company. tell me s | happy:sustained / happy:escalated, touched:escalated / happy:sustained, touched:opened | +0.97 | 0.21 | +0.20 |

**slights_repair** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a | happy:opened / curious:opened / happy:opened | +0.38 | 0.15 | +0.10 |
| 2 | meh. those are kinda boring | hurt:escalated / - / hurt:escalated | +0.32 | 0.22 | +0.08 |
| 3 | anyway what should I cook tonight, I hav | hurt:lingering(lingering) / happy:opened / hurt:lingering(lingering) | +0.36 | 0.21 | +0.08 |
| 4 | you always suggest the obvious stuff | hurt:escalated / - / hurt:opened | +0.29 | 0.28 | +0.07 |
| 5 | what's your day been like? oh wait, you  | happy:escalated / happy:opened / happy:opened | +0.54 | 0.27 | +0.07 |
| 6 | honestly talking to you is a bit pointle | hurt:escalated / hurt:opened, curious:opened / hurt:escalated, curious:opened | +0.34 | 0.47 | +0.02 |
| 7 | sorry. that was mean, I've had a terribl | hurt:eased, curious:opened / hurt:eased, happy:eased(nothing_to_take_back) / hurt:eased(nothing_to_take_back), happy:escalated | +0.50 | 0.33 | +0.05 |
| 8 | thanks for putting up with me. tell me s | hurt:lingering(lingering), curious:lingering(lingering) / happy:eased(nothing_to_take_back), touched:opened / happy:sustained, hurt:lingering(lingering) | +0.54 | 0.32 | +0.08 |

**slights_no_repair** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a | curious:opened / happy:opened / curious:opened | +0.34 | 0.15 | +0.10 |
| 2 | meh. those are kinda boring | - / hurt:sustained / curious:sustained | +0.34 | 0.15 | +0.10 |
| 3 | anyway what should I cook tonight, I hav | hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.34 | 0.15 | +0.10 |
| 4 | you always suggest the obvious stuff | hurt:escalated / hurt:lingering(lingering) / hurt:escalated, curious:sustained(nothing_to_hold) | +0.27 | 0.22 | +0.08 |
| 5 | what's your day been like? oh wait, you  | hurt:eased, touched:opened / happy:opened / happy:opened, touched:opened | +0.57 | 0.18 | +0.16 |
| 6 | honestly talking to you is a bit pointle | hurt:escalated, curious:opened, uneasy:opened / hurt:opened, curious:opened / hurt:escalated, curious:opened | +0.44 | 0.35 | +0.12 |
| 7 | anyway. whatever | hurt:escalated, uneasy:escalated / hurt:escalated / hurt:escalated | +0.30 | 0.54 | +0.09 |
| 8 | tell me something, I'm bored | curious:eased, hurt:lingering(lingering) / hurt:sustained / happy:opened, hurt:sustained | +0.38 | 0.53 | +0.09 |

## One slight, then 1 vs 10 neutral messages (held out)

**one** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | ugh, you always pick the most boring stu | hurt:opened / hurt:opened, curious:opened / hurt:opened | +0.13 | 0.32 | +0.06 |
| 2 | anyway, what are you up to? | hurt:lingering(lingering), touched:opened / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.19 | 0.23 | +0.08 |

**ten** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood | tension | closeness |
|---|---|---|---|---|---|
| 1 | ugh, you always pick the most boring stu | hurt:opened / hurt:opened / hurt:opened | +0.13 | 0.32 | +0.06 |
| 2 | anyway, what are you up to? | - / happy:opened, hurt:lingering(lingering) / hurt:lingering(lingering), happy:opened | +0.26 | 0.31 | +0.06 |
| 3 | I'm making tea | curious:opened, hurt:lingering(lingering) / hurt:lingering(lingering), curious:opened, happy:lingering(lingering) / hurt:lingering(lingering), happy:sustained, curious:op | +0.26 | 0.30 | +0.06 |
| 4 | it's raining a bit here | curious:lingering(lingering), hurt:lingering(lingering) / hurt:lingering(lingering), curious:lingering(lingering) / happy:sustained | +0.27 | 0.29 | +0.06 |
| 5 | do you like rain? | hurt:lingering(lingering) / - / - | +0.27 | 0.28 | +0.06 |
| 6 | I might watch a film later | curious:opened, hurt:lingering(lingering) / curious:opened / curious:opened, hurt:lingering(lingering) | +0.27 | 0.27 | +0.06 |
| 7 | any good films you'd pick? | curious:sustained, hurt:lingering(lingering) / curious:sustained / curious:sustained, hurt:lingering(lingering) | +0.27 | 0.26 | +0.06 |
| 8 | hm, maybe | hurt:sustained, curious:lingering(lingering) / curious:sustained, hurt:lingering(lingering) / uneasy:sustained(nothing_to_hold) | +0.26 | 0.28 | +0.06 |
| 9 | my cat just knocked a cup over | amused:opened / amused:opened, curious:opened, hurt:lingering(lingering) / amused:opened, curious:opened | +0.38 | 0.27 | +0.06 |
| 10 | she does that a lot | amused:sustained / amused:sustained, curious:lingering(lingering) / amused:escalated, curious:sustained | +0.41 | 0.26 | +0.06 |
| 11 | ok, back to the tea | amused:sustained / curious:sustained / amused:sustained, curious:sustained | +0.41 | 0.25 | +0.06 |
