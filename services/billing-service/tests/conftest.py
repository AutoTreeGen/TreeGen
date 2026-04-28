"""Pytest fixtures для billing-service.

Зеркалит ``notification-service/conftest.py``: testcontainers-postgres +
alembic upgrade head + httpx ASGITransport client.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

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
        cfg.set_main_option(
            "script_location",
            str(_repo_root() / "infrastructure" / "alembic"),
        )
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


@pytest_asyncio.fixture
async def app_client(
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[object]:
    """httpx AsyncClient против поднятого billing-service app."""
    monkeypatch.setenv("BILLING_SERVICE_DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("BILLING_SERVICE_BILLING_ENABLED", "true")
    monkeypatch.setenv("BILLING_SERVICE_STRIPE_API_KEY", "sk_test_fake")
    monkeypatch.setenv("BILLING_SERVICE_STRIPE_WEBHOOK_SECRET", "whsec_test_fake")
    monkeypatch.setenv("BILLING_SERVICE_STRIPE_PRICE_PRO", "price_test_fake")

    # Сбрасываем lru_cache get_settings — она кеширует первый снимок env.
    from billing_service.config import get_settings

    get_settings.cache_clear()

    from billing_service.database import dispose_engine, init_engine
    from billing_service.main import app
    from httpx import ASGITransport, AsyncClient

    init_engine(postgres_dsn)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dispose_engine()
    get_settings.cache_clear()
