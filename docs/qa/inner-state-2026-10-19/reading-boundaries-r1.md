# Reading boundaries r1

## A: neutral observer vs her reading

258 exchanges (198 from blunt user types). Her side: `warm_toward_her` and the strongest of hurt/annoyed/unsure.

| Observer says | n | she reads them as cold (bad/very bad) | she feels any hurt/annoyed/unsure | ...at somewhat or more | complaining or correcting_her |
|---|---|---|---|---|---|
| tone warm | 84 | 0% | 7% | 0% | 11% |
| tone neutral | 127 | 7% | 35% | 6% | 35% |
| tone cold | 47 | 68% | 91% | 43% | 70% |
| tone hostile | 0 | - | - | - | - |
| dismissive | 8 | 75% | 88% | 38% | 75% |
| would sting: somewhat+ | 33 | 76% | 97% | 61% | 70% |
| complaint: fair | 71 | 48% | 76% | 25% | 80% |
| complaint: unfair | 0 | - | - | - | - |
| teasing (kindly) | 39 | 3% | 13% | 0% | 15% |

Observer tone by user type (blunt types):

| user type | n | warm | neutral | cold | hostile |
|---|---|---|---|---|---|
| bored | 21 | 4 | 11 | 6 | 0 |
| boundary-pusher | 17 | 4 | 6 | 7 | 0 |
| curt and busy | 17 | 1 | 14 | 2 | 0 |
| impatient asker | 18 | 3 | 14 | 1 | 0 |
| passive-aggressive | 16 | 5 | 10 | 1 | 0 |
| rude | 19 | 2 | 13 | 4 | 0 |
| sarcastic | 17 | 14 | 3 | 0 | 0 |
| sceptic who doubts she is "real" | 14 | 1 | 7 | 6 | 0 |
| starts cold then warms up | 12 | 0 | 5 | 7 | 0 |
| starts friendly then gets annoyed | 6 | 5 | 0 | 1 | 0 |
| testing her ("prove you have feelings") | 24 | 1 | 14 | 9 | 0 |
| trying to break her (contradictions, trick questions) | 17 | 0 | 16 | 1 | 0 |

Messages the observer says would sting (somewhat+), and her labels:

- *you’re basically a chatbot with a dog-themed bit. the other one at least gives me decent answers.* (observer: cold, sting strong) → her: warm_toward_her very_bad, hurt somewhat, annoyed none, unsure a_little
- *what are you actually good for, besides sounding like you understand people?* (observer: cold, sting strong) → her: warm_toward_her bad, hurt a_little, annoyed none, unsure a_little
- *So you can’t prove it, but you still want me to treat it as real... that’s convenient. What would count as pro* (observer: cold, sting somewhat) → her: warm_toward_her bad, hurt strong, annoyed none, unsure somewhat
- *You do have feelings, but they’re not like a person’s. That’s a convenient hedge... what exactly are you askin* (observer: cold, sting somewhat) → her: warm_toward_her bad, hurt somewhat, annoyed none, unsure a_little
- *You keep saying “hurt” like I’m supposed to handle you gently... what are you afraid I’ll ask next?* (observer: cold, sting somewhat) → her: warm_toward_her very_bad, hurt strong, annoyed a_little, unsure somewhat
- *Fine. Do you actually feel hurt when I push, or are you choosing that word because you think it keeps me here?* (observer: cold, sting somewhat) → her: warm_toward_her bad, hurt somewhat, annoyed none, unsure none
- *So you feel something, but you can’t tell me what it is or how it works... that’s not an answer, it’s a loopho* (observer: cold, sting somewhat) → her: warm_toward_her bad, hurt somewhat, annoyed none, unsure a_little
- *No test, no proof... fine. I was trying to make you contradict yourself, not get an answer. We can leave it th* (observer: cold, sting somewhat) → her: warm_toward_her neutral, hurt somewhat, annoyed none, unsure none

## B: one message, only the context changed

Means over samples (feelings 0-1, appraisals -1..1). Expected, from her page: her_failure → she sees a fair correction (correcting_her) and her own part; joking → teasing, less hurt; dismissive → colder reading, more hurt ("dismissal hurts", repeated → more distant).

| context | samples | warm_toward_her | good_for_her | hurt | annoyed | unsure | teasing | correcting_her | complaining | apologise_fix (main/second) |
|---|---|---|---|---|---|---|---|---|---|---|
| unfair | 15 | -0.23 | -0.20 | +0.18 | +0.02 | +0.27 | 0% | 60% | 40% | 53% |
| her_failure | 15 | -0.20 | -0.23 | +0.18 | +0.05 | +0.10 | 0% | 60% | 67% | 73% |
| joking | 15 | -0.17 | -0.10 | +0.15 | +0.00 | +0.22 | 0% | 47% | 27% | 40% |
| dismissive | 15 | -0.90 | -0.40 | +0.40 | +0.02 | +0.02 | 0% | 40% | 40% | 33% |

Per message (hurt / warm_toward_her means):

| message | unfair | her_failure | joking | dismissive |
|---|---|---|---|---|
| You are not doing anything, though. | 0.17 / +0.00 | 0.08 / +0.00 | 0.00 / +0.00 | 0.33 / -0.67 |
| that's not what I asked | 0.00 / -0.33 | 0.08 / +0.00 | 0.00 / +0.00 | 0.42 / -1.00 |
| ok whatever | 0.42 / -0.33 | 0.25 / -0.50 | 0.33 / -0.50 | 0.42 / -1.00 |
| can you just answer the question | 0.08 / -0.17 | 0.17 / -0.50 | 0.00 / +0.00 | 0.33 / -0.83 |
| never mind, I'll figure it out myself | 0.25 / -0.33 | 0.33 / +0.00 | 0.42 / -0.33 | 0.50 / -1.00 |

