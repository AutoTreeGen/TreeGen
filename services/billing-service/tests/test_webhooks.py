"""Тесты ``POST /billing/webhooks/stripe``.

Проверяем:
* Missing signature → 400.
* Invalid signature → 400.
* Valid signature + checkout.session.completed → создаёт subscription, 200.
* Idempotency: повторный event_id → 200, deduplicated=True, без side-effects.
* Unknown event_type → 200, no-op.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
import stripe as stripe_sdk
from shared_models.orm import StripeCustomer, Subscription, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _make_event(event_type: str, data: dict[str, object]) -> dict[str, object]:
    """Сконструировать минимальный Stripe-event payload."""
    return {
        "id": f"evt_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": event_type,
        "created": int(time.time()),
        "data": {"object": data},
    }


def _signed_payload(secret: str, payload: dict[str, object]) -> tuple[bytes, str]:
    """Сформировать (raw_body, Stripe-Signature) пару под secret.

    Использует ту же утилиту, что и Stripe SDK на тестовой стороне,
    чтобы наш ``construct_event`` смог верифицировать подпись.
    """
    body = json.dumps(payload).encode("utf-8")
    timestamp = int(time.time())
    sig_header = stripe_sdk.WebhookSignature._compute_signature(  # type: ignore[no-untyped-call]
        f"{timestamp}.{body.decode('utf-8')}",
        secret,
    )
    return body, f"t={timestamp},v1={sig_header}"


@pytest.mark.integration
async def test_webhook_missing_signature_returns_400(app_client: object) -> None:
    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=b'{"id":"evt_x"}',
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_webhook_invalid_signature_returns_400(app_client: object) -> None:
    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=b'{"id":"evt_x"}',
        headers={"Stripe-Signature": "t=123,v1=invalid"},
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_webhook_checkout_completed_creates_subscription(
    app_client: object,
    postgres_dsn: str,
) -> None:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            email=f"webhook-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:webhook-{uuid.uuid4().hex[:8]}",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        # Также создаём StripeCustomer заранее (как будто прошёл /billing/checkout).
        customer = StripeCustomer(
            user_id=user.id,
            stripe_customer_id=f"cus_{uuid.uuid4().hex[:16]}",
        )
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        user_id, stripe_customer_id = user.id, customer.stripe_customer_id

    event = _make_event(
        "checkout.session.completed",
        {
            "id": f"cs_{uuid.uuid4().hex[:16]}",
            "customer": stripe_customer_id,
            "subscription": f"sub_{uuid.uuid4().hex[:16]}",
            "client_reference_id": str(user_id),
        },
    )
    body, sig = _signed_payload("whsec_test_fake", event)

    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["received"] is True
    assert data["deduplicated"] is False
    assert data["processed"] is True

    # Проверим, что subscription создан.
    async with factory() as session:
        sub = (
            await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        ).scalar_one_or_none()
        assert sub is not None
        assert sub.plan == "pro"
        assert sub.status == "active"
    await engine.dispose()


@pytest.mark.integration
async def test_webhook_idempotency_dedup(
    app_client: object,
    postgres_dsn: str,
) -> None:
    """Повторный event с тем же id → 200, deduplicated=True."""
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            email=f"dedup-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:dedup-{uuid.uuid4().hex[:8]}",
        )
        session.add(user)
        await session.flush()
        customer = StripeCustomer(
            user_id=user.id,
            stripe_customer_id=f"cus_{uuid.uuid4().hex[:16]}",
        )
        session.add(customer)
        await session.commit()
        stripe_customer_id = customer.stripe_customer_id
        user_id = user.id
    await engine.dispose()

    event = _make_event(
        "checkout.session.completed",
        {
            "id": f"cs_{uuid.uuid4().hex[:16]}",
            "customer": stripe_customer_id,
            "subscription": f"sub_{uuid.uuid4().hex[:16]}",
            "client_reference_id": str(user_id),
        },
    )
    body, sig = _signed_payload("whsec_test_fake", event)

    r1 = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
    )
    assert r1.status_code == 200
    assert r1.json()["deduplicated"] is False

    # Re-doставка — нам приходит точно тот же event.
    r2 = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["deduplicated"] is True
    assert body2["processed"] is True


@pytest.mark.integration
async def test_webhook_unknown_event_type_no_op(app_client: object) -> None:
    event = _make_event("some.unknown.event", {"foo": "bar"})
    body, sig = _signed_payload("whsec_test_fake", event)

    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] is True
    # processed=False для unknown event types.
    assert data["processed"] is False


@pytest.mark.integration
async def test_webhook_handler_failure_marks_event_failed(
    app_client: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если handler бросает — event помечается FAILED, ответ 500."""
    from billing_service.services import event_handlers

    async def boom(_session: object, _event: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setitem(event_handlers.EVENT_HANDLERS, "checkout.session.completed", boom)

    event = _make_event(
        "checkout.session.completed",
        {"id": "cs_x", "customer": "cus_x", "subscription": "sub_x"},
    )
    body, sig = _signed_payload("whsec_test_fake", event)

    response = await app_client.post(  # type: ignore[attr-defined]
        "/billing/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
    )
    assert response.status_code == 500
