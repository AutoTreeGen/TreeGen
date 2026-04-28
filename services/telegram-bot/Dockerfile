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
COPY services/telegram-bot/pyproject.toml       services/telegram-bot/README.md       services/telegram-bot/

RUN uv sync --frozen --no-install-workspace --no-dev

COPY packages/shared-models/src/      packages/shared-models/src/
COPY packages/gedcom-parser/src/      packages/gedcom-parser/src/
COPY services/telegram-bot/src/       services/telegram-bot/src/

RUN uv sync --frozen --no-dev

ENV PORT=8006
EXPOSE 8006

CMD ["sh", "-c", "uv run uvicorn telegram_bot.main:app --host 0.0.0.0 --port ${PORT}"]  # pragma: allowlist secret
