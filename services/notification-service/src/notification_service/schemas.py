"""Pydantic-схемы notification-service API."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Каналы доставки, известные текущему скелету. Расширяется по мере
# появления реализаций (Phase 8.1 EmailChannel, 8.3 PushChannel).
KNOWN_CHANNELS = frozenset({"in_app", "log"})


class NotifyRequest(BaseModel):
    """Тело ``POST /notify``.

    Внутренний эндпоинт — вызывается другими сервисами (parser-service,
    dna-service, hypothesis_runner, etc.).

    ``payload.ref_id`` (если присутствует) попадёт в idempotency-ключ.
    Если payload не имеет осмысленного `ref_id`, передайте уникальный id
    события явно — иначе все события того же типа от того же user'а
    свалятся в одну строку.
    """

    user_id: int = Field(ge=1, description="Получатель.")
    event_type: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Тип события (см. shared_models.enums.NotificationEventType). Неизвестный тип → 400."
        ),
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    channels: list[str] = Field(
        default_factory=lambda: ["in_app", "log"],
        description="Имена каналов из KNOWN_CHANNELS. Неизвестный → 400.",
    )

    model_config = ConfigDict(extra="forbid")


class ChannelAttempt(BaseModel):
    """Запись попытки доставки одного канала.

    Дублирует структуру элементов ``Notification.channels_attempted``
    (JSONB-массив). Сохраняется dispatcher'ом после каждой попытки —
    позволяет UI отрисовать «доставлено в in_app, не доставлено в email
    из-за SMTP-таймаута».
    """

    channel: str
    success: bool
    error: str | None = None
    attempted_at: dt.datetime


class NotifyResponse(BaseModel):
    """Ответ ``POST /notify``."""

    notification_id: uuid.UUID
    delivered: list[str] = Field(
        description="Каналы, которые подтвердили доставку (success=True).",
    )
    deduplicated: bool = Field(
        default=False,
        description=(
            "True — повторная отправка свернулась к существующей "
            "нотификации (idempotency-окно). delivered тогда совпадает "
            "с прежним результатом, а второй INSERT не выполнялся."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class NotificationSummary(BaseModel):
    """Один элемент ``GET /users/me/notifications``."""

    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    delivered_at: dt.datetime | None = None
    read_at: dt.datetime | None = None
    created_at: dt.datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Ответ ``GET /users/me/notifications``."""

    user_id: int
    total: int
    unread: int
    limit: int
    offset: int
    items: list[NotificationSummary]


class MarkReadResponse(BaseModel):
    """Ответ ``PATCH /notifications/{id}/read``."""

    id: uuid.UUID
    read_at: dt.datetime
