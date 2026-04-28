"""Pytest fixtures для parser-service.

Использует тот же подход что и shared-models: testcontainers-postgres
поднимает свой экземпляр на сессию + накатывает миграции.
"""

from __future__ import annotations

import asyncio
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
    """Поднять testcontainers-postgres с pgvector + накатить alembic head."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    import os

    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    # Без override DATABASE_URL: env.py подгружает .env (load_dotenv) и
    # перезаписывает sqlalchemy.url локальным dev-DSN, из-за чего миграции
    # уезжают не в testcontainer. Перебиваем через ENV — env.py берёт его
    # как первоисточник.
    saved_db_url = os.environ.get("DATABASE_URL")
    saved_alt_db_url = os.environ.get("AUTOTREEGEN_DATABASE_URL")
    try:
        sync_url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://", 1
        )
        os.environ["DATABASE_URL"] = sync_url
        os.environ.pop("AUTOTREEGEN_DATABASE_URL", None)

        # Применить миграции через subprocess (alembic API простой и надёжный).
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


@pytest.fixture(scope="session")
def app():
    """FastAPI-приложение parser_service. Шарится между тестами в сессии."""
    from parser_service.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(autouse=True)
def _override_arq_pool(app):
    """Подменяем get_arq_pool на AsyncMock — никаких реальных Redis-коннектов в unit-тестах."""
    from unittest.mock import AsyncMock, MagicMock

    from parser_service.queue import get_arq_pool

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=MagicMock(job_id="fake"))
    app.dependency_overrides[get_arq_pool] = lambda: fake_pool
    yield
    app.dependency_overrides.pop(get_arq_pool, None)


@pytest_asyncio.fixture
async def app_client(app, postgres_dsn: str) -> AsyncIterator:
    """httpx AsyncClient против поднятого FastAPI app, привязанного к test-DB."""
    import os

    os.environ["PARSER_SERVICE_DATABASE_URL"] = postgres_dsn
    # Force re-init lifespan
    from httpx import ASGITransport, AsyncClient
    from parser_service.database import dispose_engine, init_engine

    init_engine(postgres_dsn)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await dispose_engine()
