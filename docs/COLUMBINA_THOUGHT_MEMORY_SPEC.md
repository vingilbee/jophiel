# Her thoughts reaching memory (spec, 2026-10-28)

Status: **built 2026-10-28** (migration `b3e7d1f5a9c4`, `services/thought_memory.py`,
`tests/test_thought_memory.py`); not yet run against a real model. Next to `COLUMBINA_MEMORY_SESSION2_SPEC.md` (observation) and
`COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md` (recall).

The rule: her mind produces thoughts, and memory decides what deserves to last and looks after it. Storing
"I thought X" never turns into "X is true".

## 1. What is wrong today

| Thought | Where it goes now | Problem |
|---|---|---|
| Every thought | `mind_thoughts`, deleted after `MIND_THOUGHT_RETENTION_DAYS` (7) | Fine as a log |
| Hard thought, kind `intention` | Written straight into working memory (`memory_manager.intention_from_thought`) | Mind writes memory itself; the evidence link cascades away when the thought is pruned |
| `decision`, `guess` | A tip for her hands, a line offered to Student 2 | Gone after 7 days; she forgets what she decided or suspected |
| `question` | Offered to Student 2 to ask | Never an open memory question |
| Recall | Never sees her own reasoning | She can't say "I suspected..." / "I decided..." |

## 2. Decisions (the user's, 2026-10-28)

1. `mind_thoughts` stays: a separate log for the panel, Student 2, and her next hard thought on a topic (short-term
   continuity). It is never merged into long-term memory.
2. Only **hard** thoughts are handed to memory, and only when the thinking reached a conclusion (status `done`).
   "Finished" means the thinking, not the goal: what she means to do stays an open memory until it's done,
   dropped or replaced. Thoughts that were cut off (`unfinished`) pass to her next thought, and that one is handed over.
   Dropped thoughts (what they were about no longer holds) and stale ones (the world moved while she thought; she
   thinks again from the new state) are not handed over.
3. Code maps the conclusion's kind to a memory kind. A small judgement decides keep or skip, and whether the
   thought repeats or replaces an open memory. There is no free extraction: the remembered text is her conclusion
   as she wrote it.
4. New working-item kinds `hypothesis` and `decision`. No lessons (games keep `game_lessons`); feelings are skipped
   (the inner state covers them).
5. A thought that memory points to is never pruned.

## 3. The handoff

When `mind_hard.run` saves a hard thought with status `done`, it records one observation **in the same
transaction** as the thought (`observation.record`, never commits on its own). Memory processes it later, on its
own; a memory failure never stops thinking, and a retried worker never creates duplicates (the observation key is
unique).

- **Source** `mind`, **instance** `<scope.source>:<activity>` (for example `conversation:<conv id>`,
  `minecraft:<session id>`), **key** `thought:<thought id>:v1`. A revision is a new thought with its own id; the
  version number leaves room for in-place revisions later.
- **Kind** `thought_concluded` v1, actor `columbina`, epistemic **`inferred`** (a new value in
  `OBSERVATION_EPISTEMIC`: her own reasoning, neither observed nor reported), content origin `model`, salience
  `notable`.
- **Context**: set at handoff, as `intention_from_thought` decides it today: a game thought takes its session's
  world context, a conversation thought the focused context when it was thought. It is never rebound at processing time.
- **New column** `observations.thought_id` → `mind_thoughts.id` (ON DELETE CASCADE, the same as the other source
  links).
- **Payload** (the full record; nothing is cut off before memory judges it):

