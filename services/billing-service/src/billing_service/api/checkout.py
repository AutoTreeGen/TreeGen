"""Checkout & Customer Portal endpoints (Phase 12.0).

* ``POST /billing/checkout`` — создаёт Stripe Checkout Session, возвращает URL.
* ``GET  /billing/portal`` — создаёт Customer Portal Session, возвращает URL.
* ``GET  /billing/me`` — текущий план + лимиты + meta-информация.

Все три требуют ``X-User-Id`` header (mock auth до Phase 4.10).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, status
from shared_models.enums import Plan, SubscriptionStatus
from shared_models.orm import StripeCustomer, Subscription, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_service.config import Settings, get_settings
from billing_service.database import get_session
from billing_service.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    CurrentPlanResponse,
    PlanLimitsSchema,
    PortalResponse,
)
from billing_service.services.entitlements import (
    get_plan_limits,
    get_user_plan,
    resolve_user_id_from_header,
)
from billing_service.services.stripe_client import (
    create_checkout_session,
    create_portal_session,
    get_or_create_customer,
)

router = APIRouter(prefix="/billing")
_LOG: Final = logging.getLogger(__name__)


def _price_for_plan(settings: Settings, plan: Plan) -> str:
    """Резолвит Stripe Price ID для запрошенного плана."""
    if plan is Plan.PRO:
        return settings.stripe_price_pro
    if plan is Plan.PREMIUM:
        return settings.stripe_price_premium
    msg = f"No Stripe price configured for plan {plan.value!r}"
    raise ValueError(msg)


async def _get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Загрузить ``User`` или 404."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> CheckoutResponse:
    """Создать Stripe Checkout Session для PRO или PREMIUM-плана.

    400 если запрошен FREE (на FREE подписки нет — это default state).
    503 если billing_enabled=false (сервис в dev-mode без Stripe).
    """
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is disabled in this environment "
            "(BILLING_SERVICE_BILLING_ENABLED=false).",
        )
    if body.plan is Plan.FREE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="FREE plan does not require checkout.",
        )
    price_id = _price_for_plan(settings, body.plan)
    if not price_id:
        # Misconfiguration → 500 для прозрачности (alert'ит Sentry).
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stripe price for plan {body.plan.value!r} is not configured.",
        )

    user_id = resolve_user_id_from_header(x_user_id)
    user = await _get_user(session, user_id)

    customer = await get_or_create_customer(session, settings, user)
    result = create_checkout_session(
        settings,
        customer_id=customer.stripe_customer_id,
        price_id=price_id,
        user_id=user.id,
    )
    _LOG.info(
        "Checkout session created: user_id=%s plan=%s session_id=%s",
        user.id,
        body.plan.value,
        result.session_id,
    )
    return CheckoutResponse(checkout_url=result.url, session_id=result.session_id)


@router.get("/subscriptions/me", response_model=CurrentPlanResponse)
@router.get("/me", response_model=CurrentPlanResponse, include_in_schema=False)
async def get_my_subscription(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> CurrentPlanResponse:
    """Вернуть текущий план + лимиты + meta-информацию о подписке.

    ``GET /billing/me`` оставлен как hidden alias для backward compatibility
    с предыдущими тестами; canonical путь — ``GET /billing/subscriptions/me``.
    """
    user_id = resolve_user_id_from_header(x_user_id)
    plan = await get_user_plan(session, user_id)
    limits = get_plan_limits(plan)

    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    sub_status: SubscriptionStatus | None = None
    if sub is not None:
        try:
            sub_status = SubscriptionStatus(sub.status)
        except ValueError:
            sub_status = None

    return CurrentPlanResponse(
        plan=plan,
        status=sub_status,
        current_period_end=sub.current_period_end if sub else None,
        cancel_at_period_end=sub.cancel_at_period_end if sub else False,
        limits=PlanLimitsSchema(
            max_trees=limits.max_trees,
            max_persons_per_tree=limits.max_persons_per_tree,
            dna_enabled=limits.dna_enabled,
            fs_import_enabled=limits.fs_import_enabled,
        ),
    )


@router.post("/subscriptions/checkout", response_model=CheckoutResponse, include_in_schema=False)
async def create_checkout_alias(
    body: CheckoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> CheckoutResponse:
    """Alias для ``POST /billing/checkout`` под /subscriptions namespace'ом."""
    result: CheckoutResponse = await create_checkout(body, session, settings, x_user_id)
    return result


@router.get("/portal", response_model=PortalResponse)
async def open_portal(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> PortalResponse:
    """Создать Customer Portal Session.

    404 если у пользователя нет ``StripeCustomer`` row (никогда не платил).
    """
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is disabled in this environment.",
        )

    user_id = resolve_user_id_from_header(x_user_id)
    customer = (
        await session.execute(select(StripeCustomer).where(StripeCustomer.user_id == user_id))
    ).scalar_one_or_none()
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Stripe Customer for this user (subscribe first).",
        )
    url = create_portal_session(settings, customer_id=customer.stripe_customer_id)
    return PortalResponse(portal_url=url)
