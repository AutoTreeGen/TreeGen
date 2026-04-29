"""dna-service-specific FastAPI Depends-обёртки над shared_models.auth.

См. :mod:`parser_service.auth` — здесь та же идея, продублированная
для изоляции сервисов (каждый держит свой ``Settings``-класс).
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

from dna_service.config import Settings, get_settings
from dna_service.database import get_session


def get_clerk_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkJwtSettings:
    """503 если ``clerk_issuer`` не сконфигурирован."""
    if not settings.clerk_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk authentication not configured: DNA_SERVICE_CLERK_ISSUER is empty.",
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


async def get_current_user_id(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> uuid.UUID:
    """Resolved ``users.id``. dna-service не делает JIT-create.

    DNA-данные — special category (GDPR Art. 9, ADR-0012). User row
    должен существовать (создаётся при первом обращении к parser-service
    через JIT, либо webhook'ом). Если row нет — 403, потому что DNA
    endpoint без user-record'а — bug, не норма.
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
    return user.id


RequireUser = Annotated[uuid.UUID, Depends(get_current_user_id)]
RequireClaims = Annotated[ClerkClaims, Depends(get_current_claims)]


__all__ = [
    "RequireClaims",
    "RequireUser",
    "get_clerk_settings",
    "get_current_claims",
    "get_current_user_id",
]
