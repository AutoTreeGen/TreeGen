FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# uv (Astral) for fast dependency resolution.
RUN pip install --no-cache-dir uv

# Workspace metadata first for layer caching.
COPY pyproject.toml uv.lock README.md ./
COPY packages/shared-models/pyproject.toml packages/shared-models/
COPY services/notification-service/pyproject.toml services/notification-service/

# Resolve deps (no source yet — fast cold start on rebuild).
RUN uv sync --frozen --no-install-workspace --no-dev

# Source.
COPY packages/shared-models/src/ packages/shared-models/src/
COPY services/notification-service/src/ services/notification-service/src/

# Install workspace members.
RUN uv sync --frozen --no-dev

EXPOSE 8002

CMD ["uv", "run", "uvicorn", "notification_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
