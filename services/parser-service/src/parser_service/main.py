"""FastAPI entry point.

Запуск:
    uv run uvicorn parser_service.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from parser_service.api import dedup, familysearch, imports, trees
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

# CORS для локального dev-режима веб-приложения (Phase 4.1).
# Прод-конфиг — на API gateway / Cloud Run, отдельным ADR в Phase 4.x.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# familysearch router включается ПЕРВЫМ среди /imports/* — иначе
# legacy `GET /imports/{job_id:UUID}` маршрут перехватит `/imports/familysearch`
# до того, как FastAPI попадёт в наш роутер (UUID-валидация даст 422
# вместо нашего 201/404).
app.include_router(familysearch.router, prefix="/imports", tags=["imports", "familysearch"])
app.include_router(imports.router, prefix="/imports", tags=["imports"])
app.include_router(trees.router, tags=["trees"])
app.include_router(dedup.router, tags=["dedup"])


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness probe — простая проверка что приложение запущено."""
    return {"status": "ok"}
