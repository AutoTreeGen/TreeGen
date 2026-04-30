"""Stripe webhook event handlers (Phase 12.0, ADR-0042).

Каждый handler — pure-async-function, принимающая ``session`` и
``event`` (распарсенный через ``construct_webhook_event``). Возвращает
``None`` при успехе или бросает exception при ошибке (top-level webhook
handler ловит и помечает event как ``FAILED`` в ``stripe_event_log``).

Поддерживаемые события (Phase 12.0):

* ``checkout.session.completed`` — пользователь успешно оплатил →
  создаём/обновляем ``Subscription`` row.
* ``customer.subscription.{created,updated,deleted}`` — изменение
  plan/status/period_end. Зеркалируем в БД (``deleted`` → status=CANCELED).
* ``invoice.paid`` / ``invoice.payment_succeeded`` — успешный платёж
  за период. Помимо обновления subscription'а POST'им
  ``payment_succeeded`` email через email-service.
* ``invoice.payment_failed`` — неудачный платёж. Status → PAST_DUE +
  POST ``payment_failed`` email.

Дублирующиеся events (Stripe at-least-once) отлавливаются на уровне
top-level handler через unique constraint на
``stripe_event_log.stripe_event_id``.

Carry-forward правило (Phase 12.2): DNA-related события (kit upload,
match found) НИКОГДА не отправляются как payment_* email — это
домен notification-service, не billing-service. Здесь handler'ы
работают исключительно с invoice/subscription event'ами.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any, Final

from shared_models.enums import EmailKind, Plan, SubscriptionStatus
from shared_models.orm import StripeCustomer, Subscription
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_service.config import Settings, get_settings
from billing_service.services.email_client import send_email_async

_LOG: Final = logging.getLogger(__name__)

# Маппинг Stripe-статусов → наши SubscriptionStatus.
# Stripe-ные ``incomplete``/``incomplete_expired`` → возвращаем None
# (handler пропустит upsert): ждём перехода в ACTIVE/CANCELED.
# ``unpaid``/``paused`` → PAST_DUE (тот же UX).
_STRIPE_STATUS_MAP: Final[dict[str, str | None]] = {
    "active": SubscriptionStatus.ACTIVE.value,
    "trialing": SubscriptionStatus.TRIALING.value,
    "past_due": SubscriptionStatus.PAST_DUE.value,
    "unpaid": SubscriptionStatus.PAST_DUE.value,
    "paused": SubscriptionStatus.PAST_DUE.value,
    "canceled": SubscriptionStatus.CANCELED.value,
    "incomplete": None,
    "incomplete_expired": SubscriptionStatus.CANCELED.value,
}


def _map_status(stripe_status: str) -> str | None:
    """Stripe → наш SubscriptionStatus value (или None если skip)."""
    return _STRIPE_STATUS_MAP.get(stripe_status, None)


def _ts_to_dt(ts: int | None) -> dt.datetime | None:
    """Stripe timestamps — Unix epoch in UTC."""
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, tz=dt.UTC)


async def _resolve_user_id_from_customer(
    session: AsyncSession,
    stripe_customer_id: str,
) -> uuid.UUID | None:
    """Найти наш user_id по Stripe customer_id."""
    row = (
        await session.execute(
            select(StripeCustomer.user_id).where(
                StripeCustomer.stripe_customer_id == stripe_customer_id,
            )
        )
    ).scalar_one_or_none()
    return row if row is not None else None


async def _upsert_subscription(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    stripe_subscription_id: str,
    plan: Plan,
    status_value: str,
    current_period_end: dt.datetime | None,
    cancel_at_period_end: bool,
) -> None:
    """Idempotent upsert по ``stripe_subscription_id``.

    Lookup идёт по ``stripe_subscription_id`` (UNIQUE), не по ``user_id`` —
    у одного user может быть несколько исторических subscription'ов
    (cancel + resubscribe → новый sub_id). Это сохраняет полный audit
    trail на нашей стороне без дополнительной таблицы.
    """
    existing = (
        await session.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_subscription_id
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        record = Subscription(
            user_id=user_id,
            stripe_subscription_id=stripe_subscription_id,
            plan=plan.value,
            status=status_value,
            current_period_end=current_period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        session.add(record)
        await session.flush()
        return

    existing.user_id = user_id
    existing.plan = plan.value
    existing.status = status_value
    existing.current_period_end = current_period_end
    existing.cancel_at_period_end = cancel_at_period_end
    await session.flush()


def _extract_plan_from_subscription(
    stripe_sub: dict[str, Any],
    settings: Settings,
) -> Plan:
    """Резолвит ``Plan`` по price_id первой line item Stripe-подписки.

    Сравнивает с настроенными ``stripe_price_pro`` / ``stripe_price_premium``.
    Если ни один не матчится — fallback PRO (subscription факт оплачена,
    но мы не знаем тариф; UI покажет предупреждение в ``/billing/me``).
    """
    items = stripe_sub.get("items", {}).get("data", [])
    if not items:
        return Plan.FREE
    price_id = ((items[0] or {}).get("price") or {}).get("id", "")
    if settings.stripe_price_premium and price_id == settings.stripe_price_premium:
        return Plan.PREMIUM
    if settings.stripe_price_pro and price_id == settings.stripe_price_pro:
        return Plan.PRO
    return Plan.PRO


async def handle_checkout_completed(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``checkout.session.completed``.

    Stripe отправляет это сразу после успешного payment (subscription mode).
    В payload есть ``customer`` ID и ``subscription`` ID. Создаём/обновляем
    нашу ``Subscription`` row минимально (полная информация —
    в ``customer.subscription.created``, который придёт отдельно).
    """
    obj = event["data"]["object"]
    customer_id = obj.get("customer")
    subscription_id = obj.get("subscription")
    client_ref = obj.get("client_reference_id")

    if not customer_id or not subscription_id:
        _LOG.warning(
            "checkout.session.completed without customer/subscription: %s",
            event.get("id"),
        )
        return

    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None and client_ref is not None:
        # Fallback: восстановить связь через client_reference_id (мы туда
        # клали str(user.id) при создании Checkout Session).
        try:
            user_id = uuid.UUID(client_ref)
        except ValueError:
            user_id = None

    if user_id is None:
        _LOG.error(
            "checkout.session.completed: cannot resolve user_id for customer=%s",
            customer_id,
        )
        return

    await _upsert_subscription(
        session,
        user_id=user_id,
        stripe_subscription_id=subscription_id,
        plan=Plan.PRO,
        status_value=SubscriptionStatus.ACTIVE.value,
        current_period_end=None,  # уточнится в subscription.updated
        cancel_at_period_end=False,
    )


