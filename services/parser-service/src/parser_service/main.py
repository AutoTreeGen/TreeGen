"""FastAPI entry point.

Запуск:
    uv run uvicorn parser_service.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from parser_service.api import imports, trees
from parser_service.config import get_settings
from parser_service.database import dispose_engine, init_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — parser-service",
    description="GEDCOM import + tree-read API (Phase 3).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(imports.router, prefix="/imports", tags=["imports"])
app.include_router(trees.router, tags=["trees"])


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness probe — простая проверка что приложение запущено."""
    return {"status": "ok"}
