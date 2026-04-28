"""FastAPI entry point.

Запуск:
    uv run uvicorn parser_service.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from parser_service.api import (
    clerk_webhooks,
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
from parser_service.auth import get_current_claims
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

# Phase 4.10 (ADR-0033): большинство user-facing routers требуют
# Bearer JWT через router-level dependency. Endpoint'ы внутри получают
# users.id UUID через ``Depends(parser_service.auth.get_current_user_id)``.
# Исключения:
#   * ``/healthz`` — liveness, всегда public.
#   * ``/metrics`` — Prometheus scrape с network ACL.
#   * ``/webhooks/clerk`` — Svix-signed, аутентифицируется HMAC'ом, не JWT.
#   * SSE-роутеры — accept ?token= query param (browsers не дают
#     custom headers на EventSource), отдельный depends внутри ручек.
#   * OAuth callback внутри familysearch — приходит из браузер-redirect'а,
#     state в cookie заменяет Bearer; внутри ручки cookie-only.
_AUTH_DEPS = [Depends(get_current_claims)]

# familysearch router включается ПЕРВЫМ среди /imports/* — иначе
# legacy `GET /imports/{job_id:UUID}` маршрут перехватит `/imports/familysearch`
# до того, как FastAPI попадёт в наш роутер (UUID-валидация даст 422
# вместо нашего 201/404).
# Префикс — `/imports/familysearch` (Phase 5.1): все FS-эндпоинты
# объявлены относительно него (``""``, ``/oauth/start``, ``/me``, ...).
# Auth — на ручке, не на роутере: oauth_callback аутентифицируется
# state-cookie, не Bearer (браузер-redirect).
app.include_router(
    familysearch.router,
    prefix="/imports/familysearch",
    tags=["imports", "familysearch"],
)
# SSE-роутер до основного imports — у него специализированный
# /imports/{id}/events путь; основной /imports/{id} с плейсхолдером
# UUID матчит /events как сегмент пути если SSE подключён позже.
app.include_router(imports_sse.router, prefix="/imports", tags=["imports", "sse"])
app.include_router(imports.router, prefix="/imports", tags=["imports"], dependencies=_AUTH_DEPS)
app.include_router(trees.router, tags=["trees"], dependencies=_AUTH_DEPS)
app.include_router(sources.router, tags=["sources"], dependencies=_AUTH_DEPS)
app.include_router(dedup.router, tags=["dedup"], dependencies=_AUTH_DEPS)
app.include_router(dedup_attempts.router, tags=["dedup-attempts"], dependencies=_AUTH_DEPS)
# SSE роутер до основного hypotheses — у него специализированный путь
# /trees/{id}/hypotheses/compute-jobs/{id}/events. Порядок включения
# важен симметрично imports_sse vs imports (см. main.py выше).
app.include_router(hypotheses_sse.router, tags=["hypotheses", "sse"])
app.include_router(hypotheses.router, tags=["hypotheses"], dependencies=_AUTH_DEPS)
# persons router включается ПОСЛЕ trees (тот владеет `GET /persons/{id}`),
# но имена путей не пересекаются: тут `/persons/{id}/merge*`.
app.include_router(persons.router, tags=["persons", "merge"], dependencies=_AUTH_DEPS)
# Phase 11.0 — sharing endpoints (invitations, memberships). Auth required.
# Включён после persons чтобы /trees/{id}/* пути в trees.router не
# перехватывали /trees/{id}/invitations / /trees/{id}/members.
app.include_router(sharing.router, tags=["sharing"], dependencies=_AUTH_DEPS)
# /metrics — Prometheus exposition (Phase 9.0). Без префикса, чтобы scrape
# конфиг был стандартным. Без auth — scrape под network ACL.
app.include_router(metrics.router, tags=["meta"])
# Phase 4.12: публичный POST /waitlist для лендинга (lead capture, без auth).
app.include_router(waitlist.router, tags=["waitlist"])
# Clerk webhooks — отдельный путь /webhooks/clerk (Phase 4.10, ADR-0033).
# Аутентификация — Svix HMAC внутри ручки, не Bearer.
app.include_router(clerk_webhooks.router, tags=["auth", "webhooks"])


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness probe — простая проверка что приложение запущено."""
    return {"status": "ok"}
