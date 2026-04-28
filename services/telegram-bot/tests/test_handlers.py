"""Tests for command handlers (Phase 14.0).

Handlers вызваются напрямую с mock Message — никакого Bot/HTTP не
поднимаем. Этого достаточно для unit-теста: handler-логика не
зависит от того, через какой transport отправляется ответ.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram_bot.services.handlers import (
    handle_imports,
    handle_persons,
    handle_start,
    handle_tree,
)
from telegram_bot.services.link_tokens import LinkTokenStore


def _make_message(*, chat_id: int = 100, user_id: int = 200, text: str = "/start") -> object:
    """Сконструировать минимальный Message-mock с async .answer()."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="private"),
        from_user=SimpleNamespace(id=user_id, is_bot=False, first_name="Test"),
        text=text,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_start_mints_token_and_replies_with_link(
    link_store: LinkTokenStore,
) -> None:
    msg = _make_message(chat_id=100, user_id=200)
    await handle_start(msg, link_tokens=link_store, web_base_url="https://web.test")
    msg.answer.assert_awaited_once()
    reply = msg.answer.await_args.args[0]
    assert "https://web.test/telegram/link?token=" in reply
    # Проверяем, что выпущенный токен реально есть в store.
    token = reply.split("token=")[-1].split("\n")[0].strip()
    payload = await link_store.consume(token)
    assert payload is not None
    assert payload.tg_chat_id == 100
    assert payload.tg_user_id == 200


@pytest.mark.asyncio
async def test_start_handles_missing_from_user(
    link_store: LinkTokenStore,
) -> None:
    msg = _make_message()
    msg.from_user = None
    await handle_start(msg, link_tokens=link_store, web_base_url="https://web.test")
    msg.answer.assert_awaited_once()
    reply = msg.answer.await_args.args[0]
    assert "Telegram ID" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "label"),
    [
        (handle_imports, "/imports"),
        (handle_persons, "/persons"),
        (handle_tree, "/tree"),
    ],
)
async def test_stub_handlers_reply_with_phase_14_1_message(
    handler: object,
    label: str,
) -> None:
    msg = _make_message(text=label)
    await handler(msg)  # type: ignore[operator]
    msg.answer.assert_awaited_once()
    reply = msg.answer.await_args.args[0]
    assert label in reply
    assert "Phase 14.1" in reply
