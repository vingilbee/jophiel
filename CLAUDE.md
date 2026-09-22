# Project: MacroVision (Macronutrient Prediction App)

## Overview
A web app that predicts macronutrients (calories, protein, carbs, fat) from a photo of food using a trained ML model. Includes a chatbot for nutrition-related Q&A, and a dashboard where users track weight and calorie intake over time.

**Core features:**
- Upload/take a photo → model predicts macros
- Chatbot for diet/nutrition questions
- Dashboard: weight tracking, calorie intake tracking, historical charts
- User can manually log/edit weight and calorie entries

---

## Tech Stack
- **Frontend:** React (with Vite), package manager/runtime: **Bun** (use `bun install`, `bun run dev`, `bun add <pkg>` — not npm/yarn)
- **Backend:** Python, FastAPI, dependency/env manager: **uv** (use `uv sync`, `uv run <cmd>`, `uv add <pkg>` — not pip/poetry) `uv run uvicorn app.main:app --reload` 
- **Database:** PostgreSQL
- **ML Model:** PyTorch. Nutrition inference in `/app/ai/MacroVision` — fine-tuned ZoeDepth (depth) + an RGB/depth ViT cross-attention regressor, optionally combined with a Qwen3-VL VLM. Served via the `/api/predict` FastAPI endpoint. See the Decisions section for details.
- **Chatbot:** Columbina is a general-purpose personal companion/orchestrator backed by LangChain (`langchain-openai` + `langgraph`) → OpenAI (`gpt-4.1` by default). Domain capabilities such as nutrition live behind specialist sub-agents. Responses stream; code lives in `/app/ai`.
- **Auth:** (fill in — e.g. JWT, OAuth, Supabase Auth — TBD)
- **Image storage:** (fill in — e.g. S3, Cloudflare R2, or local disk for dev)

---

