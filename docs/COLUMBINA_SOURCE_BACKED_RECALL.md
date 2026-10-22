# Source-backed recall: how one thing changed

Date: 2026-10-21. Status: implemented behind `RECALL_HISTORY_ENABLED` (off), tested on real Postgres with a scripted reader. Not yet evaluated with the real model or through the frontend (see "Evaluation"). Not independently reviewed.

Handoff and evidence: `app/docs/COLUMBINA_SOURCE_BACKED_RECALL_HANDOFF.md` (in the `app` checkout), `docs/reports/memory-failure-diagnosis-2026-10-21.md`, `docs/reports/memory-bundle-comparison-results-2026-10-21.md`.

## The problem

The user moved a meeting from 4 pm to 5 pm, then to 6 pm. Asked for the order, Columbina answered "5 pm, then 6 pm", twice. The first time the 4 pm records weren't retrieved: they sat in General while later plans sat in their own context, and ranking favoured the named context. The second time the 4 pm passages were supplied and the answer still denied them. In a fixed-evidence comparison, an explicit history with source quotes plus the raw passages gave the full sequence 5/5 times; the raw passages alone, 0/5.

## What it does

A new memory read tool, `recall_history(question, about?, context?)`, shares the three-calls-per-reply budget with the other read tools.

| Step | Where | What |
|---|---|---|
| 1. Start | `recall.search` | One historical search (finished goals and replaced facts included) for the question. Under an explicit `context` the search is limited to it, as any tool search is. |
| 2. Expand | `services/recall_expand.py` | Follow stored links from those records: `memory_evidence` (origin, resolution, correction), a fact's `source_message_id` and its kept history, an episode's messages, an event's item. Then go back from each source to every memory it touched. Contexts are crossed. |
| 3. Read | `app/ai/recall/history.py` | One structured call (`RECALL_HISTORY_MODEL`, Luna, reasoning `low`) gets the question, the scope and the passages, and proposes steps, the current value, gaps and conflicts, each citing exact words. |
| 4. Check | `history.check` | Code keeps only what it can trace and settles what depends on it (below). |
| 5. Show | `history.render` | The rebuilt history, labelled as a reading for this question, followed by the passages themselves. |
| 6. Keep | `recall_history.save`, with the reply's receipt | `recall_reconstructions` + `recall_reconstruction_sources`. |

It is read-only toward memory: nothing completes, revises or creates a memory, and the account is never stored as a fact. A failure at any step is shown as a failure, never as "no history". What she says with the result is up to her; no prompt rule turns "no match" into a question.

## Rules

**Who said it.** A passage is one of four speakers.

| Speaker | What it is |
|---|---|
| `user` | The user's message, word for word. |
| `columbina` | Her reply, only as far as it was heard (`heard.heard_text`), or one of her thoughts. |
| `memory` | A memory record. |
| `source` | A game or app report. |

Her words are never the user's. A step that rests only on her words is kept but marked "ONLY in your own earlier words". The current value is then `unsettled`. This matters because the failing case had her own wrong answer ("5 pm was earliest") in the conversation.

**Explicit scope.** History may come from anywhere. Records outside the asked contexts are marked OUTSIDE SCOPE. A current value supported only by out-of-scope memories is `not_established`.

**Citations are traceability, not truth.** The rules:
- A quote must appear word for word in its passage, after whitespace is normalized. If it doesn't, the citation is dropped.
- A step left with no citations is dropped, and the account becomes `partial`.
- The current value becomes `unsettled` when a step it rests on (`follows`), or any later step, was dropped or rests only on her words.
- A value that doesn't appear in its quote is a warning, not a drop.
- An exact quote under a wrong reading still passes. Nothing claims otherwise.

**Order.** Steps are sorted by when their cited sources were said, not by the order the reader listed them (`reordered` records the difference). The dates shown are when things were said. An effective date appears only if a passage states one.

**Step kinds.**
- `first_recorded`: the earliest value the passages show.
- `change`: the value really changed.
- `correction`: an earlier statement was a mistake.
- `considered`: an alternative that was not adopted. This is never a change.
- `unclear`

**Budgets**, each reported when it cuts:

| Setting | Default |
|---|---|
| `RECALL_HISTORY_MAX_DEPTH` | 6 links |
| `_MAX_NODES` | 128 |
| `_MAX_FANOUT` | 24 records from one source |
| `_MAX_CHARS` | 16,000 |
| `_PASSAGE_CHARS` | 1,500 |
| `_SECONDS` | 3 |

