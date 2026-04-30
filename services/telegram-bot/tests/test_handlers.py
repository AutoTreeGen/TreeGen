"""Tests for command handlers (Phase 14.0 → 14.1).

Handlers вызваются напрямую с mock Message — никакого Bot/HTTP не
поднимаем. Pure-render functions (`render_imports`, `render_persons`,
`render_tree`) тестируются без mock'ов вообще.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram_bot.services.db_queries import (
    ImportSummary,
    PersonSearchHit,
    TreeSummary,
)
from telegram_bot.services.handlers import (
    handle_start,
    render_imports,
    render_persons,
    render_tree,
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


# -----------------------------------------------------------------------------
# /start (Phase 14.0)
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# render_imports (pure)
# -----------------------------------------------------------------------------


def test_render_imports_empty_list() -> None:
    text, keyboard = render_imports([], web_base_url="https://web.test")
    assert "пока нет импортов" in text.lower() or "нет импортов" in text
    # Even when empty, keyboard should still offer Refresh + View on web.
    assert keyboard is not None
    assert len(keyboard.inline_keyboard) == 1
    buttons = keyboard.inline_keyboard[0]
    assert any(b.callback_data == "imports:refresh" for b in buttons)
    assert any((b.url or "").startswith("https://web.test") for b in buttons)


def test_render_imports_lists_jobs_with_filenames() -> None:
    now = dt.datetime(2026, 4, 30, 12, 0, tzinfo=dt.UTC)
    imports = [
        ImportSummary(
            id=uuid.uuid4(),
            tree_id=uuid.uuid4(),
            status="succeeded",
            source_filename="family.ged",
            created_at=now,
            finished_at=now,
        ),
        ImportSummary(
            id=uuid.uuid4(),
            tree_id=uuid.uuid4(),
            status="failed",
            source_filename=None,
            created_at=now - dt.timedelta(hours=2),
            finished_at=None,
        ),
    ]
    text, keyboard = render_imports(imports, web_base_url="https://web.test/")
    assert "family.ged" in text
    assert "succeeded" in text
    assert "failed" in text
    assert "(без имени)" in text
    # trailing slash в web_base_url должен схлопнуться.
    button_urls = [b.url for row in keyboard.inline_keyboard for b in row if b.url is not None]
    assert all("https://web.test/" not in url or url.count("//") == 1 for url in button_urls)


# -----------------------------------------------------------------------------
# render_persons (pure)
# -----------------------------------------------------------------------------


def test_render_persons_empty() -> None:
    text, keyboard = render_persons([], web_base_url="https://web.test")
    assert "Никого не нашли" in text
    assert keyboard is None


def test_render_persons_creates_one_button_per_hit() -> None:
    pid1 = uuid.uuid4()
    pid2 = uuid.uuid4()
    hits = [
        PersonSearchHit(id=pid1, primary_name="John Smith", sex="M"),
        PersonSearchHit(id=pid2, primary_name=None, sex="U"),
    ]
    text, keyboard = render_persons(hits, web_base_url="https://web.test")
    assert "Найдено 2" in text
    assert "John Smith" in text
    assert "(без имени)" in text
    assert keyboard is not None
    # 2 hits → 2 button rows (one per hit).
    assert len(keyboard.inline_keyboard) == 2
    urls = [row[0].url for row in keyboard.inline_keyboard]
    assert urls[0] == f"https://web.test/persons/{pid1}"
    assert urls[1] == f"https://web.test/persons/{pid2}"


# -----------------------------------------------------------------------------
# render_tree (pure)
# -----------------------------------------------------------------------------


def test_render_tree_with_last_update() -> None:
    tid = uuid.uuid4()
    tree = TreeSummary(
        id=tid,
        name="Smith family",
        persons_count=42,
        last_updated_at=dt.datetime(2026, 4, 30, 9, 0, tzinfo=dt.UTC),
    )
    text, keyboard = render_tree(tree, web_base_url="https://web.test")
    assert "Smith family" in text
    assert "42" in text
    assert "2026-04-30" in text
    # Keyboard has Open + Stats buttons.
    assert keyboard is not None
    urls = [b.url for row in keyboard.inline_keyboard for b in row]
    assert f"https://web.test/trees/{tid}/persons" in urls
    assert f"https://web.test/trees/{tid}/stats" in urls


def test_render_tree_no_last_update_falls_back_to_dash() -> None:
    tid = uuid.uuid4()
    tree = TreeSummary(id=tid, name="Empty", persons_count=0, last_updated_at=None)
    text, _ = render_tree(tree, web_base_url="https://web.test")
    assert "Empty" in text
    assert "—" in text