async def handle_subscription_updated(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``customer.subscription.{created,updated}``.

    Полная информация о подписке: status, period_end, cancel_at_period_end,
    plan (через items[0].price.id).
    """
    sub = event["data"]["object"]
    customer_id = sub.get("customer")
    if not customer_id:
        _LOG.warning("subscription event without customer: %s", event.get("id"))
        return

    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        _LOG.error(
            "subscription event: cannot resolve user_id for customer=%s",
            customer_id,
        )
        return

    settings = get_settings()
    status_value = _map_status(sub.get("status", ""))
    if status_value is None:
        _LOG.info(
            "subscription event with skip-status (incomplete) — no upsert: id=%s",
            sub.get("id"),
        )
        return
    plan = _extract_plan_from_subscription(sub, settings)
    period_end = _ts_to_dt(sub.get("current_period_end"))
    cancel_at_period_end = bool(sub.get("cancel_at_period_end", False))

    await _upsert_subscription(
        session,
        user_id=user_id,
        stripe_subscription_id=sub["id"],
        plan=plan,
        status_value=status_value,
        current_period_end=period_end,
        cancel_at_period_end=cancel_at_period_end,
    )


async def handle_subscription_deleted(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``customer.subscription.deleted`` — подписка отменена.

    Помечаем status=CANCELED. Запись остаётся для history; ``get_user_plan``
    вернёт FREE.
    """
    sub = event["data"]["object"]
    customer_id = sub.get("customer")
    if not customer_id:
        return
    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        return

    sub_id = sub.get("id")
    if not sub_id:
        return
    existing = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == sub_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.status = SubscriptionStatus.CANCELED.value
    existing.cancel_at_period_end = False
    await session.flush()


async def _send_payment_email(
    settings: Settings,
    *,
    kind: EmailKind,
    user_id: uuid.UUID,
    stripe_event_id: str,
    invoice: dict[str, Any],
) -> None:
    """POST к email-service с idempotency_key=stripe_event_id.

    Email-service уже хранит ``email_send_log.idempotency_key UNIQUE`` —
    повторная доставка того же Stripe-event'а (наш idempotency-чек на
    ``stripe_event_log`` выше может теоретически разойтись с email-service
    при partial-failure'е) приведёт к 200 cached без дублирующего письма.
    """
    if not settings.email_service_url:
        _LOG.debug(
            "email_service_url not configured — skipping email kind=%s for event=%s",
            kind.value,
            stripe_event_id,
        )
        return
    customer_email = (invoice.get("customer_email") or "").strip()
    amount = invoice.get("amount_paid") or invoice.get("amount_due") or 0
    currency = (invoice.get("currency") or "usd").upper()
    payload = {
        "kind": kind.value,
        "to_user_id": str(user_id),
        "to_email": customer_email or None,
        "idempotency_key": stripe_event_id,
        "context": {
            "amount_minor": int(amount),
            "currency": currency,
            "invoice_url": invoice.get("hosted_invoice_url") or None,
        },
    }
    await send_email_async(settings, payload)


async def handle_invoice_payment_succeeded(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``invoice.paid`` / ``invoice.payment_succeeded`` — успешный платёж.

    Делает два независимых эффекта:

    1. Сбрасывает PAST_DUE → ACTIVE (если был), обновляет
       ``current_period_end`` на subscription'е.
    2. POST'ит ``payment_succeeded`` email через email-service
       (idempotent через stripe_event_id).
    """
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        return

    sub_id = invoice.get("subscription")
    if sub_id:
        existing = (
            await session.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == sub_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.status == SubscriptionStatus.PAST_DUE.value:
                existing.status = SubscriptionStatus.ACTIVE.value
            period_end = _ts_to_dt(invoice.get("period_end"))
            if period_end is not None:
                existing.current_period_end = period_end
            await session.flush()

    settings = get_settings()
    await _send_payment_email(
        settings,
        kind=EmailKind.PAYMENT_SUCCEEDED,
        user_id=user_id,
        stripe_event_id=event["id"],
        invoice=invoice,
    )


async def handle_invoice_payment_failed(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``invoice.payment_failed`` — неудачный платёж.

    Stripe сам переведёт subscription в past_due (придёт
    ``customer.subscription.updated``), но дублируем здесь для скорости —
    UI должен увидеть PAST_DUE сразу. Плюс POST email-уведомление.
    """
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        return

    sub_id = invoice.get("subscription")
    if sub_id:
        existing = (
            await session.execute(
                select(Subscription).where(Subscription.stripe_subscription_id == sub_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = SubscriptionStatus.PAST_DUE.value
            await session.flush()

    settings = get_settings()
    await _send_payment_email(
        settings,
        kind=EmailKind.PAYMENT_FAILED,
        user_id=user_id,
        stripe_event_id=event["id"],
        invoice=invoice,
    )


# Маппинг Stripe event_type → handler. Расширяемая карта.
EVENT_HANDLERS: Final[dict[str, Any]] = {
    "checkout.session.completed": handle_checkout_completed,
    "customer.subscription.created": handle_subscription_updated,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
    "invoice.paid": handle_invoice_payment_succeeded,
    "invoice.payment_succeeded": handle_invoice_payment_succeeded,
    "invoice.payment_failed": handle_invoice_payment_failed,
}


__all__ = [
    "EVENT_HANDLERS",
    "handle_checkout_completed",
    "handle_invoice_payment_failed",
    "handle_invoice_payment_succeeded",
    "handle_subscription_deleted",
    "handle_subscription_updated",
]
