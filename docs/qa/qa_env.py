"""Isolated QA configuration. Never imports the personal .env by default."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ARTIFACTS = ROOT / "docs" / "qa" / "artifacts"
TEMP = Path("/private/tmp/macrovision-qa-20260907")


def configure():
    sys.path.insert(0, str(BACKEND))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    TEMP.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "DATABASE_URL": "postgresql+psycopg://postgres:qa-disposable-only@127.0.0.1:55432/macrovision_qa",
        "REDIS_URL": "redis://127.0.0.1:56379/0",
        "IMAGE_STORE_DIR": str(TEMP / "images"),
        "ASSET_STORE_DIR": str(TEMP / "assets"),
        "SESSION_COOKIE_NAME": "qa_session",
        "SESSION_COOKIE_SECURE": "false",
        "CORS_ORIGINS": '["http://127.0.0.1:54173"]',
        "FRONTEND_BASE_URL": "http://127.0.0.1:54173",
        "INTERNAL_MCP_BASE_URL": "http://127.0.0.1:58000",
        "OPENAI_API_KEY": "qa-placeholder-no-real-key",
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "GOOGLE_OAUTH_CLIENT_SECRET": "",
        "GOOGLE_TOKEN_ENCRYPTION_KEY": "",
        "MACROVISION_WARM_ON_STARTUP": "false",
        "MACROVISION_LOAD_VLM": "false",
        "MACROVISION_TOOLS_ENABLED": "false",
        "BLENDER_TOOLS_ENABLED": "false",
        "GMAIL_TOOLS_ENABLED": "false",
        "DRIVE_TOOLS_ENABLED": "false",
        "LONG_TERM_MEMORY_ENABLED": "false",
        "NUDGES_ENABLED": "false",
        "VOICE_ENABLED": "false",
        "VISION_ENABLED": "true",
    })

