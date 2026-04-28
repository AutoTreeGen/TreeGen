"""Checkout & Customer Portal endpoints (Phase 12.0).

* ``POST /billing/checkout`` — создаёт Stripe Checkout Session, возвращает URL.
* ``GET  /billing/portal`` — создаёт Customer Portal Session, возвращает URL.
* ``GET  /billing/me`` — текущий план + лимиты пользователя.

Все три требуют ``X-User-Id`` header (mock auth до Phase 4.10).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, status
from shared_models.enums import Plan, SubscriptionStatus
from shared_models.orm import StripeCustomer, StripeSubscription, User
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


async def _get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Загрузить ``User`` или 404 (с тем же сообщением что и для wrong-user)."""
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
    """Создать Stripe Checkout Session под Pro-план.

    400 если запрошен FREE план (на FREE подписки нет — это default state).
    503 если billing_enabled=false (сервис в dev-mode без Stripe).
    """
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is disabled in this environment (BILLING_SERVICE_BILLING_ENABLED=false).",
        )
    if body.plan is Plan.FREE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="FREE plan does not require checkout.",
        )
    if not settings.stripe_price_pro:
        # Misconfiguration → 500 для прозрачности (alert'ит Sentry).
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_PRICE_PRO is not configured.",
        )

    user_id = resolve_user_id_from_header(x_user_id)
    user = await _get_user(session, user_id)

    customer = await get_or_create_customer(session, settings, user)
    result = create_checkout_session(
        settings,
        customer_id=customer.stripe_customer_id,
        price_id=settings.stripe_price_pro,
        user_id=user.id,
    )
    _LOG.info(
        "Checkout session created: user_id=%s session_id=%s",
        user.id,
        result.session_id,
    )
    return CheckoutResponse(checkout_url=result.url, session_id=result.session_id)


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


@router.get("/me", response_model=CurrentPlanResponse)
async def get_my_plan(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> CurrentPlanResponse:
    """Вернуть текущий план + лимиты + meta-информацию о подписке."""
    user_id = resolve_user_id_from_header(x_user_id)
    plan = await get_user_plan(session, user_id)
    limits = get_plan_limits(plan)

    sub = (
        await session.execute(
            select(StripeSubscription).where(StripeSubscription.user_id == user_id)
        )
    ).scalar_one_or_none()

    return CurrentPlanResponse(
        plan=plan,
        status=SubscriptionStatus(sub.status) if sub else None,
        current_period_end=sub.current_period_end if sub else None,
        cancel_at_period_end=sub.cancel_at_period_end if sub else False,
        limits=PlanLimitsSchema(
            max_trees=limits.max_trees,
            max_persons_per_tree=limits.max_persons_per_tree,
            dna_enabled=limits.dna_enabled,
            fs_import_enabled=limits.fs_import_enabled,
        ),
    )
