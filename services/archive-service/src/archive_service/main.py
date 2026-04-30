"""FastAPI entry point archive-service (Phase 9.0 / ADR-0055).

Запуск:
    uv run uvicorn archive_service.main:app --reload --port 8003
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import Depends, FastAPI
from shared_models.security import apply_security_middleware

from archive_service.api import familysearch as fs_router
from archive_service.auth import get_current_claims
from archive_service.config import get_settings

_LOG: Final = logging.getLogger(__name__)


app = FastAPI(
    title="AutoTreeGen — archive-service",
    description=(
        "Read-only proxy к внешним генеалогическим архивам (Phase 9.0). "
        "Первый адаптер — FamilySearch. Запись в наше дерево — Phase 9.1+."
    ),
    version="0.1.0",
)

# Phase 13.2 (ADR-0053) — security middleware: CORS, rate limit, headers.
apply_security_middleware(app, service_name="archive-service")

# Все ручки FS-роутера требуют Clerk Bearer JWT.
_AUTH_DEPS = [Depends(get_current_claims)]
app.include_router(fs_router.router, dependencies=_AUTH_DEPS)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, object]:
    """Liveness probe + key flags для prod-alerting.

    Возвращаем флаги конфигурации, чтобы prod-monitoring мог пометить
    инстанс с ``familysearch_configured=false`` или ``token_storage_configured=false``
    как deploy-misconfig (без обязательного авторитета).
    """
    settings = get_settings()
    return {
        "status": "ok",
        "familysearch_configured": bool(
            settings.familysearch_client_id and settings.familysearch_redirect_uri,
        ),
        "token_storage_configured": bool(settings.token_encryption_key),
    }
