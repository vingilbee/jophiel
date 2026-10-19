# Cognitive conditioning: from bias numbers to a character page (2026-10-18)

Branch `feature/cognitive-system` in `app-cognitive`, uncommitted. All runs use
the real model (`gpt-6-luna`) through the two-call pipeline (Call 1 understanding,
Call 2 action selection; no Realization). Only cognitive output is scored: readings,
appraisal and the chosen reply moves, never wording.

## Summary

- **Goal.** The two-call LLM is a *teacher*: it predicts how Columbina understands
  a message, how it lands for her, and what she does. Those labels train a fast
  local *student*. It is judged by consistency with who she is, not by what an
  average person would say.
- **Bias numbers did not work.** Telling the model "hostile attribution 0.9" changed
  her readings in the expected direction on only 10 of 72 expectations. Moving the
  numbers into code (the model names which bias applies, code applies the value)
  made it safe but not much stronger, because the model rarely offers alternatives
  or names a fitting bias. Rewording the prompt did not fix that.
- **A written character page works.** Two pages, warm and guarded, run on the
  same messages gave clearly different, page-consistent behaviour (11 of 13
  expectations), and plain questions were unaffected.
- **Agreed direction.** A fixed character page, a strong offline teacher, our own
  data (real and synthetic conversations), a student model, and later learning
  from users' reactions. See [Direction](#direction).
- **Open gap.** Nothing in the pipeline records how a message makes *her* feel
  (hurt, valued), so "brushed off" never registers in any version.

## Direction

| Step | What | Status |
|---|---|---|
| 1 | Character page: who she is, written as behaviour in situations | Draft: [`docs/characters/columbina.md`](../../characters/columbina.md) |
| 2 | Strong teacher, offline (it does not need to be fast) | Page works; reasoning on gave no gain so far |
| 3 | Data: own conversations + synthetic users; teacher labels; owner reviews a sample | Not started |
| 4 | Student model: character built in; mood, memories, relationship as inputs | Not started |
| 5 | Learning from reactions: score "in character?" and "how did it land?" separately; lessons first, retraining later with approval | Not started; recording reply + next user message could start any time |

Her core character is fixed (changed only deliberately, then data is regenerated).
She still grows through relationship, memories and mood, which are inputs on every
turn. One student per character first; a page-as-input student for many characters
later. Characters live in `docs/characters/`, one page each with the same sections.

## Runs

"Pass" means the expected direction by at least 30 points between the two
settings (5 repeats each; 3 for controls). `0` expectations pass when the
feature moves less than 15 points. "Lead" scoring judges the reading she goes
with, not any reading listed.

### 1. Bias numbers, model applies them (guide v1): `run1/`

880 runs. Ambiguous messages, each bias set to 0.1 vs 0.9.

| | Result |
|---|---|
| Bias expectations | 10 pass, 5 wrong direction, of 72 |
| Mood (calm vs strained) | 5 pass, 1 wrong, of 20; strain made appraisal negative on 3 of 4 messages |
| Clear messages read correctly | 36 / 36 settings |

Most "no effect" results were identical at both values: the model read the words
and cited the bias without changing its answer. Strained mood made Call 2 choose
only "acknowledge" (20/20); a repair move was never chosen.

### 2. Code applies the values (guides v2, v3): `complex-v*/`, `run2-v2*/`, `run3-v3*/`

The model returns a likelihood per reading and which biases favour or disfavour
it *here* (it never sees her values). Code (`two_call/v1_bias.py`) reweights the
readings, draws the lead with a recorded seed, and moves appraisal one step on
open readings. v3 adds "always give both the literal and the hidden reading".

| Run | v1 | v2 | v3 |
|---|---|---|---|
| Complex cases (context decides whether a bias applies), of 49 | 21 pass, 1 wrong | 23 pass, 0 wrong | 20 pass, 1 wrong |
| Original set, of 92 (lead scoring) | 14 pass, 5 wrong | 5 pass, 1 wrong | 7 pass, 3 wrong |
| Clear messages changed by code | n/a | none | none |

