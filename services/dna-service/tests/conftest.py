"""Pytest fixtures для dna-service.

Зеркалит parser-service/conftest.py: testcontainers-postgres + alembic
upgrade head + httpx ASGITransport client.
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


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """Per-test storage root, чтобы blob'ы разных тестов не пересекались."""
    root = tmp_path / "dna-blobs"
    root.mkdir()
    return root


@pytest_asyncio.fixture
async def app_client(postgres_dsn: str, storage_root: Path) -> AsyncIterator:
    """httpx AsyncClient против поднятого FastAPI app, привязанного к test-DB."""
    os.environ["DNA_SERVICE_DATABASE_URL"] = postgres_dsn
    os.environ["DNA_SERVICE_STORAGE_ROOT"] = str(storage_root)
    # Plaintext-mode для тестов (encrypted-blob handling — Phase 6.2.x).
    os.environ["DNA_SERVICE_REQUIRE_ENCRYPTION"] = "false"

    from dna_service.auth import (
        get_clerk_settings,
        get_current_claims,
    )
    from dna_service.database import dispose_engine, init_engine
    from dna_service.main import app
    from httpx import ASGITransport, AsyncClient

    # Phase 4.10: подменяем Clerk auth depends на test-stub'ы. Большинство
    # dna-service тестов написаны до auth-flow и не интересуются JWT.
    from shared_models.auth import ClerkClaims, ClerkJwtSettings

    fake_claims = ClerkClaims(
        sub="user_test_dna_clerk_sub",
        email="dna-test@autotreegen.test",
        raw={"sub": "user_test_dna_clerk_sub"},
    )

    async def _fake_current_claims() -> ClerkClaims:
        return fake_claims

    def _fake_clerk_settings():
        return ClerkJwtSettings(issuer="https://test.clerk.local")

    app.dependency_overrides[get_clerk_settings] = _fake_clerk_settings
    app.dependency_overrides[get_current_claims] = _fake_current_claims

    init_engine(postgres_dsn)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_clerk_settings, None)
    app.dependency_overrides.pop(get_current_claims, None)
    await dispose_engine()


@pytest_asyncio.fixture
async def seeded_user_and_tree(postgres_dsn: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Создать User + Tree напрямую через async-сессию для тестов.

    Возвращает `(user_id, tree_id)` для использования в payload'ах
    consent / upload эндпоинтов.
    """
    from shared_models.orm import Tree, User
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        user = User(
            email=f"dna-test-{suffix}@example.com",
            external_auth_id=f"auth0|dna-test-{suffix}",
            display_name="DNA Test User",
        )
        session.add(user)
        await session.flush()
        tree = Tree(owner_user_id=user.id, name=f"DNA Test Tree {suffix}")
        session.add(tree)
        await session.flush()
        result = (user.id, tree.id)
    await engine.dispose()
    return result
