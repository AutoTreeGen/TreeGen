FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
# Root project depends on shared-models + gedcom-parser; both must be
# installable workspace members for `uv sync` to succeed even though
# billing-service itself only needs shared-models.
COPY packages/shared-models/pyproject.toml      packages/shared-models/README.md      packages/shared-models/
COPY packages/gedcom-parser/pyproject.toml      packages/gedcom-parser/README.md      packages/gedcom-parser/
COPY services/billing-service/pyproject.toml    services/billing-service/README.md    services/billing-service/

RUN uv sync --frozen --no-install-workspace --no-dev

COPY packages/shared-models/src/ packages/shared-models/src/
COPY packages/gedcom-parser/src/ packages/gedcom-parser/src/
COPY services/billing-service/src/ services/billing-service/src/

RUN uv sync --frozen --no-dev

ENV PORT=8003
EXPOSE 8003

CMD ["sh", "-c", "uv run uvicorn billing_service.main:app --host 0.0.0.0 --port ${PORT}"]  # pragma: allowlist secret
