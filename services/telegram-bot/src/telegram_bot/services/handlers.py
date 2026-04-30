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
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.types.inline_query_result_union import InlineQueryResultUnion
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_bot.services.db_queries import (
    ImportSummary,
    InlineSearchHit,
    PersonSearchHit,
    TreeSummary,
    fetch_active_tree,
    fetch_recent_imports,
    inline_search_persons_in_active_tree,
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


# -----------------------------------------------------------------------------
# Inline-search (Phase 14.2)
# -----------------------------------------------------------------------------

# Cache TTL для inline-результатов на стороне Telegram. 60 секунд —
# баланс: пользователь может уточнять запрос побуквенно (новый кэш каждые
# 60 сек), но повторный набор того же `@bot Иванов` не дёргает наш
# backend каждый раз.
_INLINE_CACHE_SECONDS: Final = 60

# Максимум inline-результатов (Telegram'ом разрешено до 50, но 5 — UX-кап
# из spec'а Phase 14.2: ограниченный prompt в чате).
_INLINE_RESULTS_LIMIT: Final = 5


def parse_inline_query(text: str) -> tuple[str, str | None, int | None]:
    """Распарсить inline-query формата ``surname [given...] [year]``.

    * surname — первый токен (обязателен). Если пустая строка — caller
      покажет prompt «начни с фамилии».
    * year — первый 4-значный числовой токен после surname (если есть).
      Trail-токены после year считаются частью given_name (редкий кейс,
      пользователь обычно ставит год в конце).
    * given — остальные токены, joined пробелом. ``None`` если их нет.

    Pure function для unit-теста — без БД, без aiogram.
    """
    parts = [p for p in text.strip().split() if p]
    if not parts:
        return ("", None, None)
    surname = parts[0]
    rest = parts[1:]
    year: int | None = None
    given_parts: list[str] = []
    for tok in rest:
        if year is None and len(tok) == 4 and tok.isdigit():
            y = int(tok)
            if 1000 <= y <= 9999:
                year = y
                continue
        given_parts.append(tok)
    given = " ".join(given_parts) if given_parts else None
    return (surname, given, year)


def render_inline_results(
    hits: list[InlineSearchHit],
    *,
    web_base_url: str,
) -> list[InlineQueryResultArticle]:
    """Сконвертировать ``InlineSearchHit``-ы в Telegram inline-results.

    Каждая article:

    * ``id`` — стрингифицированный person.id (≤64 байт guaranteed для UUID);
    * ``title`` — primary_name либо «Без имени»;
    * ``description`` — ``"YYYY • place"``, опускается если оба пусты;
    * ``input_message_content`` — deep-link на web с краткой подписью;
    * ``url`` — тот же deep-link (Telegram отдельной ссылкой подсветит).

    Pure function для unit-теста.
    """
    web = web_base_url.rstrip("/")
    results: list[InlineQueryResultArticle] = []
    for hit in hits:
        display = hit.primary_name or "Без имени"
        deep_link = f"{web}/persons/{hit.id}?from=tg"
        desc_parts: list[str] = []
        if hit.birth_year is not None:
            desc_parts.append(str(hit.birth_year))
        if hit.birth_place_label:
            desc_parts.append(hit.birth_place_label)
        description = " • ".join(desc_parts) if desc_parts else None
        results.append(
            InlineQueryResultArticle(
                id=str(hit.id),
                title=display,
                description=description,
                url=deep_link,
                input_message_content=InputTextMessageContent(
                    message_text=f"<b>{display}</b>\n{deep_link}",
                    parse_mode="HTML",
                    link_preview_options=None,
                ),
            )
        )
    return results


@router.inline_query()
async def handle_inline_query(
    inline_query: InlineQuery,
    session_factory: async_sessionmaker[AsyncSession],
    web_base_url: str,
) -> None:
    """Inline-search по active tree (Phase 14.2).

    Aiogram dispatch'ит сюда любой ``@bot <text>`` query. Ветки:

    * Пользователь не залинкован: пустой массив + ``switch_pm_text``
      «Link your account: /start».
    * Залинкован, но нет деревьев: пустой массив + кнопка «Choose a tree»
      (deep-link в web).
    * Запрос пустой / только год: prompt «начни с фамилии».
    * Иначе — top-5 articles c deep-link'ами.

    Не sleep'ит и не делает HTTP. Cache_time=60 → Telegram не будет
    долбить нас на каждое нажатие клавиши.
    """
    surname, given, year = parse_inline_query(inline_query.query)

    async with session_factory() as session:
        user_id = await resolve_user_id_from_chat(session, tg_chat_id=inline_query.from_user.id)
        if user_id is None:
            await inline_query.answer(
                results=[],
                cache_time=_INLINE_CACHE_SECONDS,
                is_personal=True,
                switch_pm_text="Link your account: /start",
                switch_pm_parameter="link",
            )
            return

        if not surname:
            # Пустой / только year — нужно подсказать, а не сыпать full-table.
            await inline_query.answer(
                results=[],
                cache_time=_INLINE_CACHE_SECONDS,
                is_personal=True,
                switch_pm_text="Type a surname (e.g. Ivanov 1850)",
                switch_pm_parameter="hint",
            )
            return

        tree_id, hits = await inline_search_persons_in_active_tree(
            session,
            user_id=user_id,
            surname=surname,
            given=given,
            year=year,
            limit=_INLINE_RESULTS_LIMIT,
        )

    if tree_id is None:
        await inline_query.answer(
            results=[],
            cache_time=_INLINE_CACHE_SECONDS,
            is_personal=True,
            switch_pm_text="Choose a tree",
            switch_pm_parameter="dashboard",
        )
        return

    # ``InlineQueryResultUnion``-list invariance: явный list[InlineQueryResultUnion]
    # вместо list[Article], иначе mypy ругается на список-литерал.
    results: list[InlineQueryResultUnion] = list(
        render_inline_results(hits, web_base_url=web_base_url)
    )
    await inline_query.answer(
        results=results,
        cache_time=_INLINE_CACHE_SECONDS,
        is_personal=True,
    )


# -----------------------------------------------------------------------------
# digest:unsubscribe callback (Phase 14.2)
# -----------------------------------------------------------------------------


@router.callback_query(F.data == "digest:unsubscribe")
async def handle_digest_unsubscribe(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> None:
    """Поставить opt-out флаг в Redis, чтобы worker больше не слал digest.

    Storage в Redis (а не в ``user_settings.digest_enabled``) — потому
    что в Phase 14.2 alembic-миграции не вводятся (см. task spec).
    Phase 14.3 мигрирует в столбец ``users.digest_enabled`` либо в
    ``user_settings``-таблицу.
    """
    if callback.message is None or callback.message.chat is None:
        await callback.answer("Сообщение недоступно.")
        return
    chat_id = callback.message.chat.id
    async with session_factory() as session:
        user_id = await resolve_user_id_from_chat(session, tg_chat_id=chat_id)
    if user_id is None:
        await callback.answer("Связь не найдена.", show_alert=True)
        return

    await redis.set(
        f"digest:optout:{user_id}",
        "1",
        ex=365 * 24 * 60 * 60,  # 1 year
    )
    await callback.answer("🔕 Дайджест отключён.", show_alert=False)
