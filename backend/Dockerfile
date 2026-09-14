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

# Install deps first (cached) from the lockfile — reproducible, no dev deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code + Alembic config/migrations.
COPY . .

# Default: serve the API. The migrate service overrides this command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