## Project Structure
```
/frontend                 # Vite + React 19 + TS, styled with Tailwind v4
  /src
    /components       # reusable UI: Logo, Button, ProtectedRoute, AppHeader, ProviderIcon, VoiceOrb, SenseStrip, CognitiveInspector
    /pages             # route-level pages: LandingPage, LoginPage, DashboardPage (voice), ChatPage, PredictPage, IntegrationsPage
    /audio             # mic capture worklet client, PCM playback, VAD, wake word
    /senses            # the eye: screen.ts (getDisplayMedia + sampling) + screen.worker.ts (diff/encode)
    /hooks             # custom React hooks: useAuth (session context), useChatSocket, useVoiceSession,
                       #   useAwareness (report senses up), useScreenSense (the eye toggle)
    /api               # API client: client.ts (fetch wrapper), auth.ts, predict.ts, chat.ts, integrations.ts
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
        auth.py
      deps.py           # shared deps (get_current_user, get_current_user_optional)
    /models            # (empty — nutrition inference lives in /ai/MacroVision)
    /ai                # chatbot agent (LangChain + Anthropic) + nutrition model
      agent.py          # ChatOpenAI model + build_agent(tools) / cached get_agent()
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
      /Cognitive
        /system         # local perception + intent interpretation; not wired into chat yet
          schemas.py    # versioned, expandable Pydantic contracts
          perception.py # deterministic observable-message feature extraction
          classifier.py # dependency-free local Naive Bayes intent classifier
          interpretation.py # classifier inference with safe rules fallback
          pipeline.py   # perception -> interpretation composition boundary
          /training     # strict JSONL loading, offline training, deterministic evaluation
          /data         # reviewed intent datasets (legacy data is not copied automatically)
          /artifacts    # versioned local classifier artifacts
      /tools           # MCP tool servers the agent can call
        /screen         # look_at_screen — NOT MCP; one tool on the orchestrator itself
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
                       #   salience.py: whether it's worth speaking first)
    main.py             # FastAPI app entrypoint
  pyproject.toml         # uv-managed deps
  uv.lock                # uv lockfile (committed)
  alembic/              # DB migrations (if using Alembic)

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

- **Auth:** Server-side sessions (not JWT). On signin a random opaque token is set as an `httpOnly` cookie; only its SHA-256 hash is stored in the `sessions` table. Passwords hashed with Argon2 (`pwdlib`). Endpoints under `/api/auth`: `signup`, `signin`, `signout`, `GET/DELETE /me`.
- **Image storage:** Local disk for now (revisit if scaling beyond local dev/single server)
- **Chatbot:** API-based (calling an external LLM API, not self-hosted). Implemented with **LangChain** — `langchain-openai`'s `ChatOpenAI` wrapped in a **LangGraph ReAct agent** (`langgraph.prebuilt.create_react_agent`). Default model `gpt-4.1` (override via `CHAT_MODEL`). Responses stream (`stream_chat`). Per-request `AgentRuntimeContext` carries user and integration state. Requires `OPENAI_API_KEY` in `.env`; the same key powers memory, transcription, and the optional OpenAI nutrition VLM. Model client + agent graph are built once (cached), not per-request.
- **ML training:** Lives in a **separate repo**, not in this codebase. This repo only does inference (loading trained weights and serving predictions).
- **Nutrition model (inference):** Lives in `/app/ai/MacroVision`. A `MacroVisionPredictor` loads three models once and serves many predictions: (1) fine-tuned **ZoeDepth** for a metric depth map, (2) a **cross-attention ViT regressor** (`architecture.py`: RGB ViT + depth ViT fused via cross-attention → 4 macros, de-normalized with `label_mean`/`label_std` from the checkpoint), (3) optional **Qwen3-VL-8B** VLM for a per-100g estimate. Two modes: `regression` (depth+ViT only) and `combined` (VLM per-100g scaled by the regressor's portion signal; falls back to regression if the VLM is off or returns bad JSON). Get the process-wide instance via `get_predictor()` (`@lru_cache`, mirrors `get_agent()`); results are a `NutritionPrediction` dataclass with `.to_dict()`. Weights in `MacroVision/weights/` are **gitignored** (3.6 GB, transferred manually). Config via `Settings`: `MACROVISION_DEVICE` (auto cuda>mps>cpu), `MACROVISION_LOAD_VLM`, `MACROVISION_VLM_BACKEND`, `MACROVISION_VLM_MODEL`, `MACROVISION_OPENAI_MODEL`, `OPENAI_API_KEY`, `MACROVISION_MAX_UPLOAD_MB`, `MACROVISION_WARM_ON_STARTUP`. Note: bitsandbytes 4-bit is CUDA-only — on mps/cpu the local VLM loads unquantized (heavy), so use `mode=regression`, `MACROVISION_LOAD_VLM=false`, or the OpenAI backend for light local runs.
- **VLM backend (combined mode):** The `combined` mode's per-100g VLM step has two interchangeable backends, selected by `MACROVISION_VLM_BACKEND` — **only the chosen one is loaded**. `qwen` (default): the local **Qwen3-VL** (accurate but heavy — ~8B download + local inference). `openai`: the **OpenAI multimodal API** (`app/ai/MacroVision/openai_vlm.py`, `OpenAIVLMGenerator`) — no local model download/inference, needs `OPENAI_API_KEY` and a vision-capable `MACROVISION_OPENAI_MODEL` (default `gpt-4o-mini`). Both produce the same estimate dict (shared prompt + JSON parsing in `MacroVision/vlm.py`), so `predict_combined` is unchanged; a backend that errors/returns bad JSON falls back to regression as before. Adding a third backend = implement the `VLMBackend` protocol and wire it in the predictor's `__init__`.
- **Predict endpoint:** `POST /api/predict` (auth'd via session cookie). `multipart/form-data` with a `file` image + `?mode=combined|regression`. Handler is thin: `services/prediction.py` validates (image-only, ≤`MACROVISION_MAX_UPLOAD_MB`, decodes with PIL → clean 400; model failures propagate as 5xx) and calls `predictor.predict_async` (runs blocking inference in a worker thread). Returns `PredictionOut` (`schemas/prediction.py`). Models load lazily on first request unless `MACROVISION_WARM_ON_STARTUP=true` warms them in the FastAPI lifespan. Requires the `python-multipart` dep. Does **not** persist — the frontend/user logs the result separately via the dashboard. Frontend page: `/predict` (`PredictPage`, `ProtectedRoute`) — drag-drop / camera upload, mode toggle, macro result panel; API client `src/api/predict.ts` uses `FormData` (not the JSON wrapper).
- **Integrations are a list, not per-provider special cases:** `app/ai/integrations/registry.py` holds an `IntegrationProvider` per connectable service — user-facing metadata (name, description, category, capabilities) plus four callables (`is_configured`, `get_connection`, `build_authorize_url`, `disconnect`). Providers self-register at import (`google/provider.py`; `app/ai/integrations/__init__.py` imports each so the registry is populated on package load). The API and the UI are both **data-driven from this registry**, so adding Slack/Notion/Outlook = new OAuth module + registry entry, with **no route or frontend change**. Endpoints: `GET /api/integrations` (list + this user's status), `GET /api/integrations/{provider}`, `GET /api/integrations/{provider}/authorize` (consent URL as JSON so a `fetch` can read it), `DELETE /api/integrations/{provider}` (idempotent). OAuth **callbacks stay provider-specific** since each provider's response differs — Google's is `GET /api/integrations/google/callback`, which always redirects to `{FRONTEND_BASE_URL}/integrations?provider=…&status=connected|error&reason=…` and never returns JSON.
- **Orchestrator + one sub-agent per toolset:** The agent the user talks to holds **no toolset tools**. Each MCP toolset is fronted by a specialist sub-agent, and the orchestrator gets exactly one tool per specialist (`food_vision_agent`, `gmail_agent`) that takes a plain-language task and returns a report. Specs live in `app/ai/subagents/registry.py`; `builder.py` turns a spec plus its live MCP tools into that delegation tool. Rationale: (1) **context** — Gmail alone is 15 tool schemas that would otherwise sit in every turn, including turns about protein; (2) **instructions** — Gmail's confirm-before-send rules and MacroVision's mode-selection rules are unrelated and would otherwise share one prompt that grows per integration. The cost is an extra LLM hop, so the orchestrator still answers ordinary nutrition questions itself and only delegates when a toolset is genuinely needed. Sub-agents run on `SUBAGENT_MODEL` (default `gpt-4.1-mini` — they do mechanical tool work, not open-ended reasoning) with a `subagent_recursion_limit` cap, and a failing sub-agent returns an error *string* so the orchestrator degrades instead of the turn dying. Rules the orchestrator must enforce itself (e.g. getting the user's approval before delegating a send — a sub-agent can't require something it never sees) go in the spec's `orchestrator_rules` and are injected into the orchestrator's prompt. **Stream isolation:** nested sub-agent runs don't emit into the parent's `astream(stream_mode="messages")` (verified against langgraph 1.2.7, even with callbacks propagated), but every sub-agent run is tagged `SUBAGENT_TAG` and `stream_chat` drops tagged chunks as a safety net, since tags *do* reach stream metadata. Adding a toolset = a registry entry + a `tools/<name>/config.json`, with no change to `chat.py`, `agent.py`, or `prompts.py`.
- **MacroVision as MCP (mounted, not spawned):** The nutrition model is exposed as a real MCP server (`app/ai/tools/macrovision/server.py`, 4 tools: `macrovision_predict_combined`, `macrovision_predict_regression`, `macrovision_estimate_depth`, `macrovision_list_food_photos`). Unlike Gmail it is **mounted into the FastAPI process** over streamable HTTP at `MACROVISION_MCP_PATH` (default `/mcp/macrovision`) rather than spawned per turn: a stdio subprocess would reload 3.6GB of ZoeDepth + ViT weights on *every chat turn*. Mounted, it shares the `get_predictor()` singleton and the agent dials it over loopback. Two-step wiring, and forgetting the second hangs every request: `mount_macrovision_mcp(app)` at construction **and** `macrovision_mcp_lifespan()` in the app lifespan (FastAPI does not run a mounted sub-app's lifespan). It still runs standalone over stdio for external MCP clients — `MACROVISION_USER_ID=<uuid> uv run python -m app.ai.tools.macrovision.server` — which loads its own copy of the models. **Identity:** tools act on one user's stored photos, so over HTTP the user comes from an HMAC-signed bearer scope token (`scope.py`), minted per turn. The key is per-process random by default (zero config, and the endpoint is useless to anyone who didn't get a token from this process); set `INTERNAL_MCP_SECRET` only if running several uvicorn workers, where a token minted by one worker would otherwise be rejected by another. Depth is exposed as its own tool because it's the geometry *behind* a macro prediction — a low `relief_m` means a flat scene the model had little volume signal from, which explains a bad portion estimate.
- **Image store (how the agent gets a photo):** Chat is text; a model can't be handed a JPEG in a prompt. So photos are written once to a per-user disk store (`app/services/image_store.py`) and referred to everywhere by a short opaque `image_id`. Layout is `<root>/<user_id>/<image_id>.<ext>` plus a sidecar `.json` — the user id is a **directory, not a column to filter on**, so a lookup physically cannot reach another user's file and the MCP server (which has no DB session) gets that isolation for free. Ids are random, not content hashes, so they can't be probed. Endpoints: `POST /api/images`, `GET /api/images`, `GET /api/images/{id}/raw`, `DELETE /api/images/{id}`. `/api/predict` now also persists its upload and returns the `image_id`, so a photo analyzed on the Predict page can be discussed in chat afterwards. Depth maps the agent renders are saved into the same store with `kind="depth"` and `derived_from`. Root is `IMAGE_STORE_DIR` (default `backend/var/images`, gitignored). Screen frames use the same store with `kind="screen"` — see the eye entry below for their (much shorter) retention.
- **Attaching photos in chat:** The WS `run` frame carries `image_ids` (ids, not pixels — the frame stays small and the agent hands the id straight to the food-vision specialist), capped at 8 per turn. The frontend uploads on pick rather than on send, so a slow upload can't stall the message. Ids are re-validated server-side against the user's store before reaching the agent, so a forged id becomes "no attachment" rather than a mid-turn tool error. Persisted in `chat_messages.image_ids` (JSONB) — a column rather than a marker inside `content`, so history stays clean text for the model while the frontend can still render thumbnails; `get_history` folds them back in as a trailing `[Attached image ids: …]` line so "the photo I sent earlier" survives into later turns.
- **Agent tools (general shape):** Two kinds. **Static** tools go in `BASE_TOOLS` in `app/ai/agent.py` (available to everyone, no per-user state). **Per-user** tools are backed by an account the user connected and are passed to `build_agent(tools)` per turn — they can't be module-level because they hold a request-scoped session. `get_agent()` (cached, no extra tools) still serves users with nothing connected, so they pay nothing. The split between *auth* and *tools* is deliberate: `app/ai/integrations/<provider>` owns OAuth and token lifecycle; `app/ai/tools/<toolset>` owns the MCP server that calls the provider. One integration can back several tool sets (Gmail today, Calendar/Drive later) without duplicating auth.
- **Google auth (integration):** Per-user OAuth 2.0 authorization-code flow in `app/ai/integrations/google`, implemented directly against Google's endpoints with `httpx` (no `google-auth-oauthlib`/`googleapiclient` — the flow is a few form posts and this keeps it async). `oauth.py` = protocol only (authorize URL, code exchange, refresh, userinfo, revoke, signed `state`); `credentials.py` = `get_access_token(db, user_id)`, the one entry point tools use, which refreshes on demand and self-heals by deleting grants Google reports as `invalid_grant`; `scopes.py` = requested scopes grouped by capability. Persistence is `services/google_account.py` → `google_accounts` table (one row per user). **Refresh tokens are encrypted, not hashed** (`app/core/crypto.py`, Fernet) — unlike session tokens we need the plaintext back. Rotating `GOOGLE_TOKEN_ENCRYPTION_KEY` invalidates all connections. Its registry entry is `google/provider.py`. The callback is authenticated **twice**: the signed `state` carries the user id that started the flow and must match the session cookie, which is what stops a forged callback attaching an attacker's mailbox to another account. Auth on the callback is *optional* (`get_current_user_optional`) so a session that expired mid-flow redirects instead of dumping a 401 body in the browser.
- **Gmail tools (MCP):** `app/ai/tools/gmail` in three layers — `gmail_api.py` (async httpx wrapper over Gmail REST v1; flattens MIME trees and header soup into LLM-readable dicts), `server.py` (a real **MCP server** via `FastMCP`, stdio transport, 15 tools), `client.py` (spawns it and adapts its tools to LangChain via `langchain-mcp-adapters`). The server is standalone and app-agnostic: it reads one Google access token from `GMAIL_ACCESS_TOKEN`, never touches the DB, and can be pointed at by Claude Desktop or any MCP client — `GMAIL_ACCESS_TOKEN=… uv run python -m app.ai.tools.gmail.server`. Tools: search / read / list labels / profile / send / reply / create-update-list-send-delete draft / modify labels / archive / mark read / trash. Label names are resolved to ids case-insensitively so the model can say "Receipts". **One MCP session is spawned per chat turn**, not pooled per user: Google access tokens expire hourly and a chat socket can outlive that, so per-turn guarantees a fresh token and stops a crashed subprocess wedging a conversation — the cost is subprocess startup, paid only by users who connected Gmail. Failures degrade rather than break: `gmail_tools_or_none` yields `[]` if the server won't start, and a rejected token surfaces as tool-result text the agent can recover from.
- **Gmail safety model:** Scopes requested are `gmail.modify` + `gmail.compose` + `gmail.send` (+ `openid`/`userinfo.email` for identity). `gmail.readonly` is deliberately omitted — `modify` already covers reading and every extra scope adds a consent-screen line. We never request permanent-delete: trashing is the most destructive thing possible, and it's recoverable. Send/reply/send-draft are gated by an explicit system-prompt rule (`app/ai/prompts.py`, injected **only** when the user actually has Gmail connected) requiring the model to show recipients/subject/body and get confirmation first — "draft an email to X" is not approval to send. Read tools carry `readOnlyHint`, trash/delete-draft carry `destructiveHint`, so an MCP client can enforce its own confirmation UI.
- **`mcp` is pinned to `>=1.24,<2`:** `langchain-mcp-adapters` 0.3.1 imports `mcp.server.fastmcp`, which mcp 2.0 removed. Unpin once the adapters release supports mcp 2.x.
- **Deployment:** Local only for now — no cloud deployment target yet.
- **CI:** Not set up yet.
- **Frontend:** Scaffolded with Vite + React 19 + TS, styled with **Tailwind v4** (`@tailwindcss/vite`, tokens in `index.css` via `@theme`). Dark/futuristic theme (black/grey, teal accent). Routing via `react-router-dom`. Auth state in a `useAuth` context that calls `/api/auth/me` on load; `/chat` and `/predict` are wrapped in `ProtectedRoute`. Dev server proxies `/api` → `localhost:8000` so the httpOnly session cookie is same-origin (no CORS needed in dev; CORS middleware is also configured for direct calls via `CORS_ORIGINS`).
- **Chat history (server-side persistence):** Chat history lives in Postgres, not on the client. Two tables (`conversations` + `chat_messages`, see DB Schema) store each thread and its ordered turns. The client sends **only the new user message** plus the `conversation_id` it belongs to (null → start a new conversation); the backend appends it, reconstructs the full history from the DB, prompts the agent, and persists the assistant reply too. Persistence is in `services/chat.py` (`get_or_create_conversation`, `add_message`, `get_history`, `list_conversations`, `delete_conversation`) — the AI layer (`app/ai`) stays stateless and still just takes a list of role/content turns. Conversation `title` is auto-derived from the first user message; `updated_at` is bumped on every message so lists sort by latest activity. Ownership is enforced everywhere (a stale/foreign `conversation_id` is treated as "start new", never leaking another user's thread).
- **Chat REST endpoints:** Under `/api/chat` (auth'd via `get_current_user`), let the frontend load prior history: `GET /conversations` (list, newest-updated first), `GET /conversations/{id}` (conversation + full messages), `DELETE /conversations/{id}`. Client wrappers in `frontend/src/api/chat.ts`. `ChatPage` loads the most recent conversation on mount and has a **New chat** button to start a fresh thread.
- **Voice lives on `/dashboard`, not in the chat header:** the spoken loop (`useVoiceSession`: wake → listen → think → speak → re-arm) is surfaced as a full page, `DashboardPage`, wrapped in `ProtectedRoute` and linked as the first header tab. It replaced a one-line mic/state readout in the chat header — voice state is the *only* feedback a hands-free user gets, and a small text label can't answer "is it hearing me?" from across the room. Each phase gets a distinct **kind** of motion in `components/VoiceOrb.tsx`, not just a colour: still (off), breathing (idle), mic-driven bars (listening), sweeping arc (thinking), bars + outward ripples (speaking); `prefers-reduced-motion` collapses it to a static orb. The orb is a `<canvas>` driven by `requestAnimationFrame` reading `voice.getLevel()` — a **getter, not state**, so a 60 Hz visualiser never re-renders the page. Level comes from the mic RMS while listening and from an `AnalyserNode` in `audio/playback.ts` while speaking (the mic can't be used for that: capture runs with echo cancellation, which is precisely what removes the assistant's voice from it). The page also exposes `endTurn` ("I'm done" — end the utterance without waiting for VAD silence) and `interrupt` (button barge-in, sharing the code path with talking-over-it), plus Space/Esc shortcuts. `ChatPage` is now typed-only; both pages share one socket and one server-side conversation thread, so a spoken question can follow on from a typed one.
- **Agent awareness (senses):** The agent has a running sense of the moment rather than existing only per question. Assembled in `app/services/awareness.py` from two sources: **facts we own** (the clock, and three small queries — last message, today's logged intake, last diary entry) and **senses the client reports** (`SenseReport`: IANA time zone, current route, mic state, and — once Phase 2 lands — screen sharing). The client half is `frontend/src/hooks/useAwareness.ts`, which is *change-driven, not polled*, and carries flags only: no pixels or audio ever travel this path. Split by cost on purpose — this "subconscious" layer runs always, uses no LLM, and renders to ~6 lines of text, while the expensive conscious layer (the orchestrator) just reads the summary. Rendered to a **plain string** by the service and carried on `AgentRuntimeContext.awareness`, so `app.ai` stays free of app state exactly as it takes history as role/content dicts; the service supplies facts, `prompts.py` (`_AWARENESS_PROMPT`) supplies the rules about them — including "never claim a sense you don't have", which is what stops the model offering to look at a screen it can't see. Client-reported senses are **descriptive only and never authorize anything** — the browser is asserting things about itself. "Today" is resolved in the *user's* zone, not the server's, so a Jakarta user's diary doesn't reset at the wrong midnight. `AwarenessSession` is per-connection and in memory: it describes this socket's moment and should die with it (a restart resetting "you've been here 12 minutes" is the correct answer, not lost data). Note `TimestampMixin.created_at` is `timestamp without time zone`, so `_as_utc` normalizes it on read rather than migrating the column.
- **The eye (agent sees the user's screen):** Three tiers, each ~10× cheaper than the next, so the eye can stay on. **(0) Capture + diff, client-side and free** — `senses/screen.ts` starts `getDisplayMedia` (user gesture + browser picker *is* the consent step) and samples every 2s; `senses/screen.worker.ts` does everything off the main thread: 64×64 luma downscale, per-cell delta, and — only if >0.5% of cells moved — the JPEG encode. An unchanged screen produces nothing at all. A 60s heartbeat forces one through so "nothing changed" and "the eye broke" aren't indistinguishable. The reference frame advances **only on a sent frame**, or gradual drift (a fade, a video) would never trip the threshold against its own previous frame. **(1) Upload** to the image store as `kind="screen"` (~1280px long edge — Anthropic downscales past ~1568 anyway). **(2) Caption, lazily at turn start** (`app/ai/vision.py`, Haiku): ~40 tokens of text vs ~1,300 for pixels, cached per `image_id` so only the first turn after a real screen change pays, and **nothing is captioned while the user is idle** — that's what "passive by default" means. **(3) Pixels on demand** via `look_at_screen`. Retention makes screen frames a *sense, not a recording*: `image_store.prune` enforces both a count (`screen_frame_keep=12`) and a TTL (`screen_frame_ttl_minutes=15`) on every write, and `kind="screen"` is excluded from the photo listing so sharing never pollutes the gallery. `selfBrowserSurface: "exclude"` keeps the app out of its own eye; `track.onended` (Chrome's "Stop sharing") flips the UI closed, because an open-eye indicator over a dead stream is the one thing that must never happen.
- **`look_at_screen` is on the orchestrator directly** — the deliberate exception to "one sub-agent per toolset". It's one tool (no context cost), it carries no instructions that would crowd another toolset's, and "what's this error?" is latency-sensitive in a way mail isn't: an extra LLM hop is exactly what would make an always-on eye feel sluggish. Built **per turn** in `chat.py` (it closes over the newest frame id — a cached one would show a stale screen) and injected only when `context.vision_enabled`, so users with the eye shut pay nothing and `get_agent()`'s cached tool-less agent still serves them. It returns a real **`image` content block**; langgraph passes a tool's `list[dict]` return through to `ToolMessage.content` untouched when every block type is in `TOOL_MESSAGE_BLOCK_TYPES` (`image` is) — verified live, the model reads the pixels. Capped at 3 looks per turn. **No history pruning is needed**, contrary to the original plan: `get_history` replays only persisted user/assistant *text*, so tool results (and their screenshots) never survive the turn they were made in.
- **Nudges (the agent speaking first):** What separates an assistant from a chatbot, and almost entirely a *restraint* problem — an agent that can interrupt feels alive at twice a day and unbearable at twice an hour, so the interesting code in `app/services/salience.py` is the rate limiting, not the rules. **The tick is rules, not a model**: `evaluate()` runs on the existing 30s awareness heartbeat, reads a struct, and returns None almost always — if deciding whether to speak cost an LLM call, an idle user would cost money all day, which is the exact thing awareness exists to avoid. A model is only involved *after* a rule fires, to write the sentence, pinned to `nudge_model` (Haiku) via `context.model_override` so an unrequested message never costs orchestrator money. Rules today: `streak_broken`, `intake_unlogged`, `late_and_under`, `food_on_screen` (the eye paying off — it reads the caption that already exists, and never captions on its own). Adding one = a `Rule` in `RULES`; the rule decides *whether*, its `Nudge.directive` tells the agent *what*, and the agent writes the wording. Global guards: never outside 08:00–23:00 **local**, never unless the user has already spoken this session (no ambushing) and has been quiet ≥90s (no talking over), never mid-voice-turn, and ≥`nudge_min_gap_seconds` since the last one. Silence for `nudge_ignored_after_seconds` counts as ignored: the gap **doubles per ignore** and two in a row mutes the session. An explicit `nudge_feedback` dismiss mutes immediately — silence is ambiguous, being told to stop is not. `NudgeMemory` is keyed by **user, not connection**, so a flaky network reconnecting can't reset cooldowns and turn one nudge into ten.
- **Two traps a nudge turn falls into, both fixed:** (1) A history ending in an assistant message is treated by Anthropic as a **prefill to continue** — the first working version cheerfully carried on its previous sentence about breakfast instead of mentioning the diary. Every nudge run therefore appends a synthetic closing user turn, `prompts.NUDGE_TRIGGER`, which is never persisted. (2) A persisted nudge puts **two assistant messages in a row** in history, which Anthropic rejects; `get_history` now merges consecutive same-role turns and drops a leading assistant turn. Delivery uses its own `nudge_start`/`nudge_token`/`nudge_end` frames, deliberately *not* `token`, so the client's request/response bookkeeping can never mistake an unprompted message for a reply it asked for — and it's spoken aloud only when a voice session is actually live and idle, because a silent app must never suddenly start talking.
- **Chat transport:** **WebSocket** at `/api/chat/ws` (persistent, handles many turns). Auth via the session cookie sent on the handshake — unauthenticated connections are closed with code 1008 before `accept`. Per turn, the socket validates and persists the user message, creates a durable `ChatJob`, and subscribes to the worker's Redis Stream; it does not own model generation. The external `python -m app.workers.chat` process claims leased jobs, streams `token`/specialist events into `macrovision:chat:events:<turn_id>`, persists the assistant outcome, and emits `done`, `stopped`, or `error`. Streams are replayed from the beginning for active jobs on reconnect, so a socket drop does not kill generation or lose already-produced tokens; Postgres is the source of truth for final history and job recovery. `stop` writes a Redis cancellation flag observed by the worker. When Redis is unavailable, local development falls back to the legacy in-process path; production must configure Redis and run the external worker. Outside of turns the socket also carries awareness: client → `{"type":"sense",...}` on change, server → `{"type":"state","awareness":{...}}` on connect, on each sense update, and on a 30s heartbeat. `useChatSocket` replays the last sense report on reconnect, since `AwarenessSession` is per-connection. Frontend: `useChatSocket` keeps one socket with auto-reconnect and exposes a promise-based `sendMessage(conversationId, content, onToken)` that resolves with `{ conversationId, reply }`. Vite dev proxy has `ws: true` so the upgrade is proxied to `localhost:8000`.
- **WhatsApp transport:** Meta Cloud API webhooks live at `/api/whatsapp/webhook` and are disabled unless explicitly configured. A user creates a short-lived link code in the Integrations UI and proves control by sending `link CODE`; the resulting `(phone_number_id, wa_id)` identity points to the user's explicit primary conversation, so web and WhatsApp share short-term history. Signed webhook bodies are deduplicated by `wamid` and stored in `channel_inbound_messages`. A durable debounce state batches sentence fragments for 1s (3s hard maximum) into one user message and one `ChatJob`; only one job per conversation is claimed at a time, and history is bounded by job creation order so later queued inputs cannot leak into an earlier prompt. Generation remains in the existing chat worker. Its assistant/message-memory/outbound-delivery writes are atomic, and a separate WhatsApp worker sends chunked `message_deliveries` with retry/fencing while delivery/read status webhooks update the outbox. Redis only wakes workers; Postgres remains authoritative.
- **Cognitive memory architecture:** `chat_messages` remains the complete transcript, while `services/chat.get_history` sends only the newest `SHORT_TERM_MEMORY_MESSAGES` (default 24) into a live turn. `working_memories` holds one derived state snapshot per conversation rather than duplicating that transcript. After each completed assistant reply, the route writes an idempotent `memory_jobs` outbox row and best-effort Redis wake-up. The worker extracts explicit durable facts into `semantic_memories` (the losslessly renamed former `long_term_memories` table) and time-bound events into `episodic_memories`; semantic records retain evidence counts and supporting/contradicting episode ids. `learning_memories` keeps predicted perception/interpretation/action separate from later outcomes and human/AI feedback. `core_memory` is a versioned, user-independent identity made of traits, perception priors, interpretation biases, drives, action style, values, learning policy, and locked fields; the migration seeds a conservative `columbina` core with automatic core mutation disabled. Retrieval currently uses semantic memory only, combining embedding cosine similarity, lexical overlap, importance, and confidence, and fails open when embeddings are unavailable. PostgreSQL is durable; Redis only reduces job latency.
- **Cognitive input system (experimental, isolated):** `app/ai/Cognitive/system` implements deterministic perception followed by local intent interpretation without an online model call. `Perception` keeps five stable fields (`is_question`, `is_request`, `is_directed_at_agent`, `novelty`, `ambiguity`) and an expandable map of typed signals carrying value, confidence, and source. `IntentInterpreter` lazily loads an optional versioned JSON Naive Bayes artifact and falls back safely to rules when it is absent or invalid. Training and holdout evaluation are offline and dependency-free. The old `Cognitive/cognitive_nodes` prototype remains reference-only: its message, perception, and label files have mismatched lengths and incompatible/imbalanced labels, so they must be manually reviewed before reuse. This new system is not yet connected to chat; action selection, core-memory influence, emotions, and orchestration are future stages.
- **Cognitive inspector:** The Chat page's `Cognitive test` button opens a right-side diagnostic panel. `POST /api/cognitive/inspect` is authenticated and runs the isolated local pipeline against a test message plus recent user-message context and attachment counts. It returns the complete typed perception and interpretation along with processing time, input counts, and an explicit zero online-call count. Inspection never creates a conversation, invokes the orchestrator, or changes live agent behavior.

### Run the full stack locally (to test the chatbot)
1. `docker compose up -d db` then `cd backend && uv run alembic upgrade head`
2. Backend: `cd backend && ANTHROPIC_API_KEY=sk-... uv run uvicorn app.main:app --reload` (port 8000)
3. Frontend: `cd frontend && bun run dev` (port 5173) → open http://localhost:5173, sign up, go to chat.

### Enable the Gmail integration (optional)
1. Google Cloud Console → **APIs & Services**:
   - Enable the **Gmail API**.
   - **OAuth consent screen**: External, add the Gmail + `userinfo.email` scopes, and add
     your own Google account under **Test users** (an unverified app only works for those).
   - **Credentials → Create OAuth client ID → Web application**, with authorized redirect URI
     `http://localhost:8000/api/integrations/google/callback` (must match `GOOGLE_REDIRECT_URI` exactly).
