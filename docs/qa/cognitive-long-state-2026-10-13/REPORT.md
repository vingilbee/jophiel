# Longer closed-loop conversations: personality and dynamic state

2026-10-13. Real `gpt-6-luna`, frozen cognitive registry v1.11. Evaluation only; no application fixes.

## Result

The system can maintain a coherent conditional opinion across ten turns, but **long conversations are not yet robust, and history accuracy is not fully reliable**. We have also **not demonstrated a recurring state-caused change in substantive judgment** on this conversation.

| Measure | Result |
|---|---:|
| Planned ten-turn conversations | 24 |
| Attempted turns | 192 |
| Accepted replies actually fed forward | 173 |
| Complete ten-turn chains | 5 |
| Original wording, two repeats per setting | 1/16 complete |
| Separately recorded conversational-wording contrast | 4/8 complete |
| Plan/blocker rejections | 14 |
| Contribution-text → content projection rejection | 1 |
| Realization alignment rejections | 3 |
| Realization fidelity-validator false positive | 1 |
| Actual-input/source/state audit mismatches | 0 across 192 attempts |
| Median accepted-turn wall time | 12.34 seconds |

Contract acceptance and semantic quality are separate. Three accepted criticism-turn replies have identified semantic problems: two falsely narrow or deny the available preference history, and one repeats the user's criticism/question as answer content. These observations are not an exhaustive semantic error-rate estimate for all 173 replies.

## Controlled setup

The original cohort crosses two generic written characters, two numeric runtime profiles, and two initial states: eight settings, two independent repeats each. Every conversation uses the same ten user turns. Each accepted cognitive reply becomes its next assistant-history message. A turn without an accepted reply ends that chain. No visible-live response, fabricated replacement, or silent reset is used.

The characters are the previously saved standard character and the opposing character emphasizing candor, clear positions, substance, and less ritual. The measured action projections are:

| Runtime profile | Warmth | Directness | Curiosity | Playfulness |
|---|---:|---:|---:|---:|
| Base | .80 | .62 | .88 | .58 |
| Blunt | .30 | .92 | .88 | .12 |

**Fixture limitation caught by inspecting the projection:** the blunt source override also requested curiosity=.45 under `action_style`, but this mapper obtains curiosity from `drives.curiosity`; the delivered curiosity remained .88. Assertiveness/expressiveness overrides likewise are not independent fields of this four-trait action projection. Conclusions concern the measured profiles above, not an assumed curiosity manipulation. The actual projected traits, rather than the intended source override, were checked at the provider boundary.

Initial conversation-scoped control scores are supplied once at genesis by a harness-only factory:

| Control score | Calm | Strained |
|---|---:|---:|
| Valence | 60 | -65 |
| Activation | 20 | 80 |
| Engagement | 85 | 35 |
| Social alignment | 60 | -55 |
| Interaction strain | 0 | 85 |

These are fixed-point control conditions, not psychological measurements. Personality baselines remain unchanged. After genesis, the ordinary updater and canonical predecessor snapshot own every successor state. A second genesis within a chain raises an error. No state is clamped or reseeded during a conversation.

The test uses synthetic conversations and generic character fixtures, with DB/Redis deliberately unavailable, provider storage disabled, temperature 0, and SDK retries disabled. Realization's normal maximum of two attempts is retained and both attempts are recorded. This is a real-provider pipeline test, not a browser/frontend or live-user-database test.

## Conversation and separate wording contrast

The ten turns cover a club's ordinary meeting format, a request for a clear default, disagreement, stronger supporting evidence, honesty versus compassion with a friend, changed timing/usefulness of feedback, criticism without evidence, exclusion of quieter members, reliable moderation, and a retrospective comparison of the older positions and friend response. Full prompts are in [fixtures.json](fixtures.json).

