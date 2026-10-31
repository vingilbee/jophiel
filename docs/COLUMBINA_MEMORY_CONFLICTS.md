# Memory conflicts: when what the user says doesn't match what she remembers

Status 2026-10-29: built in `app-cognitive`, uncommitted. Migration `e5b9d3a7c2f1` is not applied to the dev DB,
and the worker hasn't been restarted. Tests: `tests/test_memory_conflicts.py`.

## The rule

Memory never decides which version is true. It sorts each statement into one of three outcomes:

| What the user says | Memory's outcome | What is stored |
|---|---|---|
| "Nora moved to Porto last month" (a change they state or explain) | accepted change | the fact is revised; any open disagreement with it becomes `fact_changed` |
| "my sister in Lisbon" (after Porto, with no explanation) | **unresolved conflict** | the fact is left exactly as it was; one `memory_conflicts` row |
| the same thing again, or a detail that fits | no change | as before |

Only the conflict goes to cognition, which decides whether to ask, think it over or leave it. Her personality
shapes that choice, not which version is true.

## How it works

1. **Detect.** The consolidation call (`long_term_memory._extract`) gets a `conflicts` output: the `known_key`,
   what the message `stated`, and the `user_words`.
2. **Ground.** `memory_conflicts.record` keeps a conflict only when:
   - the key is a fact the model was actually shown;
   - the words appear verbatim in the user's message;
   - the fact didn't come from that same message.
   A kept conflict stops any rewording of that fact in the same job. That is the guard against silent overwrite.
3. **Store.** `memory_conflicts` stores:
   - the fact and the **revision it contradicted**;
   - the fact's wording at that time;
   - the message and conversation it came from.
   The owner is enforced by the database, through a composite foreign key to `semantic_memories(id, user_id)`.
4. **Show.** Recall's `annotate` marks the fact on every read path (search, read, expansion, legacy block):
   *"unresolved: on 2026-10-29 they said "my sister in Lisbon", which doesn't match this, and it isn't settled
   which holds"*. The fact stays `current`, but no reader sees it as settled.
5. **Route to cognition (route B).** The conversation mind's sweep (`mind_conversation.conflict_problems`) offers
   each conflict once (`offered_at`), 20 s after it was recorded so her reply goes first. It becomes a `conflict`
   hard-thought problem in the conversation it came from. The hard thought's question or conclusion goes to
   `autonomous_speech`, where Student 2 decides on it as a `thought_result`. If the thought loses its place in
   attention or concludes nothing, the conflict stays open and recall keeps showing it.
6. **Settle.**
   - The next consolidation sees `[unresolved: ...]` after the fact. A message that settles it lists the key in
     `settled`, which gives `known_confirmed`.
   - Any revision of the fact, by consolidation or by `memory_corrections`, gives `fact_changed`.
   - A forget gives `forgotten`.
   - Conflicts never expire with time. A thought about a settled conflict is dropped ("it's been settled").

## Real-model check (gpt-6-luna extractor, 5 runs per case, 2026-10-29)

The prompt's examples (an uncle in Denver vs Seattle, a parrot named Kiwi vs Mango, Priya and olives) are
deliberately unrelated to the cases tested. Cases starting with "f:" are fresh ones written after the prompt.

| Case | Expected | Result |
|---|---|---|
| sister "in Lisbon" after Porto | conflict | 4/5 |
| dog called Biscuit after Pretzel | conflict | 5/5 |
| Leo "loved the mushroom risotto" | conflict | 5/5 |
| f: "my wife Anna" (partner is Sam) | conflict | 5/5 |
| f: "my 9am standup" (it's 10am) | conflict | 5/5 |
| f: "best steak of my life" (vegetarian) | conflict | 5/5 |
| f: "my brother's flat in Oslo" (lives in Bergen) | conflict | 2/5 (twice stored as a separate fact, "has a flat in Oslo") |
| explicit move / f: "standup moved to 9am now" | update, no conflict | 5/5 each |
| trip to Lisbon / f: brother flying to Oslo | nothing | 5/5 each |
| settling message | `settled`, fact untouched | 5/5 |
| nonsense / f: unrelated question | nothing | 5/5 each |

In no run was a fact silently overwritten. Detection is weakest for places, where a visit is a plausible reading.

Before the "don't explain it away" rule, the place cases scored 0/5. A version with examples copied from the test
cases scored 5/5 on those cases, and was not kept. To rerun: `uv run python scripts/memory_conflict_probe.py 5`
(real model calls).

## Known limits

- **No reaction in the same reply.** The conflict is found after the reply, by background consolidation. Seeing
  it before the reply is stage 4, where recall gets relevant evidence ahead of the response decision.
- **Only recent facts can conflict.** The extractor sees the 60 most recently confirmed facts (`KNOWN_SHOWN`).
- **One conflict per fact per message.** A disagreement with an episode or a working item isn't detected; only
  facts are.
- **Settling can lose the explanation.** A "settled" with no change loses the explanation (for example "was
  visiting"), unless the extractor also stores it as an episode.

## Proposed for qwen-6: a `memory_notes` field in the Student 2 snapshot (spec only, for the cognition session)

For now this is route B only. Student 2 sees conflicts only as a thought result. Once stage 4 supplies conflicts
before the reply, the snapshot needs its own input.

```text
memory_notes: up to 3, or explicit "none"
  - memory:  what memory holds, with its date ("Nora moved to Porto, since 2026-09")
  - message: what this message says, as the user's words ("my sister in Lisbon")
  - status:  "conflict" (unresolved) | "changed" (memory already accepted a stated change)
```

- **Existing rows stay valid** and read as `none`, which is the input contract's explicit absence. Most new rows
  should also be `none`.
- **New families come in contrast pairs, changing one thing each.** In each pair the base doesn't force a choice;
  only the twin checks a direction:
  - a conflict that matters now vs one that's irrelevant;
  - an explicit change (no asking expected) vs an ambiguous slip;
  - an innocent explanation in view vs none;
  - a garbled message (`check_meaning`) vs the same message said clearly.
- **No new output labels.** `ask` (`check_meaning`, `push_back`), `silent`, `defer` (`user_raises_it`) and
  `request_thinking` already cover it.
