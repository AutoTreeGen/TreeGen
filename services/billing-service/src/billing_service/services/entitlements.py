"""Резолюция плана пользователя + feature-gating (Phase 12.0, ADR-0042).

См. ADR-0042 §«Plan limits» для бизнес-смысла.

Ключевые функции:

* ``get_user_plan(session, user_id) -> Plan`` — async-резолвер плана
  из ``subscriptions``. Применяет grace-period для PAST_DUE.
* ``get_plan_limits(plan) -> PlanLimits`` — pure-function, без I/O.
* ``check_entitlement(feature)`` — фабрика FastAPI dependency'и для
  feature-gating endpoint'ов в parser-service / dna-service.

Импортируется напрямую из ``billing_service.services.entitlements`` —
билинг-сервис tracks этот модуль как **stable public API** для других
сервисов в монорепо. Breaking change → ADR + версия пакета.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from fastapi import Depends, Header, HTTPException, status
from shared_models.enums import Plan, SubscriptionStatus
from shared_models.orm import Subscription
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_service.config import Settings, get_settings
from billing_service.database import get_session

# ----- PlanLimits ------------------------------------------------------------

# Имена feature'ов для check_entitlement(...). Используются строки, а не
# enum, потому что feature flags часто прибавляются точечно — каждое
# добавление в enum было бы breaking change для shared-models.
Feature = Literal[
    "import_quota",
    "fs_import_enabled",
    "dna_enabled",
]


@dataclass(frozen=True)
class PlanLimits:
    """Лимиты конкретного плана.

    ``None`` означает «без лимита» (PRO/PREMIUM-режим).

    Эти значения заодно сериализуются в ``CurrentPlanResponse`` для UI —
    pricing-страница и settings/billing берут лимиты отсюда, чтобы
    избежать рассинхрона маркетинга и реального gating'а.
    """

    max_trees: int | None
    max_persons_per_tree: int | None
    dna_enabled: bool
    fs_import_enabled: bool


_FREE_LIMITS: Final = PlanLimits(
    max_trees=1,
    max_persons_per_tree=100,
    dna_enabled=False,
    fs_import_enabled=False,
)

_PRO_LIMITS: Final = PlanLimits(
    max_trees=None,
    max_persons_per_tree=None,
    dna_enabled=True,
    fs_import_enabled=True,
)

# PREMIUM = PRO для Phase 12.0 (gating пока сводится к Pro-флагам).
# Phase 12.x: расширим — bulk-инструменты, увеличенные quotas.
_PREMIUM_LIMITS: Final = _PRO_LIMITS


def get_plan_limits(plan: Plan) -> PlanLimits:
    """Pure-function: вернуть ``PlanLimits`` для плана."""
    if plan is Plan.PREMIUM:
        return _PREMIUM_LIMITS
    if plan is Plan.PRO:
        return _PRO_LIMITS
    return _FREE_LIMITS


# ----- get_user_plan ---------------------------------------------------------


async def _latest_subscription(session: AsyncSession, user_id: uuid.UUID) -> Subscription | None:
    """Вернуть самую свежую subscription user'а (по updated_at).

    ``subscriptions.user_id`` НЕ unique (см. ORM-модуль), поэтому
    выбираем самую свежую запись — она канонична для current plan.
    """
    return (
        await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_user_plan(session: AsyncSession, user_id: uuid.UUID) -> Plan:
    """Вернуть текущий план пользователя.

    Логика:

    1. ``ACTIVE`` или ``TRIALING`` подписка → её ``plan`` (PRO/PREMIUM).
    2. ``PAST_DUE`` в окне grace-period (см. ADR-0042 §«Failed payment»)
       → продолжаем давать доступ. Окно — ``settings.past_due_grace_days``
       дней с момента ``current_period_end``.
    3. Всё остальное (CANCELED, нет записи) → FREE.

    Эта функция — единая точка правды для feature-gating'а.
    """
    settings = get_settings()
    if not settings.billing_enabled:
        # Local-dev / CI: feature flag отключает биллинг → все юзеры на PRO.
        # См. ADR-0042 §«Feature flag».
        return Plan.PRO

    sub = await _latest_subscription(session, user_id)
    if sub is None:
        return Plan.FREE

    status_value = sub.status
    if status_value in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value):
        try:
            return Plan(sub.plan)
        except ValueError:
            return Plan.FREE

    if status_value == SubscriptionStatus.PAST_DUE.value:
        if sub.current_period_end is None:
            # Защита: PAST_DUE без period_end — невалидное состояние,
            # обращаемся как с FREE.
            return Plan.FREE
        deadline = sub.current_period_end + dt.timedelta(days=settings.past_due_grace_days)
        if dt.datetime.now(dt.UTC) <= deadline:
            try:
                return Plan(sub.plan)
            except ValueError:
                return Plan.FREE
        return Plan.FREE

    return Plan.FREE


# ----- assert_feature & resolve_user_id (для caller-сервисов) ---------------


# 402 Payment Required → frontend ловит и показывает upgrade-modal.
# Тело ответа — структурированное, чтобы UI мог автоматически собрать
# CTA («/pricing?upgrade=feature») без regexp'инга текста.
def _payment_required_detail(feature: Feature, current_plan: Plan) -> dict[str, object]:
    return {
        "error": "payment_required",
        "feature": feature,
        "current_plan": current_plan.value,
        "upgrade_url": f"/pricing?feature={feature}",
        "message": (
            f"Feature {feature!r} requires a paid plan. Current plan: {current_plan.value}."
        ),
    }


def resolve_user_id_from_header(x_user_id: str | None) -> uuid.UUID:
    """Mock auth (X-User-Id header) — Phase 4.10 заменит на JWT."""
    if x_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id header (mock auth — Phase 12.0).",
        )
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id must be a valid UUID.",
        ) from exc


def feature_allowed(feature: Feature, limits: PlanLimits) -> bool:
    """Pure-function проверка для assert_feature."""
    if feature == "import_quota":
        return True  # Импорт доступен на всех планах; квота считается отдельно.
    if feature == "fs_import_enabled":
        return limits.fs_import_enabled
    if feature == "dna_enabled":
        return limits.dna_enabled
    msg = f"Unknown feature: {feature!r}"
    raise ValueError(msg)


async def assert_feature(
    session: AsyncSession,
    user_id: uuid.UUID,
    feature: Feature,
) -> None:
    """Проверить, что пользователь может использовать ``feature``.

    Бросает 402 Payment Required при отсутствии доступа. Сами числовые
    квоты (``max_trees``, ``max_persons_per_tree``) эта функция **не**
    проверяет — они требуют count-запросов к доменным таблицам и должны
    быть встроены в конкретные endpoint'ы.
    """
    plan = await get_user_plan(session, user_id)
    limits = get_plan_limits(plan)
    if not feature_allowed(feature, limits):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=_payment_required_detail(feature, plan),
        )


# ----- check_entitlement (FastAPI dependency для billing-service own routes) -


def check_entitlement(feature: Feature) -> object:
    """FastAPI-dependency factory для feature-gating'а **внутри billing-service**.

    Использует ``get_session`` из billing-service. Для caller-сервисов
    предпочтительнее свой dependency через ``assert_feature`` — он
    использует session из своего собственного engine.
    """

    async def _dep(
        x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
        session: AsyncSession = Depends(get_session),
        settings: Settings = Depends(get_settings),
    ) -> None:
        if not settings.billing_enabled:
            return
        user_id = resolve_user_id_from_header(x_user_id)
        await assert_feature(session, user_id, feature)

    return Depends(_dep)


__all__ = [
    "Feature",
    "Plan",
    "PlanLimits",
    "assert_feature",
    "check_entitlement",
    "feature_allowed",
    "get_plan_limits",
    "get_user_plan",
    "resolve_user_id_from_header",
]
