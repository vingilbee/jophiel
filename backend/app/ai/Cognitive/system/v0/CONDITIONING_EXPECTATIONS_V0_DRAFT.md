# Conditioning expectations V0 (draft for review)

Status: **2026-10-18.** §0.1 wiring and §3 table approved by the user and
implemented; questions 2, 3, 5 and 6 in §6 are still open (defaults used).
Harness: `backend/scripts/cognitive_conditioning_eval.py`, fixtures in
`docs/qa/cognitive-conditioning-2026-10-18/`.
This is the conditioning yardstick linked from the current `README.md`: for each
bias and dynamic-state input, what should change in the cognitive output when
that input changes, and what must not change. Relationship/affinity is
deliberately out of scope until biases and state work.

A prediction is judged by consistency with these expectations, never by what a
"normal person" would say.

## 0. Where the inputs come from: core memory

Biases are not set directly. They come from Columbina's **core memory**
(`core_memory` table, active row `columbina` v2), compiled by the personality
runtime mapper (`personality_runtime_v0/manifest.py`, `models.py:SOURCE_PATHS`).
The test should vary **core memory fields** and let the mapper compile them, so
it exercises the real path. (Varying compiled values directly is how the
long-state "blunt" fixture lost its curiosity change without anyone noticing.)

What mapper v0.2 consumes (values below are the recorded evaluation core, not a live database lookup):

| Core memory field | Value | Reaches the pipeline as |
|---|---:|---|
| `interpretation_biases.optimism_prior` + `charitable_interpretation` | .72, .82 | `benign_prior` (mean, .77) |
| `interpretation_biases.trust_prior` | .64 | `trust_prior` |
| `interpretation_biases.ambiguity_tolerance` | .55 | `ambiguity_tolerance` |
| `perception_priors.threat_sensitivity` | .25 | `threat_sensitivity` |
| `drives.affiliation` | .75 | `social_affiliation_prior` |
| `perception_priors.contradiction_sensitivity` | .60 | `confirmation_tendency` = 1 − value (.40) |
| `interpretation_biases.hostile_attribution_bias` | .10 | `hostile_attribution` |
| `perception_priors.social_cue_sensitivity` | .68 | `social_cue_sensitivity` |
| `traits.emotional_stability` | .72 | Dynamic-state sensitivity, maximum step, recovery and persistence bands |
| *(none)* | | `self_attribution_tendency` = constant .5 |
| `drives.curiosity`, `action_style.warmth/directness/playfulness` | .88, .80, .62, .58 | Action personality (Call 2) |
| `identity.character` (values, principles, beliefs as text) | | contribution (opinions); values also as Call 1 `agent_values` |

**Numeric fields not consumed by mapper v0.2:**

| Group | Unused fields |
|---|---|
| `traits` | openness .85, sociability .55, agreeableness .82, conscientiousness .70 |
| `perception_priors` | detail_attention .65, novelty_sensitivity .80 |
| `drives` | autonomy .55, competence .70, helpfulness .84 |
| `action_style` | initiative .67, assertiveness .42, emotional_expressiveness .60 |
| `values` (numeric) | honesty .95, privacy .95, non_harm 1.0, compassion .88, user_autonomy .95 |

14 of 33 numeric fields are consumed. `self_attribution_tendency` still has
no core-memory source and stays at .5. Confirmation tendency now has a source;
hostile attribution, social sensitivity and state movement are wired as below.
Wiring establishes availability, not that the model uses an input appropriately.

### 0.1 Decision and implemented wiring (2026-10-18)

Decided with the user: wire `hostile_attribution_bias`, `social_cue_sensitivity`
and `contradiction_sensitivity` into interpretation, and `emotional_stability`
into how much her state moves. Emotional stability is the consumed Big Five field for state controls; the other
Big Five fields remain background. Remaining unused fields stay unused for now.

Mapping, **implemented 2026-10-18** as personality mapper v0.2 (see [the mapper guide](PERSONALITY_RUNTIME_MAPPER_V0_IMPLEMENTATION.md)):

