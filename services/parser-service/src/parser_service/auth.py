"""``current_user`` FastAPI-зависимость — temporary Phase 11.0 stub.

Единая точка резолва текущего пользователя для permission-gate'ов
(см. :mod:`parser_service.services.permissions`). До Phase 4.10
полноценного Clerk JWT verify нет; стаб работает по двум путям:

1. **Заголовок ``X-User-Id``** — UUID существующего ``users.id``. Используется
   тестами и тулзингом, чтобы быстро притвориться разными пользователями
   без поднятия SSO. Если UUID не находится в БД — 401.
2. **Fallback ``settings.owner_email``** — ищет/создаёт one-and-only owner-user.
   Исторический single-tenant режим из Phase 5.1 (ADR-0027). Используется
   локальным dev-сервером, где dev один-в-один с владельцем.

Контракт стабильный: ``Annotated[User, Depends(get_current_user)]`` — Phase 4.10
заменит реализацию (Clerk verify → DB-lookup по ``external_auth_id``), но
сигнатура и тип возврата неизменны. Все call-site'ы Phase 11.0 уже используют
эту зависимость, так что переход — только тело функции.

Пример::

    from typing import Annotated
    from fastapi import Depends
    from parser_service.auth import get_current_user

    @router.post("/whoami")
    async def whoami(user: Annotated[User, Depends(get_current_user)]) -> dict[str, str]:
        return {"email": user.email}
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from shared_models.orm import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.config import Settings, get_settings
from parser_service.database import get_session


async def _ensure_owner_user(session: AsyncSession, email: str) -> User:
    """Найти/создать owner-user по email — fallback-путь.

    Делает то же что и legacy ``_ensure_owner`` из ``api.imports`` /
    ``api.familysearch``; в Phase 11.0 эти хелперы должны быть удалены и
    заменены на :func:`get_current_user`. До этой замены оба пути работают
    параллельно и идемпотентны (берут одного и того же пользователя по
    уникальному email).
    """
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        external_auth_id=f"local:{email}",
        display_name=email.split("@", maxsplit=1)[0],
        locale="en",
    )
    session.add(user)
    await session.flush()
    return user


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> User:
    """Резолвит текущего пользователя.

    TODO Phase 4.10 — заменить на Clerk JWT verify (signature stable):
    извлекать ``Authorization: Bearer <jwt>``, верифицировать через Clerk
    JWKS, искать ``users.external_auth_id == jwt.sub`` (или создавать
    при первом login'е). Тело функции меняется, return type и название
    параметра ``user`` в роутах остаются.

    Сейчас:

    * ``X-User-Id`` указан → ищем по UUID, 401 если не найден.
    * Иначе → ``settings.owner_email``, find-or-create.

    Raises:
        HTTPException 401: ``X-User-Id`` указан, но не валидный UUID или не существует.
    """
    if x_user_id:
        try:
            user_uuid = uuid.UUID(x_user_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-User-Id is not a valid UUID",
            ) from exc
        res = await session.execute(select(User).where(User.id == user_uuid))
        user = res.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"User {user_uuid} not found",
            )
        return user

    return await _ensure_owner_user(session, settings.owner_email)


CurrentUser = Annotated[User, Depends(get_current_user)]
"""Type alias для роутов: ``user: CurrentUser`` короче, чем длинный Annotated."""
