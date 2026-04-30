"""FastAPI entry point для email-service (Phase 12.2).

Запуск:
    uv run uvicorn email_service.main:app --reload --port 8005
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from shared_models.security import apply_security_middleware

from email_service.api import health, send
from email_service.config import get_settings
from email_service.database import dispose_engine, init_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — email-service",
    description="Transactional email через Resend (Phase 12.2). См. ADR-0039.",
    version="0.1.0",
    lifespan=lifespan,
)

# Phase 13.2 (ADR-0053) — security middleware.
apply_security_middleware(app, service_name="email-service")

app.include_router(health.router)
app.include_router(send.router)
