"""POST /telegram/webhook — receive Telegram update (Phase 14.0).

Контракт:
* Telegram передаёт ``X-Telegram-Bot-Api-Secret-Token`` header со
  значением, которое мы зарегистрировали через ``setWebhook(secret_token=...)``.
  Сравниваем constant-time через ``hmac.compare_digest``.
* Если секрет не сконфигурирован (``settings.webhook_secret`` пустой) —
  503, потому что без секрета любой может слать update'ы.
* Если payload не валиден как aiogram Update — 422 (Pydantic).
* После валидации — ``Dispatcher.feed_webhook_update`` обрабатывает
  команду; ответ боту (200 OK с пустым body или с reply-payload'ом)
  возвращается клиенту.

См. ADR-0040 §«Webhook security».
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Final

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status

from telegram_bot.config import Settings, get_settings
from telegram_bot.services import dispatcher as dispatcher_module

router = APIRouter(prefix="/telegram")
_LOG: Final = logging.getLogger(__name__)

_SECRET_HEADER: Final = "X-Telegram-Bot-Api-Secret-Token"


async def _get_dispatcher() -> Dispatcher:
    """FastAPI dependency-обёртка, чтобы её можно было override'ить в тестах."""
    return dispatcher_module.get_dispatcher()


async def _get_bot() -> Bot:
    """FastAPI dependency-обёртка для Bot."""
    return dispatcher_module.get_bot()


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Telegram webhook receiver (secret-validated)",
)
async def receive_webhook(
    settings: Annotated[Settings, Depends(get_settings)],
    dispatcher: Annotated[Dispatcher, Depends(_get_dispatcher)],
    bot: Annotated[Bot, Depends(_get_bot)],
    payload: Annotated[dict[str, object], Body(...)],
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Принять Telegram update, валидировать секрет, dispatch'нуть aiogram'у."""
    if not settings.webhook_secret:
        _LOG.error("webhook_secret not configured — refusing to process updates")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret not configured",
        )
    if x_telegram_bot_api_secret_token is None or not hmac.compare_digest(
        x_telegram_bot_api_secret_token,
        settings.webhook_secret,
    ):
        # 401 без тела — не подсказываем атакующему shape валидного запроса.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid secret token",
        )
    try:
        update = Update.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Telegram update: {exc}",
        ) from exc
    await dispatcher.feed_webhook_update(bot, update)
    return {"status": "ok"}
