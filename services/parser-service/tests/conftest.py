"""Pytest fixtures для parser-service.

Использует тот же подход что и shared-models: testcontainers-postgres
поднимает свой экземпляр на сессию + накатывает миграции.
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


@pytest.fixture(autouse=True, scope="session")
def _import_inline_for_tests() -> Iterator[None]:
    """Включить ``PARSER_SERVICE_IMPORT_INLINE=1`` для всех тестов сессии.

    Phase 3.5 сделал ``POST /imports`` асинхронным (202 + arq enqueue),
    но большинство существующих тестов ожидают синхронный 201 с готовым
    деревом в response. Включаем legacy-inline режим по умолчанию,
    чтобы не переписывать десятки тестов. Тесты, которые проверяют
    именно асинхронный путь (``test_imports_async.py``), отключают
    флаг локально.
    """
    saved = os.environ.get("PARSER_SERVICE_IMPORT_INLINE")
    os.environ["PARSER_SERVICE_IMPORT_INLINE"] = "1"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("PARSER_SERVICE_IMPORT_INLINE", None)
        else:
            os.environ["PARSER_SERVICE_IMPORT_INLINE"] = saved


@pytest.fixture(autouse=True, scope="session")
def _bulk_compute_inline_for_tests() -> Iterator[None]:
    """Включить ``PARSER_SERVICE_BULK_COMPUTE_INLINE=1`` для всех тестов сессии.

    Phase 7.5 finalize сделал ``POST /trees/{id}/hypotheses/compute-all``
    асинхронным (202 + arq enqueue) — параллель Phase 3.5 для импортов.
    Существующие тесты в ``test_bulk_hypothesis_compute.py`` ожидают
    sync 201 с уже терминальным job'ом. Включаем legacy-inline по
    умолчанию; тесты async-флоу (``test_bulk_compute_async.py``) сами
    снимают флаг.
    """
    saved = os.environ.get("PARSER_SERVICE_BULK_COMPUTE_INLINE")
    os.environ["PARSER_SERVICE_BULK_COMPUTE_INLINE"] = "1"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("PARSER_SERVICE_BULK_COMPUTE_INLINE", None)
        else:
            os.environ["PARSER_SERVICE_BULK_COMPUTE_INLINE"] = saved


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


@pytest.fixture(autouse=True)
def _override_auth(app):
    """Phase 4.10: подменяем Clerk auth dependencies на test-stub'ы.

    Большинство тестов parser-service'а написаны до Clerk-auth и не
    хотят возиться с генерацией JWT; этот autouse-фикстура говорит
    FastAPI: «считай, что Bearer JWT есть и user authenticated, JIT
    создал row если её не было».

    Тесты, которые проверяют именно auth-flow (test_auth_required.py),
    локально снимают override через ``app.dependency_overrides.pop``
    или используют альтернативный test-app без override'а.
    """
    import uuid
    from typing import Any

    from fastapi import Depends
    from parser_service.auth import (
        get_clerk_settings,
        get_current_claims,
        get_current_claims_optional,
        get_current_user_id,
    )
    from parser_service.config import Settings
    from parser_service.database import get_session
    from shared_models.auth import ClerkClaims
    from shared_models.orm import User
    from sqlalchemy import select

    # Фейковый Clerk sub: фиксированный, чтобы JIT-create нашёл одного
    # и того же user'а между запросами в одном тесте.
    fake_sub = "user_test_clerk_sub"
    fake_email = "test-user@autotreegen.test"
    fake_claims = ClerkClaims(sub=fake_sub, email=fake_email, raw={"sub": fake_sub})
    # Стабильный fake user_id для тестов, использующих stub-session
    # (без реальной DB). Если test поднимает реальную сессию, в ней
    # JIT-create создаст row с тем же ``clerk_user_id``, и мы вернём
    # её фактический UUID; иначе возвращаем этот fallback.
    fallback_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    async def _fake_current_claims() -> ClerkClaims:
        return fake_claims

    async def _fake_current_claims_optional() -> ClerkClaims:
        return fake_claims

    async def _fake_current_user_id(
        session: Any = Depends(get_session),
    ) -> uuid.UUID:
        """JIT-create или найти test user'а через текущий session-override.

        Если session — это in-memory stub (test_imports_async.py), его
        ``execute`` не возвращает реального ``User``-row; ловим и
        возвращаем fallback UUID. На реальной DB-сессии (большинство
        тестов) делаем нормальный JIT-flow.
        """
        try:
            existing = (
                await session.execute(select(User).where(User.clerk_user_id == fake_sub))
            ).scalar_one_or_none()
        except Exception:
            return fallback_user_id
        if existing is not None:
            return existing.id
        # На stub-session `add`/`flush`/`commit` — no-op'ы, ничего страшного.
        try:
            user = User(
                email=fake_email,
                external_auth_id=f"clerk:{fake_sub}",
                clerk_user_id=fake_sub,
                display_name="Test User",
                locale="en",
            )
            session.add(user)
            await session.flush()
            await session.commit()
        except Exception:
            return fallback_user_id
        return user.id if user.id is not None else fallback_user_id

    # ClerkJwtSettings stub — иначе get_clerk_settings вернёт 503 при
    # пустом env. Никто из stub'ов выше его не вызывает, но depends-
    # граф ещё пытается резолвить (FastAPI сначала строит граф).
    def _fake_clerk_settings(_settings: Settings = None):  # type: ignore[assignment]
        from shared_models.auth import ClerkJwtSettings

        return ClerkJwtSettings(issuer="https://test.clerk.local")

    app.dependency_overrides[get_clerk_settings] = _fake_clerk_settings
    app.dependency_overrides[get_current_claims] = _fake_current_claims
    app.dependency_overrides[get_current_claims_optional] = _fake_current_claims_optional
    app.dependency_overrides[get_current_user_id] = _fake_current_user_id
    yield
    for dep in (
        get_clerk_settings,
        get_current_claims,
        get_current_claims_optional,
        get_current_user_id,
    ):
        app.dependency_overrides.pop(dep, None)


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
