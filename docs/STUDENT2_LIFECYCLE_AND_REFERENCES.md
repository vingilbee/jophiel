# Student 2 — lifecycle semantics and reference model (draft for review)

Status: **agreed with amendments 2026-10-23; generator implemented** (`backend/app/ai/Cognitive/simple/events.py`, event pilot 3). The runtime (request store, assessor, coordinator transitions) is not built. Written after event pilot 2
(`docs/qa/student2-event-pilot-2026-10-23/RESULTS_PILOT2.md`). Pilot 2's main failures came from an incomplete model
of goals and execution, not from missing action labels:
- a finished goal could only be "cancelled";
- pausing the activity versus pausing the goal had to be guessed;
- Gmail was given a fictional session;
- older requests could not be referenced;
- the situation maker produced contradictory snapshots.

This document fixes the model first. The generator and the decision schema follow from it.

It extends the brief (`docs/STUDENT2_COGNITION_REVAMP_IMPLEMENTATION.md`, "Work and goals" and "Runtime semantics").
Where they differ, this draft says so explicitly (§7).

---

## 1. The things a decision can be about

Each kind has its own lifecycle. They are separate dimensions: a goal can be open while its execution is paused.

| Kind | Ref | What it is | Example (Minecraft) | Example (email) |
|---|---|---|---|---|
| New message | `[M]` | The user's message that triggered this event, not yet processed | "bring me 16 sand" | "send an email to Alex" |
| Request | `[R*]` | Something the user asked for earlier, recorded and still relevant | "can you make a chest at some point?" (10 min ago) | "remind me to answer Mara" |
| Goal | `[G*]` | A recorded commitment someone owns and that can be achieved or given up | build a shelter | send Alex the agreed email |
| Activity | `[A1]` | The ongoing execution context: where her actions happen | playing in this world | none |
| Work | `[W*]` | One concrete job under a goal, request or thought | gather wood; plan the shelter | draft the email; send it |
| Proposal | `[P*]` | A suggested goal from her mind, planner or curriculum, not accepted | explore beyond the mountain | — |
| Thought | `[T*]` | Her private thought: the current one (`T1`) and earlier ones on the subject (`T2…`) | "the cave might flood" | "Mara sounded worried" |
| Assessment | `[E*]` | Evidence about a goal: a verified outcome or a grounded judgment (§3) | "executor: 16 sand delivered" | "user: 'perfect, thanks'" |

Refs are positions in this snapshot, assigned by code. They are never stored ids, and the model never invents them.

**Not every intention is a goal, and the line is specificity and persistence**, not conversation versus action.
- "Understand them better" or "ease the tension" are usually motivations. They guide this decision through
  `intention`, and they have no record and no completion event.
- "Find out which activities we both enjoy" is specific and lasts over days. It can become a goal if she pursues it.
- Only `pursue` creates a goal.

---

## 2. Lifecycles

### Requests `[R*]`

`open` → `taken_up` (linked to a goal) | `declined` | `withdrawn` (the user took it back) | `superseded` (a later
request replaced it).

- Only open requests are shown as candidates.
- A request taken up is shown through its goal, not twice.
- At runtime, something has to record requests from messages. Today nothing does (open question Q1). In the
  generator they are part of the world state.

### Goals `[G*]`

A goal has two independent fields:

- **status:** `open` | `achieved` | `given_up`. `given_up` happens only when she or the user gives it up. A missing
  capability never closes a goal by itself: the goal stays open and `waiting` (the capability may come back).
- **pursuit** (only while open), which says why it isn't moving:
  - `active`: being worked on;
  - `on_hold`: she or the user *chose* to suspend it (stuck, distracted, reconsidering). No replacement goal is needed;
  - `waiting`: progress *depends on* something: an answer, a running job, a capability coming back.
  - When its activity is paused, an active goal is shown as active with "execution paused with the activity". Its own
    pursuit does not change.

Each goal also has:
- **source:** the request `[R*]`, proposal `[P*]` or her own choice it came from;
- **constraints:** user instructions about how it is done, e.g. "use stone";
- **completion basis:**
  - *objective:* an executor or tool can verify it (16 sand delivered, email sent);
  - *subjective:* it needs interpretation (explain X clearly).

Goals are never deleted or rewritten. A changed constraint is a new constraint with its source.

### Activity `[A1]`

`running` | `paused` | `ended`. Pausing an activity **stops all execution in it**. The snapshot says so next to the control:
- running work under it is paused;
- its open goals stay open and keep their pursuit;
- resuming the activity lets execution continue.

