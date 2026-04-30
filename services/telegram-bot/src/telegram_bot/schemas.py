"""Pydantic-схемы для telegram-bot HTTP API.

Не дублирует aiogram's Update — для inbound webhook мы передаём
сырой payload в `Dispatcher.feed_webhook_update`. Эти схемы — только
для нашего собственного REST-у (`/telegram/link/confirm`).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class LinkConfirmRequest(BaseModel):
    """POST /telegram/link/confirm — body."""

    token: str = Field(
        ...,
        min_length=16,
        max_length=128,
        description="One-time link token, выданный ботом в /start.",
    )
    user_id: uuid.UUID = Field(
        ...,
        description=(
            "TreeGen user_id. В проде — извлекается из Clerk-JWT в "
            "api-gateway, передаётся в telegram-bot как trusted-header. "
            "Phase 14.0: caller передаёт явно (api-gateway-side trust)."
        ),
    )


class LinkConfirmResponse(BaseModel):
    """POST /telegram/link/confirm — response."""

    link_id: uuid.UUID
    user_id: uuid.UUID
    tg_chat_id: int
    linked_at: str  # ISO-8601 UTC


class HealthResponse(BaseModel):
    """GET /healthz — response."""

    status: str
    bot_configured: bool
    webhook_secret_configured: bool


class NotifyRequest(BaseModel):
    """POST /telegram/notify — body (Phase 14.1, ADR-0056).

    Internal endpoint, вызывается из notification-service после успешного
    создания Notification. Bot пушит ``message`` в Telegram чат
    залинкованного user'а, если ``notifications_enabled=True``.
    """

    user_id: uuid.UUID = Field(..., description="TreeGen user_id (Notification.user_id).")
    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Plain-text сообщение (Telegram cap 4096; берём 4000 с запасом).",
    )


class NotifyResponse(BaseModel):
    """POST /telegram/notify — response."""

    delivered: bool = Field(
        ...,
        description=(
            "True — сообщение отправлено через Bot.send_message. "
            "False — у user'а нет активного link или notifications_enabled=False."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable причина если delivered=False.",
    )
