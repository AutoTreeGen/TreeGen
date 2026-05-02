"""FastAPI entry point.

Запуск:
    uv run uvicorn parser_service.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from shared_models.observability import setup_logging, setup_sentry
from shared_models.security import apply_security_middleware

from parser_service.api import (
    ai_extraction,
    audio_consent,
    audio_sessions,
    chat,
    clerk_webhooks,
    dedup,
    dedup_attempts,
    digest,
    ego_anchor,
    export_audit,
    familysearch,
    hypotheses,
    hypotheses_sse,
    imports,
    imports_sse,
    metrics,
    normalize,
    persons,
    public_share,
    relationships,
    safe_merge,
    sharing,
    sources,
    trees,
    users,
    waitlist,
)
from parser_service.auth import get_current_claims
from parser_service.config import get_settings
from parser_service.court_ready import router as court_ready_router
from parser_service.database import dispose_engine, init_engine
from parser_service.queue import close_arq_pool

# Phase 13.1b — observability. Идемпотентно при пустом SENTRY_DSN /
# отсутствии LOG_FORMAT_JSON: в local-dev оба вызова no-op.
setup_logging(service_name="parser-service")
setup_sentry(service_name="parser-service", environment=os.environ.get("ENVIRONMENT"))


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

# Phase 13.2 (ADR-0053) — CORS, rate limit, security headers, body-size cap.
# Origins берутся из env CORS_ORIGINS; default — http://localhost:3000.
apply_security_middleware(app, service_name="parser-service")

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
# Phase 10.7a (ADR-0068): self-anchor + ego-relationship endpoint.
# Включён до 15.1 relationships.router чтобы 4-сегментный
# ``/trees/{id}/relationships/{person_id}`` не маскировался —
# хотя FastAPI и так разрешает по числу сегментов, явный порядок надёжнее.
app.include_router(ego_anchor.router, tags=["trees", "ego-anchor"], dependencies=_AUTH_DEPS)
# Phase 15.1 (ADR-0058): relationship-level evidence aggregation. Включён
# до sharing-router'а, чтобы /trees/{id}/relationships/... маршрут не
# перехватывался /trees/{id}/* generic'ами.
app.include_router(relationships.router, tags=["relationships"], dependencies=_AUTH_DEPS)
# Phase 10.2 (ADR-0059) — AI source extraction. Auth required;
# permission gate (EDITOR) проверяется внутри ручек через resolve
# source → tree.
app.include_router(ai_extraction.router, tags=["sources", "ai"], dependencies=_AUTH_DEPS)
# Phase 10.3 (ADR-0060) — AI normalization for places + names.
# Auth required; нет tree-scoped permission'а (вход — личная raw-строка).
# Cost-guards (kill switch + per-user-day rate limit + per-month tokens budget)
# ставятся внутри ручек, выровнено с 10.2.
app.include_router(normalize.router, tags=["ai", "normalization"], dependencies=_AUTH_DEPS)
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
# Phase 5.7b — Safe Merge applier. Path /api/v1/trees/{tree_id}/merge —
# единственный 5-сегментный путь под /api/v1/, никаких маршрут-collision'ов
# с другими /trees/{id}/* (4 сегмента) роутерами.
app.include_router(safe_merge.router, tags=["trees", "merge"], dependencies=_AUTH_DEPS)
# Phase 5.9 — Export Audit (pre-export loss preview). Stateless multipart:
# принимает .ged + список target_platforms, возвращает per-platform findings.
# Reuses Phase 5.6 compatibility rules. Auth required (по конвенции router-deps).
app.include_router(export_audit.router, tags=["gedcom", "audit"], dependencies=_AUTH_DEPS)
# Phase 10.9a — voice-to-tree (ADR-0064). Включён до sharing чтобы
# /trees/{id}/audio-* пути не перехватывались sharing-router'ом или
# trees-router'ом /trees/{id}/* generic'ами. Auth required; permission
# gates (OWNER для consent, EDITOR/VIEWER для sessions) — внутри ручек.
app.include_router(audio_consent.router, tags=["voice", "consent"], dependencies=_AUTH_DEPS)
app.include_router(audio_sessions.router, tags=["voice", "sessions"], dependencies=_AUTH_DEPS)
# Phase 10.7c — AI tree-chat (SSE-streamed). Включён до sharing/users/etc
# чтобы /trees/{id}/chat/* пути не перехватывались generic'ами. Permission
# gate (VIEWER+) на router-level через require_tree_role внутри ручки.
app.include_router(chat.router, tags=["chat", "ai", "sse"], dependencies=_AUTH_DEPS)
# Phase 11.0 — sharing endpoints (invitations, memberships). Auth required.
# Включён после persons чтобы /trees/{id}/* пути в trees.router не
# перехватывали /trees/{id}/invitations / /trees/{id}/members.
app.include_router(sharing.router, tags=["sharing"], dependencies=_AUTH_DEPS)
# Phase 11.1 — public invitation lookup (GET /invitations/{token}) без auth:
# UI accept-landing нужно показать tree+inviter ДО Clerk sign-in. Token —
# secret 122-bit UUIDv4. Симметрично ``public_share.router_public``.
app.include_router(sharing.router_public, tags=["sharing", "public"])
# Phase 11.2 — public share-link управление (owner-side). Auth required.
app.include_router(
    public_share.router_owner,
    tags=["sharing", "public"],
    dependencies=_AUTH_DEPS,
)
# Phase 11.2 — public read-only вид по token. БЕЗ auth, rate-limited внутри ручки.
app.include_router(public_share.router_public, tags=["public"])
# Phase 4.10b (ADR-0038): /users/me account settings + GDPR action requests.
app.include_router(users.router, tags=["users", "settings"], dependencies=_AUTH_DEPS)
# /metrics — Prometheus exposition (Phase 9.0). Без префикса, чтобы scrape
# конфиг был стандартным. Без auth — scrape под network ACL.
app.include_router(metrics.router, tags=["meta"])
# Phase 4.12: публичный POST /waitlist для лендинга (lead capture, без auth).
app.include_router(waitlist.router, tags=["waitlist"])
# Clerk webhooks — отдельный путь /webhooks/clerk (Phase 4.10, ADR-0033).
# Аутентификация — Svix HMAC внутри ручки, не Bearer.
app.include_router(clerk_webhooks.router, tags=["auth", "webhooks"])
# Phase 15.6 — Court-Ready Report. POST /api/v1/reports/court-ready.
# Auth required — VIEWER+ роль на tree персоны проверяется внутри ручки
# (resolve Person → Tree). Supersedes-note к ADR-0058: report endpoints
# живут здесь, не в отдельном report-service.
app.include_router(court_ready_router, tags=["reports", "court-ready"], dependencies=_AUTH_DEPS)
# Phase 14.2 — internal digest-summary endpoint. Auth — service-token
# через ``X-Internal-Service-Token`` header (зеркало telegram-bot /notify).
# БЕЗ ``_AUTH_DEPS``: caller — telegram-bot worker, не end-user.
app.include_router(digest.router, tags=["digest", "internal"])


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness probe — простая проверка что приложение запущено."""
    return {"status": "ok"}
