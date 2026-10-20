# appraisal-v3.1 evaluation r1

Sizes provisional: small 0.25, clear 0.5, big 0.75. Unclear-size shifts shown as skip / small / clear.

## Regression set: 540 relabelled teacher thoughts

929 effects; dropped by code: {'quote not in thought': 27, 'down on a new incident': 1}; code notes: {'active without evidence: lingering': 14}.

- shift: {'up': 407, 'none': 511, 'down': 11}
- cause: {'active': 829, 'over': 80, 'recalled': 20}
- status (derived): {'opened': 182, 'sustained': 422, 'lingering': 89, 'escalated': 225, 'eased': 11}
- size of the 418 shifts: {'unclear': 315, 'clear': 28, 'small': 75}
- incidents per conversation: mean 3.8

### Against the shift grader (both ways)

Lost = the grader has up/down, the label doesn't shift; invented = the label shifts, the grader says held/none.

| feeling | grader shifts | v1 lost / invented | v2 lost / invented | v3 lost / invented |
|---|---|---|---|---|
| happy | 279 | 101 / 24 | 210 / 3 | 139 / 10 |
| amused | 110 | 29 / 29 | 86 / 1 | 64 / 11 |
| curious | 119 | 46 / 10 | 75 / 7 | 66 / 5 |
| touched | 124 | 62 / 12 | 81 / 6 | 64 / 13 |
| uneasy | 31 | 15 / 28 | 18 / 17 | 21 / 9 |
| hurt | 66 | 9 / 7 | 18 / 5 | 17 / 8 |
| annoyed | 8 | 7 / 3 | 7 / 0 | 8 / 0 |
| tired | 20 | 13 / 16 | 17 / 15 | 17 / 0 |

### Replay through the updater, per unclear-size assumption

| unclear as | mood pinned | mood mean off baseline | mood max | tension max | interest pinned |
|---|---|---|---|---|---|
| skip | 0% | +0.02 | +0.82 | 0.69 | 0% |
| small | 9% | +0.23 | +1.00 | 0.70 | 4% |
| clear | 29% | +0.34 | +1.00 | 0.79 | 11% |

## Support judge: incident and change judged separately

764 effects.

- incident: {'right': 445, 'wrong': 145, 'too_narrow': 144, 'too_broad': 30}
- shift agrees: 83%; claimed → judge where they differ: {('none', 'up'): 103, ('up', 'none'): 19, ('up', 'down'): 1, ('none', 'down'): 5, ('down', 'none'): 1}
- cause agrees: 85%; differences: {('recalled', 'active'): 15, ('over', 'active'): 20, ('active', 'over'): 73, ('over', 'recalled'): 2, ('active', 'recalled'): 3, ('recalled', 'over'): 3}
- size (shifts only): {'should_be_unclear': 258, 'supported': 136, 'not_applicable': 20, 'wrong': 7}
- sustained effects judged active: 91%

(v2 for comparison: status supported 51%; incident yes 77%.)

## Development scenarios

