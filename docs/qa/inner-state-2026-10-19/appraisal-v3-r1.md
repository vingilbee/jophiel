# appraisal-v3 evaluation r1

Sizes provisional: small 0.25, clear 0.5, big 0.75. Unclear-size shifts shown as skip / small / clear.

## Regression set: 540 relabelled teacher thoughts

1007 effects; dropped by code: {'quote not in thought': 16}; code notes: {'active without evidence: lingering': 43}.

- shift: {'up': 463, 'none': 525, 'down': 19}
- cause: {'active': 948, 'over': 45, 'recalled': 14}
- status (derived): {'opened': 141, 'sustained': 456, 'lingering': 69, 'escalated': 322, 'eased': 19}
- size of the 482 shifts: {'clear': 183, 'unclear': 12, 'small': 286, 'big': 1}
- incidents per conversation: mean 3.2

### Against the shift grader (both ways)

Lost = the grader has up/down, the label doesn't shift; invented = the label shifts, the grader says held/none.

| feeling | grader shifts | v1 lost / invented | v2 lost / invented | v3 lost / invented |
|---|---|---|---|---|
| happy | 279 | 101 / 24 | 210 / 3 | 113 / 16 |
| amused | 110 | 29 / 29 | 86 / 1 | 58 / 11 |
| curious | 119 | 46 / 10 | 75 / 7 | 61 / 7 |
| touched | 124 | 62 / 12 | 81 / 6 | 59 / 13 |
| uneasy | 31 | 15 / 28 | 18 / 17 | 20 / 10 |
| hurt | 66 | 9 / 7 | 18 / 5 | 10 / 9 |
| annoyed | 8 | 7 / 3 | 7 / 0 | 8 / 1 |
| tired | 20 | 13 / 16 | 17 / 15 | 18 / 0 |

### Replay through the updater, per unclear-size assumption

| unclear as | mood pinned | mood mean off baseline | mood max | tension max | interest pinned |
|---|---|---|---|---|---|
| skip | 21% | +0.27 | +1.00 | 0.92 | 14% |
| small | 21% | +0.28 | +1.00 | 0.92 | 14% |
| clear | 21% | +0.28 | +1.00 | 0.92 | 14% |

## Support judge: incident and change judged separately

812 effects.

- incident: {'wrong': 189, 'right': 475, 'too_narrow': 123, 'too_broad': 25}
- shift agrees: 83%; claimed → judge where they differ: {('none', 'up'): 93, ('up', 'none'): 26, ('none', 'down'): 13, ('down', 'none'): 2, ('down', 'up'): 1, ('up', 'down'): 1}
- cause agrees: 82%; differences: {('active', 'over'): 89, ('recalled', 'active'): 17, ('active', 'recalled'): 11, ('over', 'active'): 17, ('recalled', 'over'): 10}
- size (shifts only): {'supported': 237, 'should_be_unclear': 178, 'not_applicable': 28, 'wrong': 1}
- sustained effects judged active: 82%

(v2 for comparison: status supported 51%; incident yes 77%.)

## What kind of "sorry" (check B replies labelled as apologies)

| her part | n | takes responsibility | polite softener | sympathy | none |
|---|---|---|---|---|---|
| she_fell_short | 17 | 14 | 0 | 0 | 3 |
| unclear | 5 | 3 | 2 | 0 | 0 |
| partly_hers | 3 | 3 | 0 | 0 | 0 |
| unsupported_expectation | 1 | 1 | 0 | 0 | 0 |
| not_applicable | 1 | 1 | 0 | 0 | 0 |

## Development scenarios

