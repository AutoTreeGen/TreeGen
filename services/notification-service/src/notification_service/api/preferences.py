"""Notification preferences API (Phase 8.0 wire-up, ADR-0029).

End-user (auth = mock через ``X-User-Id`` header — Phase 4 заменит на JWT):

* ``GET /users/me/notification-preferences`` — карта (event_type →
  enabled + channels) для всех известных типов; дефолты материализуются
  на лету.
* ``PATCH /users/me/notification-preferences/{event_type}`` — upsert
  одной строки.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from shared_models.enums import NotificationEventType
from shared_models.orm import NotificationPreference
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.database import get_session
from notification_service.schemas import (
    KNOWN_CHANNELS,
    PreferenceItem,
    PreferenceListResponse,
    PreferenceUpdateRequest,
    PreferenceUpdateResponse,
)

router = APIRouter()


# Дефолты применяются, когда у пользователя нет строки для event_type.
# Совпадают с дефолтами в ORM (NotificationPreference.channels) и в
# Alembic-миграции 0012 — три места правды, должны держаться синхронно.
DEFAULT_ENABLED = True
DEFAULT_CHANNELS: tuple[str, ...] = ("in_app", "log")


def _resolve_user_id(x_user_id: str | None) -> int:
    """Mock auth: вытащить user_id из ``X-User-Id`` header.

    Дублирует логику из ``api/notifications.py``: реальный auth заменит
    оба места одновременно. Не вытаскиваем в shared util — Phase 4
    сделает один JWT-dependency.
    """
    if x_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id header (mock auth — Phase 8.0).",
        )
    try:
        return int(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id must be a positive integer.",
        ) from exc


@router.get(
    "/users/me/notification-preferences",
    response_model=PreferenceListResponse,
    summary="End-user: notification preferences map",
)
async def list_preferences(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> PreferenceListResponse:
    """Вернуть карту prefs для всех известных event_type'ов.

    Стратегия: один запрос за всеми сохранёнными prefs пользователя,
    дальше зашиваем дефолты для отсутствующих. Это даёт UI один
    стабильный ответ вне зависимости от того, насколько user'а раньше
    «наклацал» в settings — frontend не должен отслеживать
    «сохранил/не сохранил».
    """
    user_id = _resolve_user_id(x_user_id)

    rows = (
        (
            await session.execute(
                select(NotificationPreference).where(NotificationPreference.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    by_type = {row.event_type: row for row in rows}

    items: list[PreferenceItem] = []
    for event_type in NotificationEventType:
        existing = by_type.get(event_type.value)
        if existing is None:
            items.append(
                PreferenceItem(
                    event_type=event_type.value,
                    enabled=DEFAULT_ENABLED,
                    channels=list(DEFAULT_CHANNELS),
                    is_default=True,
                )
            )
        else:
            items.append(
                PreferenceItem(
                    event_type=existing.event_type,
                    enabled=existing.enabled,
                    channels=list(existing.channels),
                    is_default=False,
                )
            )

    return PreferenceListResponse(user_id=user_id, items=items)


@router.patch(
    "/users/me/notification-preferences/{event_type}",
    response_model=PreferenceUpdateResponse,
    summary="End-user: upsert one preference row",
)
async def update_preference(
    event_type: str,
    body: PreferenceUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> PreferenceUpdateResponse:
    """Upsert ``(user_id, event_type)`` row.

    Валидация:

    * ``event_type`` должен быть известен — иначе 404 (а не 400):
      ресурс «настройка для несуществующего события» не существует.
    * Хотя бы одно из ``enabled`` / ``channels`` должно быть в body —
      иначе 400 (no-op запрос).
    * Каналы в ``channels`` — из ``KNOWN_CHANNELS`` (in_app, log).
      Неизвестный → 400.
    """
    user_id = _resolve_user_id(x_user_id)

    if event_type not in {e.value for e in NotificationEventType}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown event_type: {event_type!r}",
        )
    if body.enabled is None and body.channels is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'enabled' or 'channels' must be set.",
        )
    if body.channels is not None:
        unknown = set(body.channels) - KNOWN_CHANNELS
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown channels: {sorted(unknown)}",
            )

    existing = (
        await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.event_type == event_type,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        # Insert: применяем дефолты для пропущенных полей.
        new_row = NotificationPreference(
            user_id=user_id,
            event_type=event_type,
            enabled=body.enabled if body.enabled is not None else DEFAULT_ENABLED,
            channels=(list(body.channels) if body.channels is not None else list(DEFAULT_CHANNELS)),
        )
        session.add(new_row)
        await session.flush()
        return PreferenceUpdateResponse(
            user_id=user_id,
            event_type=event_type,
            enabled=new_row.enabled,
            channels=list(new_row.channels),
        )

    # Update: только присланные поля.
    if body.enabled is not None:
        existing.enabled = body.enabled
    if body.channels is not None:
        existing.channels = list(body.channels)
    await session.flush()
    return PreferenceUpdateResponse(
        user_id=user_id,
        event_type=existing.event_type,
        enabled=existing.enabled,
        channels=list(existing.channels),
    )
