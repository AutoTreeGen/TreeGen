"""FastAPI Depends helpers для Clerk-аутентификации (Phase 4.10, ADR-0033).

Сервисы (parser/dna/notification) импортируют отсюда два хелпера:

* :func:`get_current_claims` — обязательный auth: 401, если нет
  валидного Bearer JWT.
* :func:`get_current_claims_optional` — мягкий вариант для public
  endpoints, возвращает None при отсутствии header'а.

ClerkJwtSettings берётся из `ClerkJwtSettingsDep` — каждый сервис
регистрирует свой ``Depends`` на ``Settings`` (например, из pydantic-
settings) и **передаёт** ``ClerkJwtSettings``-фабрику через
:func:`build_auth_router`. Это нужно, чтобы shared-models не зависел
от конкретного ``Settings``-класса каждого сервиса.

Пример wiring (в каждом сервисе):

.. code-block:: python

    # services/parser-service/src/parser_service/auth.py
    from shared_models.auth import (
        ClerkJwtSettings,
        get_current_claims as _get_current_claims_unbound,
    )
    from .config import get_settings

    def get_clerk_settings(settings = Depends(get_settings)) -> ClerkJwtSettings:
        return ClerkJwtSettings(
            issuer=settings.clerk_issuer,
            audience=settings.clerk_audience or None,
        )

    async def get_current_claims(
        request: Request,
        clerk_settings: ClerkJwtSettings = Depends(get_clerk_settings),
    ) -> ClerkClaims:
        return await _get_current_claims_unbound(request, clerk_settings)

В реальной жизни этот boilerplate минимален: shared-models экспортирует
готовый ``get_current_claims``, который ожидает ``ClerkJwtSettings``
через ``Depends(get_clerk_jwt_settings)`` — вызывающий определяет одну
функцию ``get_clerk_jwt_settings`` под свои настройки.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Request, status

from shared_models.auth.clerk_jwt import (
    AuthError,
    ClerkClaims,
    ClerkJwtSettings,
    verify_clerk_jwt,
)

# Префикс Bearer-token'а в Authorization header'е. Регистронезависимо,
# но мы матчим строго в нижнем регистре (см. _split_authorization).
_BEARER_PREFIX_LOWER = "bearer "


def _split_authorization(header_value: str | None) -> str | None:
    """Извлечь raw JWT из ``Authorization: Bearer <token>``.

    Возвращает ``None``, если header отсутствует, пустой или не bearer-
    схема. Случай когда схема bearer но token пустой → тоже None
    (caller трактует как «нет аутентификации»).
    """
    if not header_value:
        return None
    stripped = header_value.strip()
    if not stripped.lower().startswith(_BEARER_PREFIX_LOWER):
        return None
    token = stripped[len(_BEARER_PREFIX_LOWER) :].strip()
    return token or None


# Каждый сервис должен предоставить свой ``Depends``-callable, который
# возвращает :class:`ClerkJwtSettings` (обычно — из pydantic-settings
# Settings класса). Здесь — type-alias, чтобы можно было typehint'ить.
ClerkSettingsDependency = Annotated[ClerkJwtSettings, "must be supplied via Depends in service"]


async def get_current_claims(
    request: Request,
    clerk_settings: ClerkJwtSettings,
) -> ClerkClaims:
    """Извлечь и провалидировать Clerk JWT из ``Authorization`` header'а.

    Raises:
        HTTPException 401, если header отсутствует, неправильно
        отформатирован, либо токен не прошёл верификацию.
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    token = _split_authorization(auth_header)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await verify_clerk_jwt(token, clerk_settings)
    except AuthError as exc:
        # Текст наружу не отдаём — может содержать namespace'и issuer'а;
        # в логи всё попадает.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_claims_optional(
    request: Request,
    clerk_settings: ClerkJwtSettings,
) -> ClerkClaims | None:
    """Like :func:`get_current_claims`, но None при отсутствии header'а.

    Если header **есть**, но токен битый — всё равно 401: мы не хотим
    тихо игнорировать сломанные авт-попытки (могут быть симптомом
    проблемы у клиента).
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    token = _split_authorization(auth_header)
    if token is None:
        return None
    try:
        return await verify_clerk_jwt(token, clerk_settings)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


__all__ = [
    "ClerkSettingsDependency",
    "get_current_claims",
    "get_current_claims_optional",
]