Each depth level is a few batched queries per kind. A message linked to many memories is the expensive case, which is why fan-out is capped.

**Ownership.**
- Every query filters by owner. A message is read only through a conversation the user owns.
- A forged link to another user's row never becomes a passage. It is only counted as `unavailable`.
- A trigger on `recall_reconstruction_sources` refuses a source row that isn't the owner's. Memory targets use composite `(id, user_id)` keys.

**Not claimed.** A memory with no links may never have had any, so expansion reports only linked rows that have gone, not "lost sources".

## One lifecycle on every read path

`recall.annotate` labels two things:
- an event that quotes an item's wording from before a correction (`no_longer_current`);
- an item that a correction revised (`MemoryRecord.corrected`).

It runs in search (events and items), `read` (with or without evidence), `timeline`, the reread of an answer's references, and history expansion. Before this, only search applied the event label, and the item label depended on `include_evidence`.

## Provenance

`recall_reconstructions` holds one row per rebuilt history, belonging to the reply's receipt:
- the question and `about` as the tool was asked (bounded);
- the scope;
- the status;
- the checked account, with citations as `{passage, start, end}` spans and no copied quotes;
- coverage (seeds, visited, stop reason, truncation, gaps, query count and milliseconds);
- the reader's model, reasoning effort, input digest (sha256 of prompt version + instructions + input), latency and tokens.

`recall_reconstruction_sources` holds one row per passage:
- exactly one real FK target: a memory, message, observation, experience, session event or thought;
- the source's fingerprint at the time;
- speaker, depth, whether it was cited, whether it was in scope, and whether it was cut short.

No source text is copied. Deleting a source deletes its row, and the reconstruction keeps its counts. Saving runs in its own savepoint inside the receipt's, so a source deleted in the meantime costs only that history (`counts.histories_unsaved`). The memories among the passages are also `supplied` references on the receipt, so association and follow-ups see them.

Inspect with `GET /api/memory/recall` (`receipts[].histories`). Reset scope `recall` removes them. Migration `e8b2d6f4a1c7`.

## Evaluation

- `tests/test_recall_history.py` (22 tests, real Postgres, scripted reader). The fixture is a pottery class moved 3 → 4 → 7 pm, deliberately not Lantern. The tests cover:
  - expansion across contexts from an item and from a fact;
  - ownership, including a forged link;
  - each budget, loops, a deleted source, and failure part way;
  - scope marking;
  - correction labels agreeing on all five read paths;
  - the check: complete, reordered, an untraceable last step (current value unsettled), an untraceable early step (current value stands), her words only, out-of-scope current value, `considered`, a failed reader;
  - the reader getting the question and scope;
  - no memory changing;
  - receipt provenance (spans not quotes, FK targets, cascade on delete);
  - the owner trigger;
  - the flag.
- **Dry run on the stored Lantern case** (database half only, no model call). The focused follow-up question started 12 search seeds, visited 32 records and messages at depth 2, and showed 32 passages, with every link followed, in 36 ms and 24 queries. The passages include all three of the user's messages: the original Saturday 4 pm plan, "moved from 4 pm to 5 pm" and "moved again, from 5 pm to 6 pm". Reader input is about 11k characters.
- `scripts/recall_history_eval.py` (real model calls; run only when approved):
  - `reconstruct` runs the production path N times on the stored case and scores history completeness and the current value separately (case-specific scoring, eval only).
  - `answer` passes each rebuilt history unchanged into `complete_chat`, using the frozen comparison's context, which includes the earlier wrong answer. `fixed` adds it to the 0/5 "relevant passages" block. `live` gives the real read tools and lets the model decide whether to call `recall_history`.

Still to do:
- run that evaluation;
- semantic tests with the real reader on mistaken-vs-changed, hypotheticals, broken chains, conflicts, similar names, and non-time properties (a marker, a place, a preference);
- a frontend retest with fresh accounts;
- measured latency and cost in replies.

## Not in this slice

These stay separate, as the handoff says:
- wrong-goal completion;
- duplicate fact keys;
- keeping hypotheticals that were never stored;
- entity grouping;
- decay;
- learning.

Source access through `read_memory(include_evidence=True)` still shows ids and labels, not source text.