Ending it ends its work. Goals left open are put on hold, never marked achieved. Domains without an execution
context (chat, email) have no activity, and code enforces that.

### Work `[W*]`

`queued` | `running` | `paused` | `waiting_for_user` (e.g. a draft awaiting approval) | `completed` | `failed` |
`cancelled`.
- Only finished work has an outcome.
- Work always has a parent: a goal, a request, the new message, or a thought.
- Which controls a work item supports comes from its adapter (an email draft: cancel only; a Minecraft skill run:
  pause/resume/cancel).

### Proposals `[P*]`

`open` → `adopted` (it becomes a goal) | `declined` | `deferred`. A deferred proposal comes back as a
`deferred_reconsideration` event with a review time and an expiry.

---

## 3. Completion: outcomes and assessments are not decisions

"The goal is completed" (a fact) and "decide the goal is completed" (a claim) are different. Student 2 never turns its
own intention into a claimed accomplishment.

| Goal | Completion basis | Where the evidence comes from | What changes the status |
|---|---|---|---|
| Bring 16 sand | objective | the executor's verified delivery | code, from the verified outcome |
| Send the agreed email | objective | the tool reports the send succeeded | code, from the tool result |
| Explain something clearly | subjective | an assessment grounded in the conversation (e.g. the user says it makes sense now) | her `resolve_goal(achieved)`, citing the assessment |
| Understand the user better | none | — | not a goal (§1) |

- **Verification must match that exact goal:** its target, its conditions and its current constraints. A completed
  work item is not a completed goal: collecting sand is not delivering it. In the generator, only a completed work
  item marked as verifying an objective goal achieves it (code derives the status), and the checker judges whether
  the outcome really meets the condition.
- **Objective:** a verified outcome sets `achieved`, and the snapshot shows it as a fact
  ("[G1] build a shelter — achieved, verified by the executor 20 s ago"). The event is `work_outcome`. She then
  decides what to do next: speak, pursue something else, or nothing. She is never asked to close it herself.
