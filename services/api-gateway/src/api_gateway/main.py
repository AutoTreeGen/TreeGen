"""FastAPI entry point для api-gateway (Phase 15.4).

Запуск:
    uv run uvicorn api_gateway.main:app --reload --port 8007
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from shared_models.observability import setup_logging, setup_sentry
from shared_models.security import apply_security_middleware

from api_gateway.api import health, proposals
from api_gateway.auth import get_current_claims
from api_gateway.config import get_settings
from api_gateway.database import dispose_engine, init_engine

# Phase 13.1b — observability. См. parser-service/main.py.
setup_logging(service_name="api-gateway")
setup_sentry(service_name="api-gateway", environment=os.environ.get("ENVIRONMENT"))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — api-gateway",
    description="Tree-domain workflow endpoints (Phase 15.4 genealogy git). См. ADR-0062.",
    version="0.1.0",
    lifespan=lifespan,
)

# Phase 13.2 (ADR-0053) — CORS, rate limit, security headers, body-size cap.
apply_security_middleware(app, service_name="api-gateway")

# Phase 4.10 (ADR-0033): user-facing routers требуют Bearer JWT через
# router-level dependency. Health — public.
_AUTH_DEPS = [Depends(get_current_claims)]

app.include_router(health.router)
app.include_router(proposals.router, dependencies=_AUTH_DEPS)
