"""FastAPI entry point для report-service (Phase 24.3).

Запуск:
    uv run uvicorn report_service.main:app --reload --port 8006
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from shared_models.observability import setup_logging, setup_sentry
from shared_models.security import apply_security_middleware

from report_service.api import health, relationship
from report_service.config import get_settings
from report_service.database import dispose_engine, init_engine

setup_logging(service_name="report-service")
setup_sentry(service_name="report-service", environment=os.environ.get("ENVIRONMENT"))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — report-service",
    description=(
        "Research-grade per-relationship PDF reports (Phase 24.3). "
        "Reuses Phase 15.6 court-ready layout vocabulary; carved out "
        "of parser-service per the supersedes-note in #180."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

apply_security_middleware(app, service_name="report-service")

app.include_router(health.router)
app.include_router(relationship.router)
