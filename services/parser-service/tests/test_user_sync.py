"""Интеграционные тесты для user_sync (Phase 4.10, ADR-0033).

Покрывает:

* первый JIT-create на свежий ``clerk_user_id``;
* повторный вызов с тем же sub возвращает уже существующий row;
* email-backfill при появлении email в claims после первого call'а
  с empty-email.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from parser_service.services.user_sync import (
    get_or_create_user_from_clerk,
    get_user_id_from_clerk,
)
from shared_models.auth import ClerkClaims
from shared_models.orm import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _fresh_sub() -> str:
    """Уникальный clerk sub per-test."""
    return f"user_test_{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_user_sync_creates_row_on_first_call(session_factory: Any) -> None:
    factory = session_factory
    sub = _fresh_sub()
    claims = ClerkClaims(sub=sub, email="alpha@example.com", raw={"sub": sub})

    async with factory() as session:
        user = await get_or_create_user_from_clerk(session, claims)
        await session.commit()
        first_id = user.id

    async with factory() as session:
        row = (await session.execute(select(User).where(User.clerk_user_id == sub))).scalar_one()
        assert row.id == first_id
        assert row.clerk_user_id == sub
        assert row.email == "alpha@example.com"
        assert row.external_auth_id == f"clerk:{sub}"


@pytest.mark.asyncio
async def test_user_sync_idempotent_on_repeated_call(session_factory: Any) -> None:
    factory = session_factory
    sub = _fresh_sub()
    claims = ClerkClaims(sub=sub, email="beta@example.com", raw={"sub": sub})

    async with factory() as session:
        user1 = await get_or_create_user_from_clerk(session, claims)
        await session.commit()
        first_id = user1.id

    async with factory() as session:
        user2 = await get_or_create_user_from_clerk(session, claims)
        await session.commit()
        assert user2.id == first_id

    async with factory() as session:
        # Только одна row — никакого race-create'а.
        rows = (
            (await session.execute(select(User).where(User.clerk_user_id == sub))).scalars().all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_user_sync_backfills_email_when_initially_missing(
    session_factory: Any,
) -> None:
    """Если первый вызов был без email, второй с email — обновляет row."""
    factory = session_factory
    sub = _fresh_sub()
    claims_no_email = ClerkClaims(sub=sub, email=None, raw={"sub": sub})
    claims_with_email = ClerkClaims(
        sub=sub, email="late@example.com", raw={"sub": sub, "email": "late@example.com"}
    )

    async with factory() as session:
        user1 = await get_or_create_user_from_clerk(session, claims_no_email)
        await session.commit()
        # Email — placeholder из ``{sub}@clerk.local`` (см. _placeholder_email).
        assert user1.email.endswith("@clerk.local")

    async with factory() as session:
        user2 = await get_or_create_user_from_clerk(session, claims_with_email)
        await session.commit()
        assert user2.id == user1.id
        assert user2.email == "late@example.com"


@pytest.mark.asyncio
async def test_get_user_id_from_clerk_returns_uuid(session_factory: Any) -> None:
    factory = session_factory
    sub = _fresh_sub()
    claims = ClerkClaims(sub=sub, email="gamma@example.com", raw={"sub": sub})

    async with factory() as session:
        user_id = await get_user_id_from_clerk(session, claims)
        await session.commit()
        assert isinstance(user_id, uuid.UUID)

    async with factory() as session:
        # Idempotent: тот же UUID при повторном вызове.
        user_id2 = await get_user_id_from_clerk(session, claims)
        await session.commit()
        assert user_id2 == user_id
