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

from fastapi import FastAPI
from shared_models.observability import configure_json_logging, init_sentry

from dna_service.api import consents, kits, matches, uploads
from dna_service.config import get_settings
from dna_service.database import dispose_engine, init_engine

# Phase 13.1 — observability bootstrap (no-op без LOG_FORMAT_JSON / SENTRY_DSN).
configure_json_logging(service_name="dna-service")
init_sentry(
    service_name="dna-service",
    environment=os.environ.get("ENVIRONMENT"),
)

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

app.include_router(consents.router)
app.include_router(uploads.router)
app.include_router(matches.router)
app.include_router(kits.router)


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
