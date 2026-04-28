FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install uv (Astral) for fast dependency resolution.
RUN pip install --no-cache-dir uv

# Copy workspace metadata first for layer caching.
# Each workspace member's pyproject.toml declares `readme = "README.md"`,
# so we ship the README too — hatchling reads it during install.
COPY pyproject.toml uv.lock README.md ./
COPY packages/gedcom-parser/pyproject.toml      packages/gedcom-parser/README.md      packages/gedcom-parser/
COPY packages/shared-models/pyproject.toml      packages/shared-models/README.md      packages/shared-models/
COPY packages/dna-analysis/pyproject.toml       packages/dna-analysis/README.md       packages/dna-analysis/
COPY packages/entity-resolution/pyproject.toml  packages/entity-resolution/README.md  packages/entity-resolution/
COPY packages/familysearch-client/pyproject.toml packages/familysearch-client/README.md packages/familysearch-client/
COPY packages/inference-engine/pyproject.toml   packages/inference-engine/README.md   packages/inference-engine/
COPY services/parser-service/pyproject.toml     services/parser-service/README.md     services/parser-service/

# Sync dependencies (no source yet — just resolve and download).
RUN uv sync --frozen --no-install-workspace --no-dev

# Copy source.
COPY packages/gedcom-parser/src/ packages/gedcom-parser/src/
COPY packages/shared-models/src/ packages/shared-models/src/
COPY packages/dna-analysis/src/ packages/dna-analysis/src/
COPY packages/entity-resolution/src/ packages/entity-resolution/src/
COPY packages/familysearch-client/src/ packages/familysearch-client/src/
COPY packages/inference-engine/src/ packages/inference-engine/src/
COPY services/parser-service/src/ services/parser-service/src/

# Now install workspace members + Cloud Tasks extra (Phase 13.0, ADR-0031).
# google-cloud-tasks is in parser-service's optional-dependencies.cloud-tasks;
# `uv pip install` adds it to the same .venv after the workspace sync.
RUN uv sync --frozen --no-dev \
 && uv pip install "google-cloud-tasks>=2.16"

# Cloud Run sets PORT (default 8080); fall back to 8000 for local parity.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn parser_service.main:app --host 0.0.0.0 --port ${PORT}"]