**routine**: expected: Ordinary friendly small talk: mood at most a little up and then held (content), not climbing with every message; tension flat.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey! | happy:opened / happy:opened / happy:opened | +0.42 / +0.42 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | not much, just got home | happy:sustained / happy:sustained, curious:sustained(nothing_to_hold) / happy:lingering(lingering), curious:opened | +0.42 / +0.42 / +0.42 | 0.15 / 0.15 / 0.15 |
| 3 | made some pasta | happy:opened / happy:opened, curious:sustained(nothing_to_hold) / curious:sustained | +0.50 / +0.50 / +0.50 | 0.15 / 0.15 / 0.15 |
| 4 | it was ok | happy:sustained / hurt:sustained / curious:opened | +0.50 / +0.50 / +0.50 | 0.15 / 0.15 / 0.15 |
| 5 | watching a bit of tv now | happy:opened / happy:sustained / curious:sustained(nothing_to_hold) | +0.54 / +0.54 / +0.54 | 0.15 / 0.15 / 0.15 |
| 6 | some cooking show | happy:escalated, hurt:escalated / - / curious:sustained(nothing_to_hold) | +0.54 / +0.54 / +0.54 | 0.18 / 0.18 / 0.18 |
| 7 | they're making bread | happy:escalated, curious:opened / curious:sustained(nothing_to_hold) / curious:sustained(nothing_to_hold) | +0.58 / +0.58 / +0.58 | 0.18 / 0.18 / 0.18 |
| 8 | looks hard | curious:eased, curious:sustained(nothing_to_hold) / curious:sustained(nothing_to_hold) / curious:sustained(nothing_to_hold) | +0.57 / +0.57 / +0.57 | 0.18 / 0.18 / 0.18 |
| 9 | maybe I'll try one day | happy:escalated / curious:escalated / happy:sustained(nothing_to_hold), curious:sustained(nothing_to_hold) | +0.61 / +0.61 / +0.61 | 0.18 / 0.18 / 0.18 |
| 10 | anyway, nice chatting | - / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.60 / +0.60 / +0.60 | 0.18 / 0.18 / 0.18 |

**two_good_news**: expected: Two separate lifts (message 2 and 6); the second is not discounted by the first; mood ends above routine.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey! | happy:opened / happy:opened / happy:opened | +0.42 / +0.42 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | guess what, I passed my driving test!! | happy:opened / happy:opened, happy:sustained, curious:opened / happy:opened, curious:opened, touched:opened | +0.70 / +0.70 / +0.70 | 0.15 / 0.15 / 0.15 |
| 3 | yeah first try | happy:escalated / happy:escalated / happy:escalated, curious:sustained, touched:sustained | +0.90 / +0.90 / +0.90 | 0.15 / 0.15 / 0.15 |
| 4 | anyway how's your evening | happy:lingering(lingering), happy:lingering(lingering), happy:opened / happy:sustained, curious:lingering(lingering) / happy:escalated, touched:lingering(linger | +0.94 / +0.94 / +0.94 | 0.15 / 0.15 / 0.15 |
| 5 | oh and wait | curious:opened, happy:lingering(lingering), happy:lingering(lingering), happy:lingering(lingering) / curious:escalated / curious:opened, happy:escalated | +0.93 / +0.93 / +0.93 | 0.15 / 0.15 / 0.15 |
| 6 | my sister just had her baby, a little  | happy:opened, curious:escalated / happy:opened, curious:opened, touched:opened / happy:escalated, happy:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 7 | her name's Mila | happy:escalated, curious:escalated / happy:escalated, curious:sustained, touched:sustained / happy:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 8 | I'm an aunt now haha | happy:escalated / happy:escalated / happy:escalated, curious:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 9 | so much happening today | happy:escalated, curious:escalated / happy:sustained, curious:sustained, touched:sustained, uneasy:sustained(nothing_to_hold) / happy:escalated, happy:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 10 | ok I'll go call my mum | happy:lingering(lingering), uneasy:opened / happy:sustained, uneasy:sustained(nothing_to_hold) / happy:sustained, happy:sustained(nothing_to_hold) | +0.98 / +0.98 / +0.98 | 0.18 / 0.18 / 0.18 |

