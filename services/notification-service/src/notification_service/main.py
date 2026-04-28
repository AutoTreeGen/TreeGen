"""FastAPI entry point для notification-service (Phase 8.0 / ADR-0024).

Запуск:
    uv run uvicorn notification_service.main:app --reload --port 8002
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from shared_models.observability import configure_json_logging, init_sentry

from notification_service.api import health, notifications, preferences
from notification_service.config import get_settings
from notification_service.database import dispose_engine, init_engine

# Phase 13.1 — observability bootstrap (no-op без LOG_FORMAT_JSON / SENTRY_DSN).
configure_json_logging(service_name="notification-service")
init_sentry(
    service_name="notification-service",
    environment=os.environ.get("ENVIRONMENT"),
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — notification-service",
    description=("Channel-routed user notifications skeleton (Phase 8.0). См. ADR-0024."),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(notifications.router)
app.include_router(preferences.router)
