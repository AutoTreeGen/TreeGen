"""Notification API endpoints (Phase 8.0 → 4.10 auth).

Internal:
* ``POST /notify`` — создать и доставить нотификацию (вызывают другие
  сервисы).

End-user (Bearer JWT issued by Clerk — Phase 4.10, ADR-0033):
* ``GET /users/me/notifications`` — список с фильтром unread + пагинация.
* ``PATCH /notifications/{id}/read`` — отметить прочитанной.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import Notification
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.auth import RequireUserId
from notification_service.config import get_settings
from notification_service.database import get_session
from notification_service.schemas import (
    KNOWN_CHANNELS,
    MarkReadResponse,
    NotificationListResponse,
    NotificationSummary,
    NotifyRequest,
    NotifyResponse,
)
from notification_service.services.dispatcher import (
    UnknownChannelError,
    UnknownEventTypeError,
    dispatch,
)

router = APIRouter()


@router.post(
    "/notify",
    response_model=NotifyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Internal: create and dispatch a notification",
)
async def notify(
    body: NotifyRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotifyResponse:
    """Создать (или вернуть существующую за окно идемпотентности) нотификацию.

    400 при неизвестном ``event_type`` или ``channels``.
    201 (или эквивалент при dedup) с ``deduplicated=true``.
    """
    # Раннее отсечение неизвестных каналов — даже до dispatcher'а это
    # удобнее: один валидационный path на API-границе.
    unknown = set(body.channels) - KNOWN_CHANNELS
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown channels: {sorted(unknown)}",
        )

    settings = get_settings()
    try:
        outcome = await dispatch(
            session,
            user_id=body.user_id,
            event_type=body.event_type,
            payload=body.payload,
            channels=body.channels,
            idempotency_window_minutes=settings.idempotency_window_minutes,
        )
    except UnknownEventTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UnknownChannelError as exc:  # pragma: no cover — отсечётся выше
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return NotifyResponse(
        notification_id=outcome.notification_id,
        delivered=outcome.delivered_channels,
        deduplicated=outcome.deduplicated,
        skipped_by_pref=outcome.skipped_by_pref,
    )


@router.get(
    "/users/me/notifications",
    response_model=NotificationListResponse,
    summary="End-user: paginated notifications for the current user",
)
async def list_user_notifications(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: RequireUserId,
    unread: bool = Query(
        default=False,
        description=(
            "Если True — возвращаются только нотификации с read_at IS NULL. "
            "По умолчанию выдаются все."
        ),
    ),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> NotificationListResponse:
    """Список нотификаций текущего пользователя (sorted by created_at desc)."""
    base = select(Notification).where(Notification.user_id == user_id)
    if unread:
        base = base.where(Notification.read_at.is_(None))
    base = base.order_by(Notification.created_at.desc())

    total = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            *([Notification.read_at.is_(None)] if unread else []),
        )
    )
    unread_total = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )

    rows_res = await session.execute(base.limit(limit).offset(offset))
    items = [NotificationSummary.model_validate(n) for n in rows_res.scalars().all()]

    return NotificationListResponse(
        user_id=user_id,
        total=int(total or 0),
        unread=int(unread_total or 0),
        limit=limit,
        offset=offset,
        items=items,
    )


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=MarkReadResponse,
    summary="End-user: mark a notification as read",
)
async def mark_read(
    notification_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: RequireUserId,
) -> MarkReadResponse:
    """Идемпотентно проставить ``read_at = NOW()`` для нотификации.

    404 если нотификации нет или она принадлежит другому пользователю
    (тот же ответ — чтобы не утекало "user X has notification Y").
    Повторный вызов — без изменений (сохраняется первоначальный ``read_at``).
    """
    res = await session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notification = res.scalar_one_or_none()
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification {notification_id} not found",
        )
    if notification.read_at is None:
        notification.read_at = dt.datetime.now(dt.UTC)
        await session.flush()

    # mypy/runtime guard: после ветки выше read_at гарантированно не None.
    assert notification.read_at is not None
    return MarkReadResponse(id=notification.id, read_at=notification.read_at)
