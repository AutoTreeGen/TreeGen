"""Pytest fixtures для notification-service.

Зеркалит ``dna-service/conftest.py``: testcontainers-postgres + alembic
upgrade head + httpx ASGITransport client.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from fastapi import status as _http_status
from shared_models.auth import ClerkClaims, ClerkJwtSettings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _repo_root() -> Path:
    """Корень репо — где живёт alembic.ini."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").exists():
            return parent
    pytest.skip("alembic.ini не найден")
    msg = "unreachable"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """testcontainers-postgres + alembic upgrade head."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    saved_db_url = os.environ.get("DATABASE_URL")
    saved_alt_db_url = os.environ.get("AUTOTREEGEN_DATABASE_URL")
    try:
        sync_url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://", 1
        )
        os.environ["DATABASE_URL"] = sync_url
        os.environ.pop("AUTOTREEGEN_DATABASE_URL", None)

        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_repo_root() / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", sync_url)
        cfg.set_main_option("script_location", str(_repo_root() / "infrastructure" / "alembic"))
        command.upgrade(cfg, "head")

        async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        yield async_url
    finally:
        if saved_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_db_url
        if saved_alt_db_url is not None:
            os.environ["AUTOTREEGEN_DATABASE_URL"] = saved_alt_db_url
        container.stop()


# Phase 4.10: тесты исторически шлют ``X-User-Id`` int header'ом —
# легче сохранить контракт через override, чем переписать 30+ тестов
# под Bearer JWT. Override-функции вынесены на module-level, чтобы
# FastAPI правильно ввёл ``Request`` из global scope (closure-функции
# давали false-positive «Field required for query.request»).


def _fake_clerk_settings_override() -> ClerkJwtSettings:
    return ClerkJwtSettings(issuer="https://test.clerk.local")


async def _claims_from_x_user_override(request: Request) -> ClerkClaims:
    x_user = request.headers.get("X-User-Id")
    if not x_user:
        raise HTTPException(
            status_code=_http_status.HTTP_401_UNAUTHORIZED,
            detail="Missing test X-User-Id header",
        )
    return ClerkClaims(sub=f"user_test_{x_user}", email=None, raw={})


async def _user_id_from_x_user_override(request: Request) -> int:
    x_user = request.headers.get("X-User-Id")
    if not x_user:
        raise HTTPException(
            status_code=_http_status.HTTP_401_UNAUTHORIZED,
            detail="Missing test X-User-Id header",
        )
    try:
        return int(x_user)
    except ValueError as exc:
        raise HTTPException(
            status_code=_http_status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id must be a positive integer",
        ) from exc


@pytest_asyncio.fixture
async def app_client(postgres_dsn: str) -> AsyncIterator:
    """httpx AsyncClient против поднятого FastAPI app, привязанного к test-DB."""
    os.environ["NOTIFICATION_SERVICE_DATABASE_URL"] = postgres_dsn

    from httpx import ASGITransport, AsyncClient
    from notification_service.auth import (
        get_clerk_settings,
        get_current_claims,
        get_current_user_id,
    )
    from notification_service.database import dispose_engine, init_engine
    from notification_service.main import app

    app.dependency_overrides[get_clerk_settings] = _fake_clerk_settings_override
    app.dependency_overrides[get_current_claims] = _claims_from_x_user_override
    app.dependency_overrides[get_current_user_id] = _user_id_from_x_user_override

    init_engine(postgres_dsn)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_clerk_settings, None)
    app.dependency_overrides.pop(get_current_claims, None)
    app.dependency_overrides.pop(get_current_user_id, None)
    await dispose_engine()
