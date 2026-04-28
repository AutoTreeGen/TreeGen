"""Email dispatch core — idempotency + opt-out + render + Resend (Phase 12.2).

Контракт ``dispatch_email``:

1. Lookup ``email_send_log`` по ``idempotency_key``. Если запись есть —
   возвращаем её снимок с ``deduplicated=True``. Никаких побочных
   эффектов.
2. Загружаем ``users`` для recipient. 404 если user не существует.
3. Если ``user.email_opt_out`` — пишем log-row со ``status=skipped_optout``,
   возвращаем не вызывая Resend.
4. Прогоняем ``params`` через ``redact_email_params`` (allowlist + DNA-rule).
5. Рендерим шаблон под ``user.locale``.
6. Если ``settings.enabled=False`` (dev/CI bypass) — log-row со
   ``status=skipped_optout``, не отправляем.
7. Иначе — вызываем Resend. Успех → ``status=sent`` + provider_message_id.
   Ошибка → ``status=failed`` + ``error``. В обоих случаях row остаётся,
   повторный POST с тем же idempotency_key вернёт cached result.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final

import httpx
from fastapi import HTTPException, status
from shared_models.enums import EmailKind, EmailSendStatus
from shared_models.orm import EmailSendLog, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_service.config import Settings
from email_service.services.redaction import redact_email_params
from email_service.services.resend_client import (
    ResendError,
    send_via_resend,
)
from email_service.services.templates import render_email

_LOG: Final = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchOutcome:
    """Результат ``dispatch_email`` — все поля mapping'аются на ORM-row."""

    log_id: uuid.UUID
    idempotency_key: str
    kind: EmailKind
    status: EmailSendStatus
    provider_message_id: str | None
    sent_at: dt.datetime | None
    error: str | None
    deduplicated: bool


async def dispatch_email(
    session: AsyncSession,
    settings: Settings,
    *,
    kind: EmailKind,
    recipient_user_id: uuid.UUID,
    idempotency_key: str,
    params: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> DispatchOutcome:
    """Главный entry-point. См. модульный docstring."""
    # 1. Idempotency lookup.
    existing = (
        await session.execute(
            select(EmailSendLog).where(EmailSendLog.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return DispatchOutcome(
            log_id=existing.id,
            idempotency_key=existing.idempotency_key,
            kind=EmailKind(existing.kind),
            status=EmailSendStatus(existing.status),
            provider_message_id=existing.provider_message_id,
            sent_at=existing.sent_at,
            error=existing.error,
            deduplicated=True,
        )

    # 2. Load user.
    user = await session.get(User, recipient_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {recipient_user_id} not found",
        )

    # 3. Redact params (defense-in-depth — call-sites уже должны
    #    фильтровать, но мы не доверяем им one-way).
    safe_params = redact_email_params(params)

    # Базовый ORM-row, далее мутируем status.
    log_row = EmailSendLog(
        idempotency_key=idempotency_key,
        kind=kind.value,
        recipient_user_id=user.id,
        status=EmailSendStatus.QUEUED.value,
        params=safe_params,
    )
    session.add(log_row)

    # 4. Opt-out / feature-flag check — пишем status=skipped_optout
    #    и НЕ вызываем Resend.
    if user.email_opt_out or not settings.enabled:
        log_row.status = EmailSendStatus.SKIPPED_OPTOUT.value
        await session.flush()
        _LOG.info(
            "Email skipped (opt_out=%s enabled=%s) kind=%s user=%s",
            user.email_opt_out,
            settings.enabled,
            kind.value,
            user.id,
        )
        return _outcome_from_row(log_row, deduplicated=False)

    # 5. Render template. Brand-context inject'ится автоматом — caller
    #    шлёт только specific-к-kind поля (amount, dates, и т.п.).
    context = {
        "brand_name": settings.brand_name,
        "support_email": settings.support_email,
        "web_base_url": settings.web_base_url.rstrip("/"),
        "user_display_name": user.display_name or user.email.split("@", maxsplit=1)[0],
        "locale": user.locale,
        **safe_params,
    }
    rendered = render_email(kind, user.locale, context)

    # 6. Send via Resend.
    try:
        result = await send_via_resend(
            settings,
            to=user.email,
            subject=rendered.subject,
            html_body=rendered.html_body,
            text_body=rendered.text_body,
            transport=transport,
        )
    except ResendError as exc:
        log_row.status = EmailSendStatus.FAILED.value
        log_row.error = str(exc)
        await session.flush()
        _LOG.warning(
            "Resend send failed kind=%s user=%s: %s",
            kind.value,
            user.id,
            exc,
        )
        return _outcome_from_row(log_row, deduplicated=False)

    log_row.status = EmailSendStatus.SENT.value
    log_row.provider_message_id = result.message_id
    log_row.sent_at = dt.datetime.now(dt.UTC)
    await session.flush()
    _LOG.info(
        "Email sent kind=%s user=%s message_id=%s",
        kind.value,
        user.id,
        result.message_id,
    )
    return _outcome_from_row(log_row, deduplicated=False)


def _outcome_from_row(row: EmailSendLog, *, deduplicated: bool) -> DispatchOutcome:
    return DispatchOutcome(
        log_id=row.id,
        idempotency_key=row.idempotency_key,
        kind=EmailKind(row.kind),
        status=EmailSendStatus(row.status),
        provider_message_id=row.provider_message_id,
        sent_at=row.sent_at,
        error=row.error,
        deduplicated=deduplicated,
    )


__all__ = ["DispatchOutcome", "dispatch_email"]
