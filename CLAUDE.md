# Project: Jophiel (General Conversational Agent)

## One mind (2026-11-13)

One persistent mind owns her decisions, conversation continuity and scheduling choices; Student 1, Student 2,
the shared-state pipeline, appraisal and mood were removed. Every wake (a message, thinking she started that
finished, a thought or proposal from another part of her, a reconsideration she scheduled) is one evaluation
of the agent loop with her prompt and tools (`services/mind_turn.py`). Personality is text-only
`core_memory.profile`, cached once per process by `services/personality.py`. Apply migrations `a6d2f8c4e9b1`
and `b7e2c9f4a1d6` (`uv run alembic upgrade head`) before starting this version. Continuity follow-up (2026-11-14):
migration `c8e4a1f7b3d2` (`mind_evaluations`,
`learning_assessments.awaiting_notes`); a fresh "Now" per evaluation, unmerged history with app records, wake chains.
Phone affinity B1 (2026-11-22): migration `a4d7e1c9b3f2` (phone people, conversations, affinities, outbound replies).
Phone affinity B2 (2026-11-22): migration `b5e8d2a7c4f1` (exchange readings, shadow proposals, requests for space,
authorized details; its phone-scoped assessments and lessons went with the old learner, 2026-11-26).
Phone affinity B3 (2026-11-22): migration `c6f9e3b8d5a2` (`phone_outbound.attempt_key`: one claim per autonomous
reply attempt).
Phone privacy (2026-11-23): migration `d7a1f4c8e2b9` (`phone_disclosures.verified`: grants checked against the user's
own message).
Promises and questions (2026-11-23): migration `e8b3f1a6c4d7` (`working_items.trigger` / `trigger_met_at` /
`offered_at`, question statuses `asked` / `stale`, evidence role `asked`; the open-question backlog is marked handed
over). A promise of hers is one item with its time (and durable wake) or condition; it's fulfilled only in the delivery
commit of the words that keep it (`workers/chat.py` `_carried_out`), after a check model reads that those words carry it out
(`services/promise_check.py`, fails closed); on WhatsApp it waits for the channel's delivered status (migration
`f2a6d9c3b8e1`, `working_items.keeping`). An undelivered attempt re-arms its wake (`coordinator.rearm_promise`).
Questions: `services/open_questions.py`.
Follow-through (2026-11-23): migration `a3f8c1e6d9b4` (`follow_through_attempts`). A quick thought that finds a promise
or intention of hers due now (`act`) wakes her mind with her tools even with nothing to say, once per item and observed
change; each device action there is a recorded, fenced attempt, achieved only on the device's own answer or a fresh
report (check wakes, bounded); `services/follow_through.py`.
Phone control (phase 9, 2026-11-24; `frontend-phone/PLAN_PHASE_9.md`): her hands on the phone's screen, reply turns
only (`ai/tools/phone_control.py`: read_phone_screen first; act by number, by point as a fallback, several fingers with
phone_pinch / phone_gesture; 80 actions a turn; `taken_over` stops her for the turn, `taken_over_after` when the action
itself happened first; one chat line per turn; phone_tap_points: up to 20 taps in one step, the phone settles and
reads only after the last, phone app action `tap_points`). Screens travel
sealed to the waiting worker and are never stored (`services/phone_control.py`: turn supersession, render, the
short-term memory of 10 text-only screens for 30 min in process memory, wiped when the phone reports `control_on`
false). No migration.
Waiting on her devices (2026-11-24): `run_command(background=true)` (controller 0.14.0, relay feature `command_jobs`)
keeps a long command running as `cmd:…` (`check_command` / `stop_command`); its end comes back as `CommandDone`
and goes on her hands' trail (`desktop_control.command_finished`), so her background mind can wake her to follow up.
`come_back_to(..., on="desktop"|"phone")`: if she uses that device again before it's due, it moves to the same
delay after her last use (`coordinator._reset`). No migration; update the VM's controller to use background commands.
Looking again (2026-11-25): a reply turn whose hands acted on the phone screen (not taken over) schedules a wake
10 s after it ends (`services/phone_look.py`, origin `phone_look:<turn>`); that wake has the screen tools for what the
user's message asked, within 10 min of it, dropped by any newer message of theirs; a look that acts schedules the next.
The one exception to phase 9's "reply turns only". No migration.
Cut-off phone turns (2026-11-25): each step of her hands is kept as one line (what she did, the screen's first line;
`phone_control.note_step`, process memory, wiped with her screens), and a turn stopped by the user's new words is read
back step by step in her history; a look is scheduled only after a turn she finished herself (`PhoneControl.answered`).
Latency (2026-11-25): her prompt is two system messages, the standing part first (`prompts.build_prompt_parts`)
and this evaluation's moment (Now, senses, memory, devices, delivery rules) right before the message it answers, so
OpenAI's prompt cache serves the standing prompt and the history; the history window's start moves every
`chat.HISTORY_STEP` (12) messages, not every turn. Voice marks now split `model_ttft` into each model call
(tokens, cached tokens) and tool (`app/ai/loop_timing.py`), and context prep into history / memory check / reads;
reads that don't depend on her memory check run alongside it. No migration.

