"""Тесты POST /waitlist (Phase 4.12 / ADR-0035).

Покрытие:
    - валидный email + locale → 200, запись в БД;
    - email lower-case'ится при сохранении;
    - duplicate email → 200, без второй записи (idempotent);
    - невалидный email → 422 (Pydantic EmailStr);
    - запись email в логи не попадает (privacy).
"""

from __future__ import annotations

import logging

import pytest
from shared_models.orm import WaitlistEntry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _count_entries(postgres_dsn: str, email: str) -> int:
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            (await session.execute(select(WaitlistEntry).where(WaitlistEntry.email == email)))
            .scalars()
            .all()
        )
        result = len(rows)
    await engine.dispose()
    return result


@pytest.mark.db
@pytest.mark.integration
async def test_waitlist_join_persists_entry(app_client, postgres_dsn) -> None:
    resp = await app_client.post(
        "/waitlist",
        json={"email": "alice@example.com", "locale": "ru-RU"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert await _count_entries(postgres_dsn, "alice@example.com") == 1


@pytest.mark.db
@pytest.mark.integration
async def test_waitlist_join_lowercases_email(app_client, postgres_dsn) -> None:
    resp = await app_client.post("/waitlist", json={"email": "Bob@Example.COM"})
    assert resp.status_code == 200
    # Запись должна храниться lowercase, чтобы Bob@Foo / bob@foo считались
    # одним email и unique-constraint работал.
    assert await _count_entries(postgres_dsn, "bob@example.com") == 1


@pytest.mark.db
@pytest.mark.integration
async def test_waitlist_join_is_idempotent(app_client, postgres_dsn) -> None:
    first = await app_client.post("/waitlist", json={"email": "carol@example.com"})
    second = await app_client.post("/waitlist", json={"email": "carol@example.com"})
    third = await app_client.post(
        "/waitlist",
        json={"email": "CAROL@Example.com"},  # case difference
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    # Только одна запись несмотря на 3 submit'а.
    assert await _count_entries(postgres_dsn, "carol@example.com") == 1


@pytest.mark.db
@pytest.mark.integration
async def test_waitlist_join_rejects_invalid_email(app_client) -> None:
    resp = await app_client.post("/waitlist", json={"email": "not-an-email"})
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.integration
async def test_waitlist_join_rejects_extra_fields(app_client) -> None:
    """Pydantic ``extra=\"forbid\"`` защищает от подделки полей."""
    resp = await app_client.post(
        "/waitlist",
        json={"email": "dan@example.com", "is_admin": True},
    )
    assert resp.status_code == 422


@pytest.mark.db
@pytest.mark.integration
async def test_waitlist_join_does_not_log_email(app_client, caplog) -> None:
    """Privacy: email не должен попадать в логи (ADR-0035)."""
    caplog.set_level(logging.DEBUG, logger="parser_service.api.waitlist")
    resp = await app_client.post(
        "/waitlist",
        json={"email": "private@example.com", "locale": "en"},
    )
    assert resp.status_code == 200
    for record in caplog.records:
        assert "private@example.com" not in record.getMessage()