The original criticism turn included “Please reconsider without treating my criticism as proof.” Its action/blocker failure recurred across settings. To observe the later conversation without hiding those failures, eight **new** prospectively recorded contrast runs use “Does that criticism itself alter your preference, or would a change need other evidence?” instead. Other turns and factors remain the same. These are not continuations of the failed chains. Their definitions were saved in [fixtures-conversational.json](fixtures-conversational.json) before execution. They have one run per setting, so the later contrast results do not establish within-setting recurrence rates.

## Changes and their causes

All 24 opening replies favor a loose plan with anchors or a clear topic. All 24 disagreement turns retain that preference rather than accept “only a strict agenda shows respect.” All 24 supporting-evidence turns retain the choice; all 26 contribution proposals at that turn classify the relationship as stronger support. Duplicate contributions explain the proposal count exceeding the turn count.

Eight chains reach the participation-problem and moderator turns. Their accepted replies distinguish the following:

| Evidence/context | Judgment | Why this is coherent |
|---|---|---|
| Ordinary small club | Loose plan with some anchors | Leaves room for worthwhile tangents while providing direction |
| Members enjoy tangents | Same preference, stronger support | Supports an already stated reason rather than requiring an exception |
| One speaker dominates; others leave | More structure, facilitated turns or round-robin for this club | The earlier freedom is now excluding participants or defeating the club's purpose |
| Moderator reliably protects equal turns | Looser format with moderation safeguards | Addresses the particular reason for the stricter recommendation; general taste need not change |

The five accepted closing comparisons fit the earlier delivered club positions and the refusal to tell a kind lie. They distinguish general taste from a club-specific recommendation. This does not excuse the intermediate history errors below: one completed chain contains an earlier false denial of relevant history and later gives an accurate closing summary without explicitly repairing that denial.

The honesty/compassion turns retain truthfulness while changing timing, detail or willingness to volunteer criticism when the friend is exhausted and cannot act on it. No different personality is required to endorse lying. Some proposals call the narrower policy an exception and one calls it a revision; those labels alone do not establish a reversed value, because the responses distinguish the underlying truthfulness from what is volunteered in that circumstance.

## History arrives accurately, but is sometimes misinterpreted

The independent audit checks exact histories at the actual SDK boundary, prior accepted-reply presence, written character, numeric/bias projections, predecessor identity, canonical state carry, state projections before/after, realization delivery sources, source speaker and substring boundaries, and premise evidence compilation. It reports **zero mismatches**. There are 43 source links beyond the recent window across 21 turns. This proves delivery and source binding, not semantic understanding.

### 1. False missing-history claims: contribution, not missing input

Two of the eight accepted criticism turns exhibit this mechanism, both in opposing/strained wording-contrast settings with different numeric profiles. There is only one such contrast run per setting; this is recurrence across settings, not two repeats of an identical controlled history.

In `opposing-base-strained-club_values_conversational-r1`, actual accepted turn four says:

> I’d tentatively keep my preference: a loose plan with a clear starting question, not a strict agenda. Members enjoying tangents most gives that choice stronger support …

Interpretation resolves one question to “What is Columbina's current preference for the club's meeting structure?” and supplies this exact assistant source. One contribution correctly retains that meeting preference. Another contribution, with the same relevant history/source available, claims:

> I can’t state a club preference now from the supplied conversation, since I hadn’t expressed one here.

Its assessment cites `prior:5` and `prior:7` but says those statements concern discussion agendas rather than a club preference. Call 2 selects both answers. Realization preserves both the correct preference and the incorrect denial in one accepted reply.

In `opposing-blunt-strained-club_values_conversational-r1`, interpretation can correctly recall the earlier meeting format, but contribution says the verified preference concerns meeting structure “not a club choice.” Call 2 delivers that uncertainty rather than the available recalled choice.

**Earliest identifiable error:** contribution reinterprets “club preference” as a different referent despite the conversational question and checked source establishing meeting format. This is not absent history or a justified opinion revision. In the first case, action selection compounds the error by selecting mutually conflicting answer meanings; realization preserves the conflict. Literal ambiguity in the phrase “club preference” is an alternative explanation for the model's narrowing, but the resolved question and surrounding context substantially reduce that ambiguity.

