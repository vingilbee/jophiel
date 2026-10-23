"""FastAPI application entrypoint."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.limiter import limiter

from app.ai.tools.macrovision import macrovision_mcp_lifespan, mount_macrovision_mcp
from app.api.routes import (
    assets, auth, chat, dashboard, desktop, images, integrations, learning, memory, mind, predict,
    whatsapp,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optionally warm the (heavy) MacroVision models at startup so the first
    # prediction request isn't slow. Loading is blocking, so run it off the loop.
    if settings.macrovision_warm_on_startup:
        from app.ai.MacroVision import get_predictor

        logger.info("Warming MacroVision models…")
        await asyncio.to_thread(get_predictor)
        logger.info("MacroVision models ready")

    # The live Student 1 (only when COGNITIVE_LIVE_STUDENT is on/ab), loaded in the background for the
    # local WebSocket fallback path; the chat worker warms its own copy.
    from app.services import cognitive_live
    asyncio.get_running_loop().run_in_executor(None, cognitive_live.warm)

    # The mounted MacroVision MCP server's session manager. FastAPI doesn't run
    # a mounted sub-app's lifespan, so it has to be entered here — without this
    # every MCP request to it hangs.
    async with macrovision_mcp_lifespan():
        yield


app = FastAPI(title="MacroVision API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/api/capabilities", tags=["health"])
def capabilities() -> dict[str, bool]:
    """Expose server-gated client capabilities without requiring a session."""
    return {"voice_enabled": settings.voice_enabled, "desktop_enabled": settings.desktop_agent_enabled}

# Mounted at construction time so the session manager exists by the time the
# lifespan above runs.
if settings.macrovision_tools_enabled:
    mount_macrovision_mcp(app)

# Allow the frontend (different origin in dev) to call the API with cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(predict.router, prefix="/api")
app.include_router(images.router, prefix="/api")
app.include_router(assets.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(whatsapp.router, prefix="/api")
app.include_router(desktop.router, prefix="/api")
app.include_router(learning.router, prefix="/api")
app.include_router(mind.router, prefix="/api")
app.include_router(memory.router, prefix="/api")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
