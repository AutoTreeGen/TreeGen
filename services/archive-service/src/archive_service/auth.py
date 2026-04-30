"""archive-service Clerk-auth dependencies (см. ADR-0033).

Сервис не пишет в нашу БД (Phase 9.0 read-only proxy), поэтому здесь
только проверка JWT и извлечение ``ClerkClaims.sub`` как user_id.
JIT-create / lookup пользователя в БД — забота parser-service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from shared_models.auth import (
    ClerkClaims,
    ClerkJwtSettings,
)
from shared_models.auth import (
    get_current_claims as _get_current_claims_unbound,
)

from archive_service.config import Settings, get_settings


def get_clerk_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkJwtSettings:
    """503 если ``clerk_issuer`` не сконфигурирован."""
    if not settings.clerk_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Clerk authentication not configured: ARCHIVE_SERVICE_CLERK_ISSUER is empty."),
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
    """Bearer-токен Clerk → :class:`ClerkClaims`. 401 при невалидности."""
    return await _get_current_claims_unbound(request, clerk_settings)


def get_current_user_id(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
) -> str:
    """Возвращает Clerk-``sub`` (используется как logical user_id для FS-квоты).

    Не делаем lookup в users-таблице — archive-service не имеет engine-а к БД.
    Если в будущем понадобится связывать с users.id (UUID), это будет
    сделано через X-User-Id header от api-gateway или через JIT-lookup
    с DI-фабрикой.
    """
    return claims.sub
