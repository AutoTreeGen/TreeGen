"""Parser-service-specific FastAPI Depends-обёртки над shared_models.auth.

Сервис связывает свой ``Settings``-класс с ``ClerkJwtSettings`` и
делает ``get_current_claims``/``require_user`` готовыми к импорту в
endpoint'ах.

Использование в endpoint'ах:

.. code-block:: python

    from parser_service.auth import RequireUser

    @router.get("/me")
    async def me(user_id: RequireUser) -> Response:
        ...

``RequireUser`` — type alias на ``Annotated[uuid.UUID, Depends(...)]``
который JIT-создаёт ``users``-row если её ещё нет.
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
from shared_models.auth import (
    get_current_claims_optional as _get_current_claims_optional_unbound,
)
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.services.user_sync import get_user_id_from_clerk


def get_clerk_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkJwtSettings:
    """Собрать :class:`ClerkJwtSettings` из ``parser_service.config.Settings``.

    503 если ``clerk_issuer`` не задан в env. Это гарантия, что
    misconfigured-окружение не "молча" выпустит unauthenticated user'а.
    """
    if not settings.clerk_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Clerk authentication not configured: PARSER_SERVICE_CLERK_ISSUER is empty."),
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
    """401 если Bearer JWT отсутствует или не валиден."""
    return await _get_current_claims_unbound(request, clerk_settings)


async def get_current_claims_optional(
    request: Request,
    clerk_settings: Annotated[ClerkJwtSettings, Depends(get_clerk_settings)],
) -> ClerkClaims | None:
    """None если Bearer header не пришёл; 401 при невалидном."""
    return await _get_current_claims_optional_unbound(request, clerk_settings)


async def get_current_user_id(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> uuid.UUID:
    """Вернуть ``users.id`` UUID, JIT-создавая row если её ещё нет."""
    return await get_user_id_from_clerk(session, claims)


# Удобные type-aliases для endpoint'ов: один импорт вместо длинной
# annotation-цепочки.
RequireUser = Annotated[uuid.UUID, Depends(get_current_user_id)]
RequireClaims = Annotated[ClerkClaims, Depends(get_current_claims)]
OptionalClaims = Annotated[ClerkClaims | None, Depends(get_current_claims_optional)]


__all__ = [
    "OptionalClaims",
    "RequireClaims",
    "RequireUser",
    "get_clerk_settings",
    "get_current_claims",
    "get_current_claims_optional",
    "get_current_user_id",
]
