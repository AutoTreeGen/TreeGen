"""Pure-async dispatcher unit-тесты (без HTTP)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from email_service.config import get_settings
from email_service.services.dispatcher import dispatch_email
from shared_models.enums import EmailKind, EmailSendStatus
from shared_models.orm import User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _ok_transport() -> httpx.AsyncBaseTransport:
    return httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"id": "re_dispatch_unit"}),
    )


@pytest.fixture
async def session(
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    monkeypatch.setenv("EMAIL_SERVICE_ENABLED", "true")
    monkeypatch.setenv("EMAIL_SERVICE_RESEND_API_KEY", "re_test_fake")
    monkeypatch.setenv("EMAIL_SERVICE_RESEND_FROM", "noreply@test.example.com")
    get_settings.cache_clear()
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.integration
async def test_dispatch_returns_sent_outcome(session: AsyncSession) -> None:
    user = User(
        email=f"dispatch-{uuid.uuid4().hex[:8]}@example.com",
        external_auth_id=f"local:dispatch-{uuid.uuid4().hex[:8]}",
        locale="en",
    )
    session.add(user)
    await session.flush()

    settings = get_settings()
    outcome = await dispatch_email(
        session,
        settings,
        kind=EmailKind.WELCOME,
        recipient_user_id=user.id,
        idempotency_key=f"welcome:{user.id}-direct",
        params={},
        transport=_ok_transport(),
    )
    assert outcome.status is EmailSendStatus.SENT
    assert outcome.deduplicated is False
    assert outcome.provider_message_id == "re_dispatch_unit"
    assert outcome.error is None


@pytest.mark.integration
async def test_dispatch_idempotent(session: AsyncSession) -> None:
    user = User(
        email=f"dispatch-dedup-{uuid.uuid4().hex[:8]}@example.com",
        external_auth_id=f"local:dispatch-dedup-{uuid.uuid4().hex[:8]}",
        locale="en",
    )
    session.add(user)
    await session.flush()

    key = f"welcome:{user.id}-direct-dedup"
    settings = get_settings()
    first = await dispatch_email(
        session,
        settings,
        kind=EmailKind.WELCOME,
        recipient_user_id=user.id,
        idempotency_key=key,
        params={},
        transport=_ok_transport(),
    )
    assert first.deduplicated is False

    # Second call with same key — без вызова transport (cached).
    second = await dispatch_email(
        session,
        settings,
        kind=EmailKind.WELCOME,
        recipient_user_id=user.id,
        idempotency_key=key,
        params={},
        transport=None,  # если бы dispatch обратился к Resend, упал бы (no key).
    )
    assert second.deduplicated is True
    assert second.log_id == first.log_id
    assert second.status is EmailSendStatus.SENT
