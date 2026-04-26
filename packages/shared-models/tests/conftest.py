"""Фикстуры для тестов shared-models.

Используем testcontainers-postgres: каждый сеанс поднимает свой контейнер с
pgvector/pg_trgm и накатывает все миграции через Alembic. Это медленно (~5–10 с
старт), но изолированно и не зависит от внешнего docker-compose.

Для быстрых unit-тестов (без БД) пользуемся обычным in-memory Pydantic-кодом —
такие тесты не нуждаются в фикстуре ``db_session``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

# Windows + Python 3.13: ProactorEventLoop ломает asyncpg/psycopg async (SCRAM).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
import pytest_asyncio
from shared_models import (
    Base,
    orm,  # noqa: F401  — регистрируем модели
    register_audit_listeners,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _alembic_root() -> Path:
    """Корень репо с alembic.ini (для миграций в тестах с реальным Postgres)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").exists():
            return parent
    pytest.skip("alembic.ini не найден от корня тестов")
    # pytest.skip(...) поднимает Skipped-исключение, эта строка недостижима, но
    # mypy требует unreachable-return; fail loudly если skip почему-то прошёл.
    msg = "unreachable: pytest.skip should have raised"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[str]:
    """Поднимает testcontainers-postgres c pgvector/pg_trgm/uuid-ossp.

    Возвращает async DSN ``postgresql+asyncpg://...``.
    Skipped, если testcontainers/Docker недоступны.
    """
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("testcontainers не установлен; pip install testcontainers[postgres]")

    container = PostgresContainer("pgvector/pgvector:pg16").with_env(
        "POSTGRES_DB", "autotreegen_test"
    )
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker недоступен для testcontainers: {exc}")

    sync_url = container.get_connection_url()
    # testcontainers возвращает psycopg2-URL; для asyncpg меняем driver.
    async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    if async_url.startswith("postgresql://"):
        async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    try:
        yield async_url
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="session")
async def engine_fixture(postgres_container: str) -> AsyncIterator[object]:
    """Async-engine с накатанными миграциями (через Alembic)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_alembic_root() / "alembic.ini"))
    # Alembic подхватит DATABASE_URL и сам конвертирует driver.
    os.environ["DATABASE_URL"] = postgres_container
    cfg.set_main_option("sqlalchemy.url", postgres_container)
    command.upgrade(cfg, "head")

    engine = create_async_engine(postgres_container, echo=False, future=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine_fixture: object) -> AsyncIterator[AsyncSession]:
    """Чистая AsyncSession + audit-listener, откат транзакции после теста."""
    SessionMaker = async_sessionmaker(  # noqa: N806 — фабрика классов
        engine_fixture,  # type: ignore[arg-type]
        expire_on_commit=False,
    )
    register_audit_listeners(SessionMaker)
    async with SessionMaker() as session, session.begin():
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
def empty_metadata() -> object:
    """Возвращает Base.metadata для тестов схемы без подключения к БД."""
    return Base.metadata
