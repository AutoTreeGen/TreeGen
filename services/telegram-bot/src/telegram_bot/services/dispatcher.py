"""aiogram Dispatcher и Bot factory.

Singleton-подход аналогичен ``database.py`` — сервис создаёт Bot и
Dispatcher один раз при lifespan-startup, тесты используют свои
инстансы с ``httpx.MockTransport``.
"""

from __future__ import annotations

import logging
from typing import Final

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from telegram_bot.services.handlers import router as commands_router
from telegram_bot.services.link_tokens import LinkTokenStore

_LOG: Final = logging.getLogger(__name__)

_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def init_bot(*, bot_token: str, bot_api_base_url: str) -> Bot:
    """Создать ``Bot`` (singleton). Идемпотентно для тестов."""
    global _bot  # noqa: PLW0603
    session = AiohttpSession(api=_make_telegram_api(bot_api_base_url))
    _bot = Bot(
        token=bot_token or _PLACEHOLDER_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=None),
    )
    return _bot


def init_dispatcher(
    *,
    link_tokens: LinkTokenStore,
    web_base_url: str,
) -> Dispatcher:
    """Создать Dispatcher с зарегистрированным command-router'ом."""
    global _dispatcher  # noqa: PLW0603
    dispatcher = Dispatcher()
    dispatcher.include_router(commands_router)
    # Эти ключи становятся kwargs handlers'ов через aiogram kwargs-DI.
    dispatcher["link_tokens"] = link_tokens
    dispatcher["web_base_url"] = web_base_url
    _dispatcher = dispatcher
    return dispatcher


def get_bot() -> Bot:
    """Вернуть проинициализированный Bot или ``RuntimeError``."""
    if _bot is None:
        msg = "Bot not initialized; call init_bot() first."
        raise RuntimeError(msg)
    return _bot


def get_dispatcher() -> Dispatcher:
    """Вернуть проинициализированный Dispatcher или ``RuntimeError``."""
    if _dispatcher is None:
        msg = "Dispatcher not initialized; call init_dispatcher() first."
        raise RuntimeError(msg)
    return _dispatcher


async def shutdown_bot() -> None:
    """Закрыть HTTP-сессию Bot'а."""
    global _bot, _dispatcher  # noqa: PLW0603
    if _bot is not None:
        await _bot.session.close()
    _bot = None
    _dispatcher = None


# ---- Internals ----

# aiogram'у нужен хотя бы placeholder-токен правильного формата:
# `<bot_id>:<auth_string>` — иначе он бросает в ``Bot.__init__``.
# Мы используем 0:dummy в локальной разработке, когда настоящего
# токена нет; в проде ``settings.bot_token`` обязателен.
_PLACEHOLDER_TOKEN: Final = "0:dummy-token-for-local-dev"


def _make_telegram_api(base_url: str) -> object:
    """Собрать ``TelegramAPIServer`` для override base_url'а.

    Импорт внутри функции — символ может перемещаться между минорными
    версиями aiogram'а; ``TelegramAPIServer.from_base`` стабилен и
    покрывает default-кейс (`https://api.telegram.org`) тоже.
    """
    from aiogram.client.telegram import TelegramAPIServer  # noqa: PLC0415

    return TelegramAPIServer.from_base(base_url)
