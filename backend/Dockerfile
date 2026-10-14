# Backend image — used for both running the API and applying migrations.
# Uses uv for dependency management (https://docs.astral.sh/uv/).
FROM python:3.13-slim

# Copy the uv binary from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# The lockfile includes a GitHub-sourced dependency, so uv needs git during
# the dependency-install layer. CA certificates are required for HTTPS clones.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install deps first (cached) from the lockfile — reproducible, no dev deps.
COPY pyproject.toml uv.lock ./
# Local path dependency (the desktop agent's action contract) must exist before sync.
COPY desktop_controller ./desktop_controller
RUN uv sync --frozen --no-dev --no-install-project

# App code + Alembic config/migrations.
COPY . .

# Default: serve the API. The migrate service overrides this command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
