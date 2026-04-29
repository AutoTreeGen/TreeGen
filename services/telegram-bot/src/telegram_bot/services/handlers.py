"""Aiogram command-handlers (Phase 14.0 → 14.1).

Каждый handler:

* `/start` — минтит link-token, отвечает ссылкой на web (Phase 14.0).
* `/imports` — последние 5 import jobs из owned trees + inline keyboard
  (Refresh + View on web).
* `/persons <name>` — top-5 persons substring-match в active tree.
* `/tree` — info по active tree (first-owned by created_at).
* `/subscribe` — toggle notifications_enabled на linked-chat'е.

Aiogram 3.x kwargs-DI: ``Dispatcher.feed_update(..., link_tokens=...,
web_base_url=..., session_factory=...)`` пробрасывает эти аргументы в
handlers по имени.
"""

from __future__ import annotations

import logging
from typing import Final

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_bot.services.db_queries import (
    ImportSummary,
    PersonSearchHit,
    TreeSummary,
    fetch_active_tree,
    fetch_recent_imports,
    resolve_user_id_from_chat,
    search_persons_in_active_tree,
    toggle_notifications,
)
from telegram_bot.services.link_tokens import LinkTokenStore

_LOG: Final = logging.getLogger(__name__)

# Maximum length for /persons <name> argument — защищает от giant ILIKE-pattern'ов
# и от случайных bot-spam'еров. 200 символов покрывает реальные имена с запасом.
_MAX_QUERY_LEN: Final = 200

# Сообщение когда user не залинкован — единое для всех authenticated commands.
_NOT_LINKED_MSG: Final = (
    "Этот чат пока не привязан к TreeGen-аккаунту. Запусти /start, чтобы создать связь."
)
_NO_ACTIVE_TREE_MSG: Final = (
    "У тебя пока нет деревьев в TreeGen. Открой web-приложение, создай дерево и попробуй снова."
)

router = Router(name="telegram_bot.commands")


# -----------------------------------------------------------------------------
# /start (Phase 14.0)
# -----------------------------------------------------------------------------


@router.message(Command("start"))
async def handle_start(
    message: Message,
    link_tokens: LinkTokenStore,
    web_base_url: str,
) -> None:
    """Минтит one-time link-token и отвечает ссылкой на web."""
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


# -----------------------------------------------------------------------------
# /imports — last 5 import jobs across owned trees
# -----------------------------------------------------------------------------


@router.message(Command("imports"))
async def handle_imports(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    web_base_url: str,
) -> None:
    """Показать последние 5 импортов owned-деревьев + inline keyboard."""
    async with session_factory() as session:
        user_id = await resolve_user_id_from_chat(session, tg_chat_id=message.chat.id)
        if user_id is None:
            await message.answer(_NOT_LINKED_MSG)
            return
        imports = await fetch_recent_imports(session, user_id=user_id, limit=5)

    text, keyboard = render_imports(imports, web_base_url=web_base_url)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "imports:refresh")
async def handle_imports_refresh(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    web_base_url: str,
) -> None:
    """Refresh-кнопка под /imports — пересобирает текст и keyboard."""
    if callback.message is None:
        await callback.answer("Сообщение недоступно для обновления.")
        return
    chat_id = callback.message.chat.id
    async with session_factory() as session:
        user_id = await resolve_user_id_from_chat(session, tg_chat_id=chat_id)
        if user_id is None:
            await callback.answer("Связь не найдена.", show_alert=True)
            return
        imports = await fetch_recent_imports(session, user_id=user_id, limit=5)

    text, keyboard = render_imports(imports, web_base_url=web_base_url)
    edit = getattr(callback.message, "edit_text", None)
    if edit is not None:
        await edit(text, reply_markup=keyboard)
    await callback.answer("Обновлено.")


def render_imports(
    imports: list[ImportSummary], *, web_base_url: str
) -> tuple[str, InlineKeyboardMarkup]:
    """Format /imports output — pure function для unit-теста."""
    if not imports:
        text = "У тебя пока нет импортов. Загрузи .ged файл на web-сайте."
    else:
        lines = ["Последние импорты:"]
        for job in imports:
            filename = job.source_filename or "(без имени)"
            ts = job.created_at.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"• {ts} — {filename} — {job.status}")
        text = "\n".join(lines)

    web = web_base_url.rstrip("/")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="imports:refresh"),
                InlineKeyboardButton(text="🌐 Открыть на web", url=f"{web}/dashboard"),
            ]
        ]
    )
    return (text, keyboard)


