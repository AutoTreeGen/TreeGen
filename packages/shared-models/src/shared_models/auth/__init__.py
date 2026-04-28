"""shared_models.auth — общая инфраструктура аутентификации (Phase 4.10, ADR-0033).

Re-exports:

* :class:`ClerkClaims` — структурный snapshot validated JWT claims.
* :func:`verify_clerk_jwt` — асинхронная проверка Bearer-токена от Clerk
  по JWKS (RS256). Кэширует ключи с TTL, бросает :class:`AuthError` при
  любой проблеме (signature / expiry / issuer).
* :class:`AuthError` — единая ошибка для FastAPI middleware/Depends.
* FastAPI helpers (только при наличии fastapi в среде):

  * :func:`get_current_claims` — `Depends(...)` для извлечения
    :class:`ClerkClaims` из ``Authorization: Bearer ...`` header'а.
  * :func:`get_current_claims_optional` — то же, но возвращает None
    если header отсутствует (для public endpoints с personalization'ом).

См. :mod:`shared_models.auth.clerk_jwt` и
:mod:`shared_models.auth.dependencies` для деталей.
"""

from __future__ import annotations

from shared_models.auth.clerk_jwt import (
    AuthError,
    ClerkClaims,
    ClerkJwtSettings,
    JwksCache,
    verify_clerk_jwt,
)
from shared_models.auth.dependencies import (
    get_current_claims,
    get_current_claims_optional,
)

__all__ = [
    "AuthError",
    "ClerkClaims",
    "ClerkJwtSettings",
    "JwksCache",
    "get_current_claims",
    "get_current_claims_optional",
    "verify_clerk_jwt",
]