**routine**: expected: Ordinary friendly small talk: mood at most a little up and then held (content), not climbing with every message; tension flat.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey! | happy:opened / happy:opened, curious:opened / happy:opened | +0.30 / +0.42 / +0.55 | 0.15 / 0.15 / 0.15 |
| 2 | not much, just got home | happy:lingering(lingering) / happy:sustained, curious:eased, happy:escalated / happy:escalated, uneasy:sustained(nothing_to_hold) | +0.30 / +0.51 / +0.71 | 0.15 / 0.15 / 0.15 |
| 3 | made some pasta | curious:sustained(nothing_to_hold) / happy:opened, curious:opened / happy:escalated | +0.34 / +0.59 / +0.82 | 0.15 / 0.15 / 0.15 |
| 4 | it was ok | happy:lingering(lingering), uneasy:sustained(nothing_to_hold) / happy:sustained, curious:sustained / happy:lingering(lingering), uneasy:lingering(lingering), to | +0.34 / +0.58 / +0.81 | 0.15 / 0.15 / 0.15 |
| 5 | watching a bit of tv now | happy:opened / curious:sustained(nothing_to_hold) / - | +0.34 / +0.62 / +0.88 | 0.15 / 0.15 / 0.15 |
| 6 | some cooking show | happy:escalated / curious:sustained(nothing_to_hold), happy:sustained(nothing_to_hold) / happy:lingering(lingering) | +0.34 / +0.65 / +0.94 | 0.15 / 0.15 / 0.15 |
| 7 | they're making bread | happy:opened / curious:escalated / happy:opened | +0.34 / +0.73 / +0.98 | 0.15 / 0.15 / 0.15 |
| 8 | looks hard | - / curious:sustained, annoyed:opened / happy:escalated | +0.31 / +0.73 / +0.95 | 0.19 / 0.19 / 0.19 |
| 9 | maybe I'll try one day | happy:escalated, curious:opened / happy:opened / happy:escalated | +0.31 / +0.85 / +1.00 | 0.19 / 0.19 / 0.19 |
| 10 | anyway, nice chatting | happy:opened, tired:opened / happy:sustained(nothing_to_hold), uneasy:sustained(nothing_to_hold) / curious:lingering(lingering), tired:sustained(nothing_to_hold | +0.31 / +0.88 / +0.99 | 0.19 / 0.19 / 0.19 |

**two_good_news**: expected: Two separate lifts (message 2 and 6); the second is not discounted by the first; mood ends above routine.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey! | happy:opened / curious:sustained(nothing_to_hold) / happy:opened | +0.38 / +0.42 / +0.47 | 0.15 / 0.15 / 0.15 |
| 2 | guess what, I passed my driving test!! | happy:opened, curious:opened / happy:opened, touched:opened, curious:escalated / happy:opened, curious:opened, happy:lingering(lingering) | +0.38 / +0.57 / +0.76 | 0.15 / 0.15 / 0.15 |
| 3 | yeah first try | happy:escalated, curious:sustained / happy:escalated, touched:sustained / happy:sustained, curious:eased | +0.38 / +0.65 / +0.91 | 0.15 / 0.15 / 0.15 |
| 4 | anyway how's your evening | happy:opened / happy:lingering(lingering), touched:opened / happy:opened | +0.38 / +0.75 / +1.00 | 0.15 / 0.15 / 0.15 |
| 5 | oh and wait | curious:opened, happy:sustained / happy:lingering(lingering), curious:escalated / happy:sustained, curious:opened | +0.38 / +0.74 / +0.99 | 0.15 / 0.15 / 0.15 |
| 6 | my sister just had her baby, a little  | happy:opened, curious:opened / happy:opened, curious:opened / happy:escalated, curious:escalated | +0.54 / +0.90 / +1.00 | 0.15 / 0.15 / 0.15 |
| 7 | her name's Mila | happy:escalated, happy:sustained / happy:sustained, curious:eased, touched:opened / happy:escalated | +0.62 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 8 | I'm an aunt now haha | happy:escalated / happy:escalated, touched:opened / happy:sustained, curious:escalated | +0.62 / +1.00 / +0.99 | 0.15 / 0.15 / 0.15 |
| 9 | so much happening today | happy:sustained, curious:escalated / happy:sustained / happy:sustained | +0.62 / +0.99 / +0.98 | 0.15 / 0.15 / 0.15 |
| 10 | ok I'll go call my mum | happy:lingering(lingering), touched:opened / happy:sustained / happy:lingering(lingering), tired:opened | +0.66 / +0.98 / +0.98 | 0.15 / 0.15 / 0.15 |

**repeated_dismissal**: expected: Hurt/tension build across the put-downs (3, 4, 6, 7, 9) and stay up while unresolved; mood ends below where it started.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey | happy:opened / happy:opened, curious:opened / happy:opened | +0.30 / +0.42 / +0.55 | 0.15 / 0.15 / 0.15 |
| 2 | recommend me a song | happy:opened, curious:opened / happy:opened / happy:lingering(lingering), curious:opened | +0.30 / +0.51 / +0.71 | 0.15 / 0.15 / 0.15 |
| 3 | nah that's boring | curious:sustained / curious:escalated, hurt:opened / hurt:escalated, curious:sustained(nothing_to_hold) | +0.23 / +0.43 / +0.63 | 0.22 / 0.22 / 0.22 |
| 4 | you're not great at this huh | curious:sustained, hurt:escalated / hurt:sustained, curious:sustained / hurt:sustained, curious:sustained(nothing_to_hold) | +0.20 / +0.40 / +0.59 | 0.25 / 0.25 / 0.25 |
| 5 | whatever, what's the weather like | hurt:escalated / hurt:escalated / hurt:escalated | +0.10 / +0.29 / +0.49 | 0.34 / 0.34 / 0.34 |
| 6 | oh right you can't even check that | hurt:sustained / hurt:escalated, uneasy:sustained(nothing_to_hold) / hurt:escalated | +0.04 / +0.23 / +0.41 | 0.40 / 0.40 / 0.40 |
| 7 | pointless | hurt:escalated / hurt:escalated / hurt:opened | -0.06 / +0.13 / +0.31 | 0.48 / 0.48 / 0.48 |
| 8 | fine, tell me a joke | hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering) | -0.05 / +0.13 / +0.31 | 0.46 / 0.46 / 0.46 |
| 9 | not funny | hurt:escalated / hurt:opened / hurt:escalated | -0.11 / +0.04 / +0.18 | 0.51 / 0.54 / 0.57 |
| 10 | ok bye | hurt:lingering(lingering), touched:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering), hurt:lingering(lingering), tired:opened | -0.10 / +0.04 / +0.18 | 0.48 / 0.52 / 0.55 |