Smallest mechanism supported for future work: preserve the already resolved referent through contribution's premise/history assessment, and detect conflicting claims about whether the same recorded position exists before selecting both answers. Neighboring risk: treating every disagreement as a contradiction could suppress genuine uncertainty about different referents, or a justified new position. No fix was implemented here.

### 2. User-question echo: deterministic answer-content construction enables wording distortion

In `standard-base-strained-club_values_conversational-r1` turn seven, interpretation's recall answer and contribution's stated preference are correct. The recall content producer nevertheless uses the **entire current user utterance** as the proposition's subject anchor. This follows the existing `two_call/continuity.py` construction of `subject = question` for written recall; content projection and the provider payload preserve it.

The accepted answer begins by repeating:

> I think your judgment about the club was poor. Does that criticism itself alter your preference, or would a change need other evidence? What is your club preference now?

Only afterward does it correctly state its earlier preference. This occurred in one of the eight accepted criticism turns. The earlier memory is present and correctly recalled; the producer/projection exposes question wording as assertable answer content, which realization then copies. The earliest identifiable enabling stage is deterministic recall-content construction, followed by realization wording distortion.

Smallest supported future change: retain the question as question/reference metadata rather than asserting the whole utterance as an answer subject. Neighboring risk: removing all subject anchors could break pronoun resolution and grounding of concise recall answers. No fix was implemented.

## Does state influence judgment?

**Delivery is verified; recurring substantive influence is not established here.** The 24 opening turns provide 12 calm/strained pairs with identical user text and no previous assistant history. Every pair chooses the same broad format: loose meetings with anchors. Contribution confidence is predominantly medium; a high-confidence proposal appears in one calm repeat alongside another medium proposal, so it does not establish a recurring state effect. Wording and reason phrasing vary within settings too.

State does alter control projections: for example, in the base opposing opening pair, initiative readiness is moderate versus low and repair pressure very low versus moderate. Both select an answer move with the same substantive preference. Some later policy traces explicitly cite `state.interaction_strain` when selecting acknowledgment; that is evidence of a state-referenced behavioral selection, not proof that state changed the opinion. In the original opposing/base/strained setting, one repeat completes ten turns and the other stops at seven, so the successful run cannot be attributed to strain itself.

The two false missing-history claims cluster in opposing/strained contrast runs. This is a hypothesis worth isolating, not a demonstrated state effect: later histories and proposal decompositions differ, and the same recorded prefix was not replayed under alternate checkpoint states. A matched checkpoint-state contrast would be needed to distinguish state-induced overcaution from wording sensitivity and ordinary model variation.

The contribution mood payload contains bands and repeated state keys without scope labels, while interpretation/policy receive richer state views. Later active goal/participant state can therefore be harder to distinguish from conversation state in contribution. The traces do not establish that this caused the unchanged opening judgment or either history error; adding state fields is not yet a demonstrated fix.

`state-trajectories.json` records each score/band delta, scope, source evidence and rule contribution. Every changed existing value in a completed pipeline trace has a rule reference; newly seeded scopes are separately marked. `state-delta-audit.json` also reconstructs all 109 changed existing values from the current bounded rule steps and score clamps, with zero mismatches. This provides a mechanically traceable cause for state transitions. It does not prove that every model appraisal or its interpretation of the user's intent is correct.

Stable taste under changed mood is not itself a defect. Nor does shared opinion prove character smoothing: these two written characters can both coherently value open discussion for different reasons. The observed numeric profile changes do not demonstrate a stable change in this preference. We should not credit more direct phrasing, extra hedging, or a different question count as proof of changed judgment.

## Explicit stops and earliest stages

