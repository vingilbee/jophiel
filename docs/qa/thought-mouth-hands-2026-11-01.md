# Thoughts, mouth and hands after the refactor

Investigated 2026-11-01 at commit `71b2b97`. This checks source wiring and integration behavior with scripted model responses, a disposable PostgreSQL database, and simulated game/Redis seams. It does not certify a live Minecraft session or the quality of model decisions.

## Findings

The main connections remain present:

| Connection | Current route |
| --- | --- |
| User message to decision and mouth | `services/shared_state.prepare` gathers context and calls `student_two.decide`; the chat worker receives the decision brief and generates the reply. |
| Requested thinking to follow-up mouth | Shared state queues coordinator work; completed thinking goes through `cognition_decisions.student_result`; authorized speech becomes a follow-up chat job with its decision brief. Silence stays private. |
| Private game thoughts to hands | `mind_hard.route` sends `for_hands` tips through `mind_minecraft.Body` to `desktop_commands`; intentions are retained separately. `mind_hard.hands_messages` combines them for the skill driver. |
| Mouth to hands | Desktop tools include goals, direct actions, pause/resume/end, and `tell_my_hands`; `desktop_game.tell_hands` publishes tips through Redis. |
| Plan to hands | The planner applies session plans; `workers/desktop._drive_skills` supplies the session view and messages to `SkillDriverRun`. The driver rejects decisions when the plan, execution generation, or tips change while the model is answering. |
| Hands back to thought/planning/speech | Progress and notices go through `desktop_sessions`; stuck and completed steps queue game events and planning. Mind reads driver state and outcomes. Proposed speech uses autonomous-speech/coordinator arbitration. |

The coordinator and hands have separate executor/worker lanes. A slow thought does not own the hands' execution loop.

## Failures and repairs

The original coordinator fixture supplied a `SimpleNamespace` without `cognition_decision_provider`. `student_two.rendering()` raised before decision routing or action execution. The chat-worker fixture had the same problem. Both now copy production settings and explicitly select the scripted local student provider, supplying the full settings contract without changing production behavior.

With an isolated database, the original coordinator/background selection reproduced **26 failures and 7 passes**. Thus the earlier report of 32 failures was not the exact count reproduced here. Repairing the shared fixture removes this failure across its dependent suites.

The background system test also still controlled `calls['jev']`, although its scripted decision reads `calls['student']`. Updated that control so the second message actually takes the intended no-thinking path.

A remaining observation-count failure came from the disposable database defaulting to `America/Vancouver`. The app treats naive message timestamps as UTC. Both system scenarios passed after configuring the disposable database as UTC. A read-only `SHOW timezone` confirmed the configured development database already uses `Etc/UTC`.

## Configuration and limits

The local configuration has `desktop_agent_enabled=True`, `cognition_decision_provider='teacher'`, and Redis configured. Test responses are scripted; passing them does not establish that the live teacher, workers, Redis, or game pack are currently healthy.

Observation-to-learning preparation remains explicitly unimplemented: `background_components.unavailable_preparation` reports that fact, and local `observation_preparation_enabled=False`. The learning capture added in the latest commit records attempts/executions; it does not implement that transformer.

Another existing limit is explicit in `student_two.apply`: a recall-only request from a finished thought has no executor owner, and `defer` has no durable resumption store. Neither is evidence that the hands were disconnected by this refactor.

No production source, live database content, or live game state was changed during this investigation.

## Final verification

**302 passed, 0 failed, 0 skipped**, in 88.05 seconds. Two dependency deprecation warnings (Torch/Python 3.14 and Starlette/httpx) remained.

The single combined run covered these backend test modules with `pytest -q --tb=short` and `TEST_DATABASE_URL` pointing to the disposable UTF-8, UTC PostgreSQL database:

```text
test_coordinator.py test_background_coordination.py test_background_system_e2e.py
test_skill_driver.py test_student_two.py test_student_two_teacher.py test_mind_runtime.py
test_shared_state_worker.py test_shared_state.py test_shared_state_cache.py
test_shared_state_interactions.py test_initial_recall.py test_game_planning.py
test_learning_capture.py test_desktop_game.py test_mind_hard.py test_autonomous_speech.py
test_agent_events.py test_game_moment.py
```

`git diff --check` also passed. Temporary failure diagnostics were removed. This is targeted integration coverage, not a full backend-suite run or a live game smoke test.
