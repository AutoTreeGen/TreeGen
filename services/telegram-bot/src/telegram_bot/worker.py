"""arq worker для telegram-bot (Phase 14.2).

Отдельный процесс, исполняющий cron-jobs (weekly digest). Запуск::

    uv run arq telegram_bot.worker.WorkerSettings

arq читает атрибуты класса ``WorkerSettings`` (без инстанцирования) —
``redis_settings``, ``cron_jobs``, ``functions``, ``on_startup``,
``on_shutdown``. Это конвенция arq, не наша произвольная схема.

Стартовый хук (:func:`startup`) собирает long-lived dependencies
(Bot, Redis, httpx-client, async-session factory) и кладёт в ``ctx`` —
оттуда их подхватывает :func:`telegram_bot.jobs.digest.send_weekly_digest`.

Why a separate worker (а не FastAPI-lifespan'е): cron-jobs должны идти
параллельно с веб-сервером, и arq-процесс делит redis-channel с
producer'ами (если когда-нибудь добавятся ad-hoc enqueue'и). См.
параллель :mod:`parser_service.worker`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar, Final

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from telegram_bot.config import get_settings
from telegram_bot.database import get_engine, init_engine
from telegram_bot.jobs.digest import send_weekly_digest

logger = logging.getLogger(__name__)

# Default совпадает с docker-compose Redis (см. docker-compose.yml).
DEFAULT_REDIS_URL: Final = "redis://localhost:6379/0"

QUEUE_NAME: Final = "telegram-bot"


def _redis_settings_from_env() -> RedisSettings:
    """Построить ``RedisSettings`` из ENV ``REDIS_URL``."""
    url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    return RedisSettings.from_dsn(url)


async def startup(ctx: dict[str, Any]) -> None:
    """Инициализировать Bot/Redis/httpx/session_factory в ``ctx``.

    Bot создаётся **отдельно** от FastAPI-lifespan'ового — worker и
    web-process работают как разные runtime'ы; share один token, но
    у каждого собственный aiohttp-сессион (closed в shutdown).
    """
    settings = get_settings()
    init_engine(settings.database_url)

    bot_session = AiohttpSession()
    bot = Bot(
        token=settings.bot_token or "0:dummy-token-for-local-dev",
        session=bot_session,
        default=DefaultBotProperties(parse_mode=None),
    )

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    http_client = httpx.AsyncClient()

    ctx["bot"] = bot
    ctx["redis"] = redis
    ctx["http_client"] = http_client
    ctx["session_factory"] = async_sessionmaker(get_engine(), expire_on_commit=False)
    ctx["parser_base_url"] = settings.parser_service_base_url
    ctx["parser_token"] = settings.parser_service_internal_token
    ctx["web_base_url"] = settings.web_base_url

    logger.info("telegram-bot arq worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Закрыть long-lived ресурсы в обратном порядке."""
    http_client: httpx.AsyncClient | None = ctx.get("http_client")
    if http_client is not None:
        await http_client.aclose()
    redis: Redis | None = ctx.get("redis")
    if redis is not None:
        await redis.aclose()
    bot: Bot | None = ctx.get("bot")
    if bot is not None:
        await bot.session.close()
    logger.info("telegram-bot arq worker stopped")


class WorkerSettings:
    """Конфигурация arq-worker'а (class-level, читается arq CLI).

    Cron-jobs:

    * ``send_weekly_digest`` — каждый понедельник 09:00 UTC.
    """

    redis_settings: ClassVar[RedisSettings] = _redis_settings_from_env()
    queue_name: ClassVar[str] = QUEUE_NAME
    functions: ClassVar[list[Any]] = [send_weekly_digest]
    cron_jobs: ClassVar[list[Any]] = [
        cron(
            send_weekly_digest,
            weekday="mon",
            hour=9,
            minute=0,
            run_at_startup=False,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