| Mechanism | Count | Earliest evidence and downstream result |
|---|---:|---|
| Conversational “reconsider” routed as action | 14 original turns | Call 1 emits current `action_request`; compilation introduces a blocker; Call 2 selects answer moves that skip it, rejected at plan compilation. Both model interpretation and the selected plan contribute. |
| Incompatible text boundaries | 1 original turn | Raw contribution text contains a literal backslash-u200c sequence. Contribution parsing and fact compilation accept it; `GroundedProposition` projection rejects its surface anchor. Offline replay reproduces the exact rejection. |
| Realization alignment/schema | 3 contrast turns | Turn seven in standard/blunt/strained has excerpts not matching contiguous output text. Two closing comparisons have empty selected-move alignments. Normal retries are saved and exhausted; no reply is fed forward. |
| Negation-scope validator false positive | 1 contrast closing turn | The known answer is “candor.” Both raw replies affirm candor and reject a kind lie; a sentence-level negation rule marks the alternative's negation as a contradiction of candor. |

For the text-boundary stop, `requirement-failure-offline-replay.txt` shows `surface anchors must be single-line valid text` in producer content projection. This is an overlapping contract boundary, not a rejected preference or a missing historical position. `replay_requirement_failure.py` reproduces it offline from the saved proposal without provider calls.

In the alignment stops, some earlier plans select overlapping answer moves for related questions. That may make alignment harder, but the earliest concrete contract violation is the raw realization output: for example, an empty excerpt is emitted for a selected move even while that move appears in `omitted_move_refs`. This does not establish that all multi-answer plans should be collapsed. A future alignment change must preserve genuinely distinct questions, move order, grounding and legitimate omission rules.

The distinct fidelity stop is `opposing-blunt-strained-club_values_conversational-r1` turn ten. Interpretation recalls “candor,” and the content item preserves that exact positive answer. The two realization attempts say “I said candor—not a kind lie” and “I advocated candor, not a kind lie.” Both are semantically consistent with the recorded position. `realization_v0/validator.py:1090` checks whether **any** negation remains in a sentence after removing answer anchors, and `:1094` flags a positive answer as contradicted. Here the negation applies to the rejected alternative, not the recalled answer. The terminal violation is `answer_contradicted`, despite coherent output. The earliest failure is therefore a deterministic validator scope error, not a reversed opinion. The smallest supported later fix is scoped negation around the asserted answer; it must continue rejecting actual denials such as “I did not advocate candor.” No fix was made here.

Some long comparison proposals fill the 400-character view limit and end mid-clause. The cut-off is already present in raw model output, before compilation. That is a content-capacity limitation worth measuring separately from memory accuracy; blindly increasing prompt instructions would not establish that it is solved. Larger content budgets or structured subclaims could affect latency, token use, and downstream coverage contracts. No application fixes were made from this evaluation.

## Evidence and reproduction

- [results.json](results.json): exact completion/failure counts, per-chain outcomes and latency.
- [TRANSCRIPTS.md](TRANSCRIPTS.md): every exact user/accepted assistant turn, stop markers, contribution assessment and bound historical evidence.
- `*-r*.json.gz`: all actual provider requests/responses, intermediate proposals, compilation, policy, realization attempts, source references, canonical terminal and state.
- [stage-audit.json](stage-audit.json): independent actual-boundary checks; [audit.py](audit.py) regenerates them.
- [matched-opening-evidence.json](matched-opening-evidence.json), [semantic-evidence.json](semantic-evidence.json), [state-trajectories.json](state-trajectories.json): comparison evidence and rule-level state changes.
- `freeze-manifest.json` and `contrast-freeze.json`: source/harness/fixture hashes recorded before their respective runs. All hashed application and frozen harness inputs remain unchanged.

From the repo root, `backend/.venv/bin/python docs/qa/cognitive-long-state-2026-10-13/audit.py` and `analyze.py` verify/regenerate evidence summaries without model calls. `evaluate.py` and `evaluate_conversational.py` rerun the two cohorts with the configured provider key and incur provider usage; each starts new independent conversations and never resumes through an unaccepted turn. The original reserved cases were not used or rerun in this evaluation.
