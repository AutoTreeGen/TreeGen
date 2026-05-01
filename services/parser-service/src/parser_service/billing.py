"""Tonkий wrapper над billing-service entitlement checks (Phase 12.0).

Зачем модуль: ``billing_service.services.entitlements.check_entitlement``
использует ``Depends(get_session)`` из billing-service, что требует
``init_engine`` на чужом engine'е. Здесь мы переиспользуем те же
бизнес-функции (``assert_feature``, ``resolve_user_id_from_header``)
но через **наш** ``parser_service.database.get_session``.

Дополнительно: parser-service пока работает в legacy single-owner режиме
(см. ``settings.owner_email``). Mock auth через ``X-User-Id`` header
поддерживается, но НЕ обязателен — отсутствие header'а в legacy-режиме
не должно ломать существующие тесты. Поэтому при ``billing_enabled=false``
и при отсутствии header'а гейт пропускает запрос (это эквивалент
single-owner = PRO до того как Clerk auth подключится в Phase 4.10).
"""

from __future__ import annotations

from typing import Annotated

from billing_service.config import Settings as BillingSettings
from billing_service.config import get_settings as get_billing_settings
from billing_service.services.entitlements import (
    Feature,
    assert_feature,
    resolve_user_id_from_header,
)
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session


def require_feature(feature: Feature) -> object:
    """Фабрика FastAPI dependency'и для feature-gating'а в parser-service.

    Поведение:

    * ``BILLING_SERVICE_BILLING_ENABLED=false`` (default в local dev) →
      пропускаем все проверки.
    * Есть ``X-User-Id`` header → резолвим user_id, проверяем feature.
    * Header отсутствует **и** billing_enabled=true → 401 (это явный
      misconfiguration: production включил биллинг, но фронт не шлёт
      user_id).

    402 Payment Required при отказе с structured detail (frontend
    парсит ``upgrade_url`` и ``feature``).
    """

    async def _dep(
        session: Annotated[AsyncSession, Depends(get_session)],
        billing_settings: Annotated[BillingSettings, Depends(get_billing_settings)],
        x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    ) -> None:
        if not billing_settings.billing_enabled:
            return
        user_id = resolve_user_id_from_header(x_user_id)
        await assert_feature(session, user_id, feature)

    return Depends(_dep)


__all__ = ["require_feature"]