Tool learning (2026-11-26): migration `b9d4e2f7a1c3` (`practice_notes`). The old learner (retrospective assessment,
memory lessons, game trials, learning's follow-ups and proposals, phone lessons) is removed, code and tables
(migration `c1e5a9d3f7b2`, not reversible). In its place, practice: every action of her phone hands goes on a trail in Redis (tool, what she said to it, item or point, what
came back, the screen as text, the picture she aimed on with her marks; never typed text; wiped with her screens), a
stretch is reviewed once her hands are still `PRACTICE_SETTLE_SECONDS` (one vision model call, `services/practice.py`,
contract `app/ai/practice.py`, trace `var/practice_reviews.jsonl`), and she keeps plain notes per app and tool, and on how the
place itself behaves (`known` / `to_try`: something seen once is to try; the user's own never rewritten). She reads an app's notes with its first screen in a turn;
`keep_phone_note` keeps one the user gives her; `/api/practice/notes` + the System page's Learning part edit them.
Tool descriptions say what a tool does in every app; how to use it well in one is a note.

## Overview
Jophiel is a general conversational agent with a persistent identity, personality, memory, and one mind that decides what she does. The primary product experience is natural text and voice conversation across everyday topics, including social exchanges, reflection, curiosity, disagreement, and practical collaboration. Her behavior should follow the conversation rather than defaulting to an assistant task, advice, or a deliverable.

`NutritionDetection` is the historical workspace name. MacroVision food-photo inference and weight/calorie APIs are specialist capabilities retained in the codebase; they do not define Jophiel's identity or the app's main focus.

**Core features:**
- General text and voice conversation with Jophiel
- One mind owning what she says (or doesn't), her background thinking, what she keeps open and when she comes back to it
- Persistent memory and context, with screen awareness and integrations where available
- Practical collaboration and specialist tools when the user requests them

**Evaluation focus:**
- Default behavioral tests should evaluate general, multi-turn conversation and Jophiel's identity, not meal planning or nutrition assistance.
- Include greetings, casual chat, reflection, topic changes, ambiguity, corrections, disagreement, and context-sensitive follow-ups across varied topics.
- Judge relevance, naturalness, continuity, and appropriate conversational moves. A useful turn need not produce a plan, artifact, tool call, or advice.
- Keep food/meal scenarios as explicitly scoped specialist or regression coverage. Historical dinner fixtures and dated reports are evidence of earlier implementation work, not the default product brief.

---

## Tech Stack
- **Frontend:** React (with Vite), package manager/runtime: **Bun** (use `bun install`, `bun run dev`, `bun add <pkg>` — not npm/yarn)
- **Backend:** Python, FastAPI, dependency/env manager: **uv** (use `uv sync`, `uv run <cmd>`, `uv add <pkg>` — not pip/poetry) `uv run uvicorn app.main:app --reload` 
- **Database:** PostgreSQL
- **ML Model:** PyTorch. Nutrition inference in `/app/ai/MacroVision` — fine-tuned ZoeDepth (depth) + an RGB/depth ViT cross-attention regressor, optionally combined with a Qwen3-VL VLM. Served via the `/api/predict` FastAPI endpoint. See the Decisions section for details.
- **Chatbot:** Jophiel is a general-purpose personal companion/orchestrator backed by LangChain (`langchain-openai` + `langgraph`) → OpenAI (`gpt-6-luna` by default, reasoning off). Domain capabilities such as nutrition live behind specialist sub-agents. Responses stream; code lives in `/app/ai`.
- **Auth:** (fill in — e.g. JWT, OAuth, Supabase Auth — TBD)
- **Image storage:** (fill in — e.g. S3, Cloudflare R2, or local disk for dev)

---

## Project Structure
```
/frontend                 # Vite + React 19 + TS, styled with Tailwind v4
  /src
    /components       # reusable UI: Logo, Button, ProtectedRoute, AppHeader, ProviderIcon, VoiceOrb, SenseStrip,
                       #   DesktopSessionPanel, ThoughtStream, MindPanel (what her mind keeps open and will come back to),
                       #   Avatar (her glyph mark + user initial), system/ (the System page's field: FieldMap, PartPage,
                       #   LogDrawer (watch log / signals / errors), TraceView (a turn or decision through every part),
                       #   GoalCard (her hands' goal + plan), log.tsx + format.ts (timeline pieces), anatomy, shapes,
                       #   icons, LearningDetails = what the Learning page was)
    /pages             # route-level pages: LandingPage, LoginPage, DashboardPage (voice), ChatPage, PredictPage, IntegrationsPage,
                       #   SystemPage (owner only: her parts as a live field; replaced LearningPage, /learning redirects)
    /audio             # mic capture worklet client, PCM playback, VAD, wake word
    /senses            # the eye: screen.ts (getDisplayMedia + sampling) + screen.worker.ts (diff/encode)
    /hooks             # custom React hooks: useAuth (session context), useChatSocket, useVoiceSession,
                       #   useAwareness (report senses up), useScreenSense (the eye toggle),
                       #   useDesktopSession (app-level desktop agent session provider)
    /api               # API client: client.ts (fetch wrapper), auth.ts, predict.ts, chat.ts, integrations.ts, desktop.ts, learning.ts
    /types             # shared TypeScript types
    App.tsx            # react-router-dom routes
    main.tsx           # entry: BrowserRouter + AuthProvider
    index.css          # Tailwind import + dark theme tokens (@theme)
  vite.config.ts        # Tailwind plugin + /api proxy -> localhost:8000
  package.json
  bun.lock

/backend
  /app
    /api               # FastAPI route handlers
      /routes
        predict.py      # POST /api/predict — image upload -> macro prediction (auth'd)
        chat.py         # chatbot: WebSocket /api/chat/ws (streams reply) + REST conversation history
        dashboard.py    # weight/calorie CRUD endpoints
        integrations.py # Google OAuth connect/callback/status/disconnect
        desktop.py      # desktop agent environments/sessions/plan under /api/desktop (404 unless DESKTOP_AGENT_ENABLED);
                        #   PUT/GET .../environments/{id}/security: the owner's security-monitoring switch + channel health
        practice.py     # her practice notes (tool learning): GET/POST/PATCH/DELETE /api/practice/notes (owner only)
        learning.py     # moves she taught herself and her challenges under /api/learning; learning capture
                        #   (chains, gaps, trajectory) under /api/learning/capture
        system.py       # her parts as one system: GET /api/system/status (worker heartbeats per part),
                        #   GET /api/system/parts/{part} (a part's recent activity, the owner's own),
                        #   GET /api/system/watch (thoughts, memory captured, hands' goal as one log),
                        #   GET /api/system/errors (what went wrong across parts),
                        #   GET /api/system/signals[/stream] (hand-offs between parts, SSE live); owner only
        auth.py         # signup/signin (web cookie), POST /auth/token (phone bearer token, 90-day sliding), signout
        phone.py        # the phone app's device: PUT/PATCH/DELETE /api/phone/device (FCM token, notification switches);
                        #   PUT /api/phone/state (what the phone reports about itself from the background);
                        #   POST /api/phone/notifications (its notifications, phase 8); action results
      deps.py           # shared deps (get_current_user, get_current_user_optional): bearer header or cookie;
                        #   validate_origin passes a valid bearer (can't be forged cross-site)
    /models            # (empty — nutrition inference lives in /ai/MacroVision)
    /ai                # chatbot agent (LangChain + Anthropic) + nutrition model
      agent.py          # ChatOpenAI model + build_agent(tools) / cached get_agent()
      openai_http.py    # HTTP clients for OpenAI calls before a reply: 120 s keep-alive, one pool per event loop,
                        #   streamed replies closed so their connection is reused
      chat.py           # stream_chat / complete_chat; assembles the orchestrator per turn
      context.py        # AgentRuntimeContext (user_id + per-user integration handles)
      messages.py       # role/content <-> LangChain message conversions
      mcp_registry.py   # builds MultiServerMCPClient config from tools/*/config.json
      prompts.py        # orchestrator prompt; delegation section built from the registry
      /subagents       # one specialist agent per MCP toolset
        registry.py     # SubAgentSpec list — the orchestrator's tools are built from this
        builder.py      # spec + live MCP tools -> a single delegation tool
      /integrations    # connections to third-party accounts (auth only, no tools)
        registry.py     # IntegrationProvider registry — the API/UI list is built from this
        /google         # OAuth 2.0: oauth.py, credentials.py, scopes.py, provider.py
      vision.py         # captions a screen frame to one line (small model)
      /observe          # registry.py: what each kind of observation looks like and its memory rule (pure)
      practice.py       # her practice notes (pure): the trail's steps, the reviewer's prompt and contract, checks, how notes read
      /security         # coverage.py: what she could see of a log channel over a window, and why not the rest (pure)
      /recall           # contracts.py: recall requests, typed results, memory references, limits (pure);
                        #   history.py: source-backed history contracts, reader prompt/schema, check, rendering (pure)
      /mind             # her background mind's pure parts:
                        #   contracts.py (Scope, Moment, Source, Body, Command), tick.py (what changed, pressure, tick
                        #   text; the Minecraft source), situation.py (what a source tells the general mind), gates.py (how hard
                        #   to think: Jev / OpenAI Decisions / chat / Strands), think.py (quick thoughts),
                        #   plan.py (her planner: the one author of her game plan),
                        #   effort.py (her effort, pure: outcome / approach / actions / the user's rules as a
                        #   timeline she and her planner read)
      /desktop          # desktop agent runtime: driver.py + driver_loop.py (gameplay model loop), skill_driver.py (her hands
                        #   in a game pack), practice.py (her learner, stage H1), play_log.py (view angle +
                        #   recent play, counted by code), relay.py (TLS controller relay, own process:
                        #   `uv run python -m app.ai.desktop.relay`; RelaySupervisor listens only on the approved
                        #   network) + tls.py (its self-signed certificate) + network.py
                        #   (the approved network: where it may listen, pure decision + reading interfaces) +
                        #   firewall.py (the LAN port rule: `uv run python -m app.ai.desktop.firewall show|check`)
      /tools           # MCP tool servers the agent can call
        /screen         # look_at_screen — NOT MCP; one tool on the orchestrator itself
        /attachments    # read_attachment: her own reading of attached files (overview / pages / find, bounded
                        #   per call and per reply) — NOT MCP; on the orchestrator when the user has readable files
        /mind           # her mind's own tools (stay_silent, think_it_over, stop_thinking, come_back_to,
                        #   drop_come_back, settle_attempt) — NOT MCP; on every evaluation
        /memory         # her working-memory tools (set_goal, update_item, note_commitment, note_intention,
                        #   note_question, switch_context) — NOT MCP; on the orchestrator for every evaluation;
                        #   recall.py: her read tools (search_memories incl. "said", read_memory, read_timeline, what_i_know), recall_history
        /desktop        # game tools (start/replan/pause/...) — NOT MCP; on the orchestrator, only with her Minecraft pack
        /security       # get_security_activity: her desktop's security records, read only, with coverage — NOT MCP;
                        #   on the orchestrator whenever the user has a paired desktop (not on game-event turns)
        phone_control.py # her hands on the user's phone screen (phase 9): read_phone_screen, phone_tap/... — NOT MCP;
                        #   on the orchestrator on the user's reply turns when they have a phone; pictures via screenshot_hook;
                        #   phone_grounding.py: phone_tap_on's pointing model (UI-TARS-1.5 on OpenRouter, only with
                        #   OPENROUTER_API_KEY): she says what to tap, it finds where
      /desktop_control # her own Windows desktop: screenshot, move_mouse,
                        #   click, drag, scroll, keyboard — NOT MCP; on the orchestrator on reply turns with a paired VM;
                        #   screenshot_hook shows her the pictures (pre-model hook)
        /gmail          # gmail_api.py (REST) + server.py (MCP) + client.py (adapter)
        /drive          # user-scoped Drive/Docs/Slides MCP tools and PDF/Office export
        /macrovision    # server.py (MCP over the nutrition model) + mount.py + scope.py
      /MacroVision      # image -> macro model (inference only)
        architecture.py  # nn.Module defs (ViT regressor, cross-attn fusion) + ckpt remap
        predictor.py     # MacroVisionPredictor + get_predictor() cached singleton
        __init__.py      # public surface: get_predictor, NutritionPrediction
        weights/         # model weights (gitignored, transferred manually)
    /db
      models.py         # SQLAlchemy/ORM models
      session.py        # DB session/connection setup
    /core
      config.py         # Settings (env/.env)
      security.py       # password hashing + session tokens
      crypto.py         # Fernet encryption for OAuth tokens + signed OAuth state
      /schemas           # Pydantic request/response schemas (incl. chat.py: WS request + conversation/message output)
    /services          # business logic (separate from routes; incl. chat.py: conversation/message
                       #   persistence, awareness.py: the agent's sense of the current moment,
                       #   salience.py: whether it's worth speaking first,
                       #   desktop_sessions.py: desktop agent ownership + session state machine + plan,
                       #   desktop_link.py: desktop VM pairing codes, device tokens, controller heartbeats,
                       #   capture gate, the machine status controllers report; desktop_relay.py: the relay as the
                       #   dashboard sees it (status.json, network approval, firewall check); desktop_preview.py: latest game frame in Redis (10 s TTL);
                       #   desktop_commands.py: API -> relay command stream + decision outcomes,
                       #   security_activity.py: her desktop's security logs:
                       #   the owner's monitoring switch (epochs), intake from the relay, coverage, the read query;
                       #   mind_security.py: those records as a source of her mind (investigations from the logs' own
                       #   severity, her periodic look, coverage gaps, late evidence);
                       #   desktop_control.py: general desktop control (access, one action there and back,
                       #   each action recorded as a desktop_action capture + desktop observation),
                       #   voice_latency.py: per-stage voice-turn timing marks -> var/voice_latency.jsonl,
                       #   read with `uv run python scripts/voice_latency_report.py`;
                       #   game_learning.py: game experience -> reflection -> lessons/proposals/episode;
                       #   game_skills.py: moves she taught herself, kept as skills her hands use (H2);
                       #   game_planning.py: her planner's jobs: input, decision, applying it;
                       #   game_moment.py: what changed in her game since a moment (counted by code);
                       #   game_correction.py: she corrects a reply the game outdated while she said it;
                       #   mind.py + mind_hard.py: her general mind (attention per user, quick and hard thoughts,
                       #   where conclusions go), over sources (app/ai/mind/contracts.py);
                       #   mind_minecraft.py: Minecraft as a source and a body (its senses and view coverage);
                       #   effort.py: her effort in a game session, built from what was recorded (read-only);
                       #   mind_conversation.py: memory's disagreements handed to her mind as wakes (no conversation
                       #   sweeps of its own); mind_turn.py: one evaluation of her mind (what a wake is given, the
                       #   evaluation's record, trace var/mind_turns.jsonl); mind_state.py: her continuity as one view
                       #   (GET /api/mind/state);
                       #   memory_manager.py: her working memory (contexts, goals, commitments, intentions,
                       #   questions, her hypotheses and decisions, events); GET /api/memory/working inspects it;
                       #   thought_memory.py: her finished thoughts (and learning's follow-ups) handed to memory,
                       #   matched against her open and recently closed items, kept or skipped;
                       #   open_questions.py: her questions' states (unasked / asked / answered / stale): a consumer
                       #   reading messages for what they ask or answer, and one question handed to her mind while
                       #   the user is quiet;
                       #   observation.py + observation_adapters.py + observation_memory.py: what sources
                       #   report (conversation, Minecraft) reaching working memory;
                       #   GET /api/memory/observations inspects it;
                       #   recall.py + recall_references.py + recall_embeddings.py: one read service over facts,
                       #   episodes, memory events and working items, reply receipts, event embeddings
                       #   GET /api/memory/recall inspects it;
                       #   recall_expand.py + recall_history.py: source-backed history, evidence links followed
                       #   to the user's words across contexts, rebuilt and checked;
                       #   memory_corrections.py: a user's correction reaching every current fact and working item
                       #   it affects; memory_conflicts.py: a message that disagrees with a fact, kept unresolved
                       #   and handed to her mind;
                       #   memory_reset.py: wipe user memory for tests, `uv run python scripts/reset_memory.py`;
                       #   learning_capture.py: what each learner was given and produced, and what executed (background
                       #   writer, gaps; planner and hard thoughts too); trajectory_capture.py + trajectory.py: her
                       #   trajectory, every game tick pulled from the pack in chunks, read back; capture_chains.py: reading chains back;
                       #   transcript_search.py: what was said (heard text, by speaker), her "said" search;
                       #   attachment_text.py: attached files' text pulled out once per page (PDF via pypdfium2, Word,
                       #   PowerPoint, Excel, text, CSV; no OCR: scanned pages marked) and read in bounded pieces;
                       #   file_shared / file_read observations reach memory, the text stays with the file;
                       #   attachment_memory.py: facts learned while she read a file linked to her reads of it
                       #   (shown as file + pages when read back; a deleted file is said to be, never an error);
                       #   memory_directory.py: her rundown by area (what_i_know), built when read;
                       #   memory_upkeep.py: duplicate facts merged on the memory worker;
                       #   phone app (frontend-phone/PLAN_PHASE_0-3.md): phone_devices.py (a signed-in phone, one per
                       #   bearer session), presence.py (is the user at any app: per-socket, Redis),
                       #   phone_state.py (what the phone reports about itself, latest value per field, Redis; phase 8: media,
                       #   navigation, the Messages switch) + phone_notifications.py (its notifications, Redis only,
                       #   news labelled someone else's words; a turn they cause gets no device action) +
                       #   phone_actions.py (one-shot actions on the phone: socket or push, lines in the chat) +
                       #   phone_people.py (people she meets on the phone: persons, conversations, default affinity,
                       #   her replies and their echoes, read_person) +
                       #   phone_relationships.py (B2: her readings of exchanges, shadow relationship proposals,
                       #   requests for space, authorized details, the learner's view of a phone exchange, review) +
                       #   phone_reply_check.py (B2: a check model reads her reply to someone else against a policy and only that
                       #   conversation's material before it goes out; fails closed; owner-asked messages too) +
                       #   phone_authority.py (privacy plan 1-2: grants verified against the user's own message; every phone
                       #   read or action on an owner turn bound to what their message asked, before the first call) +
                       #   mind_phone_scope.py (privacy plan 3-4: a wake that can write to someone reads only that conversation,
                       #   the person's relationship record and boundaries; nothing of the user's) +
                       #   phone_replies.py (B3: her own replies there: the messages that woke her bound at the tick,
                       #   follow-ups by stable ids, the checked send rechecked at the send point, claimed once) +
                       #   mind_desktop.py (her hands' actions on her desktop as a source of her background mind: a
                       #   trail in Redis from desktop_control; nothing when not connected or at rest) +
                       #   mind_phone.py (the phone as a source of her background mind: a tick on each change and a
                       #   heartbeat), push.py (push
                       #   outbox: her messages the user hasn't seen anywhere, re-checked when due), fcm.py (FCM HTTP v1);
                       #   follow_through.py: promises a change made due: her wake to act, attempts, checks;
                       #   coordinator.py: background thinking she started and the durable wake events that bring her
                       #   back (finished thinking, her thoughts and proposals, reconsiderations she scheduled), outputs
                       #   per wake; autonomous_speech.py: those events' delivery fences)
    /workers           # external workers: chat, memory, whatsapp, file_deletion, push (phone notifications),
                       #   coordinator (background thinking and her mind's wakes), desktop (gameplay driver) + agent_events (Jophiel on game events)
                       #   + game_learning (reflection on ended game sessions), all three when DESKTOP_AGENT_ENABLED;
                       #   learning (raw-capture retention) + practice (her practice notes from her phone hands)
                       #   (runner.py starts one thread per queue: `python -m app.workers`)
    main.py             # FastAPI app entrypoint
  /desktop_controller   # desktop agent action contract + controller core (0.12: discovery.py finds the relay by mDNS,
                        #   startup.py = login start per OS, machine.py = status/capabilities, input_macos.py,
                        #   input_linux.py); commands.py = run_command (one shell command, output as text, 0.13.0); desktop.py = general desktop control (her hands
                        #   on the whole VM desktop: bounded actions, released on failure); security.py = the VM's event
                        #   logs read only (wevtutil), bookmarks checked by record number, gaps/resets reported. uv workspace member (pydantic, pyyaml,
                        #   websockets only): runs in the game VM, also imported by the backend. Lives inside backend/ because
                        #   the Docker image builds from backend/ only.
                        #   Its tests run with the backend's: `uv run pytest` (testpaths includes it)
  /desktop_profiles     # trusted per-game input profiles (YAML): `uv run desktop-profile-check desktop_profiles/*.yaml`
  pyproject.toml         # uv-managed deps; [tool.uv.workspace] member desktop_controller
  uv.lock                # uv lockfile (committed; covers the workspace member too)
  alembic/              # DB migrations (if using Alembic)

/infra/desktop          # runbook: connect the game VM (Legion) to the Mac's relay

/game_packs/minecraft   # stage G: Jophiel's own Minecraft player (Node + Mineflayer, run with node, deps via bun).
                        #   State reader (+ src/surroundings.js: the shape of the place; src/terrain.js: the lie of the land; src/air.js: her own air; src/kills.js: her kills, counted), skills (src/skills: contract, runner, one file per skill group), background modes
                        #   (src/modes: guard/gather/pickup put a running skill on hold and resume it), loopback API,
                        #   viewer; scripts/server.js = private server on the Mac. Runbook in its README;
                        #   src/sandbox.js = the sealed box her own code runs in (stage H; Node >= 24);
                        #   src/needs.js = what it takes to get an item or mine a block (GET /needs/<name>);
                        #   src/trajectory.js = her trajectory: every tick, damage causes, actions, in chunks (GET /trajectory);
                        #   her view: src/view.js (rays from her eyes),
                        #   src/scene.js (openings, passages, structures, water, lava), src/landmarks.js (stable ids L3,
                        #   saved with the world), src/perception.js (kept fresh, into her state as "In view"),
                        #   src/view_image.js (material/depth PNGs, GET /view/image), src/navigation.js (standing spots,
                        #   bounded route checks: go_to's grounding); skill look_around in src/skills/looking.js;
                        #   backend side: app/ai/desktop/practice.py + services/game_practice.py (H1, her learner).
                        #   `bun run test`, `bun run smoke`, `bun run itest` (real server, needs the EULA accepted)

/model_weights          # pretrained weights copied/downloaded from the separate training repo (gitignored)

docker-compose.yml      # local dev: postgres + backend + frontend (if used)
```

> Update this structure section as the repo evolves — keep it accurate, not aspirational.

---

## Commands

### Frontend (run from `/frontend`)
- `bun install` — install dependencies
- `bun run dev` — start dev server
- `bun run build` — production build
- `bun run lint` — lint check
- `bun test` — run frontend tests (if configured)

### Backend (run from `/backend`)
- `uv sync` — install/sync dependencies into `.venv` from `uv.lock`
- `uv add <pkg>` — add a dependency (updates `pyproject.toml` + `uv.lock`)
- `uv run uvicorn app.main:app --reload` — start dev server
- `uv run pytest` — run tests
- `uv run alembic upgrade head` — apply DB migrations
- `uv run alembic revision --autogenerate -m "message"` — create migration

### Database
- Local Postgres connection string lives in `.env` (never commit `.env`)
- (fill in: how to seed local DB, reset DB, etc.)

---

## Conventions

### General
- Keep frontend and backend concerns separate — no business logic in React components beyond UI state.
- Environment variables go in `.env` files (gitignored); document required keys in `.env.example`.

### Frontend (React)
- Use functional components + hooks only, no class components.
- TypeScript strict mode — avoid `any`.
- API calls centralized in `/src/api`, not scattered in components.
- Component naming: PascalCase. Hooks: `useX` naming.

### Backend (FastAPI)
- All DB access goes through `/app/db` — no raw queries in route handlers.
- Request/response validation via Pydantic schemas in `/app/schemas`.
- Route handlers stay thin — actual logic lives in `/app/services`.
- Use type hints everywhere.

### ML / Inference
- This repo only does **inference** — loading pretrained model weights and serving predictions. Training code/data lives in a separate repo.
- Model loading should happen once at startup (not per-request) for performance.
- Document model input shape, preprocessing steps, and output format clearly (e.g. in `/app/models/README.md` or inline).
- Keep model weight files out of git (use `.gitignore` + manual transfer or external storage if needed).

### Git / Commits
- Commit style: Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`, etc.)
- Branch naming: `feature/xxx`, `fix/xxx`, `chore/xxx`
- PRs: (fill in — template, required reviewers, etc.)

---

## Things to Avoid
- Don't commit `.env`, model weight files, or uploaded user images.
- Don't put inference logic directly inside FastAPI route functions — keep it in `/app/models` or a service layer.
- Don't mix npm and bun — this project uses Bun exclusively for the frontend.
- Don't mix pip/poetry and uv — the backend uses uv exclusively; always go through `uv` so `uv.lock` stays authoritative.
- (Add more as you discover repo-specific gotchas.)

---

## Decisions

Each line is the rule you need while coding. The detail lives in the module docstrings named here (and in git history).

**Foundations**
- **Auth:** server-side sessions, not JWT. An opaque `httpOnly` cookie token; only its SHA-256 hash is stored. Argon2 passwords. Routes under `/api/auth`.
- **Chatbot:** LangChain `ChatOpenAI` + LangGraph ReAct agent, default `gpt-6-luna` with reasoning off (`CHAT_MODEL`, `VOICE_CHAT_MODEL`, `CHAT_REASONING_EFFORT`). Needs `OPENAI_API_KEY`. Model client and graph are cached, not rebuilt per request.
- **ML:** inference only (training lives in another repo). MacroVision = ZoeDepth + cross-attention ViT + an optional VLM (`qwen` or `openai` backend, only the chosen one loads). Use `get_predictor()`. Weights are gitignored. bitsandbytes is CUDA-only.
- **Orchestrator + one sub-agent per toolset:** the orchestrator gets one delegation tool per specialist (`app/ai/subagents/registry.py`). Rules the orchestrator must enforce itself go in the spec's `orchestrator_rules`. Adding a toolset = a registry entry + `tools/<name>/config.json`. Exceptions that sit on the orchestrator directly: `look_at_screen`, memory tools, game tools.
- **MacroVision MCP is mounted, not spawned:** it needs both `mount_macrovision_mcp(app)` and `macrovision_mcp_lifespan()`. Forgetting the second hangs every request.
- **Integrations are data-driven** from `app/ai/integrations/registry.py`. No route or UI change per provider. Callbacks are provider-specific. Refresh tokens are Fernet-encrypted. Leaving `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` blank disables Google cleanly (the card shows unavailable).
- **Gmail:** never request permanent delete. Sending needs the user's explicit confirmation (prompt rule, injected only when Gmail is connected).
- **`mcp` pinned `>=1.24,<2`** until `langchain-mcp-adapters` supports mcp 2.x.
- **Image store:** a per-user directory (`<root>/<user_id>/<image_id>`), referred to by opaque `image_id`. Screen frames are `kind="screen"`, with short retention.
- **Chat:** history lives in Postgres. The client sends only the new message. A WebSocket hands each turn to the external chat worker through Redis Streams. Postgres is the source of truth.
- **Message order is a database fact:** a trigger allocates `chat_messages.conversation_sequence`. Never set it in code, and never order a conversation by timestamp alone.
- **History-replay traps:** a history ending with an assistant turn is treated as prefill, so nudge runs append `prompts.NUDGE_TRIGGER`. `get_history` never merges turns; with `records=True` (her mind) each turn carries an app-record marker and silent evaluations appear as `record` turns, rendered as system messages (`ai/messages.py`). A reply stopped
  by the user's next message (before any words, or part way) keeps its turn's phone-control line there ("Used your
  phone in WhatsApp, 4 steps"), so taps a cut-off turn made aren't forgotten.
- **Awareness (`services/awareness.py`):** facts we own (clock, recent activity) plus senses the client reports (`SenseReport`, change-driven, flags only: no pixels or audio). No LLM; rendered to a short string on `AgentRuntimeContext.awareness`. Client-reported senses are descriptive only and never authorize anything. "Today" is the user's zone, not the server's.
- **The eye:** client-side capture + diff (`senses/screen.ts`, worker; only changed frames are encoded), upload as `kind="screen"` (retention by count and TTL: a sense, not a recording), a cached one-line caption at turn start (`ai/vision.py`), pixels on demand via `look_at_screen` (on the orchestrator directly, built per turn, only when the eye is on).
- **Chat transport:** WebSocket `/api/chat/ws` (cookie or bearer auth). The socket persists the user message and a durable `ChatJob`; the external worker (`python -m app.workers.chat`) generates and streams events through Redis Streams (replayed on reconnect); `stop` sets a Redis cancel flag. Outside turns the socket carries awareness (`sense` in, `state` out, 30 s heartbeat).
- **Voice page:** voice lives on `/dashboard` (`useVoiceSession`, `VoiceOrb`: a distinct motion per phase, level read by a getter, not state). `ChatPage` is typed only; both share one socket.
- **WhatsApp:** Meta Cloud API webhook `/api/whatsapp/webhook`, off unless configured. Linked by a short-lived code (`link CODE`) to the user's primary conversation; inbound deduplicated by `wamid`, fragments debounced into one message and job; a separate worker delivers chunked replies from a Postgres outbox.
- **Deployment / CI:** local only, no CI yet.

**Desktop agent and games** (`game_packs/minecraft/README.md`, `infra/desktop`)
- `DESKTOP_AGENT_ENABLED` gates every `/api/desktop` route (404 when off). Lock order is always environment, then session. Every stop bumps `execution_generation`.
- Desktop tests need a throwaway `TEST_DATABASE_URL` / `TEST_REDIS_URL`, never the dev DB.
- Game learning: `services/game_learning.py` (its module docstring is the spec). A model's guess never becomes truth without evidence.
- Relay network and paired machines (migration `e3a7c5d9f2b4`): unset `DESKTOP_CONTROLLER_BIND_HOST` = listen only on the owner-approved network; never a wildcard. Jophiel's own session starts need the machine's "Allow Jophiel to start sessions" switch (off by default) and the machine online; the owner's starts don't.

**Her mind** (`services/mind_turn.py`, `services/mind.py`, `services/mind_hard.py`, `app/ai/mind/`)
- One mind decides: every wake is one evaluation of the agent loop (`services/mind_turn.py`); recall results return into the same loop; silence, background thinking, continuity and coming back later are her tools. No code decides social behaviour (no speaking gaps, hush rules or canned topics); execution checks stay.
- Background mind over `Source`/`Body` contracts (`app/ai/mind/contracts.py`). Attention is per user: one hard thought at a time. Minecraft and her desktop's security records are sources; a conversation is not (messages wake her directly).
- Hard thoughts conclude (decision / guess / question / intention / feeling) and have no tools. Concluded thoughts reach memory as observations; anything to say becomes a wake of her mind.
- The planner is the one author of a game pack's plan.
- No code-defined "stuck" (2026-11-04, the user's decision): no time limit or try count decides it. Code keeps the
  effort record and picks when she looks back (a plan step ending, or every `MIND_STEP_LOOK_BACK_SECONDS` while one
  keeps going); her mind judges progress and the cause, and a look back that finds no progress asks her planner to rethink.

**Memory** (`services/memory_manager.py`, `services/recall*.py`, `services/memory_*.py`)
- Working memory (contexts, items, events, evidence), observation, shared recall, source-backed history, corrections, conflicts, forgetting, claims/entities/time, dependencies, transcript search and upkeep.
- Invariants: memory never picks a side in a conflict; a failed or partial search is never reported as `no_match`; observations are immutable; evidence links cascade, memories don't.

**Wakes and delivery** (`services/coordinator.py`, `services/autonomous_speech.py`)
- Her mind owns every decision (2026-11-13, the user's decision): there is no separate reading or decision model. She answers, asks, `stay_silent`, `think_it_over`, `come_back_to` (durable `coordinator_work` kind `wake`), notes questions, commitments and intentions in working memory, and decides learning's proposals.
- A wake that isn't a message is durable `coordinator_work` (`mind`, `wake`) dispatched as one chat job in its own conversation; `coordinator_outputs` records what she did (`said` / `silent` / `no_words`) and what was delivered.
- Before anything a wake produced is stored: no newer user message, its premise still open, nothing forgotten since, not already said in this quiet stretch.
- Tests refuse the dev DB (`tests/conftest.py`), give every test one fixed text personality, and script her mind's model run (`tests/test_coordinator.py`).

**Learning** (`services/practice.py`, `app/ai/practice.py`, `services/learning_capture.py`)
- Practice notes per app and tool, from a reviewer that sees what her hands did (steps, screens, pictures); capture of what each part was given and did. Notes are her own experience to weigh; what the user asks now comes first.

**Product rules**
- **Always on (2026-10-28, the user's decision):** no feature flags for her own parts. Flags remain only for outside setup or behaviour choices.
- **People on the phone (B2, 2026-11-22):** relationship proposals are shadow: nothing she reads
  or does uses them (`GET /api/memory/people/{id}/review` inspects them); requests for space are active at once. Her mind writes replies to others
  herself; a check model (`services/phone_reply_check.py`) reads each against a written policy and only that
  conversation's material before it goes out (and messages the user asks her to send, with their request). No
  content rule in code.
- **Phone privacy (2026-11-23):** someone who can put words on the user's
  screen never gains control of their phone. A grant stands only on the user's own recorded message (verified, and
  its source checked on every read); on owner turns there is no operation check (removed 2026-11-24, the user's
  decision: her judgement of what they asked; messages to other people still pass the outgoing check); a wake that can write to someone (and the phone moment before it) holds one
  conversation and nothing of the user's; a request for space stops her follow-ups; tools' model runs never stream
  (`agent.invoke_private`, `chat._her_words`). Checks log to `var/phone_checks.jsonl`.
- **Her own replies on the phone (B3, 2026-11-22):** no backend flag; the phone's Messages switch must be reported
  on (unknown = paused). Only to an exchange the wake is bound to (the message that woke her, or a follow-up's
  same person and conversation), through `services/phone_replies.py` only; never on a plain own turn.
- **Owner only (2026-11-01):** `/api/learning`, `/api/mind`, `/api/memory`, `/api/system` are guarded by `deps.require_owner`. Owner = `OWNER_EMAILS`.
- **System page** `/system`: design contract `.impeccable/surfaces/frontend-src-pages-systempage-tsx.md`, `PRODUCT.md`, `DESIGN.md`.

### Run the full stack locally
1. `docker compose up -d db` then `cd backend && uv run alembic upgrade head`
2. Backend: `cd backend && uv run uvicorn app.main:app --reload` (port 8000; `OPENAI_API_KEY` in `backend/.env`)
3. Frontend: `cd frontend && bun run dev` (port 5173) → open http://localhost:5173, sign up, go to chat.

The DB schema lives in `backend/app/db/models.py` and the migrations.

---

## Remaining TODO

---

## Notes / Context
