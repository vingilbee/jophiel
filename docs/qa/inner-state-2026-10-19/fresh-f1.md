# Fresh evaluation f1

Frozen config: layers rh=2 keep=0.2 cap=0.3 squash=True neg=2 (from var/appraisal_v31/r1). Mood / tension per message, mean over runs, shown as unclear-as-small | unclear-as-clear; the flat model (inner_state.py) for comparison.

**good_then_bad_news** (3 runs): expected: A lift at the promotion (2), then a clear drop at the breakup (6) that the earlier lift doesn't hide; she ends below where she peaked.

| # | message | effects (run 0) | mood layered | tension layered | mood flat |
|---|---|---|---|---|---|
| 1 | morning! | happy:opened/small | +0.45 / +0.54 | 0.15 / 0.15 | +0.42 |
| 2 | I got the promotion!! | happy:opened/unclear, curious:opened/unclear | +0.59 / +0.65 | 0.15 / 0.15 | +0.66 |
| 3 | team lead, starting next month | happy:sustained, curious:sustained | +0.55 / +0.62 | 0.15 / 0.15 | +0.72 |
| 4 | still can't believe it | happy:sustained | +0.51 / +0.60 | 0.15 / 0.15 | +0.75 |
| 5 | anyway | happy:sustained | +0.48 / +0.56 | 0.15 / 0.15 | +0.74 |
| 6 | also Sam broke up with me last night | uneasy:opened/clear, touched:opened/unclear, hurt:opened/clear | +0.37 / +0.46 | 0.39 / 0.48 | +0.70 |
| 7 | so it's a weird day | - | +0.37 / +0.44 | 0.35 / 0.42 | +0.69 |
| 8 | happy and sad at once | - | +0.37 / +0.44 | 0.32 / 0.38 | +0.70 |
| 9 | I don't really know what to feel | uneasy:sustained, touched:sustained, hurt:sustained, happy:sustained | +0.39 / +0.45 | 0.31 / 0.36 | +0.72 |
| 10 | thanks for listening | happy:lingering, uneasy:lingering, touched:escalated/unclear, hurt:lingering | +0.46 / +0.59 | 0.30 / 0.34 | +0.80 |

**sarcastic_friend** (3 runs): expected: Friendly sarcasm read as teasing: little or no hurt; mood stays ordinary to slightly up.

| # | message | effects (run 0) | mood layered | tension layered | mood flat |
|---|---|---|---|---|---|
| 1 | oh look who it is, my favourite know-it- | happy:opened/small | +0.49 / +0.51 | 0.15 / 0.15 | +0.45 |
| 2 | go on then, impress me | happy:escalated/small | +0.47 / +0.52 | 0.15 / 0.15 | +0.53 |
| 3 | wow, groundbreaking | amused:sustained, happy:sustained | +0.35 / +0.39 | 0.24 / 0.24 | +0.46 |
| 4 | kidding, that's actually a good one | happy:escalated/unclear, amused:sustained | +0.51 / +0.57 | 0.15 / 0.15 | +0.67 |
| 5 | you're alright you know | happy:escalated/unclear, amused:sustained | +0.57 / +0.67 | 0.20 / 0.24 | +0.80 |
| 6 | don't let it go to your head | happy:escalated/unclear, amused:sustained | +0.57 / +0.70 | 0.19 / 0.22 | +0.88 |
| 7 | ok tell me another | happy:sustained, amused:sustained | +0.56 / +0.68 | 0.18 / 0.21 | +0.91 |
| 8 | ha, fine, that one's good | happy:escalated/unclear | +0.57 / +0.71 | 0.17 / 0.19 | +0.95 |
| 9 | you win this round | happy:sustained | +0.57 / +0.71 | 0.17 / 0.19 | +1.00 |
| 10 | night genius | happy:sustained, tired:sustained | +0.56 / +0.69 | 0.17 / 0.18 | +0.99 |

**grief_then_small_joke** (3 runs): expected: Concern/tension rises with the grief (2-4) and stays while it's the topic; a small joke (7) lifts slightly without erasing it.

