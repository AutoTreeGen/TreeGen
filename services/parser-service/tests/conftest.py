"""Pytest fixtures для parser-service.

Использует тот же подход что и shared-models: testcontainers-postgres
поднимает свой экземпляр на сессию + накатывает миграции.
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
from shared_models.orm import User
from sqlalchemy import select

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ---------------------------------------------------------------------------
# Phase 4.10/4.10b auth overrides (module-level, чтобы FastAPI правильно
# вводил ``Request`` без false-positive «query.request required»).
# ---------------------------------------------------------------------------
_FAKE_SUB = "user_test_clerk_sub"
_FAKE_EMAIL = "owner@autotreegen.local"
_FALLBACK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_FAKE_CLAIMS = ClerkClaims(sub=_FAKE_SUB, email=_FAKE_EMAIL, raw={"sub": _FAKE_SUB})


def _fake_clerk_settings_override() -> ClerkJwtSettings:
    return ClerkJwtSettings(issuer="https://test.clerk.local")


async def _fake_claims_override() -> ClerkClaims:
    return _FAKE_CLAIMS


async def _fake_claims_optional_override() -> ClerkClaims:
    return _FAKE_CLAIMS


async def _fake_current_user_id_override(request: Request) -> uuid.UUID:
    """Phase 11.0 sharing-тесты authenticate'ятся через X-User-Id header.
    Без header'а — fixed-fake user (email matches owner_email shim).

    Stub-session тесты (test_imports_async) переопределяют get_session
    через ``app.dependency_overrides``, но не зовут init_engine; прямой
    вызов ``db_module.get_session()`` в этом случае raise'ит RuntimeError
    при первой итерации — ловим и возвращаем ``_FALLBACK_USER_ID``
    (эти тесты проверяют enqueue/contract, не auth).
    """
    from parser_service import database as db_module

    x_user_id = request.headers.get("X-User-Id")
    if x_user_id:
        try:
            return uuid.UUID(x_user_id)
        except ValueError:
            pass
    try:
        async for session in db_module.get_session():
            try:
                existing = (
                    await session.execute(select(User).where(User.clerk_user_id == _FAKE_SUB))
                ).scalar_one_or_none()
            except Exception:
                return _FALLBACK_USER_ID
            if existing is not None:
                return existing.id
            try:
                by_email = (
                    await session.execute(select(User).where(User.email == _FAKE_EMAIL))
                ).scalar_one_or_none()
            except Exception:
                return _FALLBACK_USER_ID
            if by_email is not None:
                by_email.clerk_user_id = _FAKE_SUB
                by_email.external_auth_id = f"clerk:{_FAKE_SUB}"
                await session.flush()
                await session.commit()
                return by_email.id
            user = User(
                email=_FAKE_EMAIL,
                external_auth_id=f"clerk:{_FAKE_SUB}",
                clerk_user_id=_FAKE_SUB,
                display_name="Test User",
                locale="en",
            )
            session.add(user)
            await session.flush()
            await session.commit()
            return user.id if user.id is not None else _FALLBACK_USER_ID
    except RuntimeError:
        return _FALLBACK_USER_ID
    return _FALLBACK_USER_ID


async def _fake_current_user_override(request: Request) -> User:
    """Phase 11.0 ``Depends(get_current_user)`` — возвращает полный User row.

    Stub-session тесты (test_imports_async) — RuntimeError catch'ится,
    возвращаем minimal User-stub без DB hit (enqueue-test'ы User-инспекцию
    не делают).
    """
    from parser_service import database as db_module

    x_user_id = request.headers.get("X-User-Id")
    try:
        async for session in db_module.get_session():
            if x_user_id:
                try:
                    user_uuid = uuid.UUID(x_user_id)
                except ValueError:
                    user_uuid = None
                if user_uuid is not None:
                    row = (
                        await session.execute(select(User).where(User.id == user_uuid))
                    ).scalar_one_or_none()
                    if row is not None:
                        return row
            existing = (
                await session.execute(select(User).where(User.clerk_user_id == _FAKE_SUB))
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            by_email = (
                await session.execute(select(User).where(User.email == _FAKE_EMAIL))
            ).scalar_one_or_none()
            if by_email is not None:
                by_email.clerk_user_id = _FAKE_SUB
                by_email.external_auth_id = f"clerk:{_FAKE_SUB}"
                await session.flush()
                await session.commit()
                return by_email
            user = User(
                email=_FAKE_EMAIL,
                external_auth_id=f"clerk:{_FAKE_SUB}",
                clerk_user_id=_FAKE_SUB,
                display_name="Test User",
                locale="en",
            )
            session.add(user)
            await session.flush()
            await session.commit()
            return user
    except RuntimeError:
        # Stub-session test: no init_engine. Возвращаем in-memory stub-User.
        return User(
            id=_FALLBACK_USER_ID,
            email=_FAKE_EMAIL,
            external_auth_id=f"clerk:{_FAKE_SUB}",
            clerk_user_id=_FAKE_SUB,
            display_name="Test User",
            locale="en",
        )
    msg = "get_session yielded no session"
    raise RuntimeError(msg)


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
    """Phase 4.10/4.10b/11.0: подменяем Clerk auth + Phase 11.0 ``get_current_user``
    на test-stub'ы (module-level functions).

    Override functions должны быть **module-level** (не closures),
    иначе FastAPI неправильно introspects их сигнатуру и ругается
    на ``request: Request`` как на missing query param.

    Тесты, которые проверяют сам auth-flow (test_auth_required.py),
    локально pop'ят override'ы.
    """
    from parser_service.auth import (
        get_clerk_settings,
        get_current_claims,
        get_current_claims_optional,
        get_current_user,
        get_current_user_id,
    )

    app.dependency_overrides[get_clerk_settings] = _fake_clerk_settings_override
    app.dependency_overrides[get_current_claims] = _fake_claims_override
    app.dependency_overrides[get_current_claims_optional] = _fake_claims_optional_override
    app.dependency_overrides[get_current_user_id] = _fake_current_user_id_override
    app.dependency_overrides[get_current_user] = _fake_current_user_override
    yield
    for dep in (
        get_clerk_settings,
        get_current_claims,
        get_current_claims_optional,
        get_current_user_id,
        get_current_user,
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
