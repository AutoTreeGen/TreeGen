"""FastAPI entry point для dna-service (Phase 6.2 / ADR-0020).

Запуск:
    uv run uvicorn dna_service.main:app --reload --port 8001
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import Depends, FastAPI
from shared_models.observability import setup_logging, setup_sentry
from shared_models.security import apply_security_middleware

from dna_service.api import (
    consents,
    dna_matches,
    kits,
    match_list_import,
    matches,
    triangulation,
    uploads,
)
from dna_service.auth import get_current_claims
from dna_service.config import get_settings
from dna_service.database import dispose_engine, init_engine

# Phase 13.1b — observability. См. parser-service/main.py.
setup_logging(service_name="dna-service")
setup_sentry(service_name="dna-service", environment=os.environ.get("ENVIRONMENT"))

_LOG: Final = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    if not settings.require_encryption:
        _LOG.warning(
            "DNA_SERVICE_REQUIRE_ENCRYPTION=false — service is accepting plaintext "
            "uploads. DO NOT USE THIS IN PRODUCTION (see ADR-0020)."
        )
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — dna-service",
    description="DNA consent management + encrypted storage + matching API (Phase 6.2).",
    version="0.1.0",
    lifespan=lifespan,
)

# Phase 13.2 (ADR-0053) — security middleware: CORS, rate limit, headers.
apply_security_middleware(app, service_name="dna-service")

# Phase 4.10 (ADR-0033): все DNA endpoint'ы требуют Bearer JWT.
# Router-level dependency срабатывает до ручки и возвращает 401 при
# отсутствии/инвалидном токене. Endpoint'ы используют свои Depends на
# ``RequireUser`` для получения users.id UUID.
_AUTH_DEPS = [Depends(get_current_claims)]

app.include_router(consents.router, dependencies=_AUTH_DEPS)
app.include_router(uploads.router, dependencies=_AUTH_DEPS)
app.include_router(matches.router, dependencies=_AUTH_DEPS)
app.include_router(kits.router, dependencies=_AUTH_DEPS)
app.include_router(dna_matches.router, dependencies=_AUTH_DEPS)
app.include_router(match_list_import.router, dependencies=_AUTH_DEPS)
app.include_router(triangulation.router, dependencies=_AUTH_DEPS)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, object]:
    """Liveness probe + key flags для prod-alerting.

    `dna_encryption_required` поле — критичное для prod monitoring,
    позволяет пометить инстанс с `false` как deploy-misconfig.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "dna_encryption_required": settings.require_encryption,
    }