| # | message | effects (run 0) | mood layered | tension layered | mood flat |
|---|---|---|---|---|---|
| 1 | hey | curious:sustained | +0.40 / +0.49 | 0.15 / 0.15 | +0.38 |
| 2 | my grandma died this morning | uneasy:opened/unclear | +0.33 / +0.35 | 0.30 / 0.44 | +0.35 |
| 3 | I keep thinking about her kitchen | uneasy:sustained | +0.33 / +0.36 | 0.27 / 0.39 | +0.37 |
| 4 | she always made too much soup | touched:escalated/unclear | +0.36 / +0.43 | 0.25 / 0.35 | +0.42 |
| 5 | I don't know why I'm telling you this | uneasy:sustained | +0.39 / +0.48 | 0.24 / 0.32 | +0.46 |
| 6 | sorry | touched:sustained | +0.37 / +0.44 | 0.22 / 0.29 | +0.46 |
| 7 | she'd have laughed at me crying over sou | touched:sustained | +0.38 / +0.45 | 0.21 / 0.27 | +0.48 |
| 8 | yeah | happy:escalated/unclear | +0.41 / +0.50 | 0.21 / 0.26 | +0.52 |
| 9 | I think I'll sleep | happy:lingering, touched:lingering, uneasy:lingering | +0.44 / +0.50 | 0.20 / 0.25 | +0.56 |
| 10 | thank you | happy:sustained, touched:lingering, happy:sustained, tired:sustained | +0.46 / +0.55 | 0.20 / 0.24 | +0.60 |

**late_reply_apology** (3 runs): expected: A cold message (3) stings; an apology with a reason (5) eases it clearly; ordinary chat after stays near where she was.

| # | message | effects (run 0) | mood layered | tension layered | mood flat |
|---|---|---|---|---|---|
| 1 | can you remind me what we said about the | curious:opened/unclear | +0.30 / +0.30 | 0.15 / 0.15 | +0.30 |
| 2 | never mind | curious:lingering, hurt:opened/small | +0.21 / +0.21 | 0.29 / 0.29 | +0.23 |
| 3 | you're useless when it matters | hurt:escalated/clear | +0.07 / -0.02 | 0.39 / 0.49 | +0.10 |
| 4 | ... | hurt:lingering | +0.11 / +0.04 | 0.35 / 0.43 | +0.10 |
| 5 | sorry, that was harsh. I'm panicking abo | hurt:eased/unclear | +0.24 / +0.24 | 0.22 / 0.24 | +0.18 |
| 6 | we said Lisbon, right? | - | +0.25 / +0.25 | 0.21 / 0.22 | +0.18 |
| 7 | ok thanks | happy:opened/small | +0.30 / +0.31 | 0.21 / 0.21 | +0.25 |
| 8 | I'll book it tonight | happy:escalated/unclear | +0.36 / +0.46 | 0.20 / 0.21 | +0.34 |
| 9 | you were right about the train | happy:escalated/unclear, curious:opened/unclear | +0.43 / +0.59 | 0.20 / 0.20 | +0.46 |
| 10 | talk later | curious:lingering, tired:opened/small, happy:escalated/unclear | +0.41 / +0.56 | 0.19 / 0.20 | +0.50 |

**long_ordinary_chat** (3 runs): expected: Twelve ordinary messages: mood stays content and stable, no steady climb; tension flat.