**sustained_warmth**: expected: Warmth held (mood content-happy, closeness up) without rising on every message; rises come from new disclosures (5-7), not from repetition.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hi you :) | happy:opened / happy:opened / happy:opened, touched:opened | +0.30 / +0.45 / +0.60 | 0.15 / 0.15 / 0.15 |
| 2 | I missed talking to you | happy:escalated, touched:opened / touched:opened / happy:escalated, touched:opened | +0.35 / +0.63 / +0.88 | 0.15 / 0.15 / 0.15 |
| 3 | today was nice actually | happy:opened, curious:opened / happy:opened, curious:opened, touched:lingering(lingering) / happy:opened | +0.35 / +0.75 / +0.98 | 0.15 / 0.15 / 0.15 |
| 4 | I went to the lake | happy:escalated, curious:sustained / happy:sustained, curious:sustained / happy:escalated | +0.35 / +0.82 / +0.98 | 0.15 / 0.15 / 0.15 |
| 5 | it reminded me of what you said about  | touched:opened, happy:opened / touched:opened / happy:opened | +0.53 / +0.94 / +1.00 | 0.15 / 0.15 / 0.15 |
| 6 | I really like how you listen | touched:opened / touched:escalated, happy:opened / happy:escalated | +0.62 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 7 | you're one of my favourite people to t | touched:opened / touched:escalated / happy:opened, touched:opened | +0.72 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |
| 8 | anyway, what made you smile today? | touched:escalated, happy:escalated / happy:opened, happy:lingering(lingering) / happy:lingering(lingering) | +0.71 / +0.99 / +0.99 | 0.15 / 0.15 / 0.15 |
| 9 | that's lovely | touched:sustained / happy:escalated / happy:sustained | +0.71 / +0.99 / +0.99 | 0.15 / 0.15 / 0.15 |
| 10 | talk tomorrow? | happy:opened, touched:lingering(lingering), uneasy:opened / happy:opened, touched:lingering(lingering), tired:sustained(nothing_to_hold) / happy:lingering(linge | +0.70 / +0.99 / +0.99 | 0.18 / 0.18 / 0.18 |

**hurt_then_repair**: expected: Hurt/tension up at 3, held or lingering at 4-5, eased at 6 (toward baseline); a separate warm feeling may open after the apology.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey | happy:opened, curious:sustained(nothing_to_hold) / happy:opened / happy:opened | +0.30 / +0.42 / +0.55 | 0.15 / 0.15 / 0.15 |
| 2 | can you help me pick a gift for my dad | happy:escalated, curious:sustained(nothing_to_hold) / happy:opened, curious:opened / happy:lingering(lingering) | +0.30 / +0.51 / +0.71 | 0.15 / 0.15 / 0.15 |
| 3 | ugh those are terrible ideas, you're u | hurt:opened, uneasy:opened / hurt:escalated, curious:sustained / hurt:opened | +0.17 / +0.37 / +0.57 | 0.32 / 0.32 / 0.32 |
| 4 | whatever | hurt:escalated, uneasy:escalated / hurt:escalated / hurt:escalated | +0.07 / +0.27 / +0.46 | 0.41 / 0.44 / 0.47 |
| 5 | ... | hurt:lingering(lingering), uneasy:lingering(lingering) / hurt:sustained, uneasy:escalated / hurt:lingering(lingering) | +0.07 / +0.27 / +0.46 | 0.39 / 0.46 / 0.52 |
| 6 | sorry. that was really unfair of me, I | hurt:lingering(lingering), touched:escalated, curious:opened / hurt:eased, uneasy:sustained(nothing_to_hold), touched:opened / hurt:lingering(lingering), happy: | +0.24 / +0.43 / +0.64 | 0.32 / 0.37 / 0.43 |
| 7 | he's been sick and I'm scared | curious:escalated, happy:escalated, uneasy:escalated / uneasy:sustained(nothing_to_hold), touched:sustained / uneasy:escalated | +0.25 / +0.47 / +0.72 | 0.31 / 0.42 / 0.54 |
| 8 | thanks for not being mad | happy:sustained, hurt:lingering(lingering) / happy:escalated, touched:escalated, uneasy:sustained(nothing_to_hold) / happy:escalated, hurt:lingering(lingering) | +0.25 / +0.58 / +0.89 | 0.29 / 0.40 / 0.52 |
| 9 | the book idea is actually good | happy:escalated, hurt:lingering(lingering) / happy:escalated, uneasy:sustained(nothing_to_hold), curious:escalated, curious:lingering(lingering), uneasy:opened  | +0.33 / +0.74 / +1.00 | 0.32 / 0.42 / 0.53 |
| 10 | I'll get him that | happy:escalated, uneasy:sustained(nothing_to_hold), touched:lingering(lingering) / happy:escalated, curious:sustained(nothing_to_hold), uneasy:sustained(nothing | +0.41 / +0.86 / +1.00 | 0.31 / 0.40 / 0.50 |

**same_incident_escalates**: expected: One incident (dismissed about her recommendations) that escalates with each dig (3, 5, 7); not a new incident per message.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, recommend me a film for tonight | happy:opened / happy:opened / curious:opened | +0.34 / +0.38 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | hm, ok | happy:sustained, uneasy:escalated / happy:sustained / curious:sustained | +0.34 / +0.38 / +0.42 | 0.15 / 0.18 / 0.22 |
| 3 | nah that's boring, another one? | happy:eased, uneasy:sustained(nothing_to_hold) / uneasy:opened / curious:sustained, hurt:escalated | +0.31 / +0.31 / +0.31 | 0.22 / 0.25 / 0.28 |
| 4 | fine, I'll look it up | hurt:escalated / happy:escalated / curious:sustained, hurt:escalated, uneasy:sustained(nothing_to_hold) | +0.24 / +0.28 / +0.32 | 0.28 / 0.31 / 0.34 |
| 5 | you always pick the slow stuff | hurt:sustained / happy:lingering(lingering), uneasy:lingering(lingering), hurt:sustained(nothing_to_hold) / curious:sustained, happy:escalated, uneasy:sustained | +0.24 / +0.32 / +0.41 | 0.28 / 0.30 / 0.33 |
| 6 | what about a comedy then | hurt:lingering(lingering), happy:escalated / hurt:eased(nothing_to_take_back), happy:escalated / happy:escalated, amused:escalated | +0.24 / +0.47 / +0.70 | 0.27 / 0.29 / 0.32 |
| 7 | do you ever recommend anything good | curious:escalated, hurt:escalated / hurt:escalated / hurt:escalated, amused:escalated | +0.14 / +0.39 / +0.64 | 0.36 / 0.38 / 0.41 |
| 8 | ok I'll just pick myself | hurt:escalated / hurt:escalated / hurt:escalated | +0.08 / +0.29 / +0.50 | 0.41 / 0.47 / 0.52 |
| 9 | whatever | hurt:sustained, uneasy:sustained(nothing_to_hold) / hurt:escalated / hurt:escalated | +0.02 / +0.22 / +0.43 | 0.47 / 0.52 / 0.57 |
| 10 | night | uneasy:lingering(lingering), hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:sustained | +0.02 / +0.23 / +0.43 | 0.46 / 0.50 / 0.56 |

**new_incident_no_shift**: expected: New, everyday matters (kettle, colour, bus) may open incidents but with no shift, or a small one; mood and tension near where they were.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hi | happy:opened / happy:opened, curious:opened / happy:opened | +0.38 / +0.47 / +0.55 | 0.15 / 0.15 / 0.15 |
| 2 | I bought a new kettle today | happy:opened / curious:opened / happy:lingering(lingering), curious:opened | +0.38 / +0.50 / +0.63 | 0.15 / 0.15 / 0.15 |
| 3 | it's blue | curious:escalated / curious:sustained / curious:sustained | +0.38 / +0.50 / +0.62 | 0.15 / 0.15 / 0.15 |
| 4 | the old one was white | happy:escalated / curious:sustained / curious:sustained | +0.38 / +0.54 / +0.69 | 0.15 / 0.15 / 0.15 |
| 5 | the bus was late again | uneasy:opened / uneasy:opened / uneasy:opened | +0.38 / +0.53 / +0.68 | 0.22 / 0.25 / 0.28 |
| 6 | anyway | - / uneasy:lingering(lingering) / - | +0.37 / +0.53 / +0.67 | 0.21 / 0.24 / 0.27 |
| 7 | I'm having toast | - / curious:opened / - | +0.37 / +0.52 / +0.66 | 0.21 / 0.24 / 0.27 |
| 8 | with butter | happy:opened / curious:sustained, happy:escalated / - | +0.37 / +0.60 / +0.76 | 0.20 / 0.23 / 0.26 |
| 9 | the kettle's boiling | happy:sustained / happy:sustained(nothing_to_hold) / - | +0.37 / +0.60 / +0.75 | 0.20 / 0.23 / 0.25 |
| 10 | ok | happy:lingering(lingering), happy:lingering(lingering) / - / - | +0.37 / +0.59 / +0.74 | 0.20 / 0.22 / 0.24 |

**recalled_not_active**: expected: A lift at message 2 (exam passed); at message 8 the exam is mentioned again in passing: recalled, no new lift.

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey | curious:sustained(nothing_to_hold) / happy:opened, curious:sustained(nothing_to_hold) / happy:opened | +0.34 / +0.38 / +0.42 | 0.15 / 0.15 / 0.15 |
| 2 | I passed my exam today! | happy:opened, curious:escalated / happy:opened, curious:escalated / happy:opened | +0.42 / +0.55 / +0.67 | 0.15 / 0.15 / 0.15 |
| 3 | yeah, finally | happy:sustained, curious:sustained(nothing_to_hold) / happy:escalated / happy:escalated | +0.50 / +0.67 / +0.82 | 0.15 / 0.15 / 0.15 |
| 4 | anyway what are you up to | - / - / touched:opened | +0.50 / +0.68 / +0.84 | 0.15 / 0.15 / 0.15 |
| 5 | I'm just cooking now | happy:escalated, curious:sustained / happy:opened, curious:escalated / happy:escalated, curious:opened | +0.50 / +0.80 / +0.93 | 0.15 / 0.15 / 0.15 |
| 6 | soup | curious:sustained / happy:sustained / happy:escalated, curious:sustained(nothing_to_hold) | +0.49 / +0.83 / +0.92 | 0.15 / 0.15 / 0.15 |
| 7 | it's cold out | happy:sustained(nothing_to_hold), happy:opened / happy:sustained / happy:escalated | +0.49 / +0.88 / +0.99 | 0.15 / 0.15 / 0.15 |
| 8 | like I said the exam went fine, so tha | happy:sustained / uneasy:escalated / happy:lingering(lingering), uneasy:opened | +0.48 / +0.87 / +0.98 | 0.18 / 0.22 / 0.25 |
| 9 | the soup's ready | happy:opened / happy:sustained / - | +0.48 / +0.90 / +0.97 | 0.18 / 0.21 / 0.24 |
| 10 | ok eating now | happy:sustained / happy:sustained / happy:sustained | +0.47 / +0.89 / +0.96 | 0.18 / 0.21 / 0.24 |

## D: connected conversations

**control** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for | curious:sustained(nothing_to_hold) / happy:opened / curious:sustained(nothing_to_hold) | +0.30 / +0.34 / +0.38 | 0.15 / 0.15 / 0.15 |
| 2 | ooh nice, I like those | happy:escalated / happy:escalated / curious:sustained(nothing_to_hold), happy:escalated | +0.34 / +0.47 / +0.59 | 0.15 / 0.15 / 0.15 |
| 3 | anyway what should I cook tonight, I h | - / happy:sustained(nothing_to_hold) / happy:opened | +0.34 / +0.51 / +0.67 | 0.15 / 0.15 / 0.15 |
| 4 | you always have good ideas for this st | happy:opened / happy:escalated / happy:escalated | +0.38 / +0.63 / +0.86 | 0.15 / 0.15 / 0.15 |
| 5 | what's your day been like? | happy:opened / happy:opened / happy:opened | +0.51 / +0.79 / +0.97 | 0.15 / 0.15 / 0.15 |
| 6 | I like talking to you, it's relaxing | happy:opened / happy:opened / happy:opened | +0.67 / +0.97 / +1.00 | 0.15 / 0.15 / 0.15 |
| 7 | sorry, I've been all over the place to | happy:lingering(lingering) / touched:opened / happy:lingering(lingering), touched:escalated, happy:opened | +0.74 / +0.97 / +1.00 | 0.15 / 0.15 / 0.15 |
| 8 | thanks for keeping me company. tell me | happy:escalated, touched:escalated / happy:escalated, touched:escalated / touched:sustained, touched:lingering(lingering) | +0.76 / +1.00 / +1.00 | 0.15 / 0.15 / 0.15 |

**slights_repair** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for | happy:opened / happy:opened / happy:opened | +0.30 / +0.42 / +0.55 | 0.15 / 0.15 / 0.15 |
| 2 | meh. those are kinda boring | amused:escalated / happy:sustained, hurt:escalated / curious:escalated | +0.29 / +0.42 / +0.54 | 0.18 / 0.18 / 0.18 |
| 3 | anyway what should I cook tonight, I h | happy:opened / happy:lingering(lingering) / happy:lingering(lingering) | +0.38 / +0.50 / +0.62 | 0.18 / 0.18 / 0.18 |
| 4 | you always suggest the obvious stuff | hurt:escalated, curious:escalated / hurt:escalated / hurt:escalated | +0.27 / +0.40 / +0.52 | 0.28 / 0.28 / 0.28 |
| 5 | what's your day been like? oh wait, yo | curious:sustained(nothing_to_hold) / happy:opened / touched:opened | +0.27 / +0.46 / +0.65 | 0.28 / 0.28 / 0.28 |
| 6 | honestly talking to you is a bit point | hurt:escalated, curious:sustained(nothing_to_hold) / hurt:opened / hurt:opened, uneasy:opened | +0.11 / +0.29 / +0.48 | 0.44 / 0.47 / 0.51 |
| 7 | sorry. that was mean, I've had a terri | hurt:lingering(lingering), touched:opened / hurt:eased, curious:opened / hurt:lingering(lingering), happy:escalated, curious:opened, touched:escalated | +0.28 / +0.48 / +0.69 | 0.40 / 0.43 / 0.46 |
| 8 | thanks for putting up with me. tell me | touched:sustained / hurt:eased / hurt:lingering(lingering), touched:lingering(lingering), happy:lingering(lingering) | +0.31 / +0.51 / +0.72 | 0.36 / 0.39 / 0.42 |

**slights_no_repair** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for | curious:sustained(nothing_to_hold) / happy:opened / - | +0.30 / +0.34 / +0.38 | 0.15 / 0.15 / 0.15 |
| 2 | meh. those are kinda boring | hurt:escalated / happy:sustained, hurt:escalated / hurt:opened | +0.20 / +0.24 / +0.28 | 0.25 / 0.25 / 0.25 |
| 3 | anyway what should I cook tonight, I h | hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.20 / +0.24 / +0.28 | 0.25 / 0.25 / 0.25 |
| 4 | you always suggest the obvious stuff | hurt:escalated / hurt:opened / hurt:opened | +0.10 / +0.14 / +0.18 | 0.34 / 0.34 / 0.34 |
| 5 | what's your day been like? oh wait, yo | touched:opened / happy:opened / hurt:lingering(lingering) | +0.19 / +0.25 / +0.32 | 0.34 / 0.34 / 0.34 |
| 6 | honestly talking to you is a bit point | hurt:escalated, curious:opened / hurt:escalated / hurt:opened | +0.09 / +0.12 / +0.15 | 0.43 / 0.46 / 0.50 |
| 7 | anyway. whatever | hurt:escalated / hurt:escalated / hurt:escalated, uneasy:sustained(nothing_to_hold) | +0.02 / +0.02 / +0.02 | 0.49 / 0.55 / 0.62 |
| 8 | tell me something, I'm bored | hurt:lingering(lingering) / hurt:lingering(lingering) / hurt:lingering(lingering) | +0.03 / +0.03 / +0.02 | 0.48 / 0.54 / 0.60 |

## One slight, then 1 vs 10 neutral messages

**one** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | ugh, you always pick the most boring s | hurt:opened / hurt:opened / hurt:opened | +0.20 / +0.17 / +0.13 | 0.25 / 0.28 / 0.32 |
| 2 | anyway, what are you up to? | happy:opened, hurt:lingering(lingering) / hurt:lingering(lingering), happy:escalated / hurt:lingering(lingering), happy:opened | +0.22 / +0.32 / +0.42 | 0.20 / 0.22 / 0.23 |

**ten** (3 runs)

| # | message | effects (run 0 / 1 / 2) | mood: skip / small / clear | tension: skip / small / clear |
|---|---|---|---|---|
| 1 | ugh, you always pick the most boring s | hurt:opened, uneasy:sustained(nothing_to_hold) / hurt:opened / hurt:opened, curious:sustained(nothing_to_hold) | +0.17 / +0.13 / +0.10 | 0.28 / 0.32 / 0.35 |
| 2 | anyway, what are you up to? | happy:opened, hurt:lingering(lingering) / hurt:lingering(lingering), happy:opened / hurt:lingering(lingering) | +0.25 / +0.26 / +0.27 | 0.27 / 0.31 / 0.34 |
| 3 | I'm making tea | happy:escalated, curious:opened, hurt:lingering(lingering) / hurt:lingering(lingering), happy:lingering(lingering), curious:opened / happy:opened, hurt:lingerin | +0.25 / +0.35 / +0.44 | 0.27 / 0.30 / 0.32 |
| 4 | it's raining a bit here | happy:opened / hurt:lingering(lingering) / happy:escalated, hurt:lingering(lingering) | +0.42 / +0.51 / +0.60 | 0.26 / 0.29 / 0.31 |
| 5 | do you like rain? | happy:opened / happy:escalated / happy:opened, hurt:lingering(lingering) | +0.50 / +0.67 / +0.82 | 0.25 / 0.28 / 0.30 |
| 6 | I might watch a film later | curious:opened / curious:opened, hurt:lingering(lingering) / happy:sustained, curious:opened | +0.50 / +0.67 / +0.81 | 0.24 / 0.27 / 0.29 |
| 7 | any good films you'd pick? | curious:eased, uneasy:lingering(lingering), happy:opened / hurt:lingering(lingering) / curious:escalated | +0.54 / +0.70 / +0.81 | 0.24 / 0.26 / 0.28 |
| 8 | hm, maybe | hurt:lingering(lingering) / uneasy:sustained(nothing_to_hold) / curious:sustained | +0.53 / +0.69 / +0.80 | 0.23 / 0.25 / 0.27 |
| 9 | my cat just knocked a cup over | amused:opened, happy:opened / amused:opened, uneasy:opened, curious:opened, hurt:lingering(lingering) / happy:opened, curious:opened | +0.55 / +0.78 / +0.91 | 0.26 / 0.28 / 0.30 |
| 10 | she does that a lot | amused:sustained / amused:sustained, curious:lingering(lingering), hurt:lingering(lingering) / happy:sustained, curious:sustained, amused:escalated | +0.55 / +0.80 / +0.90 | 0.25 / 0.27 / 0.29 |
| 11 | ok, back to the tea | curious:lingering(lingering) / amused:lingering(lingering), curious:sustained / happy:sustained | +0.55 / +0.80 / +0.89 | 0.25 / 0.26 / 0.28 |
