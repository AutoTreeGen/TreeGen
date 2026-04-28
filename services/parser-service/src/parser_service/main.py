"""FastAPI entry point.

Запуск:
    uv run uvicorn parser_service.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from parser_service.api import (
    dedup,
    dedup_attempts,
    familysearch,
    hypotheses,
    hypotheses_sse,
    imports,
    imports_sse,
    metrics,
    persons,
    sharing,
    sources,
    trees,
    waitlist,
)
from parser_service.config import get_settings
from parser_service.database import dispose_engine, init_engine
from parser_service.queue import close_arq_pool


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await close_arq_pool()
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
# Префикс — `/imports/familysearch` (Phase 5.1): все FS-эндпоинты
# объявлены относительно него (``""``, ``/oauth/start``, ``/me``, ...).
app.include_router(
    familysearch.router,
    prefix="/imports/familysearch",
    tags=["imports", "familysearch"],
)
# SSE-роутер до основного imports — у него специализированный
# /imports/{id}/events путь; основной /imports/{id} с плейсхолдером
# UUID матчит /events как сегмент пути если SSE подключён позже.
app.include_router(imports_sse.router, prefix="/imports", tags=["imports", "sse"])
app.include_router(imports.router, prefix="/imports", tags=["imports"])
app.include_router(trees.router, tags=["trees"])
app.include_router(sources.router, tags=["sources"])
app.include_router(dedup.router, tags=["dedup"])
app.include_router(dedup_attempts.router, tags=["dedup-attempts"])
# SSE роутер до основного hypotheses — у него специализированный путь
# /trees/{id}/hypotheses/compute-jobs/{id}/events. Порядок включения
# важен симметрично imports_sse vs imports (см. main.py выше).
app.include_router(hypotheses_sse.router, tags=["hypotheses", "sse"])
app.include_router(hypotheses.router, tags=["hypotheses"])
# persons router включается ПОСЛЕ trees (тот владеет `GET /persons/{id}`),
# но имена путей не пересекаются: тут `/persons/{id}/merge*`.
app.include_router(persons.router, tags=["persons", "merge"])
# Phase 11.0 — sharing endpoints (invitations, memberships).
# Включён после persons чтобы /trees/{id}/* пути в trees.router не
# перехватывали /trees/{id}/invitations / /trees/{id}/members.
app.include_router(sharing.router, tags=["sharing"])
# /metrics — Prometheus exposition (Phase 9.0). Без префикса, чтобы scrape
# конфиг был стандартным.
app.include_router(metrics.router, tags=["meta"])
# Phase 4.12: публичный POST /waitlist для лендинга (lead capture, без auth).
app.include_router(waitlist.router, tags=["waitlist"])


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness probe — простая проверка что приложение запущено."""
    return {"status": "ok"}
