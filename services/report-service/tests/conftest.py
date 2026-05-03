"""Pytest fixtures для report-service.

Зеркалит ``billing-service/conftest.py``: testcontainers-postgres +
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
    """httpx AsyncClient против поднятого report-service app."""
    monkeypatch.setenv("REPORT_SERVICE_DATABASE_URL", postgres_dsn)

    from report_service.config import get_settings

    get_settings.cache_clear()

    from httpx import ASGITransport, AsyncClient
    from report_service.api.bundles import get_bundle_storage
    from report_service.api.relationship import get_report_storage
    from report_service.database import dispose_engine, init_engine
    from report_service.main import app
    from shared_models.storage import InMemoryStorage

    init_engine(postgres_dsn)

    # Подмена storage на in-memory: реальный backend требует STORAGE_BUCKET +
    # MinIO/GCS креденшелы, не подходит для unit-теста endpoint'а. Симметрично
    # parser-service court_ready conftest. Phase 24.4: один storage instance
    # шарится между 24.3 sync endpoint и 24.4 bundle endpoints — оба сервируют
    # blob'ы из одного фейкового хранилища.
    storage = InMemoryStorage()
    app.dependency_overrides[get_report_storage] = lambda: storage
    app.dependency_overrides[get_bundle_storage] = lambda: storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    await dispose_engine()
