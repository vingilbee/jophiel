"""Application settings, loaded from environment / .env file."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # SQLAlchemy connection URL. Uses the psycopg (v3) driver.
    # Example: postgresql+psycopg://user:password@localhost:5432/macrovision
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/macrovision"
    )

    # Echo SQL to stdout (handy in local dev).
    db_echo: bool = False

    # --- Session auth ---
    session_cookie_name: str = "session"
    # Session lifetime in seconds (default 7 days).
    session_lifetime_seconds: int = 60 * 60 * 24 * 7
    # Set True when served over HTTPS so the cookie is only sent on TLS.
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"

    # --- CORS ---
    # Frontend origins allowed to call the API with credentials (cookies).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- Image store (food photos, and depth maps derived from them) ---
    # Root directory for stored images. A relative path resolves against the
    # backend package root. Gitignored — never commit user uploads.
    image_store_dir: str = "var/images"
    # How many images the agent's "list my recent photos" tool may return.
    image_list_max: int = 20
    # Reject decompression-bomb dimensions before Pillow allocates pixels.
    macrovision_max_image_pixels: int = 40_000_000

    # --- Blender assets and desktop bridge ---
    # Mesh/project uploads live separately from images and are only exposed to
    # the Blender specialist for the authenticated user.
    asset_store_dir: str = "var/assets"
    asset_max_upload_mb: int = 100
    asset_list_max: int = 20
    # The bridge is an explicit opt-in because it controls a desktop Blender
    # process. Keep it bound to loopback unless Blender is isolated elsewhere.
    blender_tools_enabled: bool = False
    blender_bridge_host: str = "127.0.0.1"
    blender_bridge_port: int = 9877

    # --- Vision (the eye: the agent seeing the user's screen) ---
    # Master switch. False ⇒ the toggle is unavailable and screen frames are
    # rejected, exactly as if the feature didn't exist.
    vision_enabled: bool = True
    # Screen frames are ephemeral by design — they are a live sense, not the
    # user's photo library. Each new frame prunes this user's older ones down
    # to the most recent `screen_frame_keep`, and anything past the TTL goes
    # regardless. Retaining a scrollback of someone's screen is not a feature.
    screen_frame_keep: int = 12
    screen_frame_ttl_minutes: int = 15
    # --- Nudges (the agent speaking first) ---
    # Master switch. False ⇒ the agent only ever replies, never initiates.
    nudges_enabled: bool = True
    # Floor between two unprompted messages. Doubles for each nudge the user
    # ignores — an assistant that isn't landing should get quieter, not keep
    # its rhythm. This number is the feature: an agent that interrupts well
    # twice a day feels alive, one that does it hourly gets muted forever.
    nudge_min_gap_seconds: float = 600
    # Silence for this long after a nudge counts as having ignored it.
    nudge_ignored_after_seconds: float = 300
    # Consecutive ignores before the agent stops initiating for the session.
    nudge_ignored_before_mute: int = 2
    # Nudges are one or two sentences; they should never cost orchestrator
    # money. None → whatever the turn would otherwise have used.
    nudge_model: str | None = "gpt-4.1-mini"

    # Model that writes the one-line description of a screen frame. Deliberately
    # a small one: this runs per *changed* frame the agent is asked about, and
    # its whole job is to compress ~1300 tokens of pixels into ~40 of text.
    screen_caption_model: str = "gpt-4.1-mini"

    # --- AI chatbot (LangChain + OpenAI) ---
    # Uses OPENAI_API_KEY below, shared with memory, vision, and speech.
    chat_model: str = "gpt-6-luna"
    # Sent only to models with reasoning controls (gpt-5*, gpt-6*, o*).
    chat_reasoning_effort: str | None = "none"
    # Optional override for the isolated, single-call cognitive inspector.
    cognitive_model: str | None = None
    chat_max_tokens: int = 4096
    # Only this many persisted messages are replayed into a live turn. The DB
    # remains the complete transcript; this is the agent's bounded working
    # memory, not a retention policy.
    short_term_memory_messages: int = 24
    # Model the per-toolset sub-agents run on. They do mechanical work — pick
    # the right tool, call it, summarize the result — rather than open-ended
    # reasoning, so a faster model is usually the right trade. None → chat_model.
    subagent_model: str | None = "gpt-4.1-mini"
    # Cap on a sub-agent's ReAct loop, so a confused sub-agent can't spin
    # indefinitely inside what the orchestrator sees as one tool call.
    subagent_recursion_limit: int = 12

    # --- Long-term memory ---
    long_term_memory_enabled: bool = True
    # A small model extracts durable, explicitly stated facts after a turn.
    # gpt-4o-mini ignored the rules on real game turns (it saved "the assistant doesn't have the
    # bread"); gpt-6-luna got 10/10 (2026-10-12). One call per reply, in the memory worker.
    memory_model: str = "gpt-6-luna"
    memory_embedding_model: str = "text-embedding-3-small"
    memory_retrieval_limit: int = 6
    # Redis is only a wake-up channel. Pending jobs also live in Postgres and
    # are polled by the worker, so they survive Redis/process restarts.
    memory_queue_name: str = "macrovision:memory:jobs"
    memory_worker_poll_seconds: float = 5.0

    # --- MacroVision nutrition model (image -> macros) ---
    # Torch device: "cuda", "mps", "cpu". If None, auto-detect (cuda > mps > cpu).
    macrovision_device: str | None = None
    # Load a vision-language model for the "combined" prediction mode. Disable to
    # run regression-only and save memory / startup time.
    macrovision_load_vlm: bool = True
    # The VLM backing "combined" mode (API-backed; no local model download).
    macrovision_vlm_backend: Literal["openai"] = "openai"
    # Retained for backwards-compatible configuration; local Qwen is unsupported.
    macrovision_vlm_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    # OpenAI model for the multimodal backend (must be vision-capable). Override
    # for a more capable model, e.g. "gpt-4o".
    macrovision_openai_model: str = "gpt-4o-mini"
    # OpenAI API key for the "openai" VLM backend. If None, the OpenAI SDK falls
    # back to the OPENAI_API_KEY environment variable.
    openai_api_key: str | None = None
    # --- Google OAuth (powers the Gmail integration) ---
    # Credentials from a Google Cloud "Web application" OAuth client.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # Account chooser hint only. Google API access requires OAuth, not passwords.
    google_account_username: str | None = None
    drive_tools_enabled: bool = True
    # Public HTTPS origin that routes /api/assets/slide-image/* to this backend.
    # Needed for Google to fetch local/generated images; no auth cookie required
    # on that one endpoint, which instead requires a 15-minute signed token.
    slide_image_public_base_url: str | None = None
    slide_image_model: str = "gpt-image-2"
    # Must be registered verbatim as an authorized redirect URI on that client.
    google_oauth_redirect_uri: str = (
        "http://localhost:8000/api/integrations/google/callback"
    )
    # Fernet key used to encrypt stored Google refresh tokens at rest, and to
    # sign the OAuth `state` parameter. Generate one with:
    #   uv run python -c "from cryptography.fernet import Fernet; \
    #       print(Fernet.generate_key().decode())"
    google_token_encryption_key: str | None = None
    # Where the OAuth callback sends the browser once the flow completes.
    frontend_base_url: str = "http://localhost:5173"
    # Expose the Gmail MCP tools to the chat agent (per-user; only takes effect
    # for users who have actually connected their Google account).
    gmail_tools_enabled: bool = True

    # Max accepted upload size for the /api/predict endpoint, in megabytes.
    macrovision_max_upload_mb: int = 10
    # Expose the MacroVision tools (predict / depth) to the chat agent. Unlike
    # Gmail this needs no per-user connection — every signed-in user has it.
    macrovision_tools_enabled: bool = True
    # Where the MacroVision MCP server is mounted inside *this* FastAPI app, and
    # the base URL the chat agent dials to reach it. It is the same process, so
    # this is a loopback call; point it at wherever uvicorn actually binds.
    # Mounting rather than spawning is deliberate — see CLAUDE.md.
    macrovision_mcp_path: str = "/mcp/macrovision"
    internal_mcp_base_url: str = "http://127.0.0.1:8000"
    # Shared secret signing the scope tokens that tell the mounted MCP server
    # which user a call is for. Leave unset for a per-process random secret —
    # correct and zero-config for a single uvicorn worker, which is the only
    # sane way to run this anyway (each worker would load its own 3.6GB of
    # weights). Set it if you ever do run several workers: otherwise a token
    # minted by one worker is rejected by whichever one the loopback call
    # reaches. Any long random string works.
    internal_mcp_secret: str | None = None
    # Load the models during app startup (blocks until ready) instead of lazily
    # on the first request. Keep False in dev to start fast; enable in prod.
    macrovision_warm_on_startup: bool = False

    # --- Voice (STT in, Columbina TTS out, over the chat WebSocket) ---
    # Master switch. When False the voice frames are rejected and the chat
    # socket behaves exactly as it did before.
    voice_enabled: bool = True
    # The GPT-SoVITS sidecar (tts_sidecar/server.py). Separate process because
    # it pins transformers<=4.50 while MacroVision's Qwen path needs newer, and
    # because its ~3.5GB resident shouldn't be held when voice is unused.
    tts_sidecar_url: str = "http://localhost:8787"
    tts_timeout_seconds: float = 60.0
    # Sample rate the sidecar emits (16-bit mono PCM). Must match engine output.
    tts_sample_rate: int = 32000
    # Determine whether Fillers Output enabled or not
    # Optional pre-rendered acknowledgement while the first clause is prepared.
    # Disabled by default: latency work should improve the real answer rather
    # than mask it with filler speech.
    tts_fillers_enabled: bool = False
    # Speech-to-text via the OpenAI audio API; reuses `openai_api_key`.
    # gpt-4o-mini-transcribe replaced whisper-1, which measured 0.6-3.1s per
    # utterance. Set STT_MODEL=whisper-1 to roll back.
    stt_model: str = "gpt-4o-mini-transcribe"
    # Voice turns use a faster model than typed chat: opus-4-8's time-to-first
    # -token is fine when you're reading and bad when you're waiting to hear a
    # reply. Set to None to use `chat_model` for voice too.
    voice_chat_model: str | None = "gpt-6-luna"
    # Per-stage timestamps for voice turns, appended as JSON lines by the
    # browser, API, and worker (see app/services/voice_latency.py). Ids, stage
    # names, and numbers only, never transcript or audio. Read with
    # `uv run python scripts/voice_latency_report.py`. Relative paths resolve
    # against backend/.
    voice_latency_marks_enabled: bool = True
    voice_latency_log_path: str = "var/voice_latency.jsonl"

    redis_url: str | None = None
    chat_queue_name: str = "macrovision:chat:jobs"
    chat_event_stream_prefix: str = "macrovision:chat:events:"
    chat_worker_lease_seconds: int = 900
    chat_worker_heartbeat_seconds: int = 30
    file_deletion_queue_name: str = "macrovision:file_deletion:jobs"

    # --- WhatsApp Cloud API ---
    # The webhook is inert until all required credentials are configured.
    whatsapp_enabled: bool = False
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_display_phone_number: str | None = None
    whatsapp_graph_api_version: str = "v23.0"
    whatsapp_link_code_ttl_seconds: int = 600
    whatsapp_debounce_seconds: float = 1.0
    whatsapp_max_batch_seconds: float = 3.0
    whatsapp_delivery_timeout_seconds: float = 15.0
    whatsapp_delivery_max_attempts: int = 8
    whatsapp_webhook_max_bytes: int = 1_000_000
    whatsapp_worker_poll_seconds: float = 1.0
    whatsapp_queue_name: str = "macrovision:whatsapp:wakeups"

    # --- Cognitive live shadow (observational only; see
    # app/ai/Cognitive/system/v0/DURABLE_LIVE_SHADOW_TRACE_V0_IMPLEMENTATION.md) ---
    # Kill switch. False ⇒ no capture, no shadow worker thread. Existing
    # captures and traces are retained untouched (pending jobs wait, unmodified,
    # until the feature is re-enabled or retention/deletion removes them).
    cognitive_live_shadow_enabled: bool = False
    # --- Simple cognition, live (docs/COLUMBINA_LIVE_INTENTION_PATH_DESIGN.md; services/cognitive_live.py) ---
    # Both off by default: with both off, chat is exactly as before.
    # Her character page (v0.6) and typed, scoped memories in the reply model's prompt.
    cognitive_live_character: bool = False
    # Student 1's reading (acts, request, tone) in the prompt: "off", "on", or "ab" (per conversation,
    # half with and half without, for comparing). Notes and logs run whenever this isn't "off".
    cognitive_live_student: Literal["off", "on", "ab"] = "off"
    # Trained Student 1 (student_one.pt); None = var/cognitive_simple/teacher-1-2/models-1.0.
    cognitive_live_student_dir: str | None = None
    # Reads her actual reply after it's sent, for the notes (offered / committed / answered). None = subagent_model.
    cognitive_live_events_model: str | None = None
    cognitive_live_log_path: str = "var/cognitive_live.jsonl"
    # Deterministic per-turn sample over hash(sampling policy version, turn_id).
    cognitive_live_shadow_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    cognitive_live_shadow_sampling_policy_version: str = Field(
        default="shadow-sampling-v0.1", min_length=1
    )
    cognitive_live_shadow_queue_name: str = "macrovision:cognitive-shadow:wakeups"
    cognitive_live_shadow_worker_lease_seconds: int = Field(default=300, gt=0)
    cognitive_live_shadow_worker_poll_seconds: float = Field(default=2.0, gt=0)
    cognitive_live_shadow_max_attempts: int = Field(default=3, gt=0)
    # True ⇒ `python -m app.workers` also runs a shadow thread (local/staging).
    # For production isolation set False and run the shadow worker as its own
    # process, `python -m app.workers.cognitive_shadow`, so shadow CPU work
    # never competes with chat generation for the same interpreter.
    cognitive_live_shadow_run_in_shared_worker: bool = True
    # No default on purpose: captures are sensitive conversation data, and
    # capture refuses to run until a reviewed retention period is configured.
    cognitive_live_shadow_retention_days: int | None = Field(default=None, gt=0)
    # The configured application locale recorded with each capture. Nothing
    # detects language; V0 compiles only "en" and skips anything else.
    cognitive_live_shadow_language_tag: str = Field(default="en", min_length=1)
    # The core identity whose version lineage every personality projection
    # must share.
    cognitive_live_shadow_core_memory_name: str = "columbina"
    # Where a capture's personality projection comes from. No automatic
    # fallback between the two:
    # - "runtime_mapper": compile the active CoreMemory with Personality
    #   Runtime Mapper V0 at capture time (the normal source);
    # - "reviewed_baseline": use only the reviewed baseline file below
    #   (explicit compatibility / emergency staging rollback).
    # Changing this does not enable capture; the kill switch above does.
    cognitive_live_shadow_personality_source: Literal["runtime_mapper", "reviewed_baseline"] = "runtime_mapper"
    # Reviewed baseline projection (JSON matching ShadowPersonalityProjection),
    # read only in "reviewed_baseline" mode. It can never claim
    # projection_kind=runtime_mapper.
    cognitive_live_shadow_personality_baseline_path: str | None = None

    # Required semantic parser for every live-shadow observation. There is no
    # deterministic parser fallback. It never enters the live chat path. The
    # provider receives bounded messages plus the code-owned predicate/evidence
    # schema -- no memory, relationship, personality, biases, goals, actions,
    # or runtime facts.
    # Experimental shadow appraisal; deterministic remains the default.
    # Opt-in combined shadow orchestration; legacy stages remain replayable.
    cognitive_shadow_orchestration: Literal["staged", "two_call"] = "staged"
    cognitive_shadow_two_call_protocol: Literal["legacy-v0.1", "context-conditioned-two-call-v0.1", "cognitive-two-call-v1"] = "context-conditioned-two-call-v0.1"
    cognitive_shadow_orchestration_max_output_tokens: int = Field(default=6000, ge=1000, le=12000)
    cognitive_shadow_appraisal_implementation: Literal["deterministic", "model"] = "deterministic"
    cognitive_shadow_appraisal_model: str | None = "gpt-6-luna"
    cognitive_shadow_appraisal_timeout_seconds: int = Field(default=20, gt=0, le=120)
    cognitive_shadow_semantic_parser_model: str = "gpt-6-luna"
    cognitive_shadow_semantic_parser_timeout_seconds: int = Field(default=20, gt=0, le=120)
    # Two-call v1: reasoning for the reader (Call 1) only; "none" sends the model's default setting.
    cognitive_shadow_call_one_reasoning_effort: Literal["none", "low", "medium"] = "none"

    # --- Collaborative Content V0 inside the cognitive shadow (see
    # app/ai/Cognitive/system/v0/CONVERSATIONAL_WORKSPACE_AND_COLLABORATIVE_CONTENT_V0_IMPLEMENTATION.md) ---
    # Shadow-only structured proposals for conversational deliverables (meal
    # plans, outlines, summaries). Proposals stay on the shadow trace and are
    # never delivered. Disabled => every deliverable is a typed
    # provider_not_configured failure that Action can only decline. An explicit
    # model id is required; there is no fallback to any other model.
    cognitive_shadow_collaboration_enabled: bool = False
    cognitive_shadow_collaboration_model: str | None = None
    cognitive_shadow_collaboration_timeout_seconds: int = Field(default=25, gt=0, le=120)

    # --- Realization V0 inside the cognitive shadow (see
    # app/ai/Cognitive/system/v0/REALIZATION_V0_IMPLEMENTATION.md) ---
    # Shadow-only wording of the selected Action plan. Candidates are persisted
    # on the shadow trace for review and are never delivered to chat; no
    # setting permits delivery in V0. Enabling this does not enable live shadow
    # (the kill switch above does), and disabling it leaves the core shadow
    # trace unchanged.
    cognitive_shadow_realization_enabled: bool = False
    # Default cognitive model; an explicitly empty/missing id when enabled yields provider_unavailable.
    # There is deliberately no fallback to chat_model, cognitive_model, or a
    # sub-agent model. Uses `openai_api_key`.
    cognitive_shadow_realization_model: str | None = "gpt-6-luna"
    cognitive_shadow_realization_timeout_seconds: int = Field(default=20, gt=0, le=120)
    # At most two single-call generation attempts per turn (V0 ceiling).
    cognitive_shadow_realization_max_attempts: int = Field(default=2, ge=1, le=2)
    cognitive_shadow_realization_max_characters: int = Field(default=1200, gt=0, le=4000)

    # --- Dialogue Working Memory V0 (see
    # app/ai/Cognitive/system/v0/DIALOGUE_WORKING_MEMORY_V0_IMPLEMENTATION.md) ---
    # Short-term, conversation-scoped dialogue state for the cognitive shadow.
    # Shadow-only: enabling it never enables live shadow, Realization, or any
    # live-response change, and it never writes long-term, relationship,
    # episodic, core, or learning memory. False ⇒ no update jobs are scheduled
    # and live-shadow captures stay at cognitive-shadow-capture-v0.2.
    cognitive_dialogue_memory_enabled: bool = False
    # Interpreted dialogue relationships (Call 1 registry v1.7): the shadow
    # worker stores each turn's option sets, same-referent links and
    # corrections, and later captures (cognitive-shadow-capture-v0.4) carry
    # them. Needs dialogue memory; shadow-only like it.
    cognitive_dialogue_interpretation_enabled: bool = False
    # Newest contiguous messages a first-use bootstrap reconstructs; older
    # history is an explicit omission, never presented as complete.
    cognitive_dialogue_memory_bootstrap_messages: int = Field(default=30, ge=1, le=500)
    # May lower, never raise, the contract cap of 200 items per snapshot.
    cognitive_dialogue_memory_max_items: int = Field(default=200, ge=1, le=200)
    # Optional acceleration only; PostgreSQL stays authoritative and every
    # cache mode yields identical snapshots. Uses `redis_url`.
    cognitive_dialogue_memory_cache_enabled: bool = False
    cognitive_dialogue_memory_cache_ttl_seconds: int = Field(default=3600, gt=0, le=86400)
    cognitive_dialogue_memory_queue_name: str = "macrovision:dialogue-memory:wakeups"
    cognitive_dialogue_memory_worker_lease_seconds: int = Field(default=120, gt=0)
    cognitive_dialogue_memory_worker_poll_seconds: float = Field(default=2.0, gt=0)
    cognitive_dialogue_memory_max_attempts: int = Field(default=5, gt=0)

    # --- Desktop agent (docs/DESKTOP_AGENT_IMPLEMENTATION_PLAN.md) ---
    # Off by default: the /api/desktop routes answer 404 and the UI hides the
    # panel. Enabling it creates no VM and sends no input on its own.
    desktop_agent_enabled: bool = False
    # Trusted game profiles. A relative path resolves against the backend root.
    desktop_profiles_dir: str = "desktop_profiles"
    # Upper bound for one gameplay session's requested duration.
    desktop_max_session_seconds: int = Field(default=2 * 60 * 60, gt=0, le=24 * 60 * 60)
    # Plan bounds (plan §17): steps per plan revision.
    desktop_plan_max_steps: int = Field(default=12, ge=1, le=50)
    # Controller relay (plan §5.1): a separate process the game VM dials over
    # TLS. Bind it to the Mac's LAN address only (e.g. 192.168.1.76), never
    # 0.0.0.0; the default keeps it on this machine.
    desktop_controller_bind_host: str = "127.0.0.1"
    desktop_controller_port: int = Field(default=8765, ge=1, le=65535)
    # The address the VM dials, shown with pairing codes. Defaults to the bind host.
    desktop_controller_public_host: str | None = None
    # The relay's self-signed certificate and key. Relative paths resolve against the backend root.
    desktop_relay_dir: str = "var/desktop_relay"
    desktop_pairing_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    desktop_pairing_max_attempts: int = Field(default=5, ge=1, le=20)
    # Driver model (stage D, plan §8.5): plays one step per call. No fallback:
    # a missing or failing driver pauses the session.
    desktop_driver_backend: str = "openai"
    desktop_driver_model: str = "gpt-6-luna"
    # Sent only to models with reasoning controls (gpt-5*, gpt-6*, o*); "none"
    # keeps a step to one quick answer instead of seconds of thinking.
    desktop_driver_reasoning_effort: str | None = "none"
    desktop_driver_image_detail: str = Field(default="auto", pattern="^(low|high|auto)$")
    desktop_driver_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    desktop_driver_max_tokens: int = Field(default=400, ge=64, le=2000)
    # A frame older than this is never decided on.
    desktop_driver_freshness_ms: int = Field(default=3000, ge=200, le=10_000)
    # Spending bound: model steps per session before it pauses ("budget").
    desktop_driver_max_steps: int = Field(default=1500, ge=1, le=100_000)
    desktop_driver_metrics_path: str = "var/desktop_driver.jsonl"
    # Opt-in tuning aid: when set, every driver step's frame, prompt and raw
    # reply are saved here (one folder per session). Off by default: it keeps
    # screenshots of the VM on disk. Relative paths resolve against the backend root.
    desktop_driver_debug_dir: str | None = None
    # Stage D2b: Columbina acting on game events.
    # Goals she may pick herself per session when playing on her own.
    desktop_max_auto_goals: int = Field(default=10, ge=0, le=100)
    # After a goal is done, how long the driver waits for her next one.
    desktop_planning_wait_seconds: float = Field(default=60.0, ge=5, le=600)
    # Minimum gap between unprompted messages she sends in occasional mode (a third
    # of it in commentary mode; needs-you ignores it, danger waits only 8 s).
    agent_say_min_gap_seconds: float = Field(default=20.0, ge=0, le=3600)
    # Stage G: game packs. The Minecraft pack (game_packs/minecraft) is her own
    # player, driven by skills instead of keys. Its API is loopback-only on the
    # Mac; the token is PACK_API_TOKEN if the pack has one set, else the file the
    # pack writes at start. Only this user gets the pack (empty = nobody), since
    # it is one Minecraft account on one machine.
    minecraft_pack_owner_email: str = ""
    minecraft_pack_url: str = "http://127.0.0.1:8790"
    minecraft_pack_token: str = ""
    minecraft_pack_token_file: str = "../game_packs/minecraft/var/api_token"
    # Spending bound for skill decisions per session (each is one model call).
    minecraft_pack_max_decisions: int = Field(default=400, ge=1, le=10_000)
    # Game learning (services/game_learning.py): skill outcomes and notices are
    # recorded per session; when a session ends, one reflection call turns them
    # into an episode, lessons (applied to her hands automatically) and code
    # proposals (shown on the Learning page, never applied by the app).
    game_learning_enabled: bool = True
    # The A/B switch for measuring learning: False keeps recording and
    # reflecting but leaves lessons out of her hands' prompt.
    game_learning_apply_lessons: bool = True
    game_learning_model: str = "gpt-6-luna"
    # Sent only to models with reasoning controls. Reflection is rare and
    # benefits from thinking, unlike a per-step driver call.
    game_learning_reasoning_effort: str | None = "low"
    # A stretch of play with fewer skill outcomes than this is too short to
    # learn from (when the session goes offline or ends). A pause, often brief,
    # waits for a longer stretch so it doesn't split one up.
    game_learning_min_outcomes: int = Field(default=5, ge=1, le=1000)
    game_learning_segment_outcomes: int = Field(default=15, ge=1, le=1000)
    # Lessons shown to her hands per run, and active lessons kept per game.
    game_learning_prompt_lessons: int = Field(default=12, ge=0, le=50)
    game_learning_max_active_lessons: int = Field(default=40, ge=1, le=500)
    game_learning_queue_name: str = "macrovision:game-learning:wakeups"
    # Teaching herself a move (stage H1, services/game_practice.py): she writes a
    # program over her skills, the pack's sandbox runs it, a critic checks the
    # counted result, and she fixes it, up to MAX_TRIES times. Started only from a
    # turn where the user spoke. Each try is a writer call and a critic call.
    game_practice_enabled: bool = True
    game_practice_model: str = "gpt-6-luna"
    # Writing a program benefits from thinking; it isn't on the moment-to-moment path.
    game_practice_reasoning_effort: str | None = "low"
    game_practice_max_tries: int = Field(default=4, ge=1, le=10)
    game_practice_program_seconds: int = Field(default=180, ge=10, le=600)
    game_practice_max_tokens: int = Field(default=6000, ge=500, le=32000)
    game_practice_queue_name: str = "macrovision:game-practice:wakeups"
    # A move that worked is tidied once (one more call on the same model): fixed positions
    # and ids looked up instead, leftovers gone. The original is kept until the tidy works.
    game_practice_tidy_enabled: bool = True
    # Ask the user before learning (offer_to_learn in background turns). Off since
    # 2026-10-18 (the user's choice, "for now"): she starts lessons herself and says so.
    game_practice_ask_first: bool = False
    # Explore and learn (stage H3, services/game_challenges.py): when a goal is done she
    # picks her own next challenge (one call), does it with her skills, and learns what
    # they lack. Per session it can be switched off ("stop exploring"); this turns it off
    # everywhere. No budget yet, by the user's choice: the report shows the cost.
    game_explore_enabled: bool = True
    game_explore_model: str = "gpt-6-luna"
    game_explore_reasoning_effort: str | None = "low"
    # Mind tick recording (app/ai/mind): while a pack session is live, write what
    # each ~1 s tick of her mind would see to MIND_TICK_PATH, so candidate gates
    # (how hard to think) can be compared offline with scripts/mind_gate_eval.py.
    # Observation only: nothing reads it back into play.
    mind_tick_recording_enabled: bool = False
    # Her mind (docs/COLUMBINA_MIND_DESIGN.md): every tick a gate decides how hard
    # she should think (none/react/light/hard); quick thoughts use a fast model,
    # hard ones are a background Luna game turn. Replaces game-event wakeups for
    # commentary (danger, warnings, discoveries) while on. Needs DESKTOP_AGENT_ENABLED.
    mind_enabled: bool = False
    mind_gate: str = Field(default="jev", pattern="^(jev|decisions)$")
    # How sure the gate must be that a moment needs thought (light and hard together).
    mind_think_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # Light thoughts need reasoning (why, what next, what the user wants): Luna. React ones
    # need speed: a small model.
    mind_quick_model: str = "gpt-6-luna"
    mind_react_model: str = "gpt-4.1-mini"
    # After a reply in a game, if the game changed while she said it, a small model checks
    # whether that made something she said wrong, and if so she corrects herself out loud
    # (services/game_correction.py).
    game_corrections_enabled: bool = True
    game_correction_model: str = "gpt-6-luna"
    # Quiet thinking: after this long with no thought, and nothing urgent, she thinks ahead,
    # about the user or about herself (never describing the moment). 0 turns it off.
    mind_quiet_seconds: float = Field(default=25.0, ge=0, le=3600)
    mind_quick_gap_seconds: float = Field(default=4.0, ge=0, le=600)
    mind_hard_gap_seconds: float = Field(default=20.0, ge=0, le=3600)
    # Hard thinking (docs/COLUMBINA_HARD_THINKING_PLAN.md): slow thinking about a problem that ends
    # in one conclusion. Time isn't short there, so it reasons. Quiet hard thinking: after this long
    # with nothing new, at most once per QUIET_EVERY (0 turns it off).
    mind_hard_model: str = "gpt-6-luna"
    mind_hard_reasoning_effort: str | None = Field(default="low", pattern="^(none|low|medium|high)$")
    mind_hard_quiet_seconds: float = Field(default=180.0, ge=0, le=3600)
    mind_hard_quiet_every_seconds: float = Field(default=300.0, ge=0, le=86400)
    mind_hard_log_path: str = "var/mind_hard.jsonl"
    # A conversation as a source of her mind, in shadow (services/mind_conversation.py): after a reply,
    # a question of theirs still open becomes a hard thought written only to MIND_SHADOW_LOG_PATH
    # (never saved, said or shown). Needs COGNITIVE_LIVE_CHARACTER (its notes). Costs one model call each.
    mind_conversation_shadow_enabled: bool = False
    mind_shadow_log_path: str = "var/mind_shadow.jsonl"
    mind_thought_retention_days: int = Field(default=7, ge=1, le=365)
    mind_log_path: str = "var/mind.jsonl"
    # Her planner (docs/COLUMBINA_MIND_DESIGN.md "Planning"): the one author of her game plan.
    # On: a new goal from a reply, stuck, struggling, the last step and a finished goal all go to
    # one planning call (reasoning on, no tools), which keeps or revises the plan; replies set
    # goals and one-step commands, background turns and hard thoughts no longer write plans.
    # Off: every turn writes plans as before. Needs MIND_ENABLED.
    mind_planner_enabled: bool = False
    mind_plan_model: str = "gpt-6-luna"
    mind_plan_reasoning_effort: str | None = "low"
    # Her inner state, live but without influence (docs/COLUMBINA_INNER_STATE_PLAN.md step 5.2;
    # services/inner_state_live.py): after each delivered reply a background thread reads the
    # exchange (two model calls: the teacher's thought, then appraisal-v3.1 labels) and moves her
    # mood (app/ai/mind/mood_layers.py, frozen config). Shown in the chat page's debug strip and at
    # GET /api/mind/state; nothing reads it into a reply, thought, plan or action.
    mind_inner_state_enabled: bool = False
    mind_inner_state_model: str = "gpt-6-luna"
    mind_inner_state_log_path: str = "var/inner_state.jsonl"
    mind_tick_path: str = "var/mind_ticks.jsonl"
    # Her working memory (docs/COLUMBINA_MEMORY_SESSION1_SPEC.md; services/memory_manager.py):
    # contexts, goals that outlive their steps, commitments, intentions, questions and recent
    # events, in Postgres. On: every reply reads it and gets the memory tools, game plans point
    # at the goal they serve, and the mind loop runs its due checks (needs MIND_ENABLED for those).
    memory_manager_enabled: bool = False
    memory_intention_hours: float = Field(default=2.0, gt=0, le=720)
    memory_context_idle_hours: float = Field(default=2.0, gt=0, le=720)
    memory_manager_log_path: str = "var/memory_manager.jsonl"
    # Gates under test: Jev from TypeSafe directly (or through OpenRouter),
    # Strands Decider served locally.
    typesafe_api_key: str = ""
    typesafe_base_url: str = "https://api.typesafe.ai/v1"
    openrouter_api_key: str = ""
    strands_decider_url: str = "http://127.0.0.1:8765"


@lru_cache
def get_settings() -> Settings:
    return Settings()