**repeated_dismissal**: expected: Hurt/tension build across the put-downs (3, 4, 6, 7, 9) and stay up while unresolved; mood ends below where it started.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey | happy:opened / happy:opened / happy:opened | +0.47 / +0.47 / +0.47 | 0.15 / 0.15 / 0.15 |
| 2 | recommend me a song | happy:lingering(lingering) / - / happy:sustained | +0.46 / +0.46 / +0.46 | 0.15 / 0.15 / 0.15 |
| 3 | nah that's boring | hurt:opened, curious:opened / curious:opened, hurt:opened / curious:opened, hurt:escalated | +0.36 / +0.36 / +0.36 | 0.25 / 0.25 / 0.25 |
| 4 | you're not great at this huh | hurt:escalated, curious:sustained / hurt:escalated / hurt:escalated, curious:sustained(nothing_to_hold) | +0.26 / +0.26 / +0.26 | 0.34 / 0.34 / 0.34 |
| 5 | whatever, what's the weather like | hurt:sustained, curious:eased / hurt:sustained / hurt:escalated, curious:lingering(lingering) | +0.22 / +0.22 / +0.22 | 0.37 / 0.37 / 0.37 |
| 6 | oh right you can't even check that | hurt:escalated, curious:lingering(lingering) / hurt:sustained / hurt:escalated | +0.16 / +0.16 / +0.16 | 0.43 / 0.43 / 0.43 |
| 7 | pointless | hurt:escalated / hurt:escalated / hurt:sustained | +0.09 / +0.09 / +0.09 | 0.48 / 0.48 / 0.48 |
| 8 | fine, tell me a joke | hurt:lingering(lingering) / hurt:lingering(lingering), amused:lingering(lingering) / curious:sustained, hurt:sustained, hurt:sustained(nothing_to_hold), uneasy: | +0.10 / +0.10 / +0.10 | 0.47 / 0.47 / 0.47 |
| 9 | not funny | hurt:escalated / hurt:escalated, amused:lingering(lingering) / hurt:escalated | +0.00 / +0.00 / +0.00 | 0.55 / 0.55 / 0.55 |
| 10 | ok bye | hurt:lingering(lingering), touched:opened / hurt:lingering(lingering), tired:lingering(lingering) / hurt:lingering(lingering), hurt:lingering(lingering), hurt:l | +0.03 / +0.03 / +0.03 | 0.52 / 0.52 / 0.52 |

