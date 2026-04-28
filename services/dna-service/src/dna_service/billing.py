"""Tonkий wrapper над billing-service entitlement checks для dna-service.

См. ``parser_service.billing`` для обоснования pattern'а.
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

from dna_service.database import get_session


def require_feature(feature: Feature) -> object:
    """Фабрика FastAPI dependency'и для feature-gating'а в dna-service."""

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