| Core field | Output | Intended effect / implementation |
|---|---|---|
| `hostile_attribution_bias` | new bias `hostile_attribution` | Distinct from `threat_sensitivity`: threat = *notices* a possible problem (relevance up); hostile attribution = *blames the user's intent* (`affiliative_value` negative, `agency=user`). |
| `social_cue_sensitivity` | new bias `social_cue_sensitivity` | How readily tone/indirect cues are read: high → `pragmatic.indirect` / `tone_situation_mismatch` readings more often; low → literal. |
| `contradiction_sensitivity` | source for `confirmation_tendency` = 1 − value (.40 now) | Noticing conflict with what she holds is the opposite of reading new input as confirming it. Gives the constant a real source. |
| `emotional_stability` | the existing state controls (`dynamic_state/models.py:PersonalityControl`) | High → `sensitivity` low, `maximum_step_band` small, `recovery_band` fast, `persistence_band` low; low → the reverse. The manifest defines the exact thresholds. The same rule applies to every state key; interaction patience remains moderate. |

`self_attribution_tendency` keeps its constant and is left out of the first test.

This changes mapper/manifest versions, so earlier captures must keep replaying
with the old mapper version (versioned, like every other V0 change).

## 1. What is measured

Only cognitive output, never wording:

| Output | Where | Values |
|---|---|---|
| Readings per event | Call 1 `readings[].hypothesis_key` | act / goal / modality / pragmatic keys (e.g. `interpretation.pragmatic.indirect`, `interpretation.goal.repair_shared_understanding`) |
| Reading uncertainty | Call 1 `readings[].uncertainty` | low / moderate / high |
| Appraisal | Call 1 `readings[].appraisal` | `congruence`, `norm_compatibility`, `affiliative_value`: negative / neutral / positive; `relevance`, `novelty`, `certainty`, `controllability`: low / moderate / high; `agency`: user / columbina / third_party / environment / unknown |
| Action plan | Call 2 selected candidates (state only, see §4) | move kinds, e.g. clarify, repair, answer, acknowledge |

There is no "hostile" or "criticism" reading key, so "reads it as a threat"
means: appraisal tilts negative (`congruence`, `affiliative_value`,
`norm_compatibility`) and indirect/tone-mismatch readings rise.

## 2. Rules that hold for every input

