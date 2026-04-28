"""FastAPI entry point для billing-service (Phase 12.0).

Запуск:
    uv run uvicorn billing_service.main:app --reload --port 8003
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from billing_service.api import checkout, health, webhooks
from billing_service.config import get_settings
from billing_service.database import dispose_engine, init_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — billing-service",
    description="Stripe subscriptions + entitlement gating (Phase 12.0). См. ADR-0034.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(checkout.router)
app.include_router(webhooks.router)
