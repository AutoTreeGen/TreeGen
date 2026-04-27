"""Notification — нотификация пользователю (Phase 8.0, ADR-0024).

Хранит факт события + результаты доставки по каналам (in-app, log,
будущие email / push). Idempotency обеспечивается через
``idempotency_key`` + 1-часовое окно — подробнее в
``services/notification-service/services/dispatcher.py`` и в
миграции 0006.

Поле ``user_id`` сейчас просто ``BigInteger`` без FK на ``users``
таблицу — она появится с auth-слоем (Phase 4 follow-up). Когда auth
появится, добавим FK миграцией. Это сознательный технический долг,
зафиксированный в ADR-0024 §Контекст.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class Notification(IdMixin, TimestampMixin, Base):
    """Запись нотификации.

    Атрибуты:
        id: UUIDv7.
        user_id: Получатель. ``BigInteger`` без FK — см. модульный
            docstring.
        event_type: Тип события — соответствует ``NotificationEventType``
            из ``shared_models.enums``. Хранится как text, валидация
            на API/dispatcher уровне.
        payload: Произвольный JSON-словарь, специфичный для типа
            события (например ``{"hypothesis_id": ..., "tree_id": ...}``
            для ``hypothesis_pending_review``). Дешевле миграций, чем
            нормализованные колонки на каждый тип.
        idempotency_key: Канонический ключ
            ``{user_id}:{event_type}:{ref_id}``, формируется на
            dispatcher-стороне (см. брифу Phase 8.0). Уникальность
            строгая — через partial unique index по строкам, созданным
            за последний час (см. миграцию 0006). Повторная попытка
            той же тройки в окне 1 час → существующий
            ``notification_id`` без дубля.
        channels_attempted: JSON-список попыток доставки. Каждый
            элемент — ``{"channel": "in_app", "success": true,
            "error": null, "attempted_at": "..."}``. Записывает
            dispatcher после каждой попытки канала, чтобы
            channel-failure-isolation был аудитируем.
        delivered_at: Момент успешной доставки **хотя бы одним**
            каналом. ``None`` пока ни один канал не подтвердил.
        read_at: Момент пометки «прочитано» через
            ``PATCH /notifications/{id}/read``. ``None`` — unread.
            Используется для unread-counter в шапке UI.
    """

    __tablename__ = "notifications"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    channels_attempted: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ``id`` (UUID), ``created_at``, ``updated_at`` — из IdMixin / TimestampMixin.
    # Явно объявляем дополнительный созданный аннотированно для
    # читабельности — но сам столбец уже есть в TimestampMixin.

    def __repr__(self) -> str:  # pragma: no cover — debug helper
        return (
            f"Notification(id={self.id!s}, user_id={self.user_id}, "
            f"event_type={self.event_type!r}, "
            f"delivered={self.delivered_at is not None}, "
            f"read={self.read_at is not None})"
        )


__all__ = ["Notification"]
