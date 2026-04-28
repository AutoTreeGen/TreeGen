"""Pydantic-схемы запросов/ответов billing-service."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field
from shared_models.enums import Plan, SubscriptionStatus


class CheckoutRequest(BaseModel):
    """Тело ``POST /billing/checkout``."""

    model_config = ConfigDict(frozen=True)

    plan: Plan = Field(description="Желаемый план — пока только 'pro' (FREE не требует чекаута).")


class CheckoutResponse(BaseModel):
    """Ответ ``POST /billing/checkout``."""

    model_config = ConfigDict(frozen=True)

    checkout_url: str = Field(description="URL Stripe Checkout Session.")
    session_id: str = Field(description="cs_* идентификатор сессии (для последующего трекинга).")


class PortalResponse(BaseModel):
    """Ответ ``GET /billing/portal``."""

    model_config = ConfigDict(frozen=True)

    portal_url: str = Field(description="URL Stripe Customer Portal.")


class CurrentPlanResponse(BaseModel):
    """Ответ ``GET /billing/me`` — текущий план + лимиты."""

    model_config = ConfigDict(frozen=True)

    plan: Plan
    status: SubscriptionStatus | None = Field(
        default=None,
        description="None если у пользователя ни одной подписки (никогда не покупал).",
    )
    current_period_end: dt.datetime | None = None
    cancel_at_period_end: bool = False
    limits: PlanLimitsSchema


class PlanLimitsSchema(BaseModel):
    """Лимиты плана (зеркалирует ``services.entitlements.PlanLimits``)."""

    model_config = ConfigDict(frozen=True)

    max_trees: int | None = Field(description="None = без лимита.")
    max_persons_per_tree: int | None
    dna_enabled: bool
    fs_import_enabled: bool


CurrentPlanResponse.model_rebuild()
