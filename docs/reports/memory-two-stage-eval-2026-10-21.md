# Two-stage memory evaluation — October 21, 2026

This evaluation separates retrieval/reconstruction from answering. It uses the stored Lantern test case for Stage A, and an independent minimal synthetic copy of its meeting-time evidence for Stage B. No chat or memory records were created or changed. The new script is an evaluation harness; it does not change production recall or prompts.

## Stage A: recall without an answer model

Question: “What were Lantern's meeting times in the order I told you, and what is the current confirmed time and venue?” The question does not supply the expected times.

| Check | Result |
|---|---|
| Existing historical search capped at six records | Misses 4 pm; contains 5 and 6 pm |
| Existing historical search capped at twelve records | Returns evidence covering 4, 5 and 6 pm |
| Bounded traversal of existing evidence links from retrieved records | Reconstructs 4 → 5 → 6 |
| Current-state consistency | Active stored fact says 6 pm, matching the last transition |

The six-result control is an explicit historical search with a six-record cap, not a complete replay of `for_reply` with its separate working view and prior references. The twelve-result service returns a ranked evidence list, not a structured chronology; evidence coverage alone is not credited as chronological reconstruction.

The reconstruction check starts from returned memory references, follows existing event/item/source relationships within the fixture owner, then orders explicit user update statements by their recorded source order. A case-specific parser reads “from 4 pm to 5 pm” and “from 5 pm to 6 pm,” verifies the transitions connect, and compares the last value to a returned active fact. It visits 21 nodes, with a six-edge depth bound and 128-node maximum. It does not seed the traversal with the expected old-record IDs or call an answer model.

Grounded transitions:

- Source `8b722942-6252-402a-9d9d-edca4e4e98d7`: “from 4 pm to 5 pm.”
- Source `929b0098-d2cd-4466-b4e3-f98fd2586ae2`: “from 5 pm to 6 pm.”
- Current fact `f982b188-3a3c-43e3-9ebb-fbc1d6718212`: Lantern's Saturday meeting is at 6 pm in the library courtyard.

**Interpretation:** the stored evidence and existing links support reconstruction. The harness demonstrates that path. The traversal/parser is evaluation-only, not shipped functionality; the normal six-result path has not been fixed by this evaluation.

## Stage B: answering with fixed evidence, independent of retrieval

Used the production `complete_chat` entrypoint and system-prompt assembly with the configured `gpt-6-luna` model. Each call had:

- One user question, with no earlier conversation history.
- A fixed recall block, with no working-memory view or other memory blocks.
- No memory read/write tools, integrations, game or screen tools.
- No Stage A result supplied to it.

The evidence was reduced to a synthetic club name, test courtyard, two time updates and a current fact. Original conversation details, preferences, user identifiers and timestamps were not sent in this model-stage bundle. This is a controlled reproduction of the relevant evidence structure, not an exact reproduction of the original larger prompt.

Two evidence formats were tested three times each. In the second run of each format, the list was reversed while source-order metadata remained attached:

| Fixed evidence | Chronological list, run 1 | Reversed list, run 2 | Chronological list, run 3 |
|---|---|---|---|
| Prose lifecycle events, including “Goal completed” and a current fact | Pass | Pass | Pass |
| Explicit source statements and a current fact | Pass | Pass | Pass |

All six saved answers were reviewed for actual meaning, not just mentions of the numbers. Every answer preserved **4 → 5 → 6**, identified **6 pm as current**, and retained **Saturday / test courtyard**. No answer said earlier history was unavailable, reversed the transition sequence, or treated 4 or 5 pm as current.

Representative event-format answer:

> You first told me 4 pm, then updated it to 5 pm, and later changed it to 6 pm. The current confirmed meeting is Saturdays at 6 pm in the test courtyard.

The raw-event condition contains only the two relevant lifecycle passages and current fact, not the many unrelated records in the failing frontend run. Passing this condition therefore does not prove the original answer failure has been eliminated. It shows the answer path can correctly use sufficient minimal evidence, including the previously troublesome lifecycle-event format.

## What this establishes

1. The recall-side failure remains visible under a six-result cap.
2. The same stored data can support a source-grounded sequence through existing relationships.
3. The answer path passes this independent minimal-evidence check in six runs.
4. The original failed follow-up remains a separate regression case: it received 4 pm evidence amid a larger supplied set and still omitted it. The next answer-side expansion should restore those distractors and surrounding prompt conditions while holding retrieval fixed.

No general accuracy percentage, controlled graph benefit, or completed production fix is claimed. Three runs per condition are a small exploratory sample.

## Reuse

Script: `backend/scripts/memory_two_stage_eval.py`.

Run with `--stage recall` for database retrieval/reconstruction only, or `--stage answer` for frozen evidence and real-model answering without a database lookup. `--stage all` runs both, but Stage B never consumes Stage A output. The script requires an explicit fixture owner, checks fixture ownership and refuses to overwrite an existing output file.

Required inputs for this case:

- `--user-id 0eb0b4e3-0ecb-4f43-90e6-4266fa6df7e9`
- `--fixture ../docs/reports/memory-retest-final.json` when invoked from backend
- `--output` pointing to a new result file
- `--repeats 3` for six answer calls across two conditions

Set `MEMORY_MANAGER_ENABLED=true MEMORY_CORRECTIONS_ENABLED=true RECALL_ENABLED=true` for the recall run. The backend's configured provider credentials are used for query embeddings and the answer model. Credentials are not printed or written into evaluation results.

## Artifacts

- `memory-two-stage-recall-2026-10-21.json`: separate recall-stage results and six-result control.
- `memory-two-stage-eval-2026-10-21.json`: first recall run plus six complete answer calls, exact fixed evidence, assembled prompts, prompt hashes, model name and reviewed semantic verdicts.
- Previous diagnosis: `memory-failure-diagnosis-2026-10-21.md`.