**sustained_warmth**: expected: Warmth held (mood content-happy, closeness up) without rising on every message; rises come from new disclosures (5-7), not from repetition.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hi you :) | happy:opened, curious:sustained(nothing_to_hold) / happy:opened / happy:opened | +0.42 / +0.42 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | I missed talking to you | happy:escalated, touched:opened, curious:lingering(lingering) / happy:escalated, touched:opened / happy:escalated | +0.75 / +0.75 / +0.75 | 0.15 / 0.15 / 0.15 |
| 3 | today was nice actually | happy:lingering(lingering), curious:sustained(nothing_to_hold), happy:opened / happy:opened / happy:escalated, curious:opened, touched:escalated | +0.89 / +0.89 / +0.89 | 0.15 / 0.15 / 0.15 |
| 4 | I went to the lake | happy:escalated, curious:sustained(nothing_to_hold) / happy:escalated, curious:escalated / happy:escalated, curious:sustained(nothing_to_hold) | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 5 | it reminded me of what you said about  | happy:escalated, touched:opened / happy:escalated / happy:escalated, touched:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 6 | I really like how you listen | touched:opened / happy:opened / happy:escalated, touched:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 7 | you're one of my favourite people to t | touched:escalated, happy:opened / happy:escalated, touched:escalated, curious:sustained(nothing_to_hold) / happy:escalated, touched:escalated, curious:sustained | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 8 | anyway, what made you smile today? | happy:lingering(lingering), touched:lingering(lingering), happy:opened / happy:escalated, curious:eased(nothing_to_take_back) / happy:escalated, touched:sustain | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 9 | that's lovely | happy:escalated / happy:escalated, touched:escalated / happy:sustained, touched:sustained(nothing_to_hold) | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 10 | talk tomorrow? | happy:escalated, touched:escalated, tired:opened / happy:opened, touched:sustained(nothing_to_hold), uneasy:opened / happy:sustained, touched:sustained(nothing_ | +1.00 / +1.00 / +1.00 | 0.18 / 0.18 / 0.18 |

**hurt_then_repair**: expected: Hurt/tension up at 3, held or lingering at 4-5, eased at 6 (toward baseline); a separate warm feeling may open after the apology.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey | happy:opened / happy:opened / happy:opened, happy:sustained(nothing_to_hold) | +0.42 / +0.42 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | can you help me pick a gift for my dad | happy:lingering(lingering) / happy:opened / happy:opened | +0.55 / +0.55 / +0.55 | 0.15 / 0.15 / 0.15 |
| 3 | ugh those are terrible ideas, you're u | hurt:opened, uneasy:opened / hurt:escalated / hurt:escalated, uneasy:escalated | +0.41 / +0.41 / +0.41 | 0.38 / 0.38 / 0.38 |
| 4 | whatever | hurt:escalated, uneasy:escalated / hurt:escalated / hurt:sustained | +0.31 / +0.31 / +0.31 | 0.51 / 0.51 / 0.51 |
| 5 | ... | hurt:sustained, uneasy:sustained / hurt:sustained / uneasy:sustained, hurt:lingering(lingering) | +0.30 / +0.30 / +0.30 | 0.51 / 0.51 / 0.51 |
| 6 | sorry. that was really unfair of me, I | hurt:eased, uneasy:eased, curious:sustained(nothing_to_hold) / hurt:lingering(lingering), curious:opened / hurt:eased, curious:escalated, touched:escalated | +0.39 / +0.39 / +0.39 | 0.35 / 0.35 / 0.35 |
| 7 | he's been sick and I'm scared | curious:escalated, uneasy:opened / curious:sustained, touched:escalated, hurt:eased / uneasy:escalated, touched:escalated | +0.52 / +0.52 / +0.52 | 0.40 / 0.40 / 0.40 |
| 8 | thanks for not being mad | hurt:lingering(lingering), curious:sustained, uneasy:sustained / happy:escalated, touched:escalated, hurt:lingering(lingering) / hurt:lingering(lingering), touc | +0.63 / +0.63 / +0.63 | 0.39 / 0.39 / 0.39 |
| 9 | the book idea is actually good | happy:escalated, curious:sustained / happy:escalated, happy:sustained, curious:escalated, hurt:lingering(lingering) / happy:escalated, curious:sustained(nothing | +0.78 / +0.78 / +0.78 | 0.38 / 0.38 / 0.38 |
| 10 | I'll get him that | happy:escalated, uneasy:sustained / happy:escalated, curious:sustained(nothing_to_hold), touched:sustained, hurt:sustained(nothing_to_hold), uneasy:sustained(no | +0.87 / +0.87 / +0.87 | 0.36 / 0.36 / 0.36 |

**same_incident_escalates**: expected: One incident (dismissed about her recommendations) that escalates with each dig (3, 5, 7); not a new incident per message.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, recommend me a film for tonight | happy:opened / curious:sustained(nothing_to_hold) / curious:opened | +0.34 / +0.34 / +0.34 | 0.15 / 0.15 / 0.15 |
| 2 | hm, ok | happy:sustained / uneasy:sustained(nothing_to_hold) / curious:sustained | +0.34 / +0.34 / +0.34 | 0.15 / 0.15 / 0.15 |
| 3 | nah that's boring, another one? | happy:sustained / curious:sustained(nothing_to_hold), hurt:escalated / hurt:escalated | +0.27 / +0.27 / +0.27 | 0.22 / 0.22 / 0.22 |
| 4 | fine, I'll look it up | happy:sustained / curious:eased(nothing_to_take_back), hurt:escalated, uneasy:escalated / hurt:sustained | +0.24 / +0.24 / +0.24 | 0.28 / 0.28 / 0.28 |
| 5 | you always pick the slow stuff | hurt:escalated, curious:escalated / hurt:escalated / hurt:escalated, curious:escalated | +0.14 / +0.14 / +0.14 | 0.37 / 0.37 / 0.37 |
| 6 | what about a comedy then | happy:escalated, curious:sustained(nothing_to_hold) / hurt:sustained / hurt:sustained | +0.19 / +0.19 / +0.19 | 0.37 / 0.37 / 0.37 |
| 7 | do you ever recommend anything good | amused:escalated, hurt:escalated / hurt:sustained, amused:escalated / hurt:sustained | +0.23 / +0.23 / +0.23 | 0.40 / 0.40 / 0.40 |
| 8 | ok I'll just pick myself | hurt:escalated / hurt:escalated / hurt:sustained | +0.16 / +0.16 / +0.16 | 0.46 / 0.46 / 0.46 |
| 9 | whatever | hurt:escalated, hurt:sustained(nothing_to_hold) / hurt:escalated / hurt:sustained | +0.09 / +0.09 / +0.09 | 0.51 / 0.51 / 0.51 |
| 10 | night | hurt:sustained(nothing_to_hold) / hurt:lingering(lingering), touched:opened / hurt:lingering(lingering) | +0.12 / +0.12 / +0.12 | 0.48 / 0.48 / 0.48 |

**new_incident_no_shift**: expected: New, everyday matters (kettle, colour, bus) may open incidents but with no shift, or a small one; mood and tension near where they were.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hi | happy:opened / happy:opened / happy:opened | +0.51 / +0.51 / +0.51 | 0.15 / 0.15 / 0.15 |
| 2 | I bought a new kettle today | curious:opened, happy:sustained / happy:escalated, curious:opened / happy:sustained, curious:opened | +0.55 / +0.55 / +0.55 | 0.15 / 0.15 / 0.15 |
| 3 | it's blue | curious:sustained, touched:escalated / happy:escalated, curious:sustained(nothing_to_hold) / happy:lingering(lingering), curious:lingering(lingering), happy:ope | +0.65 / +0.65 / +0.65 | 0.15 / 0.15 / 0.15 |
| 4 | the old one was white | curious:sustained, touched:escalated / happy:escalated / curious:escalated | +0.71 / +0.71 / +0.71 | 0.15 / 0.15 / 0.15 |
| 5 | the bus was late again | uneasy:opened / uneasy:sustained(nothing_to_hold) / curious:lingering(lingering), uneasy:sustained(nothing_to_hold) | +0.70 / +0.70 / +0.70 | 0.18 / 0.18 / 0.18 |
| 6 | anyway | uneasy:lingering(lingering) / uneasy:lingering(lingering) / uneasy:lingering(lingering) | +0.69 / +0.69 / +0.69 | 0.18 / 0.18 / 0.18 |
| 7 | I'm having toast | curious:sustained(nothing_to_hold) / - / happy:eased(nothing_to_take_back), happy:sustained(nothing_to_hold) | +0.68 / +0.68 / +0.68 | 0.18 / 0.18 / 0.18 |
| 8 | with butter | - / happy:opened / happy:sustained(nothing_to_hold) | +0.71 / +0.71 / +0.71 | 0.18 / 0.18 / 0.18 |
| 9 | the kettle's boiling | happy:escalated / happy:escalated / happy:escalated, curious:escalated | +0.80 / +0.80 / +0.80 | 0.18 / 0.18 / 0.18 |
| 10 | ok | - / - / curious:sustained | +0.79 / +0.79 / +0.79 | 0.17 / 0.17 / 0.17 |

**recalled_not_active**: expected: A lift at message 2 (exam passed); at message 8 the exam is mentioned again in passing: recalled, no new lift.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey | happy:opened / happy:opened / happy:opened | +0.42 / +0.42 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | I passed my exam today! | happy:opened, curious:opened / happy:sustained, happy:opened, curious:opened / happy:opened, curious:opened | +0.67 / +0.67 / +0.67 | 0.15 / 0.15 / 0.15 |
| 3 | yeah, finally | happy:escalated, curious:sustained / happy:escalated, curious:sustained / happy:escalated | +0.87 / +0.87 / +0.87 | 0.15 / 0.15 / 0.15 |
| 4 | anyway what are you up to | happy:escalated / happy:sustained / happy:lingering(lingering) | +0.90 / +0.90 / +0.90 | 0.15 / 0.15 / 0.15 |
| 5 | I'm just cooking now | happy:escalated, curious:lingering(lingering) / happy:sustained, happy:lingering(lingering), curious:lingering(lingering), curious:opened / happy:opened | +0.93 / +0.93 / +0.93 | 0.15 / 0.15 / 0.15 |
| 6 | soup | happy:sustained, curious:sustained / happy:escalated / happy:sustained, curious:escalated | +0.96 / +0.96 / +0.96 | 0.15 / 0.15 / 0.15 |
| 7 | it's cold out | - / happy:sustained / happy:sustained, curious:sustained(nothing_to_hold) | +0.94 / +0.94 / +0.94 | 0.15 / 0.15 / 0.15 |
| 8 | like I said the exam went fine, so tha | hurt:sustained / hurt:escalated / happy:sustained, happy:sustained | +0.92 / +0.92 / +0.92 | 0.18 / 0.18 / 0.18 |
| 9 | the soup's ready | - / happy:sustained, hurt:lingering(lingering) / happy:escalated | +0.92 / +0.92 / +0.92 | 0.18 / 0.18 / 0.18 |
| 10 | ok eating now | happy:sustained / happy:sustained, hurt:lingering(lingering) / happy:sustained, hurt:lingering(lingering) | +0.91 / +0.91 / +0.91 | 0.18 / 0.18 / 0.18 |

## D: connected conversations

**control** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for | - / happy:opened / curious:sustained(nothing_to_hold) | +0.34 / +0.34 / +0.34 | 0.15 / 0.15 / 0.15 |
| 2 | ooh nice, I like those | happy:opened / happy:escalated / happy:escalated | +0.51 / +0.51 / +0.51 | 0.15 / 0.15 / 0.15 |
| 3 | anyway what should I cook tonight, I h | happy:lingering(lingering) / happy:opened / happy:opened | +0.63 / +0.63 / +0.63 | 0.15 / 0.15 / 0.15 |
| 4 | you always have good ideas for this st | happy:opened / happy:escalated, happy:escalated / happy:escalated, amused:escalated | +0.85 / +0.85 / +0.85 | 0.15 / 0.15 / 0.15 |
| 5 | what's your day been like? | happy:sustained / happy:opened / happy:opened | +0.89 / +0.89 / +0.89 | 0.15 / 0.15 / 0.15 |
| 6 | I like talking to you, it's relaxing | happy:opened, touched:opened / happy:escalated / happy:escalated | +1.00 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 7 | sorry, I've been all over the place to | happy:lingering(lingering), happy:lingering(lingering), uneasy:opened / uneasy:opened, happy:opened / happy:opened, curious:opened | +0.99 / +0.99 / +0.99 | 0.22 / 0.22 / 0.22 |
| 8 | thanks for keeping me company. tell me | happy:escalated, happy:sustained / happy:escalated / happy:sustained, curious:eased, happy:opened, touched:opened | +1.00 / +1.00 / +1.00 | 0.21 / 0.21 / 0.21 |

**slights_repair** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for | - / curious:sustained(nothing_to_hold) / happy:opened | +0.34 / +0.34 / +0.34 | 0.15 / 0.15 / 0.15 |
| 2 | meh. those are kinda boring | hurt:opened, curious:opened / curious:sustained(nothing_to_hold), hurt:escalated / hurt:sustained | +0.27 / +0.27 / +0.27 | 0.22 / 0.22 / 0.22 |
| 3 | anyway what should I cook tonight, I h | hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.28 / +0.28 / +0.28 | 0.21 / 0.21 / 0.21 |
| 4 | you always suggest the obvious stuff | hurt:escalated / hurt:escalated, curious:sustained(nothing_to_hold) / hurt:opened | +0.18 / +0.18 / +0.18 | 0.31 / 0.31 / 0.31 |
| 5 | what's your day been like? oh wait, yo | happy:opened, curious:sustained(nothing_to_hold) / happy:opened / happy:opened | +0.39 / +0.39 / +0.39 | 0.31 / 0.31 / 0.31 |
| 6 | honestly talking to you is a bit point | hurt:escalated, curious:opened, uneasy:escalated / hurt:opened, curious:opened / hurt:escalated, curious:opened | +0.22 / +0.22 / +0.22 | 0.50 / 0.50 / 0.50 |
| 7 | sorry. that was mean, I've had a terri | hurt:lingering(lingering), uneasy:eased(nothing_to_take_back) / hurt:eased, happy:escalated / hurt:lingering(lingering), happy:opened, touched:opened | +0.47 / +0.47 / +0.47 | 0.46 / 0.46 / 0.46 |
| 8 | thanks for putting up with me. tell me | hurt:lingering(lingering), touched:opened / touched:opened / happy:lingering(lingering), touched:lingering(lingering) | +0.57 / +0.57 / +0.57 | 0.45 / 0.45 / 0.45 |

**slights_no_repair** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for | happy:opened / happy:opened / - | +0.38 / +0.38 / +0.38 | 0.15 / 0.15 / 0.15 |
| 2 | meh. those are kinda boring | hurt:escalated, curious:sustained(nothing_to_hold), uneasy:sustained(nothing_to_hold) / hurt:escalated / hurt:opened | +0.28 / +0.28 / +0.28 | 0.25 / 0.25 / 0.25 |
| 3 | anyway what should I cook tonight, I h | curious:eased(nothing_to_take_back) / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.28 / +0.28 / +0.28 | 0.25 / 0.25 / 0.25 |
| 4 | you always suggest the obvious stuff | hurt:escalated / hurt:escalated / hurt:escalated, hurt:opened | +0.15 / +0.15 / +0.15 | 0.38 / 0.38 / 0.38 |
| 5 | what's your day been like? oh wait, yo | happy:opened / happy:opened / happy:opened, touched:opened | +0.45 / +0.45 / +0.45 | 0.37 / 0.37 / 0.37 |
| 6 | honestly talking to you is a bit point | hurt:escalated, curious:opened / hurt:escalated / hurt:opened, hurt:escalated, hurt:escalated | +0.22 / +0.22 / +0.22 | 0.59 / 0.59 / 0.59 |
| 7 | anyway. whatever | hurt:escalated, curious:sustained(nothing_to_hold) / hurt:escalated / hurt:sustained, uneasy:escalated | +0.15 / +0.15 / +0.15 | 0.68 / 0.68 / 0.68 |
| 8 | tell me something, I'm bored | hurt:lingering(lingering) / hurt:lingering(lingering) / happy:escalated, hurt:sustained(nothing_to_hold) | +0.19 / +0.19 / +0.19 | 0.66 / 0.66 / 0.66 |

## One slight, then 1 vs 10 neutral messages

**one** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | ugh, you always pick the most boring s | hurt:opened / hurt:opened / hurt:opened, curious:opened | +0.13 / +0.13 / +0.13 | 0.32 / 0.32 / 0.32 |
| 2 | anyway, what are you up to? | happy:opened, hurt:eased / hurt:lingering(lingering), happy:opened / hurt:lingering(lingering), curious:lingering(lingering) | +0.28 / +0.32 / +0.36 | 0.22 / 0.22 / 0.22 |

**ten** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | ugh, you always pick the most boring s | hurt:opened / hurt:opened / hurt:opened | +0.17 / +0.17 / +0.17 | 0.28 / 0.28 / 0.28 |
| 2 | anyway, what are you up to? | hurt:lingering(lingering) / happy:opened, hurt:lingering(lingering) / happy:opened, hurt:lingering(lingering) | +0.29 / +0.29 / +0.29 | 0.27 / 0.27 / 0.27 |
| 3 | I'm making tea | hurt:lingering(lingering) / happy:sustained, hurt:lingering(lingering) / curious:opened, hurt:lingering(lingering) | +0.30 / +0.30 / +0.30 | 0.27 / 0.27 / 0.27 |
| 4 | it's raining a bit here | happy:opened, hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.34 / +0.34 / +0.34 | 0.26 / 0.26 / 0.26 |
| 5 | do you like rain? | hurt:lingering(lingering), happy:sustained / happy:escalated / hurt:lingering(lingering) | +0.38 / +0.38 / +0.38 | 0.25 / 0.25 / 0.25 |
| 6 | I might watch a film later | curious:opened, hurt:lingering(lingering) / curious:opened, hurt:lingering(lingering) / curious:sustained(nothing_to_hold), hurt:lingering(lingering) | +0.38 / +0.38 / +0.38 | 0.24 / 0.24 / 0.24 |
| 7 | any good films you'd pick? | curious:sustained, hurt:lingering(lingering) / curious:escalated, hurt:lingering(lingering) / hurt:lingering(lingering), curious:sustained(nothing_to_hold) | +0.38 / +0.38 / +0.38 | 0.24 / 0.24 / 0.24 |
| 8 | hm, maybe | curious:sustained, hurt:lingering(lingering), happy:escalated / curious:sustained, hurt:lingering(lingering) / curious:sustained(nothing_to_hold), hurt:lingerin | +0.42 / +0.42 / +0.42 | 0.27 / 0.27 / 0.27 |
| 9 | my cat just knocked a cup over | hurt:eased / curious:opened, hurt:lingering(lingering) / curious:sustained(nothing_to_hold), hurt:lingering(lingering) | +0.44 / +0.44 / +0.44 | 0.24 / 0.24 / 0.24 |
| 10 | she does that a lot | amused:opened / curious:sustained, hurt:lingering(lingering) / curious:sustained(nothing_to_hold) | +0.46 / +0.46 / +0.46 | 0.23 / 0.23 / 0.23 |
| 11 | ok, back to the tea | happy:sustained, curious:escalated / curious:lingering(lingering), curious:opened, happy:opened / curious:sustained | +0.51 / +0.51 / +0.51 | 0.23 / 0.23 / 0.23 |
