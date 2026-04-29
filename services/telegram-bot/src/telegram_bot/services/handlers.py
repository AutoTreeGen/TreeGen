"""Aiogram command-handlers (Phase 14.0).

Каждый handler:
* `/start` — минтит link-token, отвечает ссылкой на web.
* `/imports`, `/persons`, `/tree` — Phase 14.0 stub'ы. Реальный fetch
  user-данных — Phase 14.1 (см. ADR-0040 §«Phase 14.1 (deferred)»).

Aiogram 3.x kwargs-DI: ``Dispatcher.feed_update(..., link_tokens=...,
web_base_url=...)`` пробрасывает эти аргументы в handlers по имени.
В тестах мы передаём mock-store без поднятия Dispatcher'а.
"""

from __future__ import annotations

import logging
from typing import Final

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from telegram_bot.services.link_tokens import LinkTokenStore

_LOG: Final = logging.getLogger(__name__)

_STUB_14_1: Final = (
    "Эта команда появится в Phase 14.1 — пока бот только умеет связывать аккаунт через /start."
)

router = Router(name="telegram_bot.commands")


@router.message(Command("start"))
async def handle_start(
    message: Message,
    link_tokens: LinkTokenStore,
    web_base_url: str,
) -> None:
    """Минтит one-time link-token и отвечает ссылкой на web.

    `link_tokens` и `web_base_url` инжектятся через
    ``Dispatcher.feed_update(..., link_tokens=..., web_base_url=...)``.
    """
    if message.from_user is None:
        await message.answer(
            "Не удалось определить ваш Telegram ID — попробуйте написать боту в личных сообщениях.",
        )
        return
    token = await link_tokens.mint(
        tg_chat_id=message.chat.id,
        tg_user_id=message.from_user.id,
    )
    url = f"{web_base_url.rstrip('/')}/telegram/link?token={token}"
    await message.answer(
        "Привет! Чтобы связать этот Telegram-аккаунт с твоим "
        f"TreeGen-профилем, открой ссылку:\n\n{url}\n\n"
        "Ссылка действует 15 минут и одноразовая.",
    )


@router.message(Command("imports"))
async def handle_imports(message: Message) -> None:
    """Stub до Phase 14.1 — fetch из parser-service."""
    await message.answer(f"/imports — {_STUB_14_1}")


@router.message(Command("persons"))
async def handle_persons(message: Message) -> None:
    """Stub до Phase 14.1 — fetch из parser-service."""
    await message.answer(f"/persons — {_STUB_14_1}")


@router.message(Command("tree"))
async def handle_tree(message: Message) -> None:
    """Stub до Phase 14.1 — fetch активного дерева user'а."""
    await message.answer(f"/tree — {_STUB_14_1}")
