FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
# Root project depends on shared-models + gedcom-parser, so both must be
# installable workspace members at sync time. Copy enough metadata for
# every member referenced from the root closure.
COPY packages/shared-models/pyproject.toml packages/shared-models/README.md packages/shared-models/
COPY packages/gedcom-parser/pyproject.toml packages/gedcom-parser/README.md packages/gedcom-parser/
COPY packages/dna-analysis/pyproject.toml  packages/dna-analysis/README.md  packages/dna-analysis/
COPY services/dna-service/pyproject.toml   services/dna-service/README.md   services/dna-service/

RUN uv sync --frozen --no-install-workspace --no-dev

COPY packages/shared-models/src/ packages/shared-models/src/
COPY packages/gedcom-parser/src/ packages/gedcom-parser/src/
COPY packages/dna-analysis/src/ packages/dna-analysis/src/
COPY services/dna-service/src/ services/dna-service/src/

RUN uv sync --frozen --no-dev \
 && uv pip install "sentry-sdk[fastapi]>=2.20"

ENV PORT=8001
EXPOSE 8001

CMD ["sh", "-c", "uv run uvicorn dna_service.main:app --host 0.0.0.0 --port ${PORT}"]
