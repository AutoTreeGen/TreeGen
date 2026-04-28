"""POST /email/send (Phase 12.2)."""

from __future__ import annotations

import logging
from typing import Annotated, Final

import httpx
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from email_service.config import Settings, get_settings
from email_service.database import get_session
from email_service.schemas import SendRequest, SendResponse
from email_service.services.dispatcher import dispatch_email

router = APIRouter(prefix="/email")
_LOG: Final = logging.getLogger(__name__)


# Хранится отдельно от модуля для тестов: tests/conftest подменяет
# ``_TRANSPORT`` на ``httpx.MockTransport`` через monkeypatch.
_TRANSPORT: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Тестовая утилита: подменить httpx-transport (Resend mock)."""
    global _TRANSPORT  # noqa: PLW0603
    _TRANSPORT = transport


@router.post(
    "/send",
    response_model=SendResponse,
    status_code=status.HTTP_200_OK,
    summary="Отправить transactional-email (idempotent)",
)
async def send_email(
    body: SendRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SendResponse:
    """Идемпотентно отправить email. См. ADR-0039.

    * 200 — отправлено / dedup'нуто / opt-out skip / failed (status в body).
    * 404 — recipient_user_id не существует.
    * 422 — body не валиден (Pydantic, неизвестный kind).

    ``status=failed`` НЕ значит 5xx HTTP — это provider-side ошибка,
    запись остаётся в БД и caller может ретраить с тем же
    idempotency_key (мы вернём cached failure → caller увидит и
    пере-генерит ключ если нужно).
    """
    outcome = await dispatch_email(
        session,
        settings,
        kind=body.kind,
        recipient_user_id=body.recipient_user_id,
        idempotency_key=body.idempotency_key,
        params=body.params,
        transport=_TRANSPORT,
    )
    return SendResponse(
        id=outcome.log_id,
        idempotency_key=outcome.idempotency_key,
        kind=outcome.kind,
        status=outcome.status,
        provider_message_id=outcome.provider_message_id,
        deduplicated=outcome.deduplicated,
        sent_at=outcome.sent_at,
        error=outcome.error,
    )
