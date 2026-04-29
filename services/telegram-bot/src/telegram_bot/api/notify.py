"""POST /telegram/notify — push сообщение залинкованному user'у (Phase 14.1, ADR-0056).

Internal endpoint, вызывается из notification-service:

* auth: ``X-Internal-Service-Token`` header сравнивается constant-time
  с ``settings.internal_service_token``. Без секрета endpoint возвращает
  503 (как webhook без webhook_secret);
* lookup: ``TelegramUserLink`` по ``user_id`` (active + subscribed);
* delivery: ``Bot.send_message`` через aiogram-инстанс.

Не-доставленный путь (no link / unsubscribed) — это **success-200** с
``delivered=false``: notification-service не должен retry'ить такие
случаи. Доставленный путь — 200 с ``delivered=true``.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Final

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from fastapi import APIRouter, Depends, Header, HTTPException, status
from shared_models.orm import TelegramUserLink
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_bot.config import Settings, get_settings
from telegram_bot.database import get_session
from telegram_bot.schemas import NotifyRequest, NotifyResponse
from telegram_bot.services import dispatcher as dispatcher_module

router = APIRouter(prefix="/telegram")
_LOG: Final = logging.getLogger(__name__)


async def _get_bot() -> Bot:
    """FastAPI dependency-обёртка для Bot. Override'ится в тестах."""
    return dispatcher_module.get_bot()


@router.post(
    "/notify",
    response_model=NotifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Internal: push notification message to linked Telegram chat (service-token auth)",
)
async def notify(
    body: NotifyRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    bot: Annotated[Bot, Depends(_get_bot)],
    x_internal_service_token: Annotated[str | None, Header()] = None,
) -> NotifyResponse:
    """Push ``body.message`` в Telegram chat залинкованного user'а.

    Возвращает 200 OK независимо от того, был ли push доставлен —
    ``delivered`` поле в response сообщает caller'у статус. Это
    минимизирует ложные retry'и на стороне notification-service: «нет
    линка» или «unsubscribed» — нормальное состояние, не failure.
    """
    if not settings.internal_service_token:
        _LOG.error("internal_service_token not configured — refusing /notify")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal service token not configured",
        )
    if x_internal_service_token is None or not hmac.compare_digest(
        x_internal_service_token,
        settings.internal_service_token,
    ):
        # 401 без тела — не подсказываем атакующему shape валидного запроса.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )

    res = await session.execute(
        select(TelegramUserLink).where(
            TelegramUserLink.user_id == body.user_id,
            TelegramUserLink.revoked_at.is_(None),
        )
    )
    link = res.scalar_one_or_none()
    if link is None:
        return NotifyResponse(delivered=False, reason="no_active_link")
    if not link.notifications_enabled:
        return NotifyResponse(delivered=False, reason="not_subscribed")

    try:
        await bot.send_message(chat_id=link.tg_chat_id, text=body.message)
    except TelegramAPIError as exc:
        # Telegram API ошибки (rate-limit, blocked-by-user, chat-not-found)
        # не делают endpoint-fail'ом: notification-service запишет их как
        # channel-attempt failure и пойдёт дальше (channel failure isolation).
        _LOG.warning(
            "telegram send_message failed for user_id=%s chat_id=%s: %s",
            body.user_id,
            link.tg_chat_id,
            exc,
        )
        return NotifyResponse(delivered=False, reason=f"telegram_api_error: {exc}")

    return NotifyResponse(delivered=True)
