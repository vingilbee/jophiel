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
- **Now** (`Evaluation.situation`, read when the evaluation runs, never from a queued job's snapshot): the clock in
  the user's IANA zone (UTC said as UTC when it isn't known), her previous evaluation in the conversation and what
  came of it, their latest message, and the event: the CURRENT message and when it arrived, or for a wake its id,
  time and cause. A due wake adds, from records only: who asked for it (`asked_by`), the deadline she chose against
  the time she set it and now (lateness), their original message, urgency only as they stated it, the promise it is
  tied to and its status, earlier postponements with their reasons, earlier attempts and whether anything was
  delivered, and what changed since it was first set (messages, evaluations); what isn't recorded is said unknown.
  The awareness snapshot loses its clock line (and, on a wake, is labelled as reported earlier).
  THEIR PHONE: what the phone last reported about itself (`phone_state.describe`), always with how long ago, and
  marked as possibly no longer true after 30 min; left out when the phone never reported.
- The conversation: one turn per stored message, never merged, each preceded by an app record of when it was said
  (`chat.get_history(records=True)`; the one being answered is marked CURRENT). An evaluation that left no message
  (silence, nothing written, cancelled or failed) appears in place as an app record (`mind_evaluations`), never as
  words she said. App records are system-role messages, so user text can't forge them.
- Her working memory's view (goals, promises, open questions, intentions),
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
- **Coming back** (`come_back_to`, `drop_come_back`): a `wake` row due at the time she chose (at most 60 days ahead):
  `in_seconds`, or `at` as an exact local time (resolved with the user's zone; ambiguous or nonexistent local times,
  an unknown zone for a local time, and past times are refused; `app/ai/mind/when.py`); the tool returns the exact
  time kept. `asked_by`: `user` (a check-in they asked for: kept as her promise in working memory unless she names
  its item) or `me` (optional). Optionally tied to a working item: closing the item drops it. One intention keeps one
  identity (`chain`) across postponements: `moves` (with a reason) revises a pending wake or the one that just woke
  her, carrying its purpose, original request, item and who asked; the chain never has two live schedules. Dropping
  keeps her reason, also on the wake that woke her. It survives restarts, isn't superseded by new messages, and a
  wake that comes due while workers were down runs late, marked as late.
- **Outcomes, not intentions:** every evaluation leaves a `mind_evaluations` row (said / partial / silent /
  no_words / cancelled / failed, and the delivery). A promise she marks fulfilled counts only when her words are
  delivered: otherwise it opens again with the delivery's status. Due wakes that weren't carried through (and
  weren't moved or let go) stay in her state for a day, with what actually happened.
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

### Her phone (2026-11-21)

The user's phone is a polled source (`services/mind_phone.py`) over one state (`services/phone_state.py`: Redis,
latest value per field with when it began and when it was last confirmed; never a history). The phone reports
screen, lock, battery, charging, headphones, network, Do Not Disturb, ringer, motion and a named place in its `sense`
frames while the app is open and over `PUT /api/phone/state` (bearer, phone sessions only) when it isn't; whether
the app is open or a call is live comes from presence. Both readers use the same words: the source's ticks and the
"Now" of every evaluation.

- One activity per user (`uuid5(user, "jophiel:phone")`), ticked as soon as a report changes something (the report
  pushes the mind worker's wakeup) and on a heartbeat (`PHONE_TICK_SECONDS`, 60 s) for what time alone moves.
- News is what a person would notice: the app opened or closed, a call, unlocked, plugged in or unplugged, the
  battery entering a lower band (not a percent moving), headphones, network, Do Not Disturb, moving, arriving or
  leaving a named place, the part of the day, a longer quiet stretch (1 / 3 / 6 / 12 h). A first tick (a restart, a
  phone seen again) is the baseline, not news.
- A tick with nothing new doesn't reach the gate (`gate_needs_news`: no model call), and the phone never starts
  quiet thoughts (`thinks_when_quiet` is False): a phone sits unchanged for hours. Pressure is always calm and there
  are no hard problems of its own; a react becomes light (no body).
- A thought's line wakes her mind in the conversation the phone app is in (else their latest), with the conversation
  fences (`autonomous_speech.CONVERSATIONAL`) and `PHONE_SAY_TTL_SECONDS` (120 s) to be said; a thought whose moment
  the phone has left (plugged in again) is kept as stale and not said.
- Not yet: the phone app sending these fields (step 1 of the phone side) and reporting from the background (a native
  service); transitions recorded as observations for memory.

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

The 2026-11-14 continuity follow-up is implemented as described
above (Now, history boundaries, the intention's lifecycle, scheduling), with migration `c8e4a1f7b3d2`
(`mind_evaluations`; `learning_assessments.awaiting_notes`: explanations of missing evidence moved out of
`awaiting`, which holds only `attempt:<uuid>` / `execution:<uuid>`, each resolved against its own store).