2. In `backend/.env` set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
   `GOOGLE_TOKEN_ENCRYPTION_KEY` (generate:
   `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
3. `uv run alembic upgrade head` to create `google_accounts`.
4. Sign in and open the **Integrations** tab → **Connect** on Google. After consenting you
   land back on `/integrations` with a result banner. The chat agent picks up the Gmail
   tools on the next turn — no restart needed.

Leaving `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` blank disables the integration cleanly: the card
renders as "unavailable" and the chatbot works exactly as before.

Note: `GMAIL_MCP_URL` and `GOOGLE_OAUTH_STATE_COOKIE_NAME` in `.env` are **unused** — the
Gmail base URL is a constant in `tools/gmail/gmail_api.py`, and OAuth `state` is HMAC-signed
rather than stored in a cookie. Safe to delete.

### DB Schema (initial draft)

**users**
| column      | type      | notes                  |
|-------------|-----------|------------------------|
| id          | UUID/PK   |                        |
| name        | string    |                        |
| email       | string    | unique, maybe optional |
| password    | string    | hashed, never plaintext |
| created_at  | timestamp |                        |

**weight_logs**
| column     | type      | notes              |
|------------|-----------|--------------------|
| id         | UUID/PK   |                    |
| user_id    | FK -> users.id |               |
| weight     | float     | units is kg        |
| date       | date      |                    |

**calorie_logs**
| column     | type      | notes              |
|------------|-----------|--------------------|
| id         | UUID/PK   |                    |
| user_id    | FK -> users.id |               |
| calories   | float     |                    |
| fat        | float     |                    |
| carbs      | float     |                    |
| protein    | float     |                    |
| date       | date      |                    |

**sessions**
| column     | type        | notes                              |
|------------|-------------|------------------------------------|
| id         | UUID/PK     |                                    |
| user_id    | FK -> users.id | ON DELETE CASCADE               |
| token_hash | string      | SHA-256 of cookie token, unique    |
| created_at | timestamptz |                                    |
| expires_at | timestamptz | session lifetime (default 7 days)  |

**google_accounts** *(a user's connected Google account — powers the Gmail tools)*
| column                  | type        | notes                                          |
|-------------------------|-------------|------------------------------------------------|
| id                      | UUID/PK     |                                                |
| user_id                 | FK -> users.id | ON DELETE CASCADE, **unique** (one per user) |
| google_sub              | string      | nullable; Google's stable account id           |
| email                   | string      | nullable; the connected mailbox                |
| encrypted_refresh_token | text        | Fernet ciphertext — encrypted, **not** hashed  |
| encrypted_access_token  | text        | nullable; cached to skip a refresh per turn    |
| access_token_expires_at | timestamptz | nullable                                       |
| scopes                  | text        | space-separated scopes Google actually granted |
| created_at / updated_at | timestamp   |                                                |

**conversations** *(a chatbot thread)*
| column     | type        | notes                                       |
|------------|-------------|---------------------------------------------|
| id         | UUID/PK     |                                             |
| user_id    | FK -> users.id | ON DELETE CASCADE, indexed               |
| title      | string      | nullable; auto-derived from 1st user message |
| created_at | timestamp   |                                             |
| updated_at | timestamp   | bumped on every message (sort by activity)  |

**chat_messages** *(one turn within a conversation)*
| column          | type        | notes                                  |
|-----------------|-------------|----------------------------------------|
| id              | UUID/PK     |                                        |
| conversation_id | FK -> conversations.id | ON DELETE CASCADE, indexed  |
| role            | string      | `user` or `assistant`                  |
| content         | text        |                                        |
| image_ids       | JSONB, null | ids of photos attached to this turn    |
| created_at      | timestamp   | history is ordered by this             |

**long_term_memories / memory_jobs** *(durable memory + consolidation outbox)*

`long_term_memories` stores user-scoped structured facts, stable keys, status,
importance/confidence, provenance, access metadata, expiry, and an optional JSONB
embedding. `memory_jobs` stores one idempotent worker job per completed assistant
message; job state and retry errors remain inspectable in PostgreSQL.

**meals / predictions** *(not fully specified yet — likely needs: id, user_id, image_path, predicted_calories, predicted_fat, predicted_carbs, predicted_protein, date/created_at — confirm before implementing)*

> Note: weight_logs and calorie_logs are separate tables even though both are per-user/per-date — keep them decoupled since a user might log weight without logging calories that day, or vice versa.

---

## Remaining TODO
- [x] **Awareness phase 1** — time + proprioception senses, `state`/`sense` WS frames, prompt block, orb breathing
- [x] **Awareness phase 2 (eyes)** — see the two Decisions entries above. Backend verified end-to-end; the browser capture path (`getDisplayMedia` → worker → upload) typechecks and builds but has only been exercised by a real browser manually.
- [ ] **Eye follow-ups** — tune `CHANGE_RATIO`/`CELL_DELTA` in `screen.worker.ts` against real usage (a single edited line of code at 4K may not trip 0.5%); a window-title blocklist to auto-pause capture over password managers and banking; a "what the eye has seen" log with a purge button.
- [x] **Awareness phase 3 (proactivity)** — see the two Nudges entries above. Verified live end-to-end.
- [ ] **Nudge follow-ups** — the `NudgeMemory` dict is unbounded and process-local (fine for local single-server; needs a TTL or Redis before anything else). Consider surfacing mute state in the UI so a user who dismissed can turn nudges back on without a reload.
- [ ] **Wake word "columbina"** — BLOCKED on two artifacts only Vincent's Picovoice Console account can produce: an **AccessKey** (→ `frontend/.env` as `VITE_PORCUPINE_ACCESS_KEY`) and a **`columbina.ppn` built for the WebAssembly platform** (→ `frontend/public/audio/`). Then implement `PorcupineWakeWord` against the existing `WakeWordDetector` interface in `audio/wakeword.ts`, with a ring buffer chunking capture frames to Porcupine's required 512 samples @ 16 kHz. Caveats: the AccessKey ships in the client bundle and is validated over the network; free-tier *custom* keywords are believed to expire (~30 days) and need regenerating — if that bites, a built-in keyword (`computer`, `jarvis`) is a one-line swap behind the same interface.
- [ ] Set up CI (lint/test on PR) — later
- [ ] Decide deployment target when ready to go beyond local — later

---

## Notes / Context
