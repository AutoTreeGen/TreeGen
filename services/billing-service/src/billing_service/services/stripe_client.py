"""Тонкая обёртка над Stripe SDK.

Изолирует:

* Инициализацию ``stripe.api_key`` от import-time (для тестов с моками).
* Создание/повторное использование ``Customer`` per user.
* Создание ``Checkout.Session`` под Pro-план.
* Создание ``BillingPortal.Session`` для self-service управления.
* Проверку webhook-подписи (``stripe.Webhook.construct_event``).

Конкретные event-обработчики — в ``services.event_handlers``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final, cast

import stripe
from shared_models.orm import StripeCustomer, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_service.config import Settings

_LOG: Final = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckoutSessionResult:
    """Результат создания Checkout Session — то, что мы возвращаем клиенту."""

    url: str
    session_id: str


def _configure_api_key(settings: Settings) -> None:
    """Установить ``stripe.api_key`` из settings.

    Stripe SDK хранит ключ как module-level state; мы его обновляем
    каждый раз, чтобы тесты могли подменить settings через monkeypatch
    без import-time effects.
    """
    stripe.api_key = settings.stripe_api_key


async def get_or_create_customer(
    session: AsyncSession,
    settings: Settings,
    user: User,
) -> StripeCustomer:
    """Найти или создать ``StripeCustomer`` для пользователя.

    Поведение:

    1. Если у user'а уже есть ``stripe_customers`` row — возвращаем её.
    2. Иначе создаём Customer в Stripe (с email + metadata.user_id),
       persist'им маппинг и возвращаем.

    Stripe SDK — sync (``stripe.Customer.create``), но мы вызываем его
    из async-handler'а. SDK использует ``requests`` внутри, что блокирует
    event loop. Для Phase 12.0 это OK — checkout-flow низкочастотен; при
    масштабировании — переключиться на ``stripe.AsyncAnthropic``-эквивалент
    или обернуть в ``asyncio.to_thread``.
    """
    existing = (
        await session.execute(select(StripeCustomer).where(StripeCustomer.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    _configure_api_key(settings)
    customer = stripe.Customer.create(
        email=user.email,
        metadata={"user_id": str(user.id)},
    )
    record = StripeCustomer(
        user_id=user.id,
        stripe_customer_id=customer.id,
    )
    session.add(record)
    await session.flush()
    _LOG.info(
        "Created Stripe Customer for user_id=%s: customer_id=%s",
        user.id,
        customer.id,
    )
    return record


def create_checkout_session(
    settings: Settings,
    *,
    customer_id: str,
    price_id: str,
    user_id: uuid.UUID,
) -> CheckoutSessionResult:
    """Создать Stripe Checkout Session для подписки на Pro-план.

    ``customer`` привязывается к Session — при оплате Stripe автоматически
    привяжет subscription к этому customer'у, и наш webhook увидит уже
    знакомый ``customer.id``.

    ``client_reference_id`` дублирует ``user_id`` — Stripe пропускает
    эту строку через checkout.session.completed event, и у webhook'а
    есть второй канал восстановления связи user → subscription, на
    случай если ``customer.metadata.user_id`` потеряется (race condition
    при concurrent создании).
    """
    _configure_api_key(settings)
    session_obj = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=str(user_id),
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=settings.checkout_success_url,
        cancel_url=settings.checkout_cancel_url,
    )
    if not session_obj.url:
        # Stripe возвращает url=None только при misconfigured пайплайне
        # (например, missing line_items). Делаем явный 500, чтобы клиент
        # не получил пустой redirect.
        msg = "Stripe returned a checkout session without a URL"
        raise RuntimeError(msg)
    return CheckoutSessionResult(
        url=session_obj.url,
        session_id=session_obj.id,
    )


def create_portal_session(
    settings: Settings,
    *,
    customer_id: str,
) -> str:
    """Создать Stripe Customer Portal Session и вернуть её URL."""
    _configure_api_key(settings)
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=settings.portal_return_url,
    )
    # Stripe stubs disagree across mypy environments (pre-commit isolated venv
    # vs project .venv); cast keeps both happy.
    return cast("str", portal.url)  # type: ignore[redundant-cast,unused-ignore]


def construct_webhook_event(
    payload: bytes,
    sig_header: str,
    webhook_secret: str,
) -> dict[str, Any]:
    """Верифицировать подпись и вернуть распарсенный event как plain dict.

    Бросает ``stripe.error.SignatureVerificationError`` если подпись
    неверна или payload изменён. ``ValueError`` если payload — не валидный JSON.
    Caller (webhook handler) ловит и возвращает 400.

    Stripe SDK ≥12 убрал ``Event.to_dict_recursive()``; вместо обхода
    nested ``StripeObject``-ов парсим raw payload через ``json.loads``
    (мы уже знаем что bytes валидны — verify_signature проверил их
    форму). Это даёт чистый ``dict[str, Any]`` без зависимости от
    внутренних helper'ов SDK.
    """
    # ``unused-ignore`` guards against the case where Stripe stubs ARE present
    # (project venv) and ``no-untyped-call`` becomes a false positive.
    stripe.Webhook.construct_event(  # type: ignore[no-untyped-call,unused-ignore]
        payload, sig_header, webhook_secret
    )
    return cast("dict[str, Any]", json.loads(payload.decode("utf-8")))


__all__ = [
    "CheckoutSessionResult",
    "construct_webhook_event",
    "create_checkout_session",
    "create_portal_session",
    "get_or_create_customer",
]
