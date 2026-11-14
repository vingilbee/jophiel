# Mind architecture

One mind owns what she does: speaking, asking, acknowledging, partial answers, silence, further thinking, recall,
starting, changing or stopping work, activity choices, responses to proposals from other parts of her, conversation
continuity (what to note, what stays open, what she promised, what she means to do) and when to come back to
something. There is no separate interpretation stage (Student 1), decision model (Student 2), appraisal or mood
system: they were removed on 2026-11-13 (handoff: [COGNITION_TO_MIND_HANDOFF.md](COGNITION_TO_MIND_HANDOFF.md)).

## Wakes and evaluations

Every wake is one evaluation of the same mind: the agent loop (`app/ai/chat.py`) with her prompt and tools, run by
the chat worker (`workers/chat.py`; the WebSocket's in-process path for local runs).

| Wake | Where it comes from | What she reads about it |
| --- | --- | --- |
| A user message | the chat socket (text, voice, WhatsApp) | the message |
| `work_result` | thinking she started finished, failed or expired (`coordinator._conclude`) | its conclusion, or that it reached none |
| `thought` | a thought of her own from her background mind (`mind_hard.route`, quick thoughts, game corrections) | the thought, or the question she'd ask |
| `proposal` | learning's bounded game trial (`learning_trials.present_once`) | the options; she decides with `decide_proposal` |
| `conflict` | memory: something the user said disagrees with a fact (`mind_conversation.SOURCE.sweep`) | both versions; nothing settled |
| `finding` | her desktop's security records (`mind_security`) | the finding |
| `due` | a reconsideration she scheduled with `come_back_to` | what she wanted to come back to, and whether it came due late |

A wake that isn't a message is durable `coordinator_work` (kinds `mind` and `wake`), turned into one chat job
`uuid5(request, "follow_up")` in its own conversation, behind any reply in progress (`coordinator._dispatch_follow_up`).
The output row (`coordinator_outputs`, phase `follow_up`) records what she did: `said`, `silent` or `no_words`, and
what was delivered. A scheduler tick only checks due times and leases; the model is called only when something is
due or arrived.

### What each evaluation is given (`services/mind_turn.py`)

- Her text personality (`services/personality.current()`: one cached snapshot per process).
- The conversation (the chat history), her working memory's view (goals, promises, open questions, intentions),
  and what is under way: thinking she started and reconsiderations she scheduled (`Evaluation.state`).
- Memory's check for this moment (`recall_references.for_reply`: the answer before's references, standing
  constraints, a search for the message or for what the wake is about). A failed search is said as failed and
  falls back to re-reading the last answer's references; it is never shown as nothing found.
- The lessons memory holds for this user (`memory_lessons.for_decision`, tentative ones labelled as guesses) and
  how to weigh them.
- Her senses and capabilities as before: awareness, game summary, desktop, security, screen, specialists.

The database is the record between wakes; nothing lives only in process memory or an ever-growing LLM conversation.

### What she can do in it (her tools)

- **Recall** (`search_memories`, `read_memory`, `read_timeline`, `what_i_know`, `recall_history`): results come back
  into the same tool loop; no stage sits between recall and her.
- **Silence** (`stay_silent`): no message is stored; a user turn ends with status `silent` ("She chose not to
  reply."), a wake is recorded as `silent`. Writing nothing on a wake is also silence.
- **Background thinking** (`think_it_over`, `stop_thinking`): coordinator work (a `recall` first when she asks to look
  memory up) that runs without holding the evaluation open and wakes her when it ends. A new user message makes
  unfinished thinking moot (`supersede_older`).
- **Continuity** (`note_question`, `note_commitment`, `note_intention`, `update_item`, `set_goal`, `switch_context`):
  working memory persists it. A promise of hers she keeps in a wake (with no user message) can be closed there.
- **Coming back** (`come_back_to`, `drop_come_back`): a `wake` row due at the time she chose (at most 60 days ahead),
  optionally tied to a working item: closing the item drops it, scheduling again for it moves it. It survives
  restarts, isn't superseded by new messages, and a wake that comes due while workers were down runs late, marked
  as late.
- **Proposals** (`decide_proposal`: accept an option, decline, or defer by time or until the user writes) and a live
  experiment (`experiment`: pause, resume, stop). A wake that ends without a decision is `undecided`: the proposal is
  presented again, and after four tries put off with that reason.
- Game, desktop, security and specialist tools as before.

### Execution checks (not decisions)

Ownership and leases, the cancellation generation, deadlines, evidence versions, and before anything a wake produced
is stored, under the conversation's lock: no newer user message (`mind_turn.moved_on`), the premise still open (a
conflict, an item and its revision, a security investigation), nothing forgotten since, and the same thing not
already said in this quiet stretch (`autonomous_deliveries`). Duplicate events are one row (idempotency keys); a
second dispatch finds the output row and stops. The user's game commentary setting (`agent_say.allowed`) is their
rule and stays enforced for words she already chose in a game turn; how recently she or the user spoke is given to her
as a fact (`autonomous_speech.facts`), never a timer that decides for her.

## Her background mind

`services/mind.py` (quick thoughts in a live game), `services/mind_hard.py` (hard thoughts on problems from a source:
Minecraft, her desktop's security records) and her planner are unchanged in role. Their conclusions reach memory as
observations, tips reach her hands, and anything to say or ask becomes a `thought` wake: whether it is said is decided
in that evaluation. Quiet time in a live game starts a thought whose topic is hers to choose (no canned topics). A
conversation is not a background source: messages wake her directly, and coming back to a conversation is
`come_back_to`.

## Records

- `mind_turns.jsonl` (`MIND_TURN_LOG_PATH`): one line per evaluation and the coordinator's lifecycle lines: ids,
  reasons, memory status, tools used, what she did, delivery, timings. No message or memory text.
- Learning capture: learner `mind` (what she read, the tools and their results, her reply or silence) and the
  delivery record; assessments attribute causes to `mind`, `delivery`, `timing`, `hands`, `planner`, memory, the world
  or the user's side.
- The System page (owner only) shows Mind at the centre: evaluations, what she keeps, hard and quick thoughts, the
  planner and look backs; the chat page's Mind panel shows what she keeps open and what she'll come back to.

## Personality

`core_memory.profile` is one prose Text field; name, version, schema version and active status are metadata.
`services/personality.py` loads exactly one active profile once per process under a lock; a restart reloads it.
Adaptation and hot reload are outside the current scope.

## Migrations

- `a6d2f8c4e9b1`: text personality (seeds the prose character, drops the numeric groups; downgrade recreates empty
  groups).
- `b7e2c9f4a1d6`: moves open cognition notes into working memory (their open questions and hers, her promises; her
  offers as intentions), then drops `working_memories` and the training-example tables, deletes Student 1/2 attempts,
  `effect` executions, their lesson and assessment uses and `cognition` coordinator work, and updates the learner,
  execution-kind and work-kind constraints. Downgrade recreates the empty tables and old constraints, deleting her
  mind's own records first; removed rows are not restored.

Deployment: stop the workers and the API, `uv run alembic upgrade head`, start both (the personality snapshot loads
on first use). Nothing serves a student model any more: stop any `student_model.py serve` process.

## Continuity follow-up

See [MIND_CONTINUITY_FOLLOWUP_HANDOFF.md](MIND_CONTINUITY_FOLLOWUP_HANDOFF.md) for the November 14 agreed wake-context, time-awareness, history-boundary, and E2E follow-up work. These follow-up changes are specified, not yet implemented by this handoff.