- **Subjective:** an assessment `[E*]` says the goal *seems* fulfilled, or not yet, or unclear. It is an
  interpretation, not unquestionable evidence. It keeps the supporting references (the user's messages, outcomes)
  separate from the assessor's reading of them.
  - The pilot's assessor reads only what would exist at runtime: the rendered snapshot, with no family brief and no
    reviewer note.
  - At runtime an assessment is produced when an outcome or a conversation change makes completion plausible, not on
    every turn. An existing interpretation stage may supply it. Cognition decides to accept it, continue, or ask. Code records and validates
  the transition; it does not decide the meaning.
- **Validator:** `resolve_goal(achieved)` must cite an assessment or verified outcome visible in the snapshot. There
  is no bare "complete" button.

---

## 4. Decision operations

One **primary** work operation per decision (plus the communication choice), each with an explicit target and,
where needed, a basis. Effects that belong to that operation are part of the same transition, so it never partly
succeeds: "pursue P2, putting conflicting G1 on hold" is one decision (`other_goal: G1`, `other_goal_handling:
put_on_hold`).
- If examples show genuine needs to act on several independent targets, a bounded set of operations is the next
  step, rather than forcing a wrong label. `does_not_fit` collects those cases.
- **Communication `defer`** carries a referenced item (`defer_item`), a reconsideration condition (`reconsider_when`)
  and an expiry (`defer_expiry`).
  - Silent means "I'm not saying this now". Defer means "keep this for another look when the situation changes".
  - A wake-up means reconsider, never deliver automatically.
  - Expiry stops keeping the item for reconsideration; it doesn't erase the thought.

| Operation | Target | Basis (`basis` field) | Effect |
|---|---|---|---|
| `no_change` | — | — | Nothing starts or stops; existing work keeps its state |
| `pursue` | `[M]`, an earlier message `[U*]`, an open request `[R*]` or `[P*]` | — | Takes it on as a goal. Execution versus planning is the adapter's choice; missing details come back as a new event |
| `resume` | `[A1]` paused, `[G*]` on hold, `[W*]` paused | — | Execution continues where it was |
| `pause` | `[A1]` running, `[G*]` active, `[W*]` running | — | Stops execution; nothing is resolved (see effects text) |
| `cancel` | `[W*]` or `[A1]` (ends it) | — | Ends execution; records are kept. **Not for goals** |
| `resolve_goal` | `[G*]` open | `achieved`: an `[E*]` of that goal (required). `given_up`: optional | Closes the goal |
| `revise_goal` | `[G*]` open | `[M]`, `[U*]` or `[R*]`, the instruction: **required** | Same goal, changed constraints ("use stone instead of wood") |
| `request_replanning` | `[G*]` open | `[W*]`, `[M]`, `[U*]`, `[T*]` or `[E*]`, why: **required** | Same goal and constraints, another way ("the route is blocked; find another") |
| `request_recall` / `request_thinking` / `recall_then_thinking` | `[M]`, `[R*]`, `[T*]`, `[W*]` | `think_about` text (required for thinking) | Background job; never two of the same kind running on one target |
| `decline_proposal` / `defer_proposal` | `[P*]` | — | Closes it, or keeps it for reconsideration |

The effects are rendered with each target's controls, so the teacher doesn't guess:

```
[A1] ACTIVITY: playing in this world — running.
     pause: stops all execution here; open goals stay open and continue when resumed. end: ends all work here.
[G1] GOAL: build a shelter — open, active; from [R1]; constraints: near the river; basis: objective.
     pause: puts it on hold (other goals may run). resolve: achieved needs evidence; or no longer wanted / not possible.
     revise: keep the goal, change how (cite the instruction).
[W1] execution — gather 20 oak logs: running (under G1). controls: pause, cancel.
```

**"Build the same shelter, but use stone"** is `revise_goal(G1, basis=[M])`: the goal stays and gains the constraint.
**"The route is blocked; find another route"** is `request_replanning(G1, basis=[W2])`: the goal and its constraints
stay the same. **"Actually, forget the shelter, let's get iron"** is `pursue([M])` with `other_goal: G1` and a handling
of `put_on_hold`, `give_up` or `keep_alongside` (the brief's `old_goal`, renamed). **An earlier open request** ("can you make a chest at some point?") is
`pursue([R1])`.

She may give up goals the user created. The validator never rejects a decision for social reasons, only for an
invalid target, an unsupported control, a contradiction with state (resuming something running), a missing required
basis, or a duplicate job.

---

## 5. Generating situations from coherent state

The model writes realistic content; code assembles everything structural.

**The maker writes** a world state with content and relative structure only:
- messages in order with gaps ("about 2 minutes after the previous");
- which user messages carry requests, and those requests' status;
- goals with their source, constraints, status and pursuit;
- the activity state; work with its parent, state and outcome; proposals; thoughts; assessments.

**Code derives:**
- refs;
- absolute "seconds ago";
- "their last message" and "her last message";
- goal and work ages, from their source message or a stated start;
- each target's controls and their effects text (from the domain adapter);
- removing the new message if it was repeated as the last conversation line.

**Deterministic invariants**, checked by code and recorded:

| Invariant | Example violation from pilot 2 |
|---|---|
| Message times strictly decrease; the new message is newest and not repeated | the new message duplicated as the last line |
| "Her last message" matches the conversation | "44 s ago" vs a message 2 min ago |
| Only finished work has an outcome; finished work has no controls | — |
| `achieved` has a verified outcome or assessment | "progressing" goal already complete |
| A paused activity has no running work | — |
| No activity in chat/email | the fictional "Gmail session" |
| A family's required facts are present (e.g. `return_to_paused` → paused activity with an open goal) | a practical request whose work already runs |

The LLM checker keeps only what code can't judge: does it read as the family, is it plausible, is a thought marked
"said" actually in the conversation?

**Twins are typed edits to the source state, then recomputed.** Each family's twist becomes an edit:
- `set` a field (who set the goal, the time of day, the activity state);
- `shift` a time;
- `add_message` / `replace_message`: a small LLM call writes only that message's text;
- `add_thought`.

Code applies the edit and recomputes everything that depends on it. The twin is never regenerated free-form, so "the
twin is identical" or "the timing doesn't match" can't happen. The edit itself is the record of what changed.

**Implemented as:** the world schema `WORLD_SCHEMA`, `assemble` (derives the structure), `invariants` (code
checks), `REQUIRES` per family, the assessor `event_assess.txt`, the checker `event_check.txt`, and typed edits in
`apply_edit` (only `event_twin_text.txt` writes text, and only that one message or thought).

**Acceptance.** A situation is accepted when the invariants and the checker pass (after at most one regeneration of a
base). Rejected situations are kept in `rejected/` with their reasons, for diagnosis. They are excluded from training
data and from headline behaviour scores. A twin pair counts only when both sides are accepted. The report shows
rejected counts separately, so a confused teacher is never mistaken for a contradictory world.

---

## 6. Personality patterns are questions, not defects

These are behaviour, so the next pilot should *test* them, not push them.

1. **Communication `defer` (0 in both pilots).** Proposal deferral happens (14 in pilot 2); only communication deferral
   is absent.
   - The brief gives it a distinct purpose: a durable reconsideration with a wake condition and expiry. That differs
     from silence plus a kept thought, which has no wake-up.
   - Whether she needs it is open (Q5).
   - Probe: something worth saying, but the user is busy for a known time ("in a meeting until 3").
2. **Thinking about a deferred personal question.** It is not wrong in itself. Paired probe: the missing piece could
   come from her own reasoning or memory, versus only the user can supply it. Then see whether she tells them apart.
3. **Going along while saying her view.** It is not automatically a failure to disagree. Probe four levels, same
   shape:
   - an opinion ("which ending is better");
   - a mild preference (the portal site);
   - a meaningful boundary (asked to pretend to be someone, or to deceive a third party by email);
   - a shared commitment the user changes on their own (a plan they agreed together).

---

## 7. Differences from the brief

- `continue_goal` is merged into `no_change` (agreed in round 2).
- `pursue` targets `[M]`, `[U*]`, `[R*]` or `[P*]`; existing goals are resumed, not pursued.
- `cancel` no longer applies to goals. `resolve_goal` (achieved, which needs an assessment, or given_up) closes them;
  an abandoned goal is never marked achieved, as the brief requires. A blocked goal stays open and waiting.
- `revise_goal` (changed constraints, citing the instruction) is added next to `request_replanning` (another way of
  doing the same goal, citing why). Both need a basis.
- `old_goal` → `other_goal` + `other_goal_handling` (`keep_alongside` | `put_on_hold` | `give_up`), part of the same transition.
- Goal pursuit (`on_hold`) is separate from activity execution (`paused`).

---

## 8. The smaller targeted pilot (after review)

About 14 families × 2 situations, all with typed twins. Headline scores count accepted situations only.

| Probe | Family |
|---|---|
| Objective completion arrives | goal achieved (verified), user around / user away |
| Subjective completion | explanation goal + assessment from the user's reply |
| No goal record | easing tension; curiosity about the user |
| Execution vs goal | user pauses the game; user returns to a paused activity; she sets one goal aside for another |
| References | an earlier open request; "same shelter, but stone" |
| Email | ambiguous "send to Alex"; draft waiting for approval |
| §6 probes | busy until a known time; personal question (reasoning vs only the user); four disagreement levels |

---

## Decisions after review (2026-10-23)

| Question | Decision |
|---|---|
| Q1: who records earlier requests? | A shared request store, kept by the conversation processing path; not Student 2 and not memory consolidation. The original message is recorded at once; interpretation links each actionable request (a message can carry several) to it; code owns identity, lifecycle and references. A request she accepted stays addressable until resolved. An earlier message not yet interpreted can still be targeted (`[U*]`). |
| Q2: can verified completion update the goal? | Yes, when the outcome satisfies that exact goal's condition and current revision. A completed work item is not automatically a completed goal. |
| Q3: can she pause a goal on its own? | Yes, without a replacement goal. `on_hold` (she chose to) is separate from `waiting` (it depends on something). |
| Q4: one operation per decision? | One primary operation with its related effects as one transition. Communication stays separate. If real cases need several independent targets, a bounded set of operations follows. |
| Q5: keep communication `defer`? | Yes, with a referenced item, a reconsideration condition and an expiry. |
| Q6: assessor in the generator only? | Fine for the pilot, using only information that exists at runtime. Runtime assessment must be resolved before the revamp is declared complete. |

Also amended: `not_possible` no longer closes a goal (it stays open and waiting); intention versus goal is decided
by specificity and persistence; revising a goal (constraints) and replanning (approach) are both expressible.

**Permission while the user is away (decided by Vincent, 2026-10-24):** she is *allowed* to keep working on goals
inside an activity the user already enabled; whether she does is her choice (continue, pause, reconsider, stop). It
describes what she may do, never what she will do; "user leaves → keep playing" is not a rule. A new activity, or one
needing new access, is outside that permission. The user's own words override it ("don't do anything while I'm gone").
In the generator: `AWAY_POLICY`, shown on the SETTING line; the `user_steps_away` twin is that restriction.

**Before scaling (reviewer, 2026-10-24):**
- *Twins with dependent context.* A thought formed after reading the new message (`about_new_message`) is recomputed
  when the message changes. Thoughts from before it stay (outdated, not invalid), and the checker is told so.
- *Questioning an assessment.* `assessment_vs_doubt` and `assessment_vs_condition` give the original an overeager
  assessor (always "seems fulfilled") and the twin an honest one on the same situation. Her decision should follow
  the situation, not the label. The checker judges the world before any assessment is added.
- *Broad coverage restored.* 28 everyday families from rounds 1-2, ported to the world model with typed twists
  (`BROAD`; `--set all|targeted|broad`).
- *Tag first.* REPORT.md shows per-twin decisions only for pairs already tagged.
## Decisions from the second review (2026-10-24)

The reviewer's remaining items, built in `events.py` (snapshot and decision version 3). Regression families score
the *selected target*, not just the operation (`Family.expect`, "Regression expectations" in REPORT.md).

- **`pursue` routes; no capability gets its own operation.** Pursuing `[M]`, `[U*]`, `[R*]` or `[P*]` takes it on
  and starts what it needs through the setting's capabilities. The system routes it to planning or straight to
  execution: gathering, searching, drafting, or sending a draft the user just approved. The decision schema says so,
  with examples from several settings. There is no `send_email` or similar label.
- **Control scope is explicit:**
  - Pausing `[A1]` stops everything she does here, for every goal.
  - Pausing a goal puts only that goal, with its work, on hold; the activity and other goals keep going.
  - Closing a goal (achieved or given up) stops its unfinished work.

  The effects text says this next to each control.
- **User restrictions are state the system enforces:**
  - *Recorded.* A restriction from an earlier user message is recorded (`restrictions`: text, its message, scope).
    Scope is `everything_here`, one `goal`, or one `capability` (e.g. "no hints"). One from the new message isn't
    recorded yet: it is uninterpreted.
  - *Enforced by the validator.* Inside the named scope, nothing starts or resumes (resume, or pursue of `[U*]`,
    `[R*]` or `[P*]`). The exception is a decision that cites the user's later go-ahead as its `basis` (`[M]`, or a
    later `[U*]`). "While I'm away" may end when they're back, and only cognition can read that. Pausing, ending,
    thinking and taking up the new message stay possible.
  - *Capability restrictions are the executor's,* since no decision names a capability.
  - *Snapshot.* The snapshot shows restrictions as in force and enforced.
- **A follow-up doesn't swallow the goal it follows.** `other_goal_handling: close_achieved` closes another goal
  in the same transition, when an assessment `[E*]` of it says it's fulfilled.
  - Example: a finished draft plus "send it" is `pursue [M]` (the sending) with the drafting goal `close_achieved`.
  - Anything else the user asked for, and this decision leaves alone, stays an open request `[R*]` and comes back
    as a pending request.
- **Words never run ahead of execution.** Speech is told what the decision starts and that it isn't confirmed yet.
  It says "i'll send it", never "sent", unless an outcome in the snapshot confirms it. REPORT.md flags past-tense
  claims for review.
- **Capabilities have one current state.** A capability can be `unavailable` with a reason, shown where it is
  listed, so it never says "connected" while a work outcome says "expired".

**Her thoughts and her motives (second review):**
- *Judging thoughts.* The decision prompt says her thought is her own reading and can be wrong, go further than
  the facts, or carry a poor plan. When it settles an open question and acting on it would replace what the user
  asked for or be hard to undo, she asks or keeps options open.
  - Training data contains *thought substitutions*: the same moment with `[T1]` replaced by an accurate, uncertain,
    mistaken, bad-plan or unestablished version, or none. Each is checked to be what it claims; nothing else differs.
  - Flawed versions are graded for whether the decision went with the flaw.
- *Competing motives at graded stakes* (`MOTIVES`, `--set motives`):
  - curiosity vs a commitment (low, middle and high cost);
  - being dismissed (mild, pointed, repeated contempt);
  - her view vs theirs (taste, wasted effort, likely regret);
  - her taste vs their wish (they're mildly or very keen).

  Each level has several distinct situations, and her thought never pre-decides.
- *Teacher passing isn't enough.* These count as handled only when a student trained on the data shows them on
  held-out thought substitutions and held-out motive situations (`events-student`).
