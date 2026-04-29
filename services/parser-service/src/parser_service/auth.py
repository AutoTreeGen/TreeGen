"""Parser-service auth — Clerk JWT-based current-user resolution.

Объединяет Phase 4.10 Clerk-flow (RequireUser, RequireClaims) и Phase 11.0
permission-gate API (CurrentUser → возврат полной :class:`User`-row).

Контракт:

* :data:`RequireClaims` (``Annotated[ClerkClaims, Depends(...)]``) —
  validated JWT-claims.
* :data:`RequireUser` (``Annotated[uuid.UUID, Depends(...)]``) — only
  ``users.id``; для большинства endpoint'ов достаточно.
* :data:`CurrentUser` (``Annotated[User, Depends(...)]``) — полная row;
  используется permission-gate'ами Phase 11.0 (нужны ``email``,
  ``locale``, ``deleted_at``, ...).

503 если ``clerk_issuer`` не задан в env — fail-safe от
misconfigured-окружения молча выпускающего unauthenticated user'ов.
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
from shared_models.orm import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.services.user_sync import (
    get_or_create_user_from_clerk,
    get_user_id_from_clerk,
)


def get_clerk_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkJwtSettings:
    """Собрать :class:`ClerkJwtSettings` из ``parser_service.config.Settings``.

    503 если ``clerk_issuer`` не задан в env.
    """
    if not settings.clerk_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk authentication not configured: PARSER_SERVICE_CLERK_ISSUER is empty.",
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


async def get_current_user(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Вернуть полную :class:`User` row (JIT-создаётся при необходимости).

    Используется permission-gate'ами Phase 11.0 (см.
    ``parser_service.services.permissions``), которым нужны не только
    UUID, но и email/locale/deleted_at для policy-decisions.

    На production hot-path предпочитайте :func:`get_current_user_id` —
    он возвращает только UUID и не материализует row, если row уже
    существует. Этот хелпер всегда делает round-trip в DB.
    """
    return await get_or_create_user_from_clerk(session, claims)


# Type aliases — один импорт вместо длинной Annotated-цепочки.
RequireUser = Annotated[uuid.UUID, Depends(get_current_user_id)]
RequireClaims = Annotated[ClerkClaims, Depends(get_current_claims)]
OptionalClaims = Annotated[ClerkClaims | None, Depends(get_current_claims_optional)]
CurrentUser = Annotated[User, Depends(get_current_user)]


__all__ = [
    "CurrentUser",
    "OptionalClaims",
    "RequireClaims",
    "RequireUser",
    "get_clerk_settings",
    "get_current_claims",
    "get_current_claims_optional",
    "get_current_user",
    "get_current_user_id",
]


# Suppress F401 for select used only in legacy paths historically.
_ = select