# -----------------------------------------------------------------------------
# /persons <name> — substring search in active tree
# -----------------------------------------------------------------------------


@router.message(Command("persons"))
async def handle_persons(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    web_base_url: str,
) -> None:
    """Top-5 persons substring-match в active tree."""
    raw_query = (command.args or "").strip()
    if not raw_query:
        await message.answer("Использование: /persons <имя или фамилия>")
        return
    if len(raw_query) > _MAX_QUERY_LEN:
        await message.answer(f"Слишком длинный запрос (>{_MAX_QUERY_LEN} символов).")
        return

    async with session_factory() as session:
        user_id = await resolve_user_id_from_chat(session, tg_chat_id=message.chat.id)
        if user_id is None:
            await message.answer(_NOT_LINKED_MSG)
            return
        tree_id, hits = await search_persons_in_active_tree(
            session, user_id=user_id, query=raw_query, limit=5
        )

    if tree_id is None:
        await message.answer(_NO_ACTIVE_TREE_MSG)
        return

    text, keyboard = render_persons(hits, web_base_url=web_base_url)
    await message.answer(text, reply_markup=keyboard)


def render_persons(
    hits: list[PersonSearchHit], *, web_base_url: str
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Format /persons output. Pure function для unit-теста."""
    if not hits:
        return ("Никого не нашли по этому запросу.", None)

    web = web_base_url.rstrip("/")
    lines = [f"Найдено {len(hits)} (показаны top-5):"]
    buttons: list[list[InlineKeyboardButton]] = []
    for i, hit in enumerate(hits, start=1):
        display = hit.primary_name or "(без имени)"
        lines.append(f"{i}. {display}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🌐 {display}",
                    url=f"{web}/persons/{hit.id}",
                )
            ]
        )
    return ("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


# -----------------------------------------------------------------------------
# /tree — active tree info
# -----------------------------------------------------------------------------


@router.message(Command("tree"))
async def handle_tree(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    web_base_url: str,
) -> None:
    """Show active tree (first-owned by created_at) info."""
    async with session_factory() as session:
        user_id = await resolve_user_id_from_chat(session, tg_chat_id=message.chat.id)
        if user_id is None:
            await message.answer(_NOT_LINKED_MSG)
            return
        active = await fetch_active_tree(session, user_id=user_id)

    if active is None:
        await message.answer(_NO_ACTIVE_TREE_MSG)
        return

    text, keyboard = render_tree(active, web_base_url=web_base_url)
    await message.answer(text, reply_markup=keyboard)


def render_tree(tree: TreeSummary, *, web_base_url: str) -> tuple[str, InlineKeyboardMarkup]:
    """Format /tree output. Pure function для unit-теста."""
    last = (
        tree.last_updated_at.strftime("%Y-%m-%d %H:%M UTC")
        if tree.last_updated_at is not None
        else "—"
    )
    text = (
        f"Активное дерево: {tree.name}\nPersons: {tree.persons_count}\nПоследнее обновление: {last}"
    )
    web = web_base_url.rstrip("/")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Открыть на web",
                    url=f"{web}/trees/{tree.id}/persons",
                ),
                InlineKeyboardButton(
                    text="📊 Статистика",
                    url=f"{web}/trees/{tree.id}/stats",
                ),
            ]
        ]
    )
    return (text, keyboard)


# -----------------------------------------------------------------------------
# /subscribe — toggle notifications_enabled
# -----------------------------------------------------------------------------


@router.message(Command("subscribe"))
async def handle_subscribe(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Toggle ``notifications_enabled`` для chat'а.

    Privacy-by-default (ADR-0056): после link-flow chat НЕ подписан;
    user должен явно вызвать /subscribe чтобы получать push'и.
    Повторный /subscribe отключает (toggle).
    """
    async with session_factory() as session:
        linked, new_state = await toggle_notifications(session, tg_chat_id=message.chat.id)
        await session.commit()

    if not linked:
        await message.answer(_NOT_LINKED_MSG)
        return
    if new_state:
        await message.answer(
            "✅ Подписка на нотификации включена. "
            "Ты будешь получать push'и о новых DNA matches и завершённых импортах."
        )
    else:
        await message.answer("🔕 Подписка отключена. /subscribe ещё раз чтобы снова включить.")
