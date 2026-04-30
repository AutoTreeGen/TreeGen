"""Тесты ``POST /billing/checkout`` (mocked Stripe SDK).

Проверяем три сценария:
* Happy path: создаём User, мокаем Stripe SDK, получаем checkout_url.
* FREE plan → 400.
* Missing X-User-Id → 401.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from shared_models.orm import User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _make_user_email() -> str:
    return f"checkout-{uuid.uuid4().hex[:8]}@example.com"


@pytest.mark.integration
async def test_checkout_creates_session_and_returns_url(
    app_client: object,
    postgres_dsn: str,
) -> None:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email=_make_user_email(), external_auth_id="local:checkout-1")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id
    await engine.dispose()

    fake_customer = type("C", (), {"id": "cus_test_123"})()
    fake_session = type("S", (), {"id": "cs_test_456", "url": "https://stripe.example/checkout"})()

    with (
        patch("stripe.Customer.create", return_value=fake_customer),
        patch("stripe.checkout.Session.create", return_value=fake_session),
    ):
        response = await app_client.post(  # type: ignore[attr-defined]
            "/billing/checkout",
            headers={"X-User-Id": str(user_id)},
            json={"plan": "pro"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["checkout_url"] == "https://stripe.example/checkout"
    assert data["session_id"] == "cs_test_456"


@pytest.mark.integration
async def test_checkout_free_plan_rejected(
    app_client: object,
    postgres_dsn: str,
) -> None:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email=_make_user_email(), external_auth_id="local:checkout-2")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id
    await engine.dispose()

    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/checkout",
        headers={"X-User-Id": str(user_id)},
        json={"plan": "free"},
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_checkout_missing_user_id_returns_401(app_client: object) -> None:
    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/checkout",
        json={"plan": "pro"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_checkout_disabled_returns_503(
    app_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BILLING_ENABLED=false → /billing/checkout 503."""
    from billing_service.config import get_settings

    monkeypatch.setenv("BILLING_SERVICE_BILLING_ENABLED", "false")
    get_settings.cache_clear()
    try:
        response = await app_client.post(  # type: ignore[attr-defined]
            "/billing/checkout",
            headers={"X-User-Id": "1"},
            json={"plan": "pro"},
        )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()
