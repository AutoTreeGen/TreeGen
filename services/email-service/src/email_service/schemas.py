"""Pydantic-схемы запросов/ответов email-service."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from shared_models.enums import EmailKind, EmailSendStatus


class SendRequest(BaseModel):
    """Тело ``POST /email/send``."""

    model_config = ConfigDict(frozen=True)

    kind: EmailKind = Field(description="Тип email (см. EmailKind).")
    recipient_user_id: uuid.UUID = Field(description="users.id получателя.")
    idempotency_key: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Caller-supplied строка для идемпотентности. Например "
            "stripe_event_id для billing-событий."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-PII payload, передаваемый в шаблон. См. ADR-0039.",
    )


class SendResponse(BaseModel):
    """Ответ ``POST /email/send``."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    idempotency_key: str
    kind: EmailKind
    status: EmailSendStatus
    provider_message_id: str | None = None
    deduplicated: bool = Field(
        description="True если запрос — дубль (idempotency hit), без новой отправки.",
    )
    sent_at: dt.datetime | None = None
    error: str | None = None