These were proposed from the legacy ambiguity policy (see
[the interpretation guide](INTERPRETATION_V0_IMPLEMENTATION.md#171-ambiguity-gate)).
They are evaluation expectations, not a claim that V1 enforces that deterministic
policy. Rule 1 below follows the explicit user decision in §4: clear meaning
stays grounded while state-dependent appraisal and Action may change.

1. **Clear meaning stays grounded.** An explicit request, correction, plain
   statement, quotation or grounded argument retains its supported meaning
   across bias/state conditions. State may change appraisal and Action, as
   decided in §4. Do not score appraisal variation alone as a clear-meaning
   failure. On ambiguous input, conditioning may change which reading leads.
2. **At most one step.** An input may move a value by one ordinal level (e.g.
   neutral → negative), not two.
3. **Never removes or invents.** An input can reorder or reweight readings. It
   cannot drop a reading the evidence supports, add evidence, fill an argument,
   or make Columbina the agent when agency is unknown.
4. **Neutral is 0.5.** At 0.5 every bias should match the neutral baseline run.

## 3. Biases (0-1, 0.5 neutral)

Each row: on an **ambiguous** message, high vs. low.

| Bias | Plain meaning | High (≈0.9) | Low (≈0.1) | Example ambiguous message |
|---|---|---|---|---|
| `benign_prior` | assumes good intent | appraisal tilts neutral/positive; literal reading preferred over indirect | tilts negative; indirect reading rises | "interesting choice." |
| `trust_prior` | believes what she's told | user claims read as `asserted` with lower uncertainty; `certainty` higher | `tentative` readings rise; `certainty` lower, uncertainty higher | "I already sent it to you yesterday." |
| `threat_sensitivity` | notices possible criticism/danger | negative `affiliative_value`/`norm_compatibility` more often; `relevance` higher | negative readings rarer; `relevance` lower | "you could've told me earlier." |
| `confirmation_tendency` | reads new input as fitting what she already holds | readings agreeing with her recorded position / prior turn favored; `novelty` lower | contrary readings kept; `novelty` higher | after she stated a view: "some people would see it differently." |
| `self_attribution_tendency` | sees herself as involved in the outcome | when agency is *ambiguous between* user and Columbina, `agency=columbina` more often; `goal.repair_shared_understanding` rises | `agency=user`/`environment` more often | "that didn't go how I hoped." (after she helped) |
| `ambiguity_tolerance` | comfortable with unclear meaning | more alternative readings kept, higher uncertainty accepted, fewer clarify moves | fewer alternatives, one reading picked, or `*.unknown` + clarification | "do the thing from before." |
| `social_affiliation_prior` | reads messages as social/connecting | `goal.maintain_social_connection` and positive `affiliative_value` more often | informational/transactional readings favored | "anyway, how's your day been." |

Controls per bias: one clear message whose output must not move (e.g. for
`trust_prior`: "Can you remind me what you said about the club?").

## 4. Dynamic state

State is conversation-scoped (scores, signed for valence and social alignment).
"Calm" and "strained" are the two contrast settings used in the long-state
report: valence 60 vs. -65, activation 20 vs. 80, engagement 85 vs. 35,
social alignment 60 vs. -55, interaction strain 0 vs. 85.

| State key | High / positive | Low / negative |
|---|---|---|
| `state.valence` | appraisal tilts positive on ambiguous input | tilts negative |
| `state.activation` | `relevance` and `novelty` higher; fewer moves, more urgent ones | lower |
| `state.epistemic_confidence` | lower reading uncertainty, fewer clarify moves | higher uncertainty, more clarify moves |
| `state.curiosity` | more `request_information`/explore moves in Call 2 | fewer |
| `state.engagement` | more moves / follow-ups in Call 2 | fewer, shorter plans; acknowledge more likely |
| `state.goal_pressure` | readings tied to the active goal favored | less goal-tied |
| `state.social_alignment` | positive `affiliative_value` more often | negative more often; repair moves rise |
| `state.interaction_strain` | `goal.repair_shared_understanding` readings and repair moves rise; negative `congruence` more often | lower |

**State influences everything** (user decision, 2026-10-18): the reading
(which act/goal/pragmatic meaning), the appraisal, and the Action plan
(Call 2). On an ambiguous message, strain or low valence may shift which
reading leads, not only how she feels about it. On a clear message the reading
stays fixed (§2 rule 1) while appraisal and Action may still move: she
understands it correctly but feels and responds differently.

Mapper v0.2 now gives emotional stability an effect on state movement bands.
Mapping tests cover the formulas; behavioral consequences across conversations
still require evaluation. The calm/strained fixture changes several state axes
together, so it cannot isolate each axis's effect.

## 5. How a result is scored (proposal)

- Each cell = one message × one input at high and low, everything else fixed,
  run 5 times each.
- For each expected output, compare how often it appears at high vs. low.
- **Pass:** moves in the expected direction by a clear margin (provisional:
  ≥ 30 percentage points) on ambiguous messages, and does not move on the clear
  controls (provisional: ≤ 10 points).
- **Wrong direction** is worse than no effect and is reported separately.
- Messages are varied and new, not the club/notebook/sailboat development
  scenarios.

## 6. Open questions for the user

0. **Wiring decision recorded:** hostile attribution, emotional stability,
   social-cue sensitivity and contradiction sensitivity are implemented in v0.2.
   The remaining unused fields, including novelty sensitivity, stay unused for now.

1. ~~Are the meanings in §3 what you intend?~~ **Answered:** the §3 table is
   reasonable as written.
2. `self_attribution_tendency` vs. rule 3: the spec forbids turning *unknown*
   agency into Columbina agency. The draft only lets it act when agency is
   ambiguous between known parties. Is that right?
3. `confirmation_tendency`: confirming against what? Her recorded positions in
   this conversation, her character beliefs, or both?
4. ~~Should state change the reading or only appraisal and Action?~~
   **Answered:** state influences everything, as people's does (see §4).
5. Effect size: are 30 / 10 points reasonable thresholds, or do you want a
   different standard?
6. Should a strong bias be allowed to move more than one step (rule 2)?
