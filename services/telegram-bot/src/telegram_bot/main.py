"""FastAPI entry point для telegram-bot (Phase 14.0).

Запуск:
    uv run uvicorn telegram_bot.main:app --reload --port 8006
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from telegram_bot.api import health, link, webhook
from telegram_bot.config import get_settings
from telegram_bot.database import dispose_engine, init_engine
from telegram_bot.services.dispatcher import (
    init_bot,
    init_dispatcher,
    shutdown_bot,
)
from telegram_bot.services.link_tokens import LinkTokenStore


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Инициализировать engine/redis/bot при старте, закрыть при shutdown."""
    settings = get_settings()
    init_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    link_tokens = LinkTokenStore(redis, ttl_seconds=settings.link_ttl_seconds)
    link.set_redis(redis)
    link.set_link_tokens(link_tokens)
    init_bot(
        bot_token=settings.bot_token,
        bot_api_base_url=settings.bot_api_base_url,
    )
    init_dispatcher(
        link_tokens=link_tokens,
        web_base_url=settings.web_base_url,
    )
    try:
        yield
    finally:
        await shutdown_bot()
        await redis.aclose()
        await dispose_engine()


app = FastAPI(
    title="AutoTreeGen — telegram-bot",
    description="Telegram webhook receiver + opt-in account linking (Phase 14.0). См. ADR-0040.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(link.router)
