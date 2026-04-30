"""Pytest fixtures для api-gateway.

Поднимает testcontainers-postgres + накатывает alembic head; Clerk auth
override резолвит ``X-User-Id`` header'а как UUID (если есть) или
fallback'ом создаёт fixed-fake user. Позволяет каждому тесту POST'ить
от имени выбранного user'а через простой header.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import Request
from shared_models.auth import ClerkClaims, ClerkJwtSettings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _fake_clerk_settings_override() -> ClerkJwtSettings:
    return ClerkJwtSettings(issuer="https://test.clerk.local")


_FALLBACK_SUB = "user_test_clerk_sub"


async def _fake_claims_override(request: Request) -> ClerkClaims:
    """Симулировать Clerk claims; берём sub из ``X-User-Id`` header'а если есть."""
    sub = request.headers.get("X-User-Id") or _FALLBACK_SUB
    return ClerkClaims(sub=sub, email=f"{sub}@test.local", raw={"sub": sub})


async def _fake_current_user_id_override(request: Request) -> uuid.UUID:
    """Resolve UUID из ``X-User-Id`` header'а (один header для тестов).

    Header ожидается как UUID-string. Если не задан — RuntimeError, тесты
    обязаны явно указывать кто аутентифицирован.
    """
    raw = request.headers.get("X-User-Id")
    if not raw:
        msg = "X-User-Id header required by api-gateway tests"
        raise RuntimeError(msg)
    return uuid.UUID(raw)


def _repo_root() -> Path:
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
    saved = os.environ.get("DATABASE_URL")
    try:
        sync_url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://", 1
        )
        os.environ["DATABASE_URL"] = sync_url

        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_repo_root() / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", sync_url)
        cfg.set_main_option("script_location", str(_repo_root() / "infrastructure" / "alembic"))
        command.upgrade(cfg, "head")

        async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        yield async_url
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved
        container.stop()


@pytest.fixture(scope="session")
def app():
    """FastAPI app api-gateway."""
    from api_gateway.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(autouse=True)
def _override_auth(app):
    """Заменить Clerk auth-deps на test-stubs."""
    from api_gateway.auth import (
        get_clerk_settings,
        get_current_claims,
        get_current_user_id,
    )

    app.dependency_overrides[get_clerk_settings] = _fake_clerk_settings_override
    app.dependency_overrides[get_current_claims] = _fake_claims_override
    app.dependency_overrides[get_current_user_id] = _fake_current_user_id_override
    yield
    for dep in (get_clerk_settings, get_current_claims, get_current_user_id):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def app_client(app, postgres_dsn: str) -> AsyncIterator:
    """httpx AsyncClient против api-gateway, привязанного к test-DB."""
    os.environ["API_GATEWAY_DATABASE_URL"] = postgres_dsn

    from api_gateway.database import dispose_engine, init_engine
    from httpx import ASGITransport, AsyncClient

    init_engine(postgres_dsn)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dispose_engine()
