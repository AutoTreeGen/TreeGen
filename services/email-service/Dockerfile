FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY packages/shared-models/pyproject.toml      packages/shared-models/README.md      packages/shared-models/
COPY packages/gedcom-parser/pyproject.toml      packages/gedcom-parser/README.md      packages/gedcom-parser/
COPY services/email-service/pyproject.toml      services/email-service/README.md      services/email-service/

RUN uv sync --frozen --no-install-workspace --no-dev

COPY packages/shared-models/src/      packages/shared-models/src/
COPY packages/gedcom-parser/src/      packages/gedcom-parser/src/
COPY services/email-service/src/      services/email-service/src/
# Phase 12.2 — Jinja2 templates рядом с пакетом (loader использует path).
COPY services/email-service/templates/ services/email-service/templates/

RUN uv sync --frozen --no-dev

# Phase 13.1b — non-root execution per ADR-0032.
RUN groupadd --system app \
 && useradd --system --gid app --home-dir /app --no-create-home appuser \
 && chown -R appuser:app /app
USER appuser

ENV PORT=8005
EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"8005\")}/healthz').read()" || exit 1

CMD ["sh", "-c", "uv run uvicorn email_service.main:app --host 0.0.0.0 --port ${PORT}"]