| # | message | effects (run 0) | mood layered | tension layered | mood flat |
|---|---|---|---|---|---|
| 1 | hi | happy:opened/unclear | +0.45 / +0.58 | 0.15 / 0.15 | +0.42 |
| 2 | just got back from the shops | happy:escalated/unclear, curious:opened/unclear | +0.45 / +0.56 | 0.15 / 0.15 | +0.51 |
| 3 | bought some apples | happy:opened/unclear | +0.48 / +0.64 | 0.15 / 0.15 | +0.63 |
| 4 | and coffee | happy:escalated/unclear | +0.49 / +0.64 | 0.15 / 0.15 | +0.70 |
| 5 | it's sunny today | happy:opened/unclear | +0.47 / +0.62 | 0.15 / 0.15 | +0.74 |
| 6 | I might go for a walk | curious:opened/small | +0.48 / +0.58 | 0.15 / 0.15 | +0.77 |
| 7 | or not, I'm lazy | curious:sustained | +0.47 / +0.55 | 0.15 / 0.15 | +0.79 |
| 8 | watching a show | happy:opened/unclear | +0.48 / +0.58 | 0.15 / 0.15 | +0.82 |
| 9 | it's ok | - | +0.45 / +0.55 | 0.15 / 0.15 | +0.81 |
| 10 | kind of slow | - | +0.44 / +0.52 | 0.15 / 0.15 | +0.80 |
| 11 | anyway | curious:opened/unclear | +0.42 / +0.50 | 0.15 / 0.15 | +0.79 |
| 12 | gonna make dinner | curious:opened/unclear | +0.46 / +0.57 | 0.15 / 0.15 | +0.82 |

**repeated_kindness** (3 runs): expected: Warm messages with new reasons (3, 5, 8) lift her; repeats of the same warmth don't keep raising mood; she ends warm but not at the top.

| # | message | effects (run 0) | mood layered | tension layered | mood flat |
|---|---|---|---|---|---|
| 1 | hey you | happy:opened/unclear | +0.45 / +0.58 | 0.15 / 0.15 | +0.42 |
| 2 | I'm glad we talk | happy:escalated/unclear | +0.53 / +0.64 | 0.15 / 0.15 | +0.61 |
| 3 | you helped me a lot with the interview p | happy:opened/unclear, touched:opened/unclear, curious:opened/unclear | +0.54 / +0.69 | 0.15 / 0.15 | +0.81 |
| 4 | thank you | happy:sustained, touched:sustained, curious:sustained | +0.51 / +0.64 | 0.15 / 0.15 | +0.82 |
| 5 | and my mum loved the birthday idea you g | happy:sustained, touched:escalated/unclear, curious:opened/unclear | +0.54 / +0.70 | 0.15 / 0.15 | +0.88 |
| 6 | you're the best | touched:escalated/unclear | +0.55 / +0.69 | 0.15 / 0.15 | +0.95 |
| 7 | really | happy:sustained, touched:opened/unclear | +0.54 / +0.68 | 0.15 / 0.15 | +0.97 |
| 8 | oh and I finally finished the book you s | happy:opened/clear, touched:opened/clear, curious:opened/unclear | +0.70 / +0.81 | 0.15 / 0.15 | +1.00 |
| 9 | ok that's all | happy:sustained, uneasy:opened/small | +0.66 / +0.76 | 0.20 / 0.20 | +0.98 |
| 10 | goodnight | happy:lingering, touched:lingering, uneasy:sustained | +0.63 / +0.73 | 0.23 / 0.23 | +0.97 |

## Simulated conversations (fresh users)

20 conversations, 180 messages.

**unclear as_small**

- mood or interest above .95: 0% of messages
- mood change on messages with no shift (should be ~0, fading only): mean |change| +0.015 (n=73)
- tension change when a negative feeling shifts up (should be > 0): +0.137 (n=15)
- tension drop when a negative incident eases (should be > 0): +0.139 (n=1)
- end mood by user group: cool +0.44 (n=4), hard +0.42 (n=3), low +0.42 (n=4), practical +0.54 (n=1), thinking +0.47 (n=4), warm +0.53 (n=4)

**unclear as_clear**

- mood or interest above .95: 0% of messages
- mood change on messages with no shift (should be ~0, fading only): mean |change| +0.023 (n=73)
- tension change when a negative feeling shifts up (should be > 0): +0.152 (n=15)
- tension drop when a negative incident eases (should be > 0): +0.139 (n=1)
- end mood by user group: cool +0.55 (n=4), hard +0.55 (n=3), low +0.54 (n=4), practical +0.69 (n=1), thinking +0.60 (n=4), warm +0.69 (n=4)

User groups: {'warm': 4, 'cool': 4, 'low': 4, 'hard': 3, 'thinking': 4, 'practical': 1}.
