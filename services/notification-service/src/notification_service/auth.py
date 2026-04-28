"""notification-service-specific auth (Phase 4.10, ADR-0033).

См. :mod:`parser_service.auth`. Здесь мы возвращаем ``int``-user_id,
потому что :class:`shared_models.orm.Notification.user_id` — BigInteger
(Phase 8.0 quirk, см. notification.py docstring). Этот сервис
поддерживает legacy ``X-User-Id`` header'а **в дополнение** к Bearer
JWT, чтобы внутренние интегрейшены (parser-service → notify) не
ломались мгновенно: они уже шлют ``user_id`` int напрямую через
``POST /notify`` (это internal endpoint, не end-user).

End-user endpoint'ы (``/users/me/notifications``,
``/notifications/{id}/read``) принимают ТОЛЬКО Bearer JWT — старый
``X-User-Id``-mock остаётся доступен только под ENV-флагом
``NOTIFICATION_SERVICE_DEBUG=true`` для backwards-compat в тестах.

Резолюция Clerk-sub'а в int user_id:

* Lookup по :attr:`User.clerk_user_id` → :attr:`User.id` UUID.
* :func:`uuid_to_legacy_int` — детерминированный преобразователь UUID →
  int (старшие 63 bits hex). Notification.user_id хранит это число.
  Идемпотентно: один UUID → один int.

Это deliberate stop-gap до Phase 8.x miграции Notification.user_id на
UUID FK. См. ADR-0033 §«Notification user_id type».
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from shared_models.auth import (
    ClerkClaims,
    ClerkJwtSettings,
)
from shared_models.auth import (
    get_current_claims as _get_current_claims_unbound,
)
from shared_models.orm import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.config import Settings, get_settings
from notification_service.database import get_session


def get_clerk_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkJwtSettings:
    if not settings.clerk_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Clerk authentication not configured: NOTIFICATION_SERVICE_CLERK_ISSUER is empty."
            ),
        )
    return ClerkJwtSettings(
        issuer=settings.clerk_issuer,
        jwks_url=settings.clerk_jwks_url or None,
        audience=settings.clerk_audience or None,
    )


async def get_current_claims(
    request: Request,
    clerk_settings: Annotated[ClerkJwtSettings, Depends(get_clerk_settings)],
) -> ClerkClaims:
    return await _get_current_claims_unbound(request, clerk_settings)


def uuid_to_legacy_int(user_uuid: uuid.UUID) -> int:
    """UUID → стабильный 63-bit non-negative int.

    Берём старшие 63 bits ``int.bytes``, очищаем sign-bit. Коллизий на
    практике не будет (2^63 ≈ 9.2e18, user'ов не миллиарды).
    """
    full = int.from_bytes(user_uuid.bytes, "big")
    return full >> 65  # отрезаем низшие 65 бит → влезает в signed int63


async def get_current_user_id(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> int:
    """Resolved Notification.user_id (int).

    Lookup users по clerk_user_id; если row нет — 403 с пояснением
    про webhook/JIT в parser-service.
    """
    user = (
        await session.execute(select(User).where(User.clerk_user_id == claims.sub))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "User row not yet created. Visit any parser-service endpoint first, "
                "or wait for the Clerk webhook user.created event."
            ),
        )
    return uuid_to_legacy_int(user.id)


RequireUserId = Annotated[int, Depends(get_current_user_id)]
RequireClaims = Annotated[ClerkClaims, Depends(get_current_claims)]


__all__ = [
    "RequireClaims",
    "RequireUserId",
    "get_clerk_settings",
    "get_current_claims",
    "get_current_user_id",
    "uuid_to_legacy_int",
]