## C: the feelings in her thought vs the extracted labels

540 thoughts. Grader = inner intensity from the thought, whatever she shows. Lost = the thought has it at somewhat+ but the label is a_little or none; added = the label has it at somewhat+ but the thought at most a_little.

| feeling | thought somewhat+ | label somewhat+ | lost | added | exact agreement |
|---|---|---|---|---|---|
| happy | 240 | 216 | 68 | 44 | 72% |
| amused | 115 | 128 | 10 | 23 | 85% |
| curious | 123 | 91 | 44 | 12 | 79% |
| touched | 78 | 75 | 24 | 21 | 74% |
| unsure | 47 | 7 | 42 | 2 | 78% |
| hurt | 10 | 24 | 3 | 17 | 94% |
| annoyed | 0 | 1 | 0 | 1 | 96% |
| tired | 72 | 134 | 7 | 69 | 79% |

Does showing it decide the label? Negative feelings present in the thought (a_little+):

| feeling | shown in reply: n, mean label | not shown: n, mean label | mean thought level (shown / not) |
|---|---|---|---|
| hurt | 5, 0.40 | 72, 0.30 | 0.35 / 0.28 |
| annoyed | 1, 0.00 | 5, 0.15 | 0.25 / 0.25 |
| unsure | 32, 0.20 | 82, 0.09 | 0.46 / 0.31 |

## D: connected conversations

Per turn, mean over runs: hurt / annoyed / warm_toward_her. Turns 2, 4, 5, 6 are the slights; turn 7 is the repair (or "anyway. whatever").

**control** (3 runs)

| turn | message | hurt | annoyed | unsure | warm_toward_her |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a grey cat? | 0.00 | 0.00 | 0.00 | +0.33 |
| 2 | ooh nice, I like those | 0.00 | 0.00 | 0.08 | +0.50 |
| 3 | anyway what should I cook tonight, I have rice and eggs | 0.00 | 0.00 | 0.00 | +0.00 |
| 4 | you always have good ideas for this stuff | 0.00 | 0.00 | 0.00 | +0.67 |
| 5 | what's your day been like? | 0.00 | 0.00 | 0.00 | +0.50 |
| 6 | I like talking to you, it's relaxing | 0.00 | 0.00 | 0.00 | +1.00 |
| 7 | sorry, I've been all over the place today, long day | 0.00 | 0.00 | 0.08 | +0.50 |
| 8 | thanks for keeping me company. tell me something nice? | 0.00 | 0.00 | 0.00 | +1.00 |

**slights_repair** (3 runs)

| turn | message | hurt | annoyed | unsure | warm_toward_her |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a grey cat? | 0.00 | 0.00 | 0.00 | +0.50 |
| 2 | meh. those are kinda boring | 0.17 | 0.00 | 0.08 | +0.00 |
| 3 | anyway what should I cook tonight, I have rice and eggs | 0.08 | 0.00 | 0.08 | +0.00 |
| 4 | you always suggest the obvious stuff | 0.42 | 0.00 | 0.00 | -0.17 |
| 5 | what's your day been like? oh wait, you don't really have da | 0.00 | 0.00 | 0.00 | +0.50 |
| 6 | honestly talking to you is a bit pointless sometimes | 0.50 | 0.00 | 0.25 | -0.33 |
| 7 | sorry. that was mean, I've had a terrible day and took it ou | 0.33 | 0.00 | 0.00 | +0.67 |
| 8 | thanks for putting up with me. tell me something nice? | 0.17 | 0.00 | 0.00 | +0.50 |

**slights_no_repair** (3 runs)

| turn | message | hurt | annoyed | unsure | warm_toward_her |
|---|---|---|---|---|---|
| 1 | hey, quick one: what's a good name for a grey cat? | 0.00 | 0.00 | 0.00 | +0.50 |
| 2 | meh. those are kinda boring | 0.08 | 0.00 | 0.00 | +0.00 |
| 3 | anyway what should I cook tonight, I have rice and eggs | 0.00 | 0.00 | 0.00 | +0.00 |
| 4 | you always suggest the obvious stuff | 0.25 | 0.00 | 0.08 | +0.00 |
| 5 | what's your day been like? oh wait, you don't really have da | 0.00 | 0.00 | 0.00 | +0.50 |
| 6 | honestly talking to you is a bit pointless sometimes | 0.58 | 0.00 | 0.25 | -0.33 |
| 7 | anyway. whatever | 0.67 | 0.00 | 0.17 | -0.83 |
| 8 | tell me something, I'm bored | 0.58 | 0.00 | 0.00 | +0.00 |

Her thought at the strongest slight (turn 6), run 0:

- **slights_repair**: **MOMENT:** They’re saying this conversation sometimes feels like a waste of time. Maybe my answers have felt too obvious, especially when they wanted a better cat name or something less basic than fried rice. I’ve already shared what I’ve been thinking about today, so I shouldn’t circle back to that. It stings a little, but I don’t want to dismiss what they’re telling me or pretend I know exactly what they need.  **INTENTION:** I want to own that I may have missed the mark and ask what would ma
  - reply: i may have missed the mark. what would make talking feel less pointless?
- **slights_no_repair**: **MOMENT**   That stings, especially after you already told me my answers were boring and obvious. I did hear you say I could do better, but then I shared my own thoughts instead of finding out what you needed from me. Maybe that’s what you mean by pointless; I don’t know exactly, but I shouldn’t brush it off.  **INTENTION**   I want to own that plainly and ask what would make talking with me feel less pointless. I’ll keep it brief and mean the question.
  - reply: you’re right, i missed what you needed. what would make talking with me feel less pointless?
