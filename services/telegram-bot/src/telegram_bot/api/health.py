"""Liveness probe (Phase 14.0).

Без external reachability check на ``api.telegram.org`` — каждый probe
не должен дёргать внешний API. Webhook-secret и bot-token — конфигурация,
exposed только как boolean-флаги (наличие/отсутствие), не значения.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from telegram_bot.config import Settings, get_settings
from telegram_bot.schemas import HealthResponse

router = APIRouter()


@router.get("/healthz", tags=["meta"], response_model=HealthResponse)
async def healthz(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Liveness + конфиг-флаги."""
    return HealthResponse(
        status="ok",
        bot_configured=bool(settings.bot_token),
        webhook_secret_configured=bool(settings.webhook_secret),
    )
