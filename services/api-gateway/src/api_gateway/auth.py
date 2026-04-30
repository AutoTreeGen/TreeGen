"""api-gateway auth — Clerk JWT-based current-user resolution.

Зеркало ``parser_service.auth`` (см. ADR-0033). 503 при пустом
``clerk_issuer`` — fail-safe от misconfigured-окружения.
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

from api_gateway.config import Settings, get_settings
from api_gateway.database import get_session


def get_clerk_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkJwtSettings:
    """Собрать :class:`ClerkJwtSettings` из ``api_gateway.config.Settings``.

    503 если ``clerk_issuer`` не задан в env.
    """
    if not settings.clerk_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk authentication not configured: API_GATEWAY_CLERK_ISSUER is empty.",
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
    """401 если Bearer JWT отсутствует или невалиден."""
    return await _get_current_claims_unbound(request, clerk_settings)


async def get_current_user_id(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> uuid.UUID:
    """Resolve ``users.id`` UUID по Clerk-sub'у.

    Если row нет — 403 с пояснением (api-gateway не делает JIT-create;
    user должен быть создан через parser-service /users/me-flow или Clerk
    webhook).
    """
    result = await session.execute(
        select(User.id).where(User.clerk_user_id == claims.sub),
    )
    user_id = result.scalar_one_or_none()
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "User row not yet created. Visit any parser-service endpoint first, "
                "or wait for the Clerk webhook user.created event."
            ),
        )
    return user_id


# Type aliases — один импорт вместо длинной Annotated-цепочки.
RequireUser = Annotated[uuid.UUID, Depends(get_current_user_id)]
RequireClaims = Annotated[ClerkClaims, Depends(get_current_claims)]


__all__ = [
    "RequireClaims",
    "RequireUser",
    "get_clerk_settings",
    "get_current_claims",
    "get_current_user_id",
]
