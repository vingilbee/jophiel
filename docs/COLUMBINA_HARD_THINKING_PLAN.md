# Hard thinking: plan for review (2026-10-17)

Status: **built 2026-10-17, all five steps, not yet tried live.** Changes one part of
`COLUMBINA_MIND_DESIGN.md` (the `hard` level). Quick thoughts (react / light) stay as they are.
Code: `app/ai/mind/hard.py` (the thinker), `app/services/mind_hard.py` (triggers, drops, where
conclusions go), `think_it_over` in `app/ai/tools/desktop/tools.py`, migration `b8e4c2a6d9f1`,
`scripts/mind_hard_eval.py`. The open questions below were settled with the proposed defaults.
Not built from the plan: "going underground or more than 64 m from the user" (marked later) and
the code pattern "something of hers disappeared that she didn't use" (left to the gate).

## Why

What hard thinking does today (76 hard thoughts since 2026-10-14):

- **Triggers are fast moments.** The user spoke (23), her hands switched skill (17), the
  goal or step changed (15), she was paused or resumed. 16 never finished.
- **Output is mostly a status line.** "I'm still on the way back", "I'm paused", "I can't X,
  help me", or a second answer to what the user just said.
- **It acts.** It's a background game turn with tools, so it set plans and spoke at the same
  time as her mouth, 4-19 s behind. That caused most of the conflicts on 2026-10-17 ("make a
  bed" lost to "find another sheep"; a 13 s old "I'm paused" reached her reply).

What people do instead: slow thinking works on something that **stays true for minutes**
(a problem, a plan, a person, a lesson). It starts when something **doesn't fit**, or
when there's **time**, and it ends in a **conclusion** that changes what they do next.

## The new hard thinking, in one table

| | |
|---|---|
| Starts on | the same thing keeps failing; a surprise; before something big; her mouth hands something over; quiet time |
| Reads | the problem with its history (counted), the conversation, memories searched for the problem, game lessons and learned moves, earlier thoughts on it, her character |
| Can't | use game tools, change the plan, speak aloud |
| Ends in | one conclusion: decision / guess to test / question for the user / intention / feeling about the user |
| Goes to | the thoughts panel (always), her hands (as advice), her mouth ("on your mind"); a question is asked by her mouth |
| Time | 10-30 s is fine; dropped if the problem it was about is gone |

## 1. Triggers

Old triggers that stop starting hard thoughts: "the user said", "your hands: X -> Y",
paused/resumed, new goal/step events. Those stay **light** at most. One hard thought runs at a
time (as now); at most one waits, and a newer one replaces a waiting one (the mouth's
hand-off always wins). At least 20 s between two (`MIND_HARD_GAP_SECONDS`, as now).

| Trigger | Who decides | When exactly | The problem it's given |
|---|---|---|---|
| **Keeps failing** | code | her hands' 4th and 10th failed try on one step (the `struggling` notice already does this, `skill_driver` `step_log`) | the step, its done_when, every counted try and why it failed, inventory change since the step began |
| **Surprise** | Jev + code | Jev: its `hard` level now means "this doesn't fit what you expected", not "this is important". Code: a result that says failed while the step's target happened anyway (sheep: "target is gone" while mutton went up), or something of hers disappeared that she didn't use (the bed) | the outcome, what she expected (the step), what the game counted |
| **Before something big** | code | a new goal with 3+ steps; a learned move run 5+ times in a row; going underground or more than 64 m from the user (later) | the goal and steps, what she carries, her lessons for it: "what could go wrong, what do I need, how do I get back" |
| **Mouth hand-off** | her mouth | the new tool `think_it_over(what)` on a reply turn: the user's request is unclear, big, or needs a plan. She still answers now ("Okay, let me think about how to do that") | the user's words, what she answered, the conversation |
| **Quiet time** | code | nothing new for 3 min while playing, at most once per 5 min (light quiet thoughts at 25 s stay) | a rotating topic: the user (what they want from today), the session so far, herself; never the same topic twice in a row |

`stuck`, `plan_low`, `goal_done` and `needs_human` stay **control turns** (Luna with tools,
as now): something must act there. They now also read her latest conclusions.
`struggling` stops waking a control turn and becomes the "keeps failing" trigger.

## 2. What the thinker reads

Built by a new `app/ai/mind/hard.py` (input) and run in the `mind` worker's pool, not as an
`agent_events` job.

- **The problem**: the trigger's own block from the table above, counted by code.
- **Now, briefly**: health, food, holding, inventory, time of day, who's near, "Around you".
  Not the full one-second state dump.
- **The conversation**: the last 16 messages, hers included, with how long ago each was.
- **Memories**: `long_term_memory.retrieve_for_prompt(user, query=<the problem in words>)`,
  which brings facts and past sessions (episodes) together. Today's hard thoughts get none.
- **Game lessons and her learned moves**: the same lines her game summary has.
- **Earlier thoughts on this problem** (same trigger and step/goal, last 10 min): build on
  them, never repeat them.
- **Her character** (core memory v2 `identity.character`: values, what she cares about):
  so a feeling or a question sounds like her.

## 3. What comes out

One call: `MIND_HARD_MODEL` (default gpt-6-luna), `MIND_HARD_REASONING_EFFORT` (default
`low`: time isn't short here), strict JSON schema, no tools, 30 s timeout.

```
{
  "thinking": "a few private sentences: what's going on, what she considered",
  "kind": "decision | guess | question | intention | feeling",
  "conclusion": "first person, at most 30 words",
  "for_hands": "a tip for her hands, or empty",
  "question": "what she'd ask the user, or empty (kind question only)"
}
```

Rules in the prompt: conclude something; never describe the moment back; never say what
she's doing right now; a decision must name what she'd do differently; if nothing is worth
concluding, `kind` is empty and nothing is saved (counted in the log).

| Kind | Example (from real sessions) | Goes to |
|---|---|---|
| **decision** | "Digging up isn't working here; follow the water, that's how we got out last time." | hands (tip), mouth (on your mind) |
| **guess** | "The sheep move says failed, but the mutton went up: it kills them. Count the loot." | hands (tip), mouth |
| **question** | "Does he want me on top of the pillar, or just to learn the move?" | asked by her mouth (below) |
| **intention** | "Keep 5 planks to get back down." | hands and mouth, until done or 20 min |
| **feeling** | "He's teaching me today; I want to show him I've got it." | mouth (on your mind) |

A decision that changes **what** she does (not how) is never applied by the thought. It
becomes a question to the user, or, when she's playing on her own (keep playing on), a note
the next control turn must read.

## 4. Where it goes

- **Thoughts panel**: every hard thought, with its trigger and kind; its private
  `thinking` folded under it; dropped ones greyed with why ("dropped: the goal changed").
- **Her hands**: `for_hands` goes through the existing tips channel
  (`desktop_commands.send_hands_message`), labelled as her own thinking, kept with up to
  2 others. Intentions get their own short list in her hands' prompt (3 at most, 20 min).
- **Her mouth**: "On your mind" lines now group: conclusions (with kind), the open
  question, intentions, and "still thinking about: X". Status-line text no longer appears
  there, because thoughts no longer produce it.
- **Questions (asked from thoughts, as you chose)**: one open question at a time. Said
  aloud by her mouth's path (`agent_say`, a new kind `question`) at a natural pause:
  - the user hasn't spoken for 8 s;
  - she isn't mid-reply (the socket already waits for a reply to finish).

  If the user speaks first, the question is in "on your mind" so her reply can ask it. It
  expires after 3 min or when its problem is gone. Voice is only ever her mouth: the
  thought itself is never spoken.

## 5. The user talking while she thinks

The thought isn't interrupted. When it finishes, the change check (`game_moment`, built
2026-10-17) and the trigger decide:

| | Dropped when |
|---|---|
| Keeps failing | that step is done, or the goal changed |
| Surprise | never dropped; marked out of date if the world moved |
| Before something big | the goal changed |
| Mouth hand-off | the user changed the goal since (a user-turn plan change) |
| Quiet | never dropped |

A goal change the user makes **cancels** a running thought about the old goal at once,
to save the call. Otherwise the conclusion arrives, and her mouth sees it next to what the
user said since.

## 6. Storage

`mind_thoughts` gets (one migration):

- `trigger`: failing / surprise / big / handoff / quiet
- `conclusion_kind`
- `thinking`: private text, panel only
- `for_hands`
- `question`
- `dropped_reason`
- status `dropped` added to the check constraint

The trigger's anchor (step id, goal, plan revision) goes in `topic`. The old
`thought:` jobs in `agent_events` are no longer queued by the mind.

## 7. Building it, in order

1. **Stop the old triggers; new thinker; storage; panel.** Hard thoughts start only on
   "keeps failing" and quiet time. They produce typed conclusions, show in the panel, and
   reach her mouth. Nothing goes to her hands yet.
2. **Mouth hand-off**: `think_it_over` and its prompt rule.
3. **Routing**: hand tips, intentions, the question path.
4. **Before something big, and surprise**: the code patterns; Jev's `hard` redefined, checked
   on recorded ticks with `scripts/mind_gate_eval.py`.
5. **Measuring**: `scripts/mind_hard_eval.py`, which replays real triggers from recorded
   sessions through the thinker offline. It reports:
   - the share of conclusions that are status lines (target near 0);
   - how often each kind comes up;
   - the drop rate;
   - cost per hour.

Each step is tested on its own and tried live before the next.

## Not in this plan

- Steps ending by what's true (killed N, has N, at the peak), lesson results reaching the
  hands, the sheep move counting a kill as success. That's idea 2, next.
- Changing light or react thinking (only their triggers that move away from hard).

## Questions for review

1. Quiet hard thinking: after 3 min of nothing, at most once per 5 min. Too often, or not
   often enough?
2. Should `struggling` stop waking a control turn (it becomes a thought instead)? A
   thought can't replan, so a step that truly can't work would wait for `stuck`.
3. Reasoning `low` on the thinker: slower (maybe 10-20 s) but deeper. Or `none` for
   about 3-5 s?
4. Intentions last 20 min, or until the session ends?
