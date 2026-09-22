"""Application settings, loaded from environment / .env file."""
from functools import lru_cache
from typing import Literal

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
    chat_model: str = "gpt-4.1"
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
    memory_model: str = "gpt-4o-mini"
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
    stt_model: str = "whisper-1"
    # Voice turns use a faster model than typed chat: opus-4-8's time-to-first
    # -token is fine when you're reading and bad when you're waiting to hear a
    # reply. Set to None to use `chat_model` for voice too.
    voice_chat_model: str | None = "gpt-4.1-mini"

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
    



@lru_cache
def get_settings() -> Settings:
    return Settings()
