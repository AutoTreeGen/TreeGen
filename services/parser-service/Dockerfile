FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv (Astral) for fast dependency resolution.
RUN pip install --no-cache-dir uv

# Copy workspace metadata first for layer caching.
COPY pyproject.toml uv.lock README.md ./
COPY packages/gedcom-parser/pyproject.toml packages/gedcom-parser/
COPY packages/shared-models/pyproject.toml packages/shared-models/
COPY packages/dna-analysis/pyproject.toml packages/dna-analysis/
COPY services/parser-service/pyproject.toml services/parser-service/

# Sync dependencies (no source yet — just resolve and download).
RUN uv sync --frozen --no-install-workspace --no-dev

# Copy source.
COPY packages/gedcom-parser/src/ packages/gedcom-parser/src/
COPY packages/shared-models/src/ packages/shared-models/src/
COPY packages/dna-analysis/src/ packages/dna-analysis/src/
COPY services/parser-service/src/ services/parser-service/src/

# Now install workspace members.
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "parser_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