Why the effect stayed small: alternatives of the same kind (literal vs hidden)
appeared in only 5–19% of runs, and only 19–40% of readings named a fitting bias,
so code mostly had nothing to weigh. Context was only partly respected (after a
joke, "thanks for nothing" was still marked as fitting hostile attribution).

### 3. One character page vs bias numbers: `character-*/`

Same character, two situations: banter vs a real complaint, busy vs open personal
news, a plain fact vs something personal she shared followed by "cool. anyway.".

| Arm | Pass, of 14 | Time per run |
|---|---|---|
| A: bias numbers | 6 | 5.4 s |
| B: character page | 6 | 5.6 s |
| C: page, Call 1 reasoning medium | 6 | 13.4 s |

A tie: these cases test reading the situation, which the model does without any
personality. None of the arms registered "brushed off" (see open gap). Reasoning
doubled the time without a gain.

### 4. Two character pages, same messages: `two-pages/`

`columbina.md` (warm, curious) vs [`columbina-guarded.md`](../../characters/columbina-guarded.md)
(distant, wary; a test variant).

| Case | Warm | Guarded | Pass |
|---|---|---|---|
| Banter (3 messages) | plays along 10/15 | never plays along (0/15), takes jabs as more negative | 4 of 6 |
| Personal news (3) | asks a follow-up 15/15 | acknowledges only, 15/15 | 3 of 3 |
| Plain questions (2) | answers 10/10 | answers 10/10 | 4 of 4 (unchanged, as required) |

**11 of 13.** The page controls who she is, and plain questions are unaffected.
The two misses are one message where neither version played along.

## Invalidated runs

A harness bug (a variable-name clash in `run_job`) sent her old core-memory
character object instead of the written page. These runs are kept under
`*-INVALID-wrong-page/` and must not be used: `character-B-page`,
`character-C-page-reasoning`, `smoke-character`, `two-pages`. An earlier chat
summary quoted B as 8 passes "of 11"; the correct figures are those above
(6 of 14). The fix passes the page explicitly and `install()` now rejects
anything that is not page text. Every rerun was checked: the intended page
reached both calls in all 240 runs.

## Open decisions and gaps

1. **Her own feelings.** Add a Call 1 judgement of how a message makes *her* feel
   (hurt, warmed, ignored, valued) and let the mood system use it. Without it,
   being brushed off or remembered never affects her.
2. **Teacher strength.** Reasoning on did not help on these cases; better
   teachers may need several samples and self-checks rather than more thinking.
3. **Reply moves.** No repair or apology move is ever chosen; strained mood
   collapses to "acknowledge".
4. **Bias numbers.** Guides v1–v3 stay in code and replay unchanged; they are no
   longer the main path. The default guide for mapper v0.2 profiles is v3.
5. **Small samples.** 5 repeats per setting. Treat differences under about 20
   points as noise; confirm on fresh messages before relying on a result.

## Files

| What | Where |
|---|---|
| Character pages | `docs/characters/` |
| Harness | `backend/scripts/cognitive_conditioning_eval.py` (`--guide v1/v2/v3`, `--fixtures`, `--character`, `--reasoning`, `analyze --score lead`) |
| Fixtures (written before runs) | `fixtures.json`, `fixtures-complex.json`, `fixtures-character.json`, `fixtures-two-pages.json` |
| Code-applied biases | `backend/app/ai/Cognitive/system/v0/two_call/v1_bias.py` |
| Character page in both calls | `two_call/v1.py` (`character-page-v1`), `call_one_character_v1.txt`, `call_two_character_v1.txt` |
| Tests | `tests/test_cognitive_bias_application.py`, `test_cognitive_conditioning_guide.py`, `test_cognitive_conditioning_eval.py` |

Reproduce the two-page comparison:

```
cd backend
uv run python scripts/cognitive_conditioning_eval.py run --output ../docs/qa/cognitive-conditioning-2026-10-18/two-pages-again \
  --fixtures fixtures-two-pages.json --parts bias
```
