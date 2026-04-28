"""Обработчики Stripe webhook events.

Каждый handler — pure-async-function, принимающая ``session`` и
``event`` (распарсенный через ``construct_webhook_event``). Возвращает
``None`` при успехе или бросает exception при ошибке (top-level webhook
handler ловит и помечает event как ``FAILED`` в ``stripe_events``).

Поддерживаемые типы (Phase 12.0):

* ``checkout.session.completed`` — пользователь успешно оплатил →
  создаём/обновляем ``StripeSubscription`` row.
* ``customer.subscription.updated`` — изменение plan/status/period_end
  (например, апгрейд, авто-renewal). Зеркалируем в БД.
* ``customer.subscription.deleted`` — подписка отменена (немедленно или
  по истечению period). Помечаем status=CANCELED.
* ``invoice.payment_succeeded`` — успешный платёж за период. Сбрасываем
  past_due (если был), обновляем current_period_end.
* ``invoice.payment_failed`` — неудачный платёж. Stripe сам переведёт
  subscription в past_due; мы зеркалируем status.

Дублирующиеся events (Stripe at-least-once) отлавливаются на уровне
top-level handler через unique constraint на ``stripe_events.stripe_event_id``.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any, Final

from shared_models.enums import Plan, SubscriptionStatus
from shared_models.orm import StripeCustomer, StripeSubscription
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_LOG: Final = logging.getLogger(__name__)

# Маппинг Stripe-статусов → наши SubscriptionStatus.
# Stripe-статусы, которых нет у нас (``trialing``, ``unpaid``, ``paused``),
# мапим на ближайший: trialing→ACTIVE, unpaid/paused→PAST_DUE.
_STRIPE_STATUS_MAP: Final[dict[str, str]] = {
    "active": SubscriptionStatus.ACTIVE.value,
    "trialing": SubscriptionStatus.ACTIVE.value,
    "past_due": SubscriptionStatus.PAST_DUE.value,
    "unpaid": SubscriptionStatus.PAST_DUE.value,
    "paused": SubscriptionStatus.PAST_DUE.value,
    "canceled": SubscriptionStatus.CANCELED.value,
    "incomplete": SubscriptionStatus.INCOMPLETE.value,
    "incomplete_expired": SubscriptionStatus.CANCELED.value,
}


def _map_status(stripe_status: str) -> str:
    """Преобразовать Stripe status в наш SubscriptionStatus value.

    Если Stripe прислал неизвестный статус — fallback в INCOMPLETE
    (безопаснее всего: фичи будут off, пользователь увидит, что что-то
    не так и обратится в support, а не получит вечный доступ).
    """
    return _STRIPE_STATUS_MAP.get(stripe_status, SubscriptionStatus.INCOMPLETE.value)


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
    stripe_sub_id: str,
    plan: Plan,
    status_value: str,
    current_period_end: dt.datetime | None,
    cancel_at_period_end: bool,
) -> None:
    """Idempotent upsert для ``stripe_subscriptions`` row.

    Уникальный constraint на ``user_id`` гарантирует одну row на user'а;
    если user меняет план — обновляем существующую запись, не создаём новую.
    """
    existing = (
        await session.execute(
            select(StripeSubscription).where(StripeSubscription.user_id == user_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        record = StripeSubscription(
            user_id=user_id,
            stripe_sub_id=stripe_sub_id,
            plan=plan.value,
            status=status_value,
            current_period_end=current_period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        session.add(record)
        await session.flush()
        return

    existing.stripe_sub_id = stripe_sub_id
    existing.plan = plan.value
    existing.status = status_value
    existing.current_period_end = current_period_end
    existing.cancel_at_period_end = cancel_at_period_end
    await session.flush()


def _extract_plan_from_subscription(stripe_sub: dict[str, Any]) -> Plan:
    """Phase 12.0 знает только два плана: PRO и FREE.

    Любая активная Stripe-подписка по нашему Price ID → PRO. Будущие
    тарифы (Phase 12.x) потребуют маппинга price_id → Plan здесь.
    """
    items = stripe_sub.get("items", {}).get("data", [])
    if not items:
        return Plan.FREE
    return Plan.PRO


async def handle_checkout_completed(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``checkout.session.completed``.

    Stripe отправляет это сразу после успешного payment (subscription mode).
    В payload есть ``customer`` ID (а subscription ID — в
    ``subscription``). Создаём/обновляем нашу ``StripeSubscription`` row.
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

    # Полная информация о subscription приходит отдельным событием
    # ``customer.subscription.created``/``updated``; здесь делаем
    # минимальный upsert, чтобы UI сразу увидел Pro.
    await _upsert_subscription(
        session,
        user_id=user_id,
        stripe_sub_id=subscription_id,
        plan=Plan.PRO,
        status_value=SubscriptionStatus.ACTIVE.value,
        current_period_end=None,  # уточнится в subscription.updated
        cancel_at_period_end=False,
    )


async def handle_subscription_updated(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``customer.subscription.updated`` / ``.created``.

    Полная информация о подписке: status, period_end, cancel_at_period_end.
    """
    sub = event["data"]["object"]
    customer_id = sub.get("customer")
    if not customer_id:
        _LOG.warning("subscription event without customer: %s", event.get("id"))
        return

    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        _LOG.error("subscription event: cannot resolve user_id for customer=%s", customer_id)
        return

    plan = _extract_plan_from_subscription(sub)
    status_value = _map_status(sub.get("status", ""))
    period_end = _ts_to_dt(sub.get("current_period_end"))
    cancel_at_period_end = bool(sub.get("cancel_at_period_end", False))

    await _upsert_subscription(
        session,
        user_id=user_id,
        stripe_sub_id=sub["id"],
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

    existing = (
        await session.execute(
            select(StripeSubscription).where(StripeSubscription.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.status = SubscriptionStatus.CANCELED.value
    existing.cancel_at_period_end = False
    await session.flush()


async def handle_invoice_payment_succeeded(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``invoice.payment_succeeded`` — успешный платёж за период.

    Используется для:
    * Сброса PAST_DUE → ACTIVE (если карта была обновлена в grace period).
    * Обновления ``current_period_end`` (хотя обычно это приходит и через
      ``subscription.updated``, но мы дублируем для resilience).
    """
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        return

    existing = (
        await session.execute(
            select(StripeSubscription).where(StripeSubscription.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    if existing.status == SubscriptionStatus.PAST_DUE.value:
        existing.status = SubscriptionStatus.ACTIVE.value
    period_end = _ts_to_dt(invoice.get("period_end"))
    if period_end is not None:
        existing.current_period_end = period_end
    await session.flush()


async def handle_invoice_payment_failed(
    session: AsyncSession,
    event: dict[str, Any],
) -> None:
    """``invoice.payment_failed`` — неудачный платёж.

    Stripe сам переведёт subscription в past_due (придёт ``subscription.updated``),
    но дублируем здесь для скорости — UI должен увидеть PAST_DUE сразу.
    """
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    user_id = await _resolve_user_id_from_customer(session, customer_id)
    if user_id is None:
        return
    existing = (
        await session.execute(
            select(StripeSubscription).where(StripeSubscription.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.status = SubscriptionStatus.PAST_DUE.value
    await session.flush()


# Маппинг Stripe event_type → handler. Расширяемая карта.
EVENT_HANDLERS: Final[dict[str, Any]] = {
    "checkout.session.completed": handle_checkout_completed,
    "customer.subscription.created": handle_subscription_updated,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
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
