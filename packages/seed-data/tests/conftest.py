"""Pytest fixtures для seed-data (Phase 22.1b).

Зеркалит ``billing-service/conftest.py``: testcontainers-postgres +
alembic upgrade head + per-test session factory.
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


FIXTURES_DIR: Path = Path(__file__).resolve().parent / "fixtures" / "seed"


@pytest.fixture
def synthetic_surname_path() -> Path:
    return FIXTURES_DIR / "synthetic_surname_clusters.json"


@pytest.fixture
def synthetic_places_path() -> Path:
    return FIXTURES_DIR / "synthetic_places.json"


@pytest.fixture
def synthetic_country_extension_path() -> Path:
    return FIXTURES_DIR / "synthetic_country_extension.json"


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
async def session_factory(postgres_dsn: str) -> AsyncIterator[object]:
    """Per-test async session factory bound к testcontainers postgres."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(postgres_dsn)
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield sf
    await engine.dispose()