| Field | |
|---|---|
| `thought_id` | |
| `conclusion_kind` | decision / guess / question / intention / feeling |
| `conclusion` | Her conclusion, whole |
| `question` | When she wants to ask the user |
| `reason` | Her private `thinking`, whole (the user's choice: never cut off) |
| `trigger`, `topic` | What started it, and its base topic (`mind.base_topic`) |
| `status` | `completed` (v1 only hands these over) |
| `basis` | What she read: `{"messages": [ids], "memories": ["event:42", "item:…", "fact:…"], "observations": [ids]}` |
| `supersedes` | The thought it carried over from, when it continued an unfinished one |

`basis` is new. Sources fill `HardInput.basis` while gathering: the message ids of the conversation lines shown,
and the references of the recall records supplied (`recall_references.for_thought` returns them along with the
text).

The direct `memory_manager.intention_from_thought` call is removed. Her hands still get an intention as a tip
(Redis, unchanged). Quick thoughts and the planner's `plan` thoughts are not handed over.

## 4. The memory side: consumer `thought_memory`

A second observation consumer, beside `memory`. A kind lists its consumers (`KindSpec.consumers`, default
`("memory",)`), so `thought_concluded` gets a `thought_memory` receipt only. It runs on the memory worker thread
outside the coordinator's tick budget, because it can make a model call. It has the same receipt lifecycle
(pending / retry / applied / skipped / failed, backoff, `failed` after 4 tries).

**Step 1: code maps the kind.**

| Conclusion | Memory | Default |
|---|---|---|
| `intention` | intention (`active`) | Judged |
| `question` | question (`open`), owner `columbina` | Judged |
| `guess` | **hypothesis** (`open`) | Judged |
| `decision` | **decision** (`active`) | Judged |
| `feeling` | none | Skipped, "feelings are the inner state's" |

**Step 2: the judgement** (one small structured call, `MEMORY_THOUGHT_MODEL`, else `SUBAGENT_MODEL`). It sees the
payload, the context's label, and that context's open items of the same kind (up to 12, by id). It answers:

- `keep`: true/false, plus `why`. Skip what only mattered for that moment, such as a step-by-step game move.
- `relation`: `new`, `same_as:<item id>` (she reached it again), or `replaces:<item id>` (a decision or intention
  that changes an earlier one).

Code checks every id against the shown list, and an unknown id is treated as `new`. If the call fails, the receipt
goes to retry; a skip is never assumed.

**Step 3: memory-owned operations apply it** (`memory_manager`, under the user's advisory lock):

- `new`: `add_item(kind, text=conclusion, owner="columbina", epistemic="inferred", reason=...)`, plus evidence:
  the thought as `origin`, the observation as `origin`, and each `basis` message or observation as **`basis`**, a
  new evidence role meaning "what she read when she thought it". It never counts as support. Her reasoning is kept
  whole in the new `working_items.reason` column; trigger, topic and the memory references she read stay in the
  observation's payload (memories can't be evidence sources).
- `same_as`: no new item. The thought is linked as `restated` (another new role). **A restatement is never
  support**: only `support` evidence (a message or observation) counts toward a hypothesis being true.
- `replaces`: the old item closes (`superseded` for a decision, `dropped` for an intention), with
  `resolution` evidence = the thought; the new item is added as in `new`.
- A skip records its reason on the receipt and nothing in memory.

## 5. Lifecycles

| Kind | Open | Closed | Who closes it in v1 |
|---|---|---|---|
| hypothesis | `open` | `supported`, `disproved`, `unresolved` | The mouth (`update_item`, with the user's message as resolution evidence); the due check makes one `unresolved` after `MEMORY_HYPOTHESIS_DAYS` (14) untouched |
| decision | `active` | `superseded`, `withdrawn` | A later decision on the same thing (`replaces`); the mouth |
| intention | `active` | `done`, `dropped`, `lapsed` (unchanged) | As today, plus `replaces` |
| question | `open` | `answered`, `dropped` (unchanged) | As today |

Closing never rewrites: the old wording, status history and evidence stay, so "what she believed then" is still
readable. Corrections (`memory_corrections`) treat the two new kinds like the others. Automatic support or
disproof from later evidence is **not in v1**.

## 6. Recall says where a memory came from

`recall.render_records` puts an origin label in front of each record (`recall.contracts.origin_label`), and an
item's reason as "Why you thought so: ...":

| Record | Label |
|---|---|
| Fact or event the user stated | "The user told you:" |
| Observed event | "You observed:" / "Minecraft reported:" (unchanged) |
| hypothesis `open` | "You suspected (not confirmed):" |
| hypothesis `supported` / `disproved` / `unresolved` | "You suspected, later confirmed:" / "…, later shown wrong:" / "…, never settled:" |
| decision `active` / `superseded` | "You decided:" / "You had decided (since changed):" |
| intention / question from her | "You meant to:" / "You wondered:" |

The working-memory view (`## What we're doing`) uses the same words. Hard thoughts and replies read these through
recall as they read any memory.

## 7. Pruning

`mind.prune` skips a thought that `memory_evidence.thought_id` or `recall_reconstruction_sources.thought_id`
points to. Observations of a pruned thought cascade; only an observation still unprocessed after the retention
window could be lost, and that is logged.

## 8. Migration (one, reversible)

- `WORKING_ITEM_KINDS` + `hypothesis`, `decision`; statuses as in §5 (rebuild `ck_working_items_kind` /
  `_status` / `_closed`); `working_items.reason` (Text, null).
- `MEMORY_EVIDENCE_ROLES` + `basis`, `restated`.
- `OBSERVATION_EPISTEMIC` + `inferred`.
- `observations.thought_id` (FK, CASCADE, partial index), with the identity check updated.
- Downgrade: refused while rows use the new kinds, roles or values (the same pattern as earlier memory
  migrations); otherwise exact.

## 9. Tests (real Postgres, scripted judgement, no model calls)

1. A done hard thought records one observation in the thought's transaction; a dropped, stale or unfinished one
   records none; a quick thought records none.
2. Replaying the same handoff, or retrying the worker, makes no duplicate observation or item.
3. guess → open hypothesis, epistemic `inferred`, with origin and basis evidence; recall renders "You suspected
   (not confirmed):"; it never appears as a fact.
4. The same guess again → `same_as`, one item, `restated` evidence, still no support.
5. A new decision `replaces` → the old one is `superseded` with its history kept; recall labels both.
6. intention → active intention in the game's world context (game) or the focused context (chat). The direct path
   is gone.
7. feeling → skipped with its reason; the judgement says skip → receipt `skipped`, no memory.
8. The judgement fails → retry, then `failed` after 4; an unknown id from the judgement → `new`.
9. A memory failure never fails the thought (the thought and its observation still commit).
10. Prune keeps cited thoughts and deletes uncited ones.
11. The migration goes up, down and up on a throwaway DB; downgrade is refused when a hypothesis exists.

## 10. Not in v1

Lessons from thoughts; automatic support or disproof of a hypothesis from later evidence; in-place revisions of a
thought; handing over quick thoughts; changes to her character or training.
